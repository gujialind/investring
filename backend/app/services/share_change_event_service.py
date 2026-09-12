"""
份额变动事件服务

将份额变动事件的校验、计算、确认拆分、取消/取消确认逻辑从路由层提取，
供 REST API、CLI、快照重算自动确认多处复用（消除 snapshot_service 反向依赖 router）。

service 层只抛领域异常（BusinessError/NotFoundError），不 import fastapi、不 commit。
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import List, NamedTuple, Optional

from sqlalchemy.orm import Session

from app.models.share_change_event import ShareChangeEvent
from app.models.portfolio import Portfolio
from app.models.platform import Platform
from app.models.product import Product
from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.services.trading_utils import is_trading_day, get_latest_snapshot_date
from app.services.exceptions import BusinessError, NotFoundError
from app.services.product_service import resolve_product_market
from app.services.audit_service import record_audit, _diff_fields
from app.constants.audit_actions import (
    ACTION_CREATE, ACTION_UPDATE, ACTION_CONFIRM, ACTION_UNCONFIRM,
    ACTION_CANCEL, ACTION_DELETE,
    RESOURCE_SHARE_CHANGE_EVENT,
)
from app.utils.quantize import quantize_amount, quantize_shares

logger = logging.getLogger(__name__)

FUND_LEVEL_TYPES = {"share_split", "share_merge", "bonus_share"}
PLATFORM_LEVEL_TYPES = {"cash_dividend", "reinvest_dividend", "forced_adjustment"}

# 确认与预览共用的拒绝文案（#460 评审：单点定义，杜绝「同码不同消息」两侧漂移）
MSG_POSITION_SNAPSHOT_MISSING = "权益登记日持仓快照不存在"
MSG_FUND_LEVEL_NO_HOLDINGS = "权益登记日无持仓，无需确认"


class EventFieldResult(NamedTuple):
    """事件变动的计算结果（纯数据，不挂 ORM 对象）。

    「份额类字段统一量化到 2 位、shares_after 与 shares_change 严格自洽」
    这组不变量由 compute_event_fields 单点定义——它只算不写，确认路径负责
    把结果写回事件（含审计 diff 语义），预览路径直接原样返回（issue #424）。
    """

    shares_change: Optional[Decimal]
    shares_after: Decimal
    cash_change: Optional[Decimal]


def compute_event_fields(
    event_type: str,
    entitlement_shares: Optional[Decimal],
    *,
    ratio: Optional[Decimal] = None,
    div_cash: Optional[Decimal] = None,
    reinvest_nav: Optional[Decimal] = None,
    shares_change: Optional[Decimal] = None,
    cash_change: Optional[Decimal] = None,
) -> Optional[EventFieldResult]:
    """按 event_type 计算 shares_change/shares_after/cash_change（纯函数，issue #424）。

    确认（confirm_share_change_event / _confirm_fund_level_event）与确认预览
    （compute_share_change_event_preview）共用本实现，「预览 == 确认」由此保证；
    本函数**不修改任何 ORM 对象**，调用方自行决定写回或原样返回。

    规则：
    - cash_dividend：金额口径，`cash_change = quantize_amount(es × div_cash)`
    - reinvest_dividend：唯一「金额 → 份额」的类型——**红利金额先量化到分**，
      再按再投资净值折算份额（与 cash_dividend 同口径，亦与基金公司算法一致：
      先按分确定应得红利，再折算份额，issue #425）
    - share_split / share_merge：先量化 shares_after 再反算 shares_change（乘除产生新份额）
    - bonus_share / reinvest_dividend：先量化 shares_change 再用 es + shares_change 重算 shares_after
    - forced_adjustment：shares_change / cash_change 由用户直接填写，此处只量化；
      两者皆为空时返回 None（「无用户填报的变动量可预览」，确认路径先由
      _validate_adjustment_not_empty 拦截，预览路径同）

    份额类字段统一量化到 2 位（四舍五入），cash_change 是金额同样量化到 2 位（issue #94）。
    """
    es = entitlement_shares or Decimal("0")
    if event_type == "cash_dividend":
        return EventFieldResult(
            shares_change=Decimal("0"),
            shares_after=es,
            cash_change=quantize_amount(es * Decimal(str(div_cash or 0))),
        )
    if event_type == "reinvest_dividend":
        # issue #425：红利金额先量化到分（与 cash_dividend 同口径，亦与基金公司算法一致：
        # 先按「分」确定应得红利，再按再投资净值折算份额）。跳过这一步会把
        # 「金额量化损失（< 0.005 元）」带进份额，跨四舍五入边界时差 0.01 份。
        dividend_amount = quantize_amount(es * Decimal(str(div_cash or 0)))
        new_shares = quantize_shares(dividend_amount / Decimal(str(reinvest_nav or 1)))
        return EventFieldResult(
            shares_change=new_shares,
            shares_after=es + new_shares,
            cash_change=Decimal("0"),
        )
    if event_type == "share_split":
        shares_after = quantize_shares(es * Decimal(str(ratio or 1)))
        return EventFieldResult(
            shares_change=shares_after - es, shares_after=shares_after, cash_change=Decimal("0")
        )
    if event_type == "share_merge":
        shares_after = quantize_shares(es / Decimal(str(ratio or 1)))
        return EventFieldResult(
            shares_change=shares_after - es, shares_after=shares_after, cash_change=Decimal("0")
        )
    if event_type == "bonus_share":
        new_shares = quantize_shares(es * Decimal(str(ratio or 0)))
        return EventFieldResult(
            shares_change=new_shares,
            shares_after=es + new_shares,
            cash_change=Decimal("0"),
        )
    if event_type == "forced_adjustment":
        # issue #263：shares_change / cash_change 是用户直填值（唯一存处）
        if shares_change is None and cash_change is None:
            return None
        # **未填写的一项保持 None，绝不折成 0**（与重构前逐字一致）：快照的事件应用
        # 循环以 `event.shares_change is None` 判定「纯现金调整」并跳过份额段
        # （snapshot_service），若把 None 折成 Decimal("0")，对 CASH 产品的合法纯现金
        # 调整会被现金行守卫误杀为 POSITION_NOT_FOUND。
        return EventFieldResult(
            shares_change=(
                quantize_shares(Decimal(str(shares_change))) if shares_change is not None else None
            ),
            shares_after=es,
            cash_change=(
                quantize_amount(Decimal(str(cash_change))) if cash_change is not None else None
            ),
        )
    # 未知类型：维持重构前「不计算、不写字段」的行为（event_type 是自由字符串，
    # 无创建期白名单；此处新增拒绝会改变既有记录的确认行为），返回 None 由调用方跳过
    return None


def _require_entitlement_snapshot(db: Session, event: ShareChangeEvent) -> None:
    """权益登记日持仓快照存在性探针（确认与预览共用单点，MISSING_POSITION_SNAPSHOT）。

    惰性设计：只在持仓查询未命中时由 resolve_entitlement_shares /
    _confirm_fund_level_event 调用——命中路径零额外查询（#460 评审：「探针 +
    重查」的双查询模式让每次预览对同一数据两次往返）。
    """
    probe = db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == event.portfolio_code,
        PortfolioPosition.snapshot_date == event.entitlement_date,
    ).first()
    if not probe:
        raise BusinessError("MISSING_POSITION_SNAPSHOT", MSG_POSITION_SNAPSHOT_MISSING)


def resolve_entitlement_shares(db: Session, event: ShareChangeEvent) -> Decimal:
    """读取事件的权益登记日权益份额（确认与预览共用口径，issue #424）。

    快照存在、但按口径无命中持仓行时返回 `Decimal("0")`——与确认路径现状一致，
    不在此处新增「无持仓即拒绝」的语义（forced_adjustment 的精查是例外，见 Raises）。

    Raises:
        BusinessError: MISSING_POSITION_SNAPSHOT——权益登记日持仓快照不存在
            （惰性探针，仅持仓查询未命中时触发，与确认路径同码同消息）；
            POSITION_NOT_FOUND——forced_adjustment 在权益登记日无
            (产品, market, 平台) 持仓行（issue #278：LOF market 误填提前快失败；
            探针先于精查，快照缺失时优先报 MISSING_POSITION_SNAPSHOT）
    """
    if event.platform_code is None:
        # 基金级事件：各平台持仓份额之和
        positions = db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == event.portfolio_code,
            PortfolioPosition.product_code == event.product_code,
            PortfolioPosition.snapshot_date == event.entitlement_date,
            PortfolioPosition.shares > 0,
        ).all()
        if not positions:
            _require_entitlement_snapshot(db, event)
            return Decimal("0")
        return sum((Decimal(str(pos.shares or 0)) for pos in positions), Decimal("0"))

    if event.event_type == "forced_adjustment":
        ent_position = db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == event.portfolio_code,
            PortfolioPosition.product_code == event.product_code,
            PortfolioPosition.market == event.market,
            PortfolioPosition.platform_code == event.platform_code,
            PortfolioPosition.snapshot_date == event.entitlement_date,
        ).first()
        if not ent_position:
            _require_entitlement_snapshot(db, event)
            raise BusinessError(
                "POSITION_NOT_FOUND",
                f"权益登记日 {event.entitlement_date} 无对应持仓 "
                f"{event.product_code}({event.market}) 平台 {event.platform_code}，"
                f"请核对产品/市场/平台",
            )
        return Decimal(str(ent_position.shares or 0))

    # 平台级事件：按 platform_code 过滤读取
    entitlement_position = db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == event.portfolio_code,
        PortfolioPosition.product_code == event.product_code,
        PortfolioPosition.platform_code == event.platform_code,
        PortfolioPosition.snapshot_date == event.entitlement_date,
    ).first()
    if not entitlement_position:
        _require_entitlement_snapshot(db, event)
        return Decimal("0")
    return Decimal(str(entitlement_position.shares or 0))


def apply_event_fields(event: ShareChangeEvent) -> None:
    """把 compute_event_fields 的结果写回事件对象（唯一写回点，issue #424）。

    仅确认路径调用——写回是**刻意**的：`record_audit` 的 diff（`_diff_fields`）读事件
    当前字段，值必须落在对象上才进得了审计载荷与库；预览路径不得调用本函数，
    否则 pending 对象被改后列表行会显示未落库的预览值（#424 引入预览时的核心约束）。

    forced_adjustment 双空时 compute_event_fields 返回 None，此时不写回任何字段
    （确认路径的前置校验已先拦截双空）。

    **forced_adjustment 的两项语义**（与重构前逐字一致，#263/#424）：
    - `shares_change` / `cash_change` 为空时**不写回**（保持 None）——快照的事件应用循环
      以 `event.shares_change is None` 判定「纯现金调整」并跳过份额段，折成 0 会让
      对 CASH 产品的合法纯现金调整被现金行守卫误杀（snapshot_service）；
    - `shares_after` 恒不写回：它是用户直填的「调整后余额」（与 shares_change 同为用户
      输入、unconfirm 同样不清空），照「es + shares_change」推导既会覆盖用户输入，
      也会让纯现金调整凭空获得一个 shares_after。
    """
    result = compute_event_fields(
        event.event_type,
        event.entitlement_shares,
        ratio=event.ratio,
        div_cash=event.div_cash,
        reinvest_nav=event.reinvest_nav,
        shares_change=event.shares_change,
        cash_change=event.cash_change,
    )
    if result is None:
        return
    if result.shares_change is not None:
        event.shares_change = result.shares_change
    if event.event_type != "forced_adjustment":
        event.shares_after = result.shares_after
    if result.cash_change is not None:
        event.cash_change = result.cash_change


def compute_share_change_event_preview(db: Session, event: ShareChangeEvent) -> dict:
    """确认前预览事件变动量（纯计算，不落库），与真实确认共用同一实现（issue #424）。

    只做查询与计算：**不修改 event 对象、不 flush/commit、不产审计**。confirm 路径
    写回落库，本函数把同一份计算结果原样返回，据此保证「预览 == 确认」。

    Returns:
        {"entitlement_shares": Decimal, "shares_change": Decimal|None,
         "shares_after": Decimal|None, "cash_change": Decimal|None}
        —— forced_adjustment 双空时三个变动字段为 None（与确认同一拒绝口径）；
        forced_adjustment 的 shares_after 恒为 None——确认路径不把该字段纳入计算写回
        （见 apply_event_fields），预览照实回 None，不编造确认不会产生的值

    Raises:
        BusinessError: INVALID_STATUS——非 pending（与确认同一条消息）；
            EMPTY_ADJUSTMENT / SHARES_CHANGE_ON_CASH_PRODUCT / MISSING_POSITION_SNAPSHOT /
            POSITION_NOT_FOUND——全部与确认路径同源同码同消息
    """
    # 与确认路径同一组前置校验（公共 preamble，同序同码同消息，杜绝预览与确认分叉）
    _validate_confirm_preconditions(db, event)

    entitlement_shares = resolve_entitlement_shares(db, event)
    if event.platform_code is None and entitlement_shares == 0:
        # 基金级事件在权益登记日无任何 shares > 0 持仓时确认会失败
        # （_confirm_fund_level_event 抛 ValueError → MISSING_POSITION_SNAPSHOT）。
        # 预览若照常返回 0/0.00 就是「假预览」：看着能确认、点下去才炸。
        # 文案与确认路径共用同一常量（#460 评审：原两侧同码不同消息）
        raise BusinessError("MISSING_POSITION_SNAPSHOT", MSG_FUND_LEVEL_NO_HOLDINGS)
    result = compute_event_fields(
        event.event_type,
        entitlement_shares,
        ratio=event.ratio,
        div_cash=event.div_cash,
        reinvest_nav=event.reinvest_nav,
        shares_change=event.shares_change,
        cash_change=event.cash_change,
    )
    is_forced_adjustment = event.event_type == "forced_adjustment"

    # 刻意不复刻创建期的平台覆盖校验（check_platform_coverage）：那是「录入完整性」
    # 规则而非状态门，塞进只读预览会与确认路径行为分叉（#424）
    return {
        "entitlement_shares": entitlement_shares,
        "shares_change": result.shares_change if result else None,
        "shares_after": (
            None if (result is None or is_forced_adjustment) else result.shares_after
        ),
        "cash_change": result.cash_change if result else None,
    }


# issue #279：现金型/在途虚拟产品（product_type 口径，种子/迁移 0006），
# 份额变动不得作用于非净值型资产（cash_amount IS NOT NULL 行）
CASH_LIKE_PRODUCT_TYPES = {"CASH", "IN_TRANSIT"}
# 确认时结构上必然产生份额变动的事件类型（compute_event_fields 由基数/比例推导，
# 与用户是否显式填写 shares_change 无关）
STRUCTURAL_SHARE_TYPES = {"share_split", "share_merge", "bonus_share", "reinvest_dividend"}


def _validate_adjustment_not_empty(
    event_type: str,
    shares_change: Optional[Decimal],
    cash_change: Optional[Decimal],
) -> None:
    """issue #279：forced_adjustment 必须至少填写一项，否则确认后零效果且无告警。"""
    if event_type == "forced_adjustment" and shares_change is None and cash_change is None:
        raise BusinessError(
            "EMPTY_ADJUSTMENT",
            "forced_adjustment 必须至少填写 shares_change / cash_change 之一",
        )


def _validate_product_allows_shares_change(
    db: Session,
    product_code: Optional[str],
    market: Optional[str],
    event_type: str,
    shares_change: Optional[Decimal],
) -> None:
    """issue #279：现金型/在途产品不得承载份额变动。

    STRUCTURAL_SHARE_TYPES 无条件拒绝（确认时必产生份额变动）；
    其余类型（含 forced_adjustment）在显式填写 shares_change 时拒绝。
    产品不存在时放行（维持既有行为，不引入新的存在性校验）。
    """
    if product_code is None:
        return
    query = db.query(Product).filter(Product.code == product_code)
    if market is not None:
        query = query.filter(Product.market == market)
    product_types = {row.product_type for row in query.all()}
    if not (product_types & CASH_LIKE_PRODUCT_TYPES):
        return
    if event_type in STRUCTURAL_SHARE_TYPES or shares_change is not None:
        raise BusinessError(
            "SHARES_CHANGE_ON_CASH_PRODUCT",
            f"{product_code} 为现金型/在途虚拟产品，不接受份额变动",
        )


def _validate_confirm_preconditions(db: Session, event: ShareChangeEvent) -> None:
    """确认与确认预览共用的前置校验序列（#424；#460 评审抽公共 preamble）。

    「预览 == 确认」要求两条路径的前置拒绝同序、同码、同消息——新增/调整确认
    前置时改此一处，两路径同时生效，杜绝静默分叉。含：状态门 + #279 双校验
    （forced_adjustment 双空、现金型产品份额变动；确认侧兜底防存量脏数据或
    绕过创建/更新入口直造的记录）。权益登记日快照存在性探针不在此——它内聚在
    resolve_entitlement_shares / _confirm_fund_level_event 的未命中分支
    （惰性探针，命中零额外查询，见 _require_entitlement_snapshot）。
    """
    if event.status != "pending":
        raise BusinessError("INVALID_STATUS", "仅 pending 状态可确认")
    _validate_adjustment_not_empty(
        event.event_type, event.shares_change, event.cash_change
    )
    _validate_product_allows_shares_change(
        db, event.product_code, event.market, event.event_type, event.shares_change
    )


def check_platform_coverage(
    db: Session,
    *,
    portfolio_code: str,
    product_code: str,
    entitlement_date: date,
    ex_date: date,
    platform_code: Optional[str],
) -> List[str]:
    """检查同 ex_date 的平台级事件是否覆盖所有有持仓的平台。
    返回未覆盖的平台列表（空列表表示全覆盖）。
    """
    positions = db.query(PortfolioPosition.platform_code).filter(
        PortfolioPosition.portfolio_code == portfolio_code,
        PortfolioPosition.product_code == product_code,
        PortfolioPosition.snapshot_date == entitlement_date,
        PortfolioPosition.shares > 0,
    ).distinct().all()
    held_platforms = {p[0] for p in positions if p[0]}

    existing = db.query(ShareChangeEvent.platform_code).filter(
        ShareChangeEvent.portfolio_code == portfolio_code,
        ShareChangeEvent.product_code == product_code,
        ShareChangeEvent.ex_date == ex_date,
        ShareChangeEvent.status != "cancelled",
        ShareChangeEvent.platform_code.isnot(None),
    ).distinct().all()
    covered = {p[0] for p in existing if p[0]}
    if platform_code:
        covered.add(platform_code)

    return list(held_platforms - covered)


def _confirm_fund_level_event(db: Session, event: ShareChangeEvent) -> None:
    """基金级事件确认：自动拆分为各平台子记录。
    供 confirm_share_change_event 和 auto_confirm_after_snapshot 共用。
    """
    all_positions = db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == event.portfolio_code,
        PortfolioPosition.product_code == event.product_code,
        PortfolioPosition.snapshot_date == event.entitlement_date,
        PortfolioPosition.shares > 0,
    ).all()

    if not all_positions:
        # 惰性探针：快照缺失报 MISSING_POSITION_SNAPSHOT（BusinessError 直传，
        # 不经 ValueError 包装）；快照存在才是「无持仓」——文案与预览共用常量
        _require_entitlement_snapshot(db, event)
        raise ValueError(MSG_FUND_LEVEL_NO_HOLDINGS)

    total_shares = Decimal("0")
    now = datetime.now()
    # #37 savepoint：子记录拆分失败回滚 savepoint，不影响外层事务
    sp = db.connection().begin_nested()
    try:
        for pos in all_positions:
            platform_shares = Decimal(str(pos.shares or 0))
            total_shares += platform_shares
            child = ShareChangeEvent(
                portfolio_code=event.portfolio_code,
                product_code=event.product_code,
                market=event.market,
                event_type=event.event_type,
                ex_date=event.ex_date,
                entitlement_date=event.entitlement_date,
                platform_code=pos.platform_code,
                event_source=event.event_source,
                parent_event_id=event.id,
                entitlement_shares=platform_shares,
                shares_before=platform_shares,
                ratio=event.ratio,
                div_cash=event.div_cash,
                reinvest_nav=event.reinvest_nav,
                status="confirmed",
                confirmed_at=now,
            )
            apply_event_fields(child)
            db.add(child)
        db.flush()
        sp.commit()
    except Exception:
        sp.rollback()
        raise

    # 父记录设汇总值
    event.entitlement_shares = total_shares
    event.shares_before = total_shares
    apply_event_fields(event)
    event.status = "confirmed"
    event.confirmed_at = now


def _validate_event_dates(
    db: Session, portfolio_code: str, ex_date: date, entitlement_date: date
) -> None:
    """校验事件双日期：均为交易日、ex_date > entitlement_date、晚于最新快照日。

    创建与更新（pending 改日期）共用，保证两条路径校验一致。
    """
    # 权益登记日必须是交易日
    if not is_trading_day(db, entitlement_date):
        raise BusinessError("INVALID_ENTITLEMENT_DATE", "权益登记日不是交易日")
    # 除息日必须是交易日
    if not is_trading_day(db, ex_date):
        raise BusinessError("INVALID_EX_DATE", "除息日不是交易日")
    # 除息日必须严格大于权益登记日
    if ex_date <= entitlement_date:
        raise BusinessError(
            "INVALID_DATE_ORDER",
            "除息日必须严格大于权益登记日（ex_date > entitlement_date）",
        )
    # 除息日必须晚于最新快照日
    latest_snapshot = get_latest_snapshot_date(db, portfolio_code)
    if latest_snapshot and ex_date <= latest_snapshot:
        raise BusinessError(
            "DATE_BEFORE_SNAPSHOT",
            f"除息日必须晚于最新快照日（{latest_snapshot}）",
        )


def create_share_change_event(
    db: Session,
    *,
    portfolio_code: str,
    event_type: str,
    ex_date: date,
    entitlement_date: date,
    product_code: Optional[str] = None,
    market: Optional[str] = None,
    platform_code: Optional[str] = None,
    entitlement_shares: Optional[Decimal] = None,
    shares_before: Optional[Decimal] = None,
    shares_change: Optional[Decimal] = None,
    shares_after: Optional[Decimal] = None,
    cash_change: Optional[Decimal] = None,
    cash_product_code: Optional[str] = None,
    div_cash: Optional[Decimal] = None,
    reinvest_nav: Optional[Decimal] = None,
    ratio: Optional[Decimal] = None,
    event_source: str = "manual",
    tushare_event_id: Optional[str] = None,
    notes: Optional[str] = None,
    force_cover: bool = False,
) -> ShareChangeEvent:
    """创建份额变动事件（含全部校验与平台分级约束），供 REST 与 CLI 共用。不 commit。"""
    # #343（口径同 #258 market 归一）：空串入参归一为 None——平台级仍由
    # PLATFORM_REQUIRED 拦截，基金级空串落库 NULL 而非触发平台外键违约 500
    platform_code = platform_code or None
    if not product_code:
        raise BusinessError("PRODUCT_REQUIRED", "份额变动事件必须指定 product_code")
    _validate_event_dates(db, portfolio_code, ex_date, entitlement_date)

    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", "组合不存在")

    # issue #258（口径同 #83 调仓创建）：market 省略/空串时按产品唯一市场补全；
    # 一码多市场（LOF）报 MARKET_AMBIGUOUS；产品不存在报 PRODUCT_NOT_FOUND；
    # 显式 (code, market) 组合不存在报 NOT_FOUND——杜绝复合外键违约 500
    product_code, market = resolve_product_market(db, product_code, market)
    if not db.query(Product).filter(
        Product.code == product_code, Product.market == market
    ).first():
        details = {"product_code": product_code, "market": market}
        other_markets = sorted(
            row[0] or ""
            for row in db.query(Product.market)
            .filter(Product.code == product_code)
            .all()
        )
        if other_markets:
            details["available_markets"] = other_markets
        raise NotFoundError(
            "NOT_FOUND", f"产品 {product_code}({market}) 不存在", details=details
        )

    # issue #279：双空强制调整与现金型产品份额变动在创建期拦截（REST/CLI 共用）
    _validate_adjustment_not_empty(event_type, shares_change, cash_change)
    _validate_product_allows_shares_change(db, product_code, market, event_type, shares_change)

    # 分级校验：平台级必填 platform_code，基金级禁止指定
    if event_type in PLATFORM_LEVEL_TYPES:
        if not platform_code:
            raise BusinessError(
                "PLATFORM_REQUIRED",
                f"{event_type} 为平台级事件，必须指定 platform_code",
            )
        platform = db.query(Platform).filter(Platform.code == platform_code).first()
        if not platform:
            raise NotFoundError("PLATFORM_NOT_FOUND", f"平台 {platform_code} 不存在")
        # 全覆盖校验（默认阻断，force_cover 降为 warning）
        uncovered = check_platform_coverage(
            db, portfolio_code=portfolio_code, product_code=product_code,
            entitlement_date=entitlement_date, ex_date=ex_date, platform_code=platform_code,
        )
        if uncovered:
            if not force_cover:
                raise BusinessError(
                    "PLATFORM_NOT_COVERED",
                    f"平台覆盖不全，未覆盖平台: {uncovered}，可传 force_cover=true 降级为 warning",
                )
            logger.warning(f"平台覆盖不全（force_cover），未覆盖平台: {uncovered}")
    elif event_type in FUND_LEVEL_TYPES:
        if platform_code:
            raise BusinessError(
                "PLATFORM_NOT_ALLOWED",
                f"{event_type} 为基金级事件，不应指定 platform_code",
            )

    new_event = ShareChangeEvent(
        portfolio_code=portfolio_code,
        product_code=product_code,
        market=market,
        event_type=event_type,
        ex_date=ex_date,
        entitlement_date=entitlement_date,
        platform_code=platform_code,
        # 用户直填的份额类字段统一量化到 2 位（cash_change 是金额不量化）
        entitlement_shares=quantize_shares(entitlement_shares),
        shares_before=quantize_shares(shares_before),
        shares_change=quantize_shares(shares_change),
        shares_after=quantize_shares(shares_after),
        cash_change=cash_change,
        cash_product_code=cash_product_code,
        div_cash=div_cash,
        reinvest_nav=reinvest_nav,
        ratio=ratio,
        event_source=event_source,
        tushare_event_id=tushare_event_id,
        notes=notes,
        status="pending",
    )
    db.add(new_event)
    db.flush()

    record_audit(
        db,
        action=ACTION_CREATE,
        resource_type=RESOURCE_SHARE_CHANGE_EVENT,
        resource_id=str(new_event.id),
        resource_name=f"{portfolio_code}/{product_code}/{event_type}",
        new_value={
            "portfolio_code": portfolio_code,
            "product_code": product_code,
            "market": market,
            "event_type": event_type,
            "ex_date": ex_date,
            "entitlement_date": entitlement_date,
            "platform_code": platform_code,
            "shares_change": new_event.shares_change,
            "cash_change": new_event.cash_change,
            "status": "pending",
        },
    )

    return new_event


def update_share_change_event(
    db: Session, event: ShareChangeEvent, updates: dict
) -> ShareChangeEvent:
    """更新份额变动事件（仅 pending 可改），供 REST 与 CLI 共用。不 commit。

    - confirmed 拒绝直改（含基金级子记录，子记录恒为 confirmed），
      须先 unconfirm，经快照保护（SNAPSHOT_DEPENDENCY）把关
    - 日期变更时用合并后生效值重跑创建时的双日期校验
    """
    if event.status == "confirmed":
        raise BusinessError(
            "CANNOT_MODIFY_CONFIRMED",
            "已确认的份额变动事件不可直接修改，请先取消确认后再修改",
        )

    if updates.keys() & {"ex_date", "entitlement_date"}:
        effective_ex_date = updates.get("ex_date", event.ex_date)
        effective_entitlement_date = updates.get(
            "entitlement_date", event.entitlement_date
        )
        _validate_event_dates(
            db, event.portfolio_code, effective_ex_date, effective_entitlement_date
        )

    # issue #279：按合并后值校验（event_type/product_code 不可改，恒取事件现值），
    # 封死 PUT 改成双空或为现金型产品补填份额变动的绕过路径
    merged_shares_change = updates.get("shares_change", event.shares_change)
    merged_cash_change = updates.get("cash_change", event.cash_change)
    _validate_adjustment_not_empty(
        event.event_type, merged_shares_change, merged_cash_change
    )
    _validate_product_allows_shares_change(
        db, event.product_code, event.market, event.event_type, merged_shares_change
    )

    old_values, new_values = _diff_fields(event, updates)

    for field, value in updates.items():
        setattr(event, field, value)

    # 无实际变更不留痕：PUT 整对象重提交是编辑表单常态，否则每次保存都灌一条
    # old/new 皆 NULL 的空载荷行（零取证价值），同「空删除不留痕」口径
    if old_values or new_values:
        record_audit(
            db,
            action=ACTION_UPDATE,
            resource_type=RESOURCE_SHARE_CHANGE_EVENT,
            resource_id=str(event.id),
            resource_name=f"{event.portfolio_code}/{event.product_code}/{event.event_type}",
            old_value=old_values or None,
            new_value=new_values or None,
        )

    return event


def confirm_share_change_event(db: Session, event: ShareChangeEvent) -> ShareChangeEvent:
    """确认份额变动事件（回写 entitlement_shares、计算、基金级自动拆分）。不 commit。"""
    # 前置校验与预览共用同一 preamble（同序同码同消息）：状态门 + #279 双校验；
    # 快照存在性探针内聚在 resolve_entitlement_shares / _confirm_fund_level_event
    _validate_confirm_preconditions(db, event)

    if event.platform_code is None:
        # 基金级事件：自动拆分为各平台子记录
        try:
            _confirm_fund_level_event(db, event)
        except ValueError as e:
            raise BusinessError("MISSING_POSITION_SNAPSHOT", str(e))
    else:
        # 权益份额口径与预览共用单点实现（issue #424）：
        # forced_adjustment 的 (产品, market, 平台) 精查在此（POSITION_NOT_FOUND，#278），
        # 其余平台级按 platform_code 过滤（无命中 → 0）
        entitlement_shares = resolve_entitlement_shares(db, event)
        event.entitlement_shares = entitlement_shares
        event.shares_before = entitlement_shares
        apply_event_fields(event)
        event.status = "confirmed"
        event.confirmed_at = datetime.now()

    record_audit(
        db,
        action=ACTION_CONFIRM,
        resource_type=RESOURCE_SHARE_CHANGE_EVENT,
        resource_id=str(event.id),
        resource_name=f"{event.portfolio_code}/{event.product_code}/{event.event_type}",
        old_value={"status": "pending"},
        new_value={
            "status": "confirmed",
            "entitlement_shares": event.entitlement_shares,
            "shares_before": event.shares_before,
            "shares_change": event.shares_change,
            "shares_after": event.shares_after,
            "cash_change": event.cash_change,
        },
    )

    return event


def cancel_share_change_event(db: Session, event: ShareChangeEvent) -> ShareChangeEvent:
    """取消份额变动事件（仅 pending）。不 commit。"""
    if event.status != "pending":
        raise BusinessError("INVALID_STATUS", "仅 pending 状态可取消")
    event.status = "cancelled"

    record_audit(
        db,
        action=ACTION_CANCEL,
        resource_type=RESOURCE_SHARE_CHANGE_EVENT,
        resource_id=str(event.id),
        resource_name=f"{event.portfolio_code}/{event.product_code}/{event.event_type}",
        old_value={"status": "pending"},
        new_value={"status": "cancelled"},
    )

    return event


def unconfirm_share_change_event(db: Session, event: ShareChangeEvent) -> ShareChangeEvent:
    """取消确认份额变动事件（快照保护 + 子记录级联 + 清空计算字段）。不 commit。

    - 仅 confirmed 状态可 unconfirm
    - 子记录（parent_event_id 非空）单独 unconfirm 拒绝
    - ex_date 及之后已有快照则拒绝
    - 基金级父记录级联删除所有子记录
    """
    if event.status != "confirmed":
        raise BusinessError("INVALID_STATUS", "仅 confirmed 状态可取消确认")

    # 子记录不允许单独 unconfirm
    if event.parent_event_id is not None:
        raise BusinessError(
            "CANNOT_UNCONFIRM_CHILD",
            "基金级事件的子记录不允许单独取消确认，请对父记录执行 unconfirm",
        )

    # 快照保护：ex_date 及之后已有快照则拒绝
    snapshots_after = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == event.portfolio_code,
        PortfolioValueSnapshot.snapshot_date >= event.ex_date,
    ).count()
    if snapshots_after > 0:
        raise BusinessError(
            "SNAPSHOT_DEPENDENCY",
            f"该事件已被快照纳入（{event.ex_date} 及之后有 {snapshots_after} 张快照），"
            f"请先删除 {event.ex_date} 及之后的快照",
        )

    if event.platform_code is None:
        # 基金级父记录：级联删除所有子记录
        db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == event.id
        ).delete(synchronize_session=False)

    # 置 pending 并清空确认时回写的计算字段
    event.status = "pending"
    event.confirmed_at = None
    event.entitlement_shares = None
    event.shares_before = None
    # issue #263：forced_adjustment 的 shares_change/shares_after/cash_change 是
    # 用户直填值（唯一存处），unconfirm 不得清空，否则重新确认时静默丢失调整量；
    # 其余类型的这些字段为确认时计算值，照常清空
    if event.event_type != "forced_adjustment":
        event.shares_change = None
        event.shares_after = None
        event.cash_change = None

    record_audit(
        db,
        action=ACTION_UNCONFIRM,
        resource_type=RESOURCE_SHARE_CHANGE_EVENT,
        resource_id=str(event.id),
        resource_name=f"{event.portfolio_code}/{event.product_code}/{event.event_type}",
        old_value={"status": "confirmed"},
        new_value={"status": "pending"},
    )

    return event


def delete_share_change_event(db: Session, event: ShareChangeEvent) -> None:
    """删除份额变动事件（级联删除子记录）。不 commit。"""
    record_audit(
        db,
        action=ACTION_DELETE,
        resource_type=RESOURCE_SHARE_CHANGE_EVENT,
        resource_id=str(event.id),
        resource_name=f"{event.portfolio_code}/{event.product_code}/{event.event_type}",
        old_value={
            "status": event.status,
            "event_type": event.event_type,
            "ex_date": event.ex_date,
            "entitlement_date": event.entitlement_date,
            "shares_change": event.shares_change,
            "cash_change": event.cash_change,
        },
    )

    db.query(ShareChangeEvent).filter(
        ShareChangeEvent.parent_event_id == event.id
    ).delete(synchronize_session=False)

    db.delete(event)
