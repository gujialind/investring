"""持仓聚合读侧（#595）：组合详情页「按产品 / 按平台」双视图的数据装配。

口径要点（端点 docstring 不重复，改口径只改这里）：
- 数据源为组合级最新快照日的 `portfolio_position` 行；清仓行不落在快照中
  （生成侧对净值型行跳过 shares<=0 且现金为 0 的行；零额现金行是生成侧
  的正常产物），在此基础上仍显式过滤市值/份额为 0 的聚合，防御脏数据。
- 行市值口径与快照生成一致：现金行（`cash_amount IS NOT NULL`，含 CASH 与在途）
  取 `cash_amount`，净值型行取 `market_value`（快照生成对现金行同写两列，此处按
  业务判据读取，不依赖该同写巧合）。
- `total_market_value` 含在途（市值口径：在途计市值），产品卡/平台卡的占比基数
  同此；在途虚拟产品（product_type="IN_TRANSIT"）不出现为产品卡，但仍计入所属
  平台市值。
- 持有收益与持仓列表同公式（精度路径不同：本层 Decimal，列表 float+round）：
  非现金行 = 市值 − 份额×成本价 + 事件现金加回；CASH 行取现金累计收益；
  当日收益、事件加回复用 `compute_derived_fields` 一次聚合（issue #103），
  不另起口径。行级缺份额/成本时该行收益不计入聚合，聚合记缺口并整体置 None，
  不发布「看似完整的部分和」（WARNING 留痕）。
- 累计收益（含已实现）接 #598 `compute_cumulative_profits` 的产品市场/平台/
  平台-产品三粒度；当日无组合市值快照时降级为 None（前端按可空占位渲染），
  `INVALID_AMOUNT` 等存量数据错误不吞。
- 过滤规则只隐藏卡，不改变合计基数：被过滤行的市值仍留在
  `total_market_value` 与平台市值里（防御性过滤的残差口径）。
- 读侧不做量化：金额型累加保持 Decimal（`daily_profit`、现金收益与事件加回
  沿用 `compute_derived_fields` 的既有 float 输出，本文件不改写其精度）；
  float 序列化舍入（round(…, 4)）只在 router shaping 层做（同 positions.py
  读侧惯例）。
"""

import logging
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.asset_classification import AssetClassification
from app.models.platform import Platform
from app.models.portfolio import Portfolio
from app.models.portfolio_position import PortfolioPosition
from app.models.product import Product
from app.services.cumulative_profit_service import compute_cumulative_profits
from app.services.exceptions import NotFoundError
from app.services.position_service import compute_derived_fields

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")

# 维度标签字段名（与 PositionResponse 的五维度 code/name 成对一致）
_DIM_FIELDS = ("asset_class", "region", "style", "size", "segment")


def _row_value(row: PortfolioPosition) -> Decimal:
    """行市值：现金行（含在途）取 cash_amount，净值型行取 market_value。"""
    if row.cash_amount is not None:
        return Decimal(str(row.cash_amount))
    if row.market_value is None:
        logger.warning(
            "持仓聚合：组合 %s 产品 %s@%s 平台 %s 市值与现金均为 NULL，按 0 计入合计",
            row.portfolio_code, row.product_code, row.market, row.platform_code,
        )
    return Decimal(str(row.market_value or 0))


def _load_latest_rows(db: Session, portfolio_code: str):
    """组合级最新快照日 + 当日全部持仓行；无快照返回 (None, [])。"""
    latest = db.query(func.max(PortfolioPosition.snapshot_date)).filter(
        PortfolioPosition.portfolio_code == portfolio_code
    ).scalar()
    if latest is None:
        return None, []
    rows = db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == portfolio_code,
        PortfolioPosition.snapshot_date == latest,
    ).all()
    return latest, rows


def _load_product_meta(db: Session, rows) -> dict:
    """批量装配产品名称、五维度 code/name（防 N+1，口径同 positions 列表读侧）。

    同时返回在途产品键集合（product_type="IN_TRANSIT"，数据判据，避免硬编码
    code 清单在新增在途品种时漏改）。传全部行（含在途），调用方先取在途键
    再过滤产品卡行。
    """
    codes = {r.product_code for r in rows}
    name_map = {}
    dims_by_product = {}
    dim_codes = set()
    transit_keys = set()
    if codes:
        for prod in db.query(
            Product.code, Product.market, Product.name, Product.product_type,
            Product.asset_class_code, Product.region_code, Product.style_code,
            Product.size_code, Product.segment_code,
        ).filter(Product.code.in_(codes)).all():
            if prod.product_type == "IN_TRANSIT":
                transit_keys.add((prod.code, prod.market))
                continue
            name_map[(prod.code, prod.market)] = prod.name
            dims = (prod.asset_class_code, prod.region_code, prod.style_code,
                    prod.size_code, prod.segment_code)
            dims_by_product[(prod.code, prod.market)] = dims
            dim_codes.update(c for c in dims if c)
    dim_name_by_code = {}
    if dim_codes:
        for ac_code, ac_name in db.query(
            AssetClassification.code, AssetClassification.name
        ).filter(AssetClassification.code.in_(dim_codes)).all():
            dim_name_by_code[ac_code] = ac_name
    return {
        "name_map": name_map,
        "dims_by_product": dims_by_product,
        "dim_name_by_code": dim_name_by_code,
        "transit_keys": transit_keys,
    }


def _load_transit_keys(db: Session, rows) -> set:
    """在途产品键集合（product_type="IN_TRANSIT"）；供 by-platform 过滤产品数。"""
    codes = {r.product_code for r in rows}
    if not codes:
        return set()
    return {
        (code, market)
        for code, market, product_type in db.query(
            Product.code, Product.market, Product.product_type
        ).filter(Product.code.in_(codes)).all()
        if product_type == "IN_TRANSIT"
    }


def _load_platform_names(db: Session, rows) -> dict:
    codes = {r.platform_code for r in rows if r.platform_code}
    if not codes:
        return {}
    return {
        code: name
        for code, name in db.query(Platform.code, Platform.name)
        .filter(Platform.code.in_(codes)).all()
    }


def _load_cumulative_maps(db: Session, portfolio_code: str, snapshot_date) -> tuple:
    """#598 三粒度累计收益 → 三张映射；无市值快照降级空映射（字段输出 None）。"""
    try:
        result = compute_cumulative_profits(db, portfolio_code, snapshot_date)
    except NotFoundError:
        return {}, {}, {}
    by_platform_product = {
        (r["product_code"], r["market"], r["platform_code"]): r["cumulative_profit"]
        for r in result["platform_products"]
    }
    by_product = {
        (r["product_code"], r["market"]): r["cumulative_profit"]
        for r in result["products"]
    }
    by_platform = {
        r["platform_code"]: r["cumulative_profit"] for r in result["platforms"]
    }
    return by_platform_product, by_product, by_platform


def _dim_payload(meta: dict, product_code: str, market: str) -> dict:
    dims = meta["dims_by_product"].get((product_code, market))
    payload = {}
    if dims:
        for field, code in zip(_DIM_FIELDS, dims):
            payload[f"{field}_code"] = code
            payload[f"{field}_name"] = meta["dim_name_by_code"].get(code) if code else None
    return payload


def aggregate_holdings_by_product(db: Session, portfolio_code: str) -> dict:
    """按产品聚合视图：跨平台合计 + 平台分布切片 + 维度元数据。口径见模块 docstring。"""
    if db.query(Portfolio.code).filter(Portfolio.code == portfolio_code).first() is None:
        raise NotFoundError("NOT_FOUND", f"组合 {portfolio_code} 不存在")

    snapshot_date, rows = _load_latest_rows(db, portfolio_code)
    if snapshot_date is None:
        return {
            "portfolio_code": portfolio_code,
            "snapshot_date": None,
            "total_market_value": _ZERO,
            "products": [],
        }

    total = sum((_row_value(r) for r in rows), _ZERO)
    meta = _load_product_meta(db, rows)
    transit_keys = meta["transit_keys"]
    card_rows = [r for r in rows if (r.product_code, r.market) not in transit_keys]
    platform_names = _load_platform_names(db, card_rows)
    derived = compute_derived_fields(db, card_rows)
    cum_by_key, cum_by_product, _ = _load_cumulative_maps(db, portfolio_code, snapshot_date)

    aggregates: dict = {}
    for row in card_rows:
        key = (row.product_code, row.market)
        pos_key = (row.portfolio_code, row.product_code, row.market, row.platform_code)
        value = _row_value(row)
        is_cash_row = row.cash_amount is not None

        if is_cash_row:
            shares = None
            cost = None
            profit = derived["cash_profits"].get(pos_key)  # float | None
            profit_skipped = False
        else:
            shares = Decimal(str(row.shares or 0))
            if row.shares is not None and row.cost_price is not None:
                cost = Decimal(str(row.shares)) * Decimal(str(row.cost_price))
            else:
                cost = None
            profit = None
            # 行级缺份额/成本：该行收益不计入聚合，记缺口待发布前统一置 None
            profit_skipped = not (
                row.shares is not None and row.cost_price is not None
                and row.market_value is not None
            )
            if not profit_skipped:
                addback = Decimal(str(derived["event_addbacks"].get(pos_key, 0.0)))
                profit = Decimal(str(row.market_value)) - cost + addback

        daily = derived["daily_profits"].get(pos_key)  # float | None，与成本无关
        cumulative_slice = cum_by_key.get(
            (row.product_code, row.market, row.platform_code)
        )

        agg = aggregates.setdefault(key, {
            "product_code": row.product_code,
            "market": row.market,
            "product_name": meta["name_map"].get(key),
            "market_value": _ZERO,
            "shares": None,
            "cash_amount": None,
            "_cost": _ZERO,
            "_has_cost": False,
            "_profit_skipped": 0,
            "holding_profit": None,
            "daily_profit": None,
            "cumulative_profit": cum_by_product.get(key),
            "_platforms": {},
        })
        agg["market_value"] += value
        if shares is not None:
            agg["shares"] = (agg["shares"] or _ZERO) + shares
        if is_cash_row:
            agg["cash_amount"] = (agg["cash_amount"] or _ZERO) + value
        if cost is not None:
            agg["_cost"] += cost
            agg["_has_cost"] = True
        if profit_skipped:
            agg["_profit_skipped"] += 1
        if profit is not None:
            agg["holding_profit"] = (agg["holding_profit"] or _ZERO) + (
                profit if isinstance(profit, Decimal) else Decimal(str(profit))
            )
        if daily is not None:
            agg["daily_profit"] = (agg["daily_profit"] or 0.0) + daily

        slice_key = row.platform_code
        plat_slice = agg["_platforms"].setdefault(slice_key, {
            "platform_code": row.platform_code,
            "platform_name": platform_names.get(row.platform_code),
            "market_value": _ZERO,
            "shares": None,
            "cash_amount": None,
            "_profit_skipped": 0,
            "holding_profit": None,
            "cumulative_profit": cumulative_slice,
        })
        plat_slice["market_value"] += value
        if shares is not None:
            plat_slice["shares"] = (plat_slice["shares"] or _ZERO) + shares
        if is_cash_row:
            plat_slice["cash_amount"] = (plat_slice["cash_amount"] or _ZERO) + value
        if profit_skipped:
            plat_slice["_profit_skipped"] += 1
        if profit is not None:
            plat_slice["holding_profit"] = (plat_slice["holding_profit"] or _ZERO) + (
                profit if isinstance(profit, Decimal) else Decimal(str(profit))
            )

    products = []
    for key, agg in aggregates.items():
        is_cash = agg["cash_amount"] is not None
        # 过滤规则（见模块 docstring）：市值 == 0 或份额为空/0 不展示（现金只看
        # 市值）；过滤只隐藏卡，不改变合计基数。
        if agg["market_value"] == 0:
            continue
        if not is_cash and (agg["shares"] is None or agg["shares"] == 0):
            continue
        if agg["_profit_skipped"]:
            logger.warning(
                "持仓按产品聚合：组合 %s 产品 %s@%s 有 %d 行缺份额/成本，"
                "持有收益置 None（不发布部分和）",
                portfolio_code, key[0], key[1], agg["_profit_skipped"],
            )
            agg["holding_profit"] = None
            agg["holding_profit_percent"] = None
        slices = sorted(
            agg["_platforms"].values(),
            key=lambda s: (s["market_value"], s["platform_code"] or ""),
            reverse=True,
        )
        for s in slices:
            if s["_profit_skipped"]:
                logger.warning(
                    "持仓按产品聚合：组合 %s 产品 %s@%s 平台 %s 切片有 %d 行缺份额/成本，"
                    "切片持有收益置 None",
                    portfolio_code, key[0], key[1],
                    s["platform_code"], s["_profit_skipped"],
                )
                s["holding_profit"] = None
            s.pop("_profit_skipped", None)
            s["ratio_in_product"] = (
                float(s["market_value"]) / float(agg["market_value"])
                if agg["market_value"] else None
            )
        products.append({
            **agg,
            **_dim_payload(meta, key[0], key[1]),
            **{
                "ratio": float(agg["market_value"]) / float(total) if total else None,
                "holding_profit_percent": (
                    float(agg["holding_profit"]) / float(agg["_cost"]) * 100
                    if agg["_has_cost"] and agg["_cost"] and agg["holding_profit"] is not None
                    else None
                ),
            },
            "platforms": slices,
        })

    products.sort(key=lambda p: (p["market_value"], p["product_code"], p["market"]),
                  reverse=True)
    for p in products:
        p.pop("_platforms", None)
        p.pop("_cost", None)
        p.pop("_has_cost", None)
        p.pop("_profit_skipped", None)

    return {
        "portfolio_code": portfolio_code,
        "snapshot_date": snapshot_date,
        "total_market_value": total,
        "products": products,
    }


def aggregate_holdings_by_platform(db: Session, portfolio_code: str) -> dict:
    """按平台聚合视图：市值（含现金与在途）、现金余额、非现金产品数。口径见模块 docstring。"""
    if db.query(Portfolio.code).filter(Portfolio.code == portfolio_code).first() is None:
        raise NotFoundError("NOT_FOUND", f"组合 {portfolio_code} 不存在")

    snapshot_date, rows = _load_latest_rows(db, portfolio_code)
    if snapshot_date is None:
        return {
            "portfolio_code": portfolio_code,
            "snapshot_date": None,
            "total_market_value": _ZERO,
            "platforms": [],
        }

    total = sum((_row_value(r) for r in rows), _ZERO)
    transit_keys = _load_transit_keys(db, rows)
    platform_names = _load_platform_names(db, rows)
    platform_types = {
        code: ptype
        for code, ptype in db.query(Platform.code, Platform.platform_type)
        .filter(Platform.code.in_({r.platform_code for r in rows if r.platform_code}))
        .all()
    } if any(r.platform_code for r in rows) else {}
    derived = compute_derived_fields(db, [
        r for r in rows if (r.product_code, r.market) not in transit_keys
    ])
    _, _, cum_by_platform = _load_cumulative_maps(db, portfolio_code, snapshot_date)

    aggregates: dict = {}
    # 在途行只贡献平台市值，不贡献产品数/现金/收益
    for row in rows:
        value = _row_value(row)
        if row.platform_code is None:
            logger.warning(
                "持仓按平台聚合：组合 %s 产品 %s@%s 的持仓行 platform_code 为 NULL，"
                "落入匿名平台卡",
                portfolio_code, row.product_code, row.market,
            )
        agg = aggregates.setdefault(row.platform_code, {
            "platform_code": row.platform_code,
            "platform_name": platform_names.get(row.platform_code),
            "platform_type": platform_types.get(row.platform_code),
            "market_value": _ZERO,
            "cash_balance": _ZERO,
            "_product_keys": set(),
            "_profit_skipped": 0,
            "holding_profit": None,
            "cumulative_profit": cum_by_platform.get(row.platform_code),
        })
        agg["market_value"] += value
        if (row.product_code, row.market) in transit_keys:
            continue
        pos_key = (row.portfolio_code, row.product_code, row.market, row.platform_code)
        if row.cash_amount is not None:
            agg["cash_balance"] += value
            profit = derived["cash_profits"].get(pos_key)
        else:
            agg["_product_keys"].add((row.product_code, row.market))
            profit = None
            if row.shares is not None and row.cost_price is not None \
                    and row.market_value is not None:
                addback = Decimal(str(derived["event_addbacks"].get(pos_key, 0.0)))
                profit = Decimal(str(row.market_value)) - (
                    Decimal(str(row.shares)) * Decimal(str(row.cost_price))
                ) + addback
            else:
                # 行级缺份额/成本：记缺口待发布前统一置 None（同按产品视图）
                agg["_profit_skipped"] += 1
        if profit is not None:
            agg["holding_profit"] = (agg["holding_profit"] or _ZERO) + (
                profit if isinstance(profit, Decimal) else Decimal(str(profit))
            )

    platforms = []
    for agg in aggregates.values():
        # 过滤规则（见模块 docstring）：平台市值（含现金与在途）== 0 不展示
        if agg["market_value"] == 0:
            continue
        if agg["_profit_skipped"]:
            logger.warning(
                "持仓按平台聚合：组合 %s 平台 %s 有 %d 行缺份额/成本，"
                "持有收益置 None（不发布部分和）",
                portfolio_code, agg["platform_code"], agg["_profit_skipped"],
            )
            agg["holding_profit"] = None
        platforms.append({
            **agg,
            "product_count": len(agg["_product_keys"]),
            "ratio": float(agg["market_value"]) / float(total) if total else None,
        })
    platforms.sort(key=lambda p: (p["market_value"], p["platform_code"] or ""),
                   reverse=True)
    for p in platforms:
        p.pop("_product_keys", None)
        p.pop("_profit_skipped", None)

    return {
        "portfolio_code": portfolio_code,
        "snapshot_date": snapshot_date,
        "total_market_value": total,
        "platforms": platforms,
    }
