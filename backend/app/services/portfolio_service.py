"""
组合管理服务

组合的创建、更新、关闭/重新激活状态机，以及净值历史/收益率/资金流查询，
从路由层提取供 REST 与 CLI 共用。service 层只抛领域异常，不 commit。
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants.asset_dimensions import RULE_DIMENSIONS
from app.constants.audit_actions import ACTION_ACTIVATE, ACTION_DELETE, RESOURCE_PORTFOLIO
from app.models.asset_classification import AssetClassDimensionRule, AssetClassification
from app.models.base import Base
from app.models.investor import Investor
from app.models.investor_holding import InvestorHolding
from app.models.portfolio import Portfolio
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.subscription import Subscription
from app.models.trade import Trade
from app.services.audit_service import record_audit
from app.services.cumulative_profit_service import compute_cumulative_profits
from app.services.exceptions import BusinessError, NotFoundError
from app.utils.business_time import business_today

logger = logging.getLogger(__name__)

# display_config 哨兵：区分 PUT「不传 = 不修改」与「显式 null = 清空」（issue #144）
UNSET = object()


def _get_portfolio_or_404(db: Session, code: str) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.code == code).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", f"组合 {code} 不存在")
    return portfolio


def validate_display_config(db: Session, config: Optional[dict]) -> Optional[dict]:
    """校验并归一持仓明细二级组合键的维度子集配置（issue #144 / #915）。

    值形态（#915 Q1=A）：值为逗号分隔维度名（如 "region,segment"），单值
    天然兼容为单元素子集。返回归一后的配置：空 dict 归一为 None（语义等同
    默认，保持「仅存显式覆盖项」单一表示，DB 不出现 {} 与 NULL 双表示）；
    每个子集按固定全序 region→style→size→segment 重排入库（Q5），顺序不
    表达语义，"segment,region" 与 "region,segment" 归一后同值。

    校验规则（失败抛 INVALID_DISPLAY_CONFIG 422，拒绝即零写入）：
    - key 必须是字典中 dimension==asset_class 的维度值（不校验 is_active，
      停用大类无持仓时配置无副作用）；
    - value 必须是非空字符串，按逗号拆分、逐项 strip；空串/纯空白/拆分后
      为空一律拒绝（本批不支持「强制平铺」，Q8）；
    - 拆分后每项必须 ∈ RULE_DIMENSIONS，且在该大类的
      asset_class_dimension_rule 登记（required/optional 均可；无行 =
      forbidden，如 ASSET_CASH 任何配置均拒绝）。值域不硬编码，规则矩阵
      新登记维度运行期即可配（#135 理念）；
    - 重复项拒绝（子集语义无二义）。
    """
    if config is None:
        return None
    if not isinstance(config, dict):
        raise BusinessError("INVALID_DISPLAY_CONFIG", "display_config 必须是对象")
    if not config:
        return None
    class_codes = {
        row.code
        for row in db.query(AssetClassification.code).filter(
            AssetClassification.dimension == "asset_class"
        )
    }
    rules: dict = {}
    for row in db.query(AssetClassDimensionRule).all():
        rules.setdefault(row.asset_class_code, set()).add(row.dimension)
    normalized: dict = {}
    for class_code, value in config.items():
        if class_code not in class_codes:
            raise BusinessError(
                "INVALID_DISPLAY_CONFIG",
                f"display_config key {class_code} 不是 asset_class 维度值",
                details={"asset_class_code": class_code, "value": value},
            )
        allowed = rules.get(class_code, set())
        if not isinstance(value, str) or not value.strip():
            raise BusinessError(
                "INVALID_DISPLAY_CONFIG",
                f"大类 {class_code} 的分组维度子集不能为空",
                details={"asset_class_code": class_code, "value": value},
            )
        parts = [part.strip() for part in value.split(",")]
        selected: set = set()
        for dimension in parts:
            if not dimension:
                raise BusinessError(
                    "INVALID_DISPLAY_CONFIG",
                    f"大类 {class_code} 的分组维度子集不能为空",
                    details={"asset_class_code": class_code, "value": value},
                )
            if dimension not in RULE_DIMENSIONS:
                raise BusinessError(
                    "INVALID_DISPLAY_CONFIG",
                    f"大类 {class_code} 的分组维度子集含非法维度 {dimension}"
                    "（限 region/style/size/segment）",
                    details={
                        "asset_class_code": class_code,
                        "dimension": dimension,
                        "value": value,
                    },
                )
            if dimension not in allowed:
                raise BusinessError(
                    "INVALID_DISPLAY_CONFIG",
                    f"大类 {class_code} 不可按维度 {dimension} 二级分组"
                    + ("（该大类无维度规则行）" if not allowed else "（未在规则矩阵登记）"),
                    details={
                        "asset_class_code": class_code,
                        "dimension": dimension,
                        "value": value,
                    },
                )
            if dimension in selected:
                raise BusinessError(
                    "INVALID_DISPLAY_CONFIG",
                    f"大类 {class_code} 的分组维度子集含重复维度 {dimension}",
                    details={
                        "asset_class_code": class_code,
                        "dimension": dimension,
                        "value": value,
                    },
                )
            selected.add(dimension)
        normalized[class_code] = ",".join(
            dimension for dimension in RULE_DIMENSIONS if dimension in selected
        )
    return normalized


def list_portfolios(
    db: Session,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询组合列表，附聚合字段 total_value / total_profit / cumulative_return / investor_count。

    聚合口径（issue #69；#905 PR3 B 按 kind 分流，R8 仪表盘跨口径合并读侧）：
    - nav（净值型，数值逐字不变）：total_value/total_profit 与详情 _derive_portfolio_totals 同源
      （共用 _nav_totals 纯公式）；列表经 _nav_list_aggregates 批量 4 查询（latest/first/holdings/subs，
      #905 PR3 S1 消除逐组合读放大）；cumulative_return=首末 unit_price 百分数（与 get_returns 一致）；
      investor_count=最新快照日 investor_holding 中 shares>0 的投资人数。无快照时 total_value/
      total_profit/cumulative_return 为 None，investor_count 为 0。
    - ledger（台账型）：total_value/total_profit 走 compute_ledger_totals_batch（与详情同源，批量
      固定 3 查询、非逐组合 N+1）；draft→None，active/closed 无记录→0（SPEC §5）；无净值曲线/份额，
      cumulative_return=None、investor_count=0。
    """
    query = db.query(Portfolio)
    if status:
        query = query.filter(Portfolio.status == status)
    total = query.count()
    portfolios = query.offset((page - 1) * page_size).limit(page_size).all()

    # 台账型批量聚合（#905 PR3 B，R8 仪表盘跨口径合并读侧）：固定 3 查询覆盖本页全部
    # 台账组合，非逐组合 N+1；只读 ledger 表、不碰净值快照子查询（D1）。nav 分支反之
    # 只读快照/申赎/份额，不碰 ledger 表。
    ledger_codes = [p.code for p in portfolios if p.kind == "ledger"]
    if ledger_codes:
        from app.services.ledger_profit_service import compute_ledger_totals_batch
        ledger_totals = compute_ledger_totals_batch(db, ledger_codes)
    else:
        ledger_totals = {}
    # nav 侧同样批量（#905 PR3 S1）：固定 4 查询，消除逐组合申赎/快照读放大。
    nav_codes = [p.code for p in portfolios if p.kind != "ledger"]
    nav_agg = _nav_list_aggregates(db, nav_codes)

    items = []
    for p in portfolios:
        cumulative_return = None
        investor_count = 0
        if p.kind == "ledger":
            # 台账型：total_value/total_profit 走台账口径（与详情 compute_ledger_totals 同源）。
            # draft→None；active/closed 无记录→0（SPEC §5）；台账型无净值曲线/无投资人份额。
            t = ledger_totals.get(p.code, {"total_value": None, "total_profit": None})
            total_value = t["total_value"]
            total_profit = t["total_profit"]
        else:
            # nav：批量聚合 + 与详情同源纯公式 _nav_totals；cumulative_return/investor_count 口径逐字不变。
            a = nav_agg.get(p.code, {})
            lat = a.get("latest")
            tv = float(lat.total_value) if lat is not None and lat.total_value is not None else None
            nt = _nav_totals(tv, a.get("net_invested", 0.0))
            total_value = nt["total_value"]
            total_profit = nt["total_profit"]
            cumulative_return = a.get("cumulative_return")
            investor_count = a.get("investor_count", 0)
        items.append({
            "code": p.code,
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "kind": p.kind,
            "owner_investor_code": p.owner_investor_code,
            "started_at": p.started_at,
            "closed_at": p.closed_at,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "display_config": p.display_config,
            "auto_snapshot_enabled": p.auto_snapshot_enabled,
            "total_value": total_value,
            "total_profit": total_profit,
            "cumulative_return": cumulative_return,
            "investor_count": investor_count,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _nav_totals(total_value: Optional[float], net_invested: float) -> dict:
    """nav total_value/total_profit 纯公式（issue #99）：total_profit=市值−净投入，round(...,2)。

    详情 _derive_portfolio_totals 与列表 list_portfolios 共用此单点（#905 PR3），保证同源、
    不在列表另造算法。total_value None → 两者 None（nav 缺失即缺失，不冒充 0）。
    """
    if total_value is None:
        return {"total_value": None, "total_profit": None}
    return {"total_value": total_value, "total_profit": round(total_value - net_invested, 2)}


def _sum_net_invested(subs) -> float:
    """净投入 = Σ confirmed 申购 − Σ confirmed 赎回（amount None 跳过）。"""
    net_invested = 0.0
    for sub_type, amount in subs:
        if amount is None:
            continue
        if sub_type == "subscribe":
            net_invested += float(amount)
        elif sub_type == "redeem":
            net_invested -= float(amount)
    return net_invested


def _nav_totals_from_latest(db: Session, code: str, latest) -> dict:
    """nav 组合 total_value/total_profit（issue #99 口径），给定最新快照（详情路径，逐组合查询）。"""
    if not latest or latest.total_value is None:
        return {"total_value": None, "total_profit": None}
    subs = (
        db.query(Subscription.sub_type, Subscription.amount)
        .filter(
            Subscription.portfolio_code == code,
            Subscription.status == "confirmed",
            Subscription.confirm_date <= latest.snapshot_date,
        )
        .all()
    )
    return _nav_totals(float(latest.total_value), _sum_net_invested(subs))


def _nav_list_aggregates(db: Session, nav_codes: list) -> dict:
    """nav 列表批量聚合（#905 PR3 S1）：固定 4 查询（latest/first/holdings/subs）覆盖本页全部
    nav 组合，非逐组合 N+1——与 ledger 批量同口径，消除列表循环里的逐组合申赎/快照读放大。
    数值口径与逐组合路径（_nav_totals_from_latest + 快照 cumulative_return/investor_count）逐字一致。
    返回 {code: {latest, cumulative_return, investor_count, net_invested}}。
    """
    if not nav_codes:
        return {}
    latest = {}
    for r in (
        db.query(PortfolioValueSnapshot)
        .filter(PortfolioValueSnapshot.portfolio_code.in_(nav_codes))
        .order_by(PortfolioValueSnapshot.snapshot_date.desc())
        .all()
    ):
        latest.setdefault(r.portfolio_code, r)
    first = {}
    for r in (
        db.query(PortfolioValueSnapshot)
        .filter(PortfolioValueSnapshot.portfolio_code.in_(nav_codes))
        .order_by(PortfolioValueSnapshot.snapshot_date.asc())
        .all()
    ):
        first.setdefault(r.portfolio_code, r)
    counts: dict = {}
    for code, sd in (
        db.query(InvestorHolding.portfolio_code, InvestorHolding.snapshot_date)
        .filter(InvestorHolding.portfolio_code.in_(nav_codes), InvestorHolding.shares > 0)
        .all()
    ):
        if code in latest and latest[code].snapshot_date == sd:
            counts[code] = counts.get(code, 0) + 1
    subs_by_code: dict = {}
    for code, st, amt, cd in (
        db.query(
            Subscription.portfolio_code, Subscription.sub_type,
            Subscription.amount, Subscription.confirm_date,
        )
        .filter(Subscription.portfolio_code.in_(nav_codes), Subscription.status == "confirmed")
        .all()
    ):
        if code in latest and cd is not None and cd <= latest[code].snapshot_date:
            subs_by_code.setdefault(code, []).append((st, amt))
    agg = {}
    for code in nav_codes:
        lat = latest.get(code)
        fir = first.get(code)
        cumulative = None
        if lat is not None and fir is not None and fir.unit_price:
            cumulative = round(
                (float(lat.unit_price) - float(fir.unit_price)) / float(fir.unit_price) * 100, 4
            )
        agg[code] = {
            "latest": lat,
            "cumulative_return": cumulative,
            "investor_count": counts.get(code, 0),
            "net_invested": _sum_net_invested(subs_by_code.get(code, [])),
        }
    return agg


def _derive_portfolio_totals(db: Session, code: str) -> dict:
    """组合概要派生字段（issue #99）：total_value / total_profit。

    total_profit = 最新快照 total_value − 净投入；净投入 = Σ confirmed 申购金额
    − Σ confirmed 赎回金额，且仅计 confirm_date <= 最新快照日的记录（#250，
    与 total_value 的快照时点口径对齐）。无快照时两者均为 None。

    issue #905 PR2：台账型（kind='ledger'）走台账收益口径（当前市值 − 净入金，
    step-forward），净值型走下方快照口径，逐字不变。
    """
    portfolio = db.query(Portfolio).filter(Portfolio.code == code).first()
    if portfolio and portfolio.kind == "ledger":
        from app.services.ledger_profit_service import compute_ledger_totals
        totals = compute_ledger_totals(db, code)
        return {"total_value": totals["total_value"], "total_profit": totals["total_profit"]}

    latest = (
        db.query(PortfolioValueSnapshot)
        .filter(PortfolioValueSnapshot.portfolio_code == code)
        .order_by(PortfolioValueSnapshot.snapshot_date.desc())
        .first()
    )
    return _nav_totals_from_latest(db, code, latest)


def get_latest_value_snapshot(db: Session, code: str) -> PortfolioValueSnapshot:
    """获取组合最新一条市值快照，无快照抛 NOT_FOUND。"""
    _get_portfolio_or_404(db, code)
    latest = (
        db.query(PortfolioValueSnapshot)
        .filter(PortfolioValueSnapshot.portfolio_code == code)
        .order_by(PortfolioValueSnapshot.snapshot_date.desc())
        .first()
    )
    if not latest:
        raise NotFoundError("NOT_FOUND", f"组合 {code} 暂无快照")
    return latest


def _load_investor_cumulative_map(
    db: Session, code: str, snapshot_date: date, *, strict: bool = False,
) -> dict:
    """#694 第四粒度累计收益 → {investor_code: cumulative_profit}。

    strict=False（缺省最新日路径）：无当日组合市值快照时记 WARNING 并降级
    空映射（cumulative_profit 字段输出 None）；strict=True（调用方显式传
    snapshot_date，#672）：NotFoundError 原样上抛。INVALID_AMOUNT 等存量
    数据错误两种模式都不吞（口径同 holding_aggregation_service 的
    _load_cumulative_maps）。
    """
    try:
        result = compute_cumulative_profits(db, code, snapshot_date)
    except NotFoundError:
        if strict:
            raise
        logger.warning(
            "投资人列表：组合 %s 在 %s 有持仓行但无组合市值快照，"
            "cumulative_profit 字段降级 None",
            code, snapshot_date,
        )
        return {}
    return {r["investor_code"]: r["cumulative_profit"] for r in result["investors"]}


def get_portfolio_investors(
    db: Session, code: str, snapshot_date: Optional[date] = None,
) -> List[dict]:
    """获取组合快照日的投资人列表：当前持仓行（shares > 0）∪ 清仓出资人。

    #693：读出 investor_holding 已有派生列（DB 原值保 Decimal，float 序列化
    舍入只在 router shaping 层做，口径同 holding_aggregation_service）。
    ratio = shares / 当日市值快照 total_shares（份额占比与市值占比同快照日
    数学等价，全体投资人共用 unit_price，故只出这一列）；profit_percent =
    profit / total_cost × 100。分母为 NULL/0 时降级 None（不除零、不返回 0，
    同 holding_aggregation_service 的 holding_profit_percent 三重降级）。

    #694：cumulative_profit 取第四粒度（方案 A：D 日市值 + Σconfirmed 赎回
    − Σconfirmed 申购）；清仓出资人（有 confirmed 申赎历史但当日无持仓行）
    份额/市值计 0、保留累计收益、is_liquidated=True，排在持仓行之后。
    snapshot_date 可选（#672 语义）：缺省 = investor_holding 自身最新快照日
    （无行返回空列表，cumulative_profit 无市值快照时降级 None）；显式传入
    严格校验——该日无持仓行或无市值快照均 NOT_FOUND 上抛。
    """
    _get_portfolio_or_404(db, code)
    strict = snapshot_date is not None
    if strict:
        latest_date = snapshot_date
        has_rows = (
            db.query(InvestorHolding.id)
            .filter(
                InvestorHolding.portfolio_code == code,
                InvestorHolding.snapshot_date == latest_date,
            )
            .first()
        )
        if has_rows is None:
            raise NotFoundError(
                "NOT_FOUND", f"组合 {code} 在 {snapshot_date} 无投资人份额快照"
            )
    else:
        latest_date = (
            db.query(InvestorHolding.snapshot_date)
            .filter(InvestorHolding.portfolio_code == code)
            .order_by(InvestorHolding.snapshot_date.desc())
            .limit(1)
            .scalar()
        )
        if latest_date is None:
            return []
    # 两表由同一次快照生成同批落库，正常恒有当日行；脏数据/部分失败时缺行，
    # ratio 降级 None 而不 404（严格路径的 404 由上方持仓行探针与
    # _load_investor_cumulative_map(strict=True) 承担）
    total_shares = (
        db.query(PortfolioValueSnapshot.total_shares)
        .filter(
            PortfolioValueSnapshot.portfolio_code == code,
            PortfolioValueSnapshot.snapshot_date == latest_date,
        )
        .scalar()
    )
    rows = (
        db.query(InvestorHolding, Investor.name)
        .join(Investor, Investor.code == InvestorHolding.investor_code)
        .filter(
            InvestorHolding.portfolio_code == code,
            InvestorHolding.snapshot_date == latest_date,
            InvestorHolding.shares > 0,
        )
        .order_by(InvestorHolding.shares.desc())
        .all()
    )
    cum_map = _load_investor_cumulative_map(db, code, latest_date, strict=strict)
    items = []
    for h, name in rows:
        profit_percent = (
            h.profit / h.total_cost * 100
            if h.profit is not None and h.total_cost
            else None
        )
        items.append({
            "investor_code": h.investor_code,
            "name": name,
            "shares": h.shares,
            "frozen_shares": h.frozen_shares,
            "cost_per_share": h.cost_per_share,
            "market_value": h.market_value,
            "total_cost": h.total_cost,
            "profit": h.profit,
            "profit_percent": profit_percent,
            "ratio": h.shares / total_shares if total_shares else None,
            "cumulative_profit": cum_map.get(h.investor_code),
            "is_liquidated": False,
        })
    # 清仓出资人：confirmed 申赎历史 ∖ 当日持仓行；份额/市值计 0、保留累计收益
    liquidated_codes = {
        investor_code
        for (investor_code,) in db.query(Subscription.investor_code)
        .distinct()
        .filter(
            Subscription.portfolio_code == code,
            Subscription.status == "confirmed",
            Subscription.confirm_date <= latest_date,
        )
        .all()
    } - {h.investor_code for h, _ in rows}
    if liquidated_codes:
        name_map = dict(
            db.query(Investor.code, Investor.name)
            .filter(Investor.code.in_(liquidated_codes))
            .all()
        )
        liquidated = [
            {
                "investor_code": lc,
                "name": name_map.get(lc, lc),
                "shares": Decimal("0"),
                "frozen_shares": Decimal("0"),
                "cost_per_share": None,
                "market_value": Decimal("0"),
                "total_cost": None,
                "profit": None,
                "profit_percent": None,
                "ratio": Decimal("0") if total_shares else None,
                "cumulative_profit": cum_map.get(lc),
                "is_liquidated": True,
            }
            for lc in liquidated_codes
        ]
        # 清仓行置后：累计收益降序（None 最后）、tie 按 code 升序（确定性可钉测试）
        liquidated.sort(key=lambda it: (
            it["cumulative_profit"] is None,
            -(it["cumulative_profit"] or Decimal("0")),
            it["investor_code"],
        ))
        items.extend(liquidated)
    return items


def create_portfolio(
    db: Session,
    *,
    code: str,
    name: str,
    description: Optional[str] = None,
    display_config: Optional[dict] = None,
    kind: str = "nav",
    owner_investor_code: Optional[str] = None,
) -> Portfolio:
    """创建组合（初始状态 draft）。不 commit。

    issue #905：kind ∈ {nav, ledger}（默认 nav，存量兼容）；ledger 必填
    owner_investor_code（组合级归属投资人，非份额关系）且投资人须存在，nav 禁填
    owner。kind/owner 创建后不可改（update 白名单排除 + router 显式拒绝）。
    """
    if db.query(Portfolio).filter(Portfolio.code == code).first():
        raise BusinessError("ALREADY_EXISTS", f"组合 {code} 已存在", http_status=400)
    if kind not in ("nav", "ledger"):
        raise BusinessError(
            "INVALID_PARAM", "组合类型 kind 仅支持 nav / ledger", details={"field": "kind"}
        )
    if kind == "ledger":
        if not owner_investor_code:
            raise BusinessError(
                "INVALID_PARAM",
                "台账型组合必须指定归属投资人 owner_investor_code",
                details={"field": "owner_investor_code"},
            )
        if not db.query(Investor).filter(Investor.code == owner_investor_code).first():
            raise NotFoundError("NOT_FOUND", f"投资人 {owner_investor_code} 不存在")
    elif owner_investor_code is not None:
        raise BusinessError(
            "INVALID_PARAM",
            "净值型组合不接受 owner_investor_code",
            details={"field": "owner_investor_code"},
        )
    portfolio = Portfolio(
        code=code,
        name=name,
        description=description,
        status="draft",
        kind=kind,
        owner_investor_code=owner_investor_code if kind == "ledger" else None,
        display_config=validate_display_config(db, display_config),
    )
    db.add(portfolio)
    return portfolio


def assert_nav_portfolio(portfolio: Portfolio) -> None:
    """类型闸门单点（issue #905）：台账型组合禁止进入净值型专属写入口。

    各写入口（申赎/调仓/现金转移/份额事件/快照/现金覆盖/分红配置）在组合存在性
    校验后调用本 helper；台账型抛 PORTFOLIO_KIND_NOT_ALLOWED。净值型（kind='nav'，
    含存量默认）直接放行，行为逐字不变。闸门收敛单点防类型字面量判断漂移。
    """
    if portfolio.kind == "ledger":
        raise BusinessError(
            "PORTFOLIO_KIND_NOT_ALLOWED",
            f"组合 {portfolio.code} 为平台市值台账型，不支持该净值型操作",
            details={"portfolio_code": portfolio.code, "kind": portfolio.kind},
        )


def activate_ledger_portfolio(db: Session, code: str) -> Portfolio:
    """台账型组合手动激活（issue #905，D4）：draft → active，绕开申购激活路径。

    仅台账型 draft 可激活；净值型调此端点拒绝（净值型激活仍由首笔申购确认驱动，
    subscription_service 不动）。started_at 写激活日（台账型承载激活事实，与净值型
    首购确认日口径分流）。不 commit。
    """
    portfolio = _get_portfolio_or_404(db, code)
    if portfolio.kind != "ledger":
        raise BusinessError(
            "PORTFOLIO_KIND_NOT_ALLOWED",
            f"组合 {code} 非台账型，手动激活仅适用于平台市值台账型组合",
            details={"portfolio_code": code, "kind": portfolio.kind},
        )
    if portfolio.status != "draft":
        raise BusinessError(
            "INVALID_STATUS", f"仅草稿态台账型组合可激活，当前状态: {portfolio.status}"
        )
    portfolio.status = "active"
    portfolio.started_at = business_today()
    record_audit(
        db,
        action=ACTION_ACTIVATE,
        resource_type=RESOURCE_PORTFOLIO,
        resource_id=code,
        resource_name=portfolio.name,
        old_value={"status": "draft"},
        new_value={"status": "active", "kind": portfolio.kind},
    )
    return portfolio


def update_portfolio(
    db: Session,
    *,
    code: str,
    name: Optional[str] = None,
    description=UNSET,
    display_config=UNSET,
    auto_snapshot_enabled: Optional[bool] = None,
) -> Portfolio:
    """更新组合信息。不 commit。

    description 哨兵语义（#579 口径 A）：UNSET = 不修改；None = 清空；str = 覆盖。
    显式 null 由 router 层 null_guard 收口后进 allow（列可空且响应 Optional，
    null = 清空，asset_classification.description 先例）——此前 `is not None`
    跳过使 description 恒无法清空。
    display_config 哨兵语义（issue #144）：UNSET = 不修改；None = 清空恢复
    默认；dict = 校验后覆盖（router 以 "display_config" in updates 区分）。
    """
    portfolio = _get_portfolio_or_404(db, code)
    if name is not None:
        portfolio.name = name
    if description is not UNSET:
        portfolio.description = description
    if display_config is not UNSET:
        # 校验并归一（空 dict → None）；JSON 列赋新对象（避免 in-place 突变不触发脏检测）
        portfolio.display_config = validate_display_config(db, display_config)
    if auto_snapshot_enabled is not None:
        portfolio.auto_snapshot_enabled = auto_snapshot_enabled
    return portfolio


def close_portfolio(db: Session, code: str) -> Portfolio:
    """关闭组合（仅 active；已关闭或有 pending 交易则拒绝）。不 commit。

    状态闸门顺序即契约：已关闭 → 非 active → 存在 pending。前两条是状态机
    前置，第三条是数据约束；把 `PORTFOLIO_NOT_ACTIVE` 排在 pending 检查之前，
    draft 组合不会因「无 pending 交易」被误关（issue #820 口径 Q2）。
    """
    portfolio = _get_portfolio_or_404(db, code)
    if portfolio.status == "closed":
        raise BusinessError("PORTFOLIO_ALREADY_CLOSED", "组合已关闭")
    if portfolio.status != "active":
        raise BusinessError("PORTFOLIO_NOT_ACTIVE", "组合未激活")

    pending_subs = db.query(Subscription).filter(
        Subscription.portfolio_code == code, Subscription.status == "pending"
    ).count()
    pending_trades = db.query(Trade).filter(
        Trade.portfolio_code == code, Trade.status == "pending"
    ).count()
    if pending_subs > 0 or pending_trades > 0:
        raise BusinessError(
            "PENDING_TRANSACTIONS_EXIST",
            f"存在待处理交易: {pending_subs} 笔申赎, {pending_trades} 笔调仓",
        )

    portfolio.status = "closed"
    portfolio.closed_at = datetime.utcnow()
    return portfolio


def reactivate_portfolio(db: Session, code: str) -> Portfolio:
    """重新激活已关闭组合（仅 closed 可激活）。不 commit。"""
    portfolio = _get_portfolio_or_404(db, code)
    if portfolio.status != "closed":
        raise BusinessError(
            "PORTFOLIO_NOT_CLOSED",
            f"仅已关闭组合可激活，当前状态: {portfolio.status}",
        )
    portfolio.status = "active"
    portfolio.closed_at = None
    return portfolio


def _portfolio_dependent_tables() -> list:
    """按元数据派生「有外键直接引用 portfolio.code」的表：返回 (表, 本侧外键列) 列表。

    注意保证范围仅限**外键引用**：不带 FK 的 `portfolio_code` 列（现只有
    idempotency_cache，见守门测试的显式豁免清单）不在计数内。
    名单不手写（issue #820）：新增外键引用表自动进入删除判据，非 FK 引用
    由 `tests/unit/test_portfolio_delete_dependents.py` 的断言翻红暴露。
    """
    entries = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == Portfolio.__tablename__:
                entries.append((table, fk.parent, fk.constraint))
                break
    return entries


def _count_portfolio_dependents(db: Session, code: str) -> dict:
    """返回引用本组合且**有行**的依赖表 → 计数（空 dict 即「空壳」）。"""
    counts: dict = {}
    for table, fk_column, _constraint in _portfolio_dependent_tables():
        n = (
            db.query(func.count())
            .select_from(table)
            .filter(fk_column == code)
            .scalar()
        )
        if n:
            counts[table.name] = n
    return counts


def delete_draft_portfolio(db: Session, code: str) -> None:
    """物理删除空壳组合（issue #820，扩围口径：draft 与 closed 两态）。不 commit。

    判据是「status ∈ {draft, closed} **且** 依赖表计数全为 0」，两者缺一不可：
    unconfirm 首笔申购会把 active 退回 draft 而申赎行仍在（#820 事实 C1），
    只信 status 会删掉有历史账务的组合；closed 同理——注册后未清账的组合
    各表计数不为零，走 `PORTFOLIO_HAS_DEPENDENT_DATA`。行锁与子表 INSERT 的 FK 共享锁互斥，
    消除「先计数后删除」的竞态；MySQL FK RESTRICT 只作次防线——应用侧 SQLite
    连接未开 `PRAGMA foreign_keys`，不能把兜底交给方言行为。
    审计在 `db.delete()` 之前写：`record_audit` 会先 flush 业务改动，先删会让
    审计失败与业务删除的先后关系不可辨。失败路径（404/409）不落审计行。
    """
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.code == code)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not portfolio:
        raise NotFoundError("NOT_FOUND", f"组合 {code} 不存在")

    if portfolio.status not in ("draft", "closed"):
        raise BusinessError(
            "PORTFOLIO_NOT_DELETABLE",
            f"仅空壳草稿或已关闭组合可删除，当前状态: {portfolio.status}",
            http_status=409,
            details={"status": portfolio.status},
        )

    dependencies = _count_portfolio_dependents(db, code)
    if dependencies:
        raise BusinessError(
            "PORTFOLIO_HAS_DEPENDENT_DATA",
            "组合仍存在关联数据，无法删除",
            http_status=409,
            details={"dependencies": dependencies},
        )

    record_audit(
        db,
        action=ACTION_DELETE,
        resource_type=RESOURCE_PORTFOLIO,
        resource_id=code,
        resource_name=portfolio.name,
        old_value={
            "code": portfolio.code,
            "name": portfolio.name,
            "status": portfolio.status,
            "started_at": portfolio.started_at.isoformat() if portfolio.started_at else None,
            "closed_at": portfolio.closed_at.isoformat() if portfolio.closed_at else None,
        },
    )
    db.delete(portfolio)
    logger.info("删除空壳组合: code=%s status=%s", code, portfolio.status)


def get_nav_history(
    db: Session,
    code: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list:
    """查看组合净值历史（单层列表，按日期升序）。"""
    _get_portfolio_or_404(db, code)
    query = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == code
    )
    if start_date:
        query = query.filter(PortfolioValueSnapshot.snapshot_date >= start_date)
    if end_date:
        query = query.filter(PortfolioValueSnapshot.snapshot_date <= end_date)
    snapshots = query.order_by(PortfolioValueSnapshot.snapshot_date.asc()).all()
    return [
        {
            "snapshot_date": s.snapshot_date.isoformat(),
            "unit_price": float(s.unit_price) if s.unit_price is not None else None,
            "total_value": float(s.total_value) if s.total_value is not None else None,
            "total_shares": float(s.total_shares) if s.total_shares is not None else None,
        }
        for s in snapshots
    ]


def get_returns(db: Session, code: str) -> dict:
    """查看组合收益率（快照不足返回 None 字段）。"""
    _get_portfolio_or_404(db, code)
    snapshots = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == code
    ).order_by(PortfolioValueSnapshot.snapshot_date.asc()).all()

    if not snapshots:
        return {
            "portfolio_code": code,
            "cumulative_return": None,
            "annualized_return": None,
            "initial_nav": None,
            "current_nav": None,
            "holding_days": None,
        }

    initial = snapshots[0]
    current = snapshots[-1]
    initial_nav = float(initial.unit_price)
    current_nav = float(current.unit_price)
    cumulative_return = (current_nav - initial_nav) / initial_nav * 100
    holding_days = (current.snapshot_date - initial.snapshot_date).days
    annualized_return = None
    if holding_days > 0:
        annualized_return = ((current_nav / initial_nav) ** (365 / holding_days) - 1) * 100

    return {
        "portfolio_code": code,
        "cumulative_return": round(cumulative_return, 4),
        "annualized_return": round(annualized_return, 4) if annualized_return is not None else None,
        "initial_nav": initial_nav,
        "current_nav": current_nav,
        "holding_days": holding_days,
    }


def get_cash_flow(db: Session, code: str) -> dict:
    """查看组合资金流（confirmed 申赎汇总）。"""
    portfolio = _get_portfolio_or_404(db, code)
    # issue #905 PR2：台账型走台账本金进出累计（ledger_flow），净值型走申赎汇总逐字不变。
    if portfolio.kind == "ledger":
        from app.models.ledger_flow import LedgerFlow
        flows = db.query(LedgerFlow).filter(LedgerFlow.portfolio_code == code).all()
        inflow = sum(float(f.amount) for f in flows if f.direction == "inflow")
        outflow = sum(float(f.amount) for f in flows if f.direction == "outflow")
        return {
            "portfolio_code": code,
            "total_inflow": round(inflow, 4),
            "total_outflow": round(outflow, 4),
            "net_inflow": round(inflow - outflow, 4),
        }
    subs = db.query(Subscription).filter(
        Subscription.portfolio_code == code,
        Subscription.status == "confirmed",
    ).all()
    inflow = sum(float(s.amount) for s in subs if s.sub_type == "subscribe" and s.amount)
    outflow = sum(float(s.amount) for s in subs if s.sub_type == "redeem" and s.amount)
    return {
        "portfolio_code": code,
        "total_inflow": round(inflow, 4),
        "total_outflow": round(outflow, 4),
        "net_inflow": round(inflow - outflow, 4),
    }
