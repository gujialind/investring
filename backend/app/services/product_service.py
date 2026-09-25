"""
产品管理服务

confirm_days 计算（单一实现）与产品 CRUD，从路由层提取供 REST 与 CLI 共用。
service 层只抛领域异常，不 commit。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.constants.asset_dimensions import RULE_DIMENSIONS, RULE_REQUIRED
from app.models.asset_classification import (
    AssetClassification,
    AssetClassDimensionRule,
    AssetDimensionApplicability,
)
from app.models.portfolio_position import PortfolioPosition
from app.models.price_record import PriceRecord
from app.models.product import Product
from app.models.share_change_event import ShareChangeEvent
from app.models.trade import Trade
from app.services.exceptions import BusinessError, NotFoundError
from app.services.null_guard import reject_explicit_nulls

# issue #232：product_type 合法枚举（create/update 共用校验）；
# market 枚举仅 update 守卫使用（create 不校验 market）
VALID_PRODUCT_TYPES = ("ETF", "OEF", "LOF", "CASH", "IN_TRANSIT")
VALID_MARKETS = ("CN_EXCHANGE", "CN_OTC", "HK_MUTUAL")
# 系统虚拟产品（部署期种子），product_type/market 为系统语义，禁止纠错修改
SYSTEM_PRODUCT_CODES = ("CASH", "IN_TRANSIT_BUY", "IN_TRANSIT_SELL", "IN_TRANSIT_DIVIDEND")
# issue #327：虚拟产品（不可交易）的 product_type 判定口径，产品列表默认按此排除
VIRTUAL_PRODUCT_TYPES = ("CASH", "IN_TRANSIT")

# 维度字段 → 字典 dimension（issue #128）
DIMENSION_FIELDS = ("asset_class_code", "region_code", "style_code", "size_code", "segment_code")
_DIMENSION_OF_FIELD = {
    "asset_class_code": "asset_class",
    "region_code": "region",
    "style_code": "style",
    "size_code": "size",
    "segment_code": "segment",
}
_FIELD_OF_DIMENSION = {v: k for k, v in _DIMENSION_OF_FIELD.items()}
# 规则化维度（asset_class 自身不参与规则校验）
_RULE_FIELD_OF_DIMENSION = {
    dimension: _FIELD_OF_DIMENSION[dimension] for dimension in RULE_DIMENSIONS
}
# create 路径 confirm_days 哨兵（#231/#236/#241）：区分「未传（推导）」与「显式
# null（拒绝）」——pydantic 默认值使两者在解析后不可分辨，router 仅显式传入时下发
UNSET_CONFIRM_DAYS = object()


def validate_dimension_tags(
    db: Session, dims: dict, *, changed_fields: Optional[set] = None,
) -> None:
    """校验五维度标签（create 传入值 / update 合并值），四层叠加、只收紧不放松：

    1. 每个非 NULL code 必须存在于维度字典且 dimension 匹配字段；
    2. is_active 软失效（#135）：create（changed_fields=None）所有非 NULL 值须
       active；update 仅校验 changed_fields 中实际变化字段的新值——存量引用的
       停用值不阻断对其他字段的编辑；
    3. 维度级规则（#135 矩阵落库，读 asset_class_dimension_rule）：required 缺失
       / forbidden（无规则行）有值 → 422；asset_class 为 NULL（虚拟产品）时其余
       维度必须全 NULL；大类无规则行 = 现金型全 forbidden（新建大类默认态）；
    4. 值级适用校验（#135，读 asset_dimension_applicability）：非 asset_class
       维度值必须关联产品的 asset_class，details 带 applicable_asset_classes。
    非法组合抛 INVALID_DIMENSION_TAGS（422）。
    """
    # 1+2. 存在性 / dimension 匹配 / active
    for field in DIMENSION_FIELDS:
        code = dims.get(field)
        if code is None:
            continue
        ac = db.query(AssetClassification).filter(AssetClassification.code == code).first()
        if not ac or ac.dimension != _DIMENSION_OF_FIELD[field]:
            raise BusinessError(
                "INVALID_DIMENSION_TAGS",
                f"维度值 {code} 不存在或不属于 {_DIMENSION_OF_FIELD[field]} 维度",
                details={"field": field, "code": code},
            )
        if not ac.is_active and (changed_fields is None or field in changed_fields):
            raise BusinessError(
                "INVALID_DIMENSION_TAGS",
                f"维度值 {code} 已停用",
                details={"field": field, "code": code},
            )

    asset = dims.get("asset_class_code")
    if asset is None:
        extra = [f for f in DIMENSION_FIELDS if f != "asset_class_code" and dims.get(f)]
        if extra:
            raise BusinessError(
                "INVALID_DIMENSION_TAGS",
                "未指定 asset_class 时其余维度必须为空",
                details={"fields": extra},
            )
        return

    # 3. 维度级规则（矩阵落库，DB 为运行期事实来源；无规则行的大类 = 现金型全 forbidden）
    rule_rows = db.query(AssetClassDimensionRule).filter(
        AssetClassDimensionRule.asset_class_code == asset
    ).all()
    rules = {row.dimension: row.rule for row in rule_rows}
    missing = [
        field for dimension, field in _RULE_FIELD_OF_DIMENSION.items()
        if rules.get(dimension) == RULE_REQUIRED and not dims.get(field)
    ]
    if missing:
        raise BusinessError(
            "INVALID_DIMENSION_TAGS",
            f"{asset} 缺少必填维度：{', '.join(missing)}",
            details={"asset_class_code": asset, "missing": missing},
        )
    present = [
        field for dimension, field in _RULE_FIELD_OF_DIMENSION.items()
        if rules.get(dimension) is None and dims.get(field)
    ]
    if present:
        raise BusinessError(
            "INVALID_DIMENSION_TAGS",
            f"{asset} 不允许维度：{', '.join(present)}",
            details={"asset_class_code": asset, "forbidden": present},
        )

    # 4. 值级适用校验：所选维度值必须关联该产品的 asset_class
    allowed = {
        row.dimension_value_code
        for row in db.query(AssetDimensionApplicability).filter(
            AssetDimensionApplicability.asset_class_code == asset
        )
    }
    for field in DIMENSION_FIELDS:
        if field == "asset_class_code":
            continue
        code = dims.get(field)
        if code and code not in allowed:
            applicable = [
                row.asset_class_code
                for row in db.query(AssetDimensionApplicability).filter(
                    AssetDimensionApplicability.dimension_value_code == code
                )
            ]
            raise BusinessError(
                "INVALID_DIMENSION_TAGS",
                f"维度值 {code} 不适用于 {asset}",
                details={
                    "field": field, "code": code,
                    "applicable_asset_classes": applicable,
                },
            )


def calculate_confirm_days(market: Optional[str], is_qdii: bool) -> int:
    """确认天数默认值推导器（缺省路径单一实现，issue #228）：
    - CN_EXCHANGE: 0（场内当天）
    - CN_OTC 且非 QDII: 1（T+1）
    - CN_OTC 且 QDII: 2（T+2）
    - 其他: 1

    调用点：create_product 创建时**未显式传** confirm_days 的缺省推导
    （#231/#236/#241 起显式传入优先，不经此函数）；update_product 仅在 market
    **实际变化**且未显式传 confirm_days 时重推导（issue #232 唯一例外——
    场内 0 天残留到场外会造成当日确认的资金语义错误）。
    confirm_days / is_qdii 自身的更新始终纯显式（不传不改）。
    """
    if market == "CN_EXCHANGE":
        return 0
    if market == "CN_OTC":
        return 2 if is_qdii else 1
    return 1


def resolve_product_market(
    db: Session, product_code: str, market: Optional[str] = None
) -> tuple:
    """解析产品市场（issue #83，单一实现，供 trade/product 各入口复用）：
    - market 非空：原样返回 (product_code, market)
    - 省略且该 code 仅存在一个市场：自动补全
    - 省略且存在多个市场（LOF 一码多市场）：抛 MARKET_AMBIGUOUS（422）
    - code 不存在：抛 PRODUCT_NOT_FOUND（404）
    """
    if market:
        return product_code, market
    markets = sorted(
        row[0] or ""
        for row in db.query(Product.market).filter(Product.code == product_code).all()
    )
    if not markets:
        raise NotFoundError(
            "PRODUCT_NOT_FOUND",
            f"产品 {product_code} 不存在",
            details={"product_code": product_code},
        )
    if len(markets) > 1:
        raise BusinessError(
            "MARKET_AMBIGUOUS",
            f"产品 {product_code} 存在多个市场，请指定 market",
            details={"product_code": product_code, "available_markets": markets},
        )
    return product_code, markets[0]


def build_product_name_map(db: Session, pairs: set) -> dict:
    """读侧派生公共助手（#175/#342 单一实现）：批量建 (code, market) -> name 映射（防 N+1）。
    pairs 为 {(product_code, market)}；LOF 一码多市场查出的冗余行按 pairs 过滤。"""
    if not pairs:
        return {}
    codes = {c for c, _ in pairs if c}
    return {
        (p.code, p.market): p.name
        for p in db.query(Product.code, Product.market, Product.name)
        .filter(Product.code.in_(codes))
        .all()
        if (p.code, p.market) in pairs
    }


def validate_product_type(product_type: str) -> None:
    """issue #232：product_type 枚举校验（create/update 共用）。非法值抛 INVALID_PRODUCT_TYPE（422）。"""
    if product_type not in VALID_PRODUCT_TYPES:
        raise BusinessError(
            "INVALID_PRODUCT_TYPE",
            f"非法产品类型：{product_type}",
            details={"valid": list(VALID_PRODUCT_TYPES)},
        )


def validate_nav_lag_days(market: Optional[str], nav_lag_days: Optional[int]) -> None:
    """issue #235/#240：nav_lag_days 业务校验（create/update 共用，单一实现）。

    约束全部收在服务层（#240 跟进 #5 决策）：schema 层不设 Field(ge=0)，
    同一业务规则只产生一种 422 形状——BusinessError（detail.error=
    INVALID_NAV_LAG_DAYS），依赖错误码的消费方（CLI hints、前端）可统一识别；
    此前 schema ge=0 拦截负值返回 pydantic 列表形状 422（无 error 码），
    与场内规则的两形状并存。类型错误（如字符串）仍由 pydantic 在请求解析层拒绝。

    - 必须 >= 0：负值会被快照取价侧 `_snapshot_nav_date_and_rule` 静默钳制为 0（见 #235）；
    - 场内基金（CN_EXCHANGE，ETF/上市 LOF）当日取价、无滞后：必须是 0。此规则跨字段，
      依赖 market + nav_lag_days 合并终态（create 用传入值，update 用合并后终态）。
    非法值抛 INVALID_NAV_LAG_DAYS（422）。"nav_lag_days: null" 对 NOT NULL 列语义为清除，
    一并拒绝。
    """
    if nav_lag_days is None:
        raise BusinessError(
            "INVALID_NAV_LAG_DAYS",
            "nav_lag_days 不能为 null",
            details={"nav_lag_days": nav_lag_days},
        )
    if nav_lag_days < 0:
        raise BusinessError(
            "INVALID_NAV_LAG_DAYS",
            f"nav_lag_days 必须 >= 0，收到 {nav_lag_days}",
            details={"nav_lag_days": nav_lag_days},
        )
    if market == "CN_EXCHANGE" and nav_lag_days != 0:
        raise BusinessError(
            "INVALID_NAV_LAG_DAYS",
            f"场内基金（CN_EXCHANGE）当日取价，nav_lag_days 必须为 0，收到 {nav_lag_days}",
            details={"market": market, "nav_lag_days": nav_lag_days},
        )


def validate_confirm_days(market: Optional[str], confirm_days: Optional[int]) -> None:
    """issue #240 跟进 #6：confirm_days 业务校验（create 显式传入 / update 终态共用，单一实现）。

    校验收在服务层单一实现（统一 BusinessError 形状，INVALID_CONFIRM_DAYS），
    schema 层不加 ge 约束，与 #240 跟进 #5 决策一致：
    - null 拒绝：列可空且读侧（trade_service 多处）按 `confirm_days or 0` 降级，
      存 null 会静默变当日确认（确认间隔语义静默翻转）；
    - 必须 >= 0：负值会被 `get_next_trading_day` 的 max(days, 0) 钳制为 0，
      静默变当日确认（与 #235 修复前 nav_lag_days 被静默钳制同型）；
    - 场内（CN_EXCHANGE）当天确认：必须是 0，与 calculate_confirm_days 推导一致。
    非法值抛 INVALID_CONFIRM_DAYS（422）。
    创建路径（issue #231/#236/#241）：显式传入经此校验；未传则按
    calculate_confirm_days 推导（推导值结构性合法，不经此校验）。
    """
    if confirm_days is None:
        raise BusinessError(
            "INVALID_CONFIRM_DAYS",
            "confirm_days 不能为 null",
            details={"confirm_days": confirm_days},
        )
    if confirm_days < 0:
        raise BusinessError(
            "INVALID_CONFIRM_DAYS",
            f"confirm_days 必须 >= 0，收到 {confirm_days}",
            details={"confirm_days": confirm_days},
        )
    if market == "CN_EXCHANGE" and confirm_days != 0:
        raise BusinessError(
            "INVALID_CONFIRM_DAYS",
            f"场内基金（CN_EXCHANGE）当天确认，confirm_days 必须为 0，收到 {confirm_days}",
            details={"market": market, "confirm_days": confirm_days},
        )


def _validate_identity_change(db: Session, product: Product, updates: dict) -> None:
    """issue #232：product_type / market 修改守卫（按值**实际变化**触发——
    前端编辑恒带 product_type，显式传原值不进门禁）。

    - 系统虚拟产品（CASH/IN_TRANSIT_*）禁止修改任一身份字段；
    - product_type 枚举校验 + 存在 pending trade/event 时拒绝（防确认口径半途翻转）；
    - market 枚举校验 + 目标 (code, market) 查重 + 零引用要求（任一 trade/event/
      position 引用即拒——历史数据在旧 market 定价语义下生成，迁移修键不修语义）。
    """
    # 守卫按「值实际变化」触发：前端编辑恒带 product_type，未变化不得进门禁
    # （否则有 pending 交易的产品仅改名也 422）
    type_change = "product_type" in updates and updates["product_type"] != product.product_type
    new_market = updates.get("market")
    market_change = new_market is not None and new_market != product.market
    if not (type_change or market_change):
        return

    if product.code in SYSTEM_PRODUCT_CODES:
        raise BusinessError(
            "SYSTEM_PRODUCT_PROTECTED",
            f"系统虚拟产品 {product.code} 禁止修改 product_type/market",
            details={"code": product.code},
        )

    if type_change:
        validate_product_type(updates["product_type"])
        pending_trades = db.query(Trade).filter(
            Trade.product_code == product.code,
            Trade.market == product.market,
            Trade.status == "pending",
        ).count()
        pending_events = db.query(ShareChangeEvent).filter(
            ShareChangeEvent.product_code == product.code,
            ShareChangeEvent.market == product.market,
            ShareChangeEvent.status == "pending",
        ).count()
        if pending_trades or pending_events:
            raise BusinessError(
                "PENDING_TRANSACTIONS_EXIST",
                f"产品 {product.code}({product.market}) 存在 pending 交易/事件，"
                "处理完成前禁止修改产品类型",
                details={"pending_trades": pending_trades, "pending_events": pending_events},
            )

    if market_change:
        if new_market not in VALID_MARKETS:
            raise BusinessError(
                "INVALID_MARKET",
                f"非法市场：{new_market}",
                details={"valid": list(VALID_MARKETS)},
            )
        exists = db.query(Product).filter(
            Product.code == product.code, Product.market == new_market
        ).first()
        if exists:
            raise BusinessError(
                "ALREADY_EXISTS",
                f"产品 {product.code}({new_market}) 已存在",
                http_status=400,
            )
        references = {
            "trades": db.query(Trade).filter(
                Trade.product_code == product.code, Trade.market == product.market
            ).count(),
            "events": db.query(ShareChangeEvent).filter(
                ShareChangeEvent.product_code == product.code,
                ShareChangeEvent.market == product.market,
            ).count(),
            "positions": db.query(PortfolioPosition).filter(
                PortfolioPosition.product_code == product.code,
                PortfolioPosition.market == product.market,
            ).count(),
        }
        if any(references.values()):
            raise BusinessError(
                "MARKET_CHANGE_REFERENCED",
                f"产品 {product.code}({product.market}) 存在交易/事件/持仓引用，"
                "禁止修改市场；请在正确市场新建产品记录并补录",
                details={"references": references},
            )


# price_record 随行迁移时逐字段搬运的列：从表模型派生，
# 排除主键/复合键列与 DB 自维护时间戳，新增列自动随行不留坑
_PRICE_RECORD_MOVE_FIELDS = tuple(
    col.name for col in PriceRecord.__table__.columns
    if col.name not in {"id", "product_code", "market", "created_at", "updated_at"}
)


def _migrate_product_market(db: Session, product: Product, new_market: str) -> None:
    """issue #232：零引用产品改 market（复合主键变更）。

    price_record 随行迁移走「删 → 改父 → 重插」：MySQL/SQLite 外键约束即时检查，
    先改父或先改子都违约，该序列跨方言安全。调用前须已通过零引用守卫。
    迁移后挂 market_change_hint 提示重新同步行情（旧行情按旧市场通道同步，可能口径不符）。
    """
    old_market = product.market
    records = db.query(PriceRecord).filter(
        PriceRecord.product_code == product.code, PriceRecord.market == old_market
    ).all()
    moved = [
        {field: getattr(record, field) for field in _PRICE_RECORD_MOVE_FIELDS}
        for record in records
    ]
    for record in records:
        db.delete(record)
    if records:
        db.flush()
    product.market = new_market
    db.flush()
    for data in moved:
        db.add(PriceRecord(product_code=product.code, market=new_market, **data))
    if moved:
        product.market_change_hint = (
            f"market {old_market}→{new_market}：{len(moved)} 条行情已随行迁移；"
            "旧行情按原市场通道同步，口径可能不符，建议 sync-history 重新回填"
        )


def create_product(
    db: Session,
    *,
    code: str,
    market: Optional[str],
    name: str,
    product_type: str,
    asset_class_code: Optional[str] = None,
    region_code: Optional[str] = None,
    style_code: Optional[str] = None,
    size_code: Optional[str] = None,
    segment_code: Optional[str] = None,
    is_qdii: bool = False,
    nav_lag_days: int = 0,
    confirm_days: Optional[int] = UNSET_CONFIRM_DAYS,  # 哨兵区分未传/显式 null
    data_source: Optional[str] = None,
    sync_history: bool = False,
) -> Product:
    """创建产品（confirm_days 显式优先、缺省推导，校验五维度适用矩阵）。不 commit。

    confirm_days（issue #231/#236/#241）：显式传入优先（含显式 null，走
    validate_confirm_days 拒绝）；未传（UNSET_CONFIRM_DAYS 哨兵）按
    market+is_qdii 经 calculate_confirm_days 推导。

    nav_lag_days（issue #228）：快照估值取价滞后交易日数，由调用方显式传入
    （场外 QDII / 互认基金传 1），不做推导。

    sync_history=True 时（issue #90）创建后立即回填历史净值；
    同步失败不回滚产品创建，结果挂在返回对象的瞬态属性 sync_result。
    """
    existing = db.query(Product).filter(
        Product.code == code, Product.market == market
    ).first()
    if existing:
        raise BusinessError("ALREADY_EXISTS", f"产品 {code}({market}) 已存在", http_status=400)

    validate_product_type(product_type)
    # issue #235/#240：nav_lag_days >= 0；场内（CN_EXCHANGE）必须 0（服务层单一实现，
    # 统一 INVALID_NAV_LAG_DAYS 形状）
    validate_nav_lag_days(market or "", nav_lag_days)

    dims = {
        "asset_class_code": asset_class_code, "region_code": region_code,
        "style_code": style_code, "size_code": size_code, "segment_code": segment_code,
    }
    validate_dimension_tags(db, dims)

    # issue #231/#236/#241：confirm_days 显式优先（含显式 null，校验拒绝；market 归一
    # 与推导同口径）、缺省按 market+is_qdii 推导（推导值结构性合法 0/1/2，无需校验）
    if confirm_days is not UNSET_CONFIRM_DAYS:
        validate_confirm_days(market or "CN_OTC", confirm_days)
    else:
        confirm_days = calculate_confirm_days(market or "CN_OTC", is_qdii)
    product = Product(
        code=code,
        market=market or "",
        name=name,
        product_type=product_type,
        confirm_days=confirm_days,
        nav_lag_days=nav_lag_days,
        is_qdii=is_qdii,
        data_source=data_source,
        **dims,
    )
    db.add(product)

    if sync_history:
        from datetime import date as _date
        from app.services.market_data_service import sync_price_data

        db.flush()
        try:
            result = sync_price_data(db, code, product.market, None, _date.today())
            product.sync_result = {
                "success": bool(result.get("success")),
                "message": result.get("message"),
                "synced_count": result.get("synced_count"),
            }
        except Exception as e:  # 同步失败不阻断创建，用户可事后 sync-history 补回填
            product.sync_result = {"success": False, "message": str(e), "synced_count": 0}

    return product


def update_product(
    db: Session,
    *,
    code: str,
    market: str,
    updates: dict,
) -> Product:
    """更新产品信息（仅更新显式传入字段）。不 commit。

    confirm_days / nav_lag_days 为显式更新（不传不改，issue #228），唯一例外：
    market 实际变化且未显式传 confirm_days 时按创建规则重推导（issue #232，
    见 calculate_confirm_days）。
    """
    product = db.query(Product).filter(
        Product.code == code, Product.market == market
    ).first()
    if not product:
        raise NotFoundError("NOT_FOUND", f"产品 {code}({market}) 不存在")

    # issue #232：身份字段（product_type/market）守卫——枚举/系统产品/pending/零引用。
    # 先于 #573 的显式 null 收口：本守卫只在值**实际变化**时进门禁，且自身会先拒非法值，
    # 收口若排在它前面会抢占 SYSTEM_PRODUCT_PROTECTED / PENDING_TRANSACTIONS_EXIST /
    # INVALID_PRODUCT_TYPE 等更可诊断的专用码（同体实测：系统产品 {"name": null,
    # "product_type": "ETF"} 收口前置时返回 INVALID_PARAM，后置时回到
    # SYSTEM_PRODUCT_PROTECTED）。两类错误都在写库前，不影响「拒绝即零写入」。
    if "product_type" in updates or "market" in updates:
        _validate_identity_change(db, product, updates)

    # issue #573：显式 null 收口（仍在维度/取价/确认天数校验与全部写操作之前，
    # 拒绝即零写入）——is_qdii/name 落 NULL 会让 ProductResponse 校验 500
    # 且此后该行 GET 恒 500；market 显式 null 此前是静默 no-op（调用方以为腾空/重置），
    # 一并拒绝。五个维度标签 null = 清标签（合并终态仍交 validate_dimension_tags）；
    # product_type/confirm_days/nav_lag_days 由各自专用校验器收口（保留专用错误码）。
    reject_explicit_nulls(
        updates,
        allow={
            "product_type", "confirm_days", "nav_lag_days",
            "asset_class_code", "region_code", "style_code",
            "size_code", "segment_code",
        },
    )

    # 维度标签按合并后结果校验适用矩阵（部分更新不允许造成非法组合）；
    # is_active 仅对实际变化的字段校验（#135：存量引用停用值不阻断其他编辑）
    if any(f in updates for f in DIMENSION_FIELDS):
        merged = {f: updates.get(f, getattr(product, f)) for f in DIMENSION_FIELDS}
        changed = {
            f for f in DIMENSION_FIELDS
            if f in updates and updates[f] != getattr(product, f)
        }
        validate_dimension_tags(db, merged, changed_fields=changed)

    updates = dict(updates)
    new_market = updates.pop("market", None)
    # issue #235/#240：nav_lag_days 业务校验——按「合并后终态」判断（market / nav_lag_days 任一
    # 变化均校验最终合法性），服务层单一实现（统一 INVALID_NAV_LAG_DAYS 形状）；跨字段规则：
    # 场内（CN_EXCHANGE）必须 0。因此在 market 迁移（pop）前用终态 market/lag 校验，避免
    # CN_OTC→CN_EXCHANGE 迁移后残留 lag>0（当日价变 T-N 的静默口径翻转）。
    effective_market = new_market if new_market is not None else str(product.market or "")
    validate_nav_lag_days(
        effective_market,
        updates.get("nav_lag_days", product.nav_lag_days),
    )
    # issue #240 跟进 #6：confirm_days 终态校验（同构，统一 INVALID_CONFIRM_DAYS 形状）。
    # 终态取值：显式传入 > market 变化且未传时按新市场重推导 > 存量值；
    # 重推导值结构性合法（0/1/2），校验为显式传入路径兜底
    market_change = new_market is not None and new_market != product.market
    if "confirm_days" in updates:
        effective_confirm_days = updates["confirm_days"]
    elif market_change:
        effective_confirm_days = calculate_confirm_days(
            new_market, updates.get("is_qdii", product.is_qdii)
        )
    else:
        effective_confirm_days = product.confirm_days
    validate_confirm_days(effective_market, effective_confirm_days)
    if market_change:
        # market 是复合主键：price_record 随行迁移（删→改父→重插），不走 setattr；
        # confirm_days 未显式传入时按创建时规则重推导（场内 0 天残留到场外会造成
        # 当日确认的资金语义错误，此处为 #228「纯显式」的唯一例外）
        if "confirm_days" not in updates:
            updates["confirm_days"] = effective_confirm_days
        _migrate_product_market(db, product, new_market)

    for field, value in updates.items():
        setattr(product, field, value)
    return product
