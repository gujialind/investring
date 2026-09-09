"""
持仓相关服务函数

从 routers 层提取的共享函数，供 CLI 和 router 共用。
"""
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.manual_market_value import ManualMarketValue
from app.models.portfolio_position import PortfolioPosition
from app.models.share_change_event import ShareChangeEvent
from app.models.subscription import Subscription
from app.models.trade import Trade
from app.models.portfolio import Portfolio
from app.models.platform import Platform
from app.services.trading_utils import (
    get_latest_snapshot_date,
    get_latest_snapshot_date_le,
    is_trading_day,
)
from app.services.exceptions import BusinessError, NotFoundError
from app.services.audit_service import record_audit
from app.constants.audit_actions import (
    ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE,
    RESOURCE_MANUAL_MARKET_VALUE,
)
from app.utils.quantize import quantize_amount


def compute_cash_balance(
    db: Session,
    portfolio_code: str,
    platform_code: Optional[str] = None,
    as_of_date: Optional[date] = None,
) -> Decimal:
    """
    显式计算 as_of_date 时的现金余额。

    源1：trade 表 confirmed CASH trades（confirm_date <= as_of_date）
    源2：event 表 confirmed events（ex_date <= as_of_date, cash_change != 0）

    不含 manual_market_value 覆盖。用于：
    - 无快照时 calculate_available_cash 的降级基线
    - cash-position 端点审计字段（computed_value）
    快照生成走 _generate_portfolio_position 增量累加路径，不调用此函数。
    有快照时 calculate_available_cash 直接读 portfolio_position 快照表，亦不调用此函数。
    """
    if as_of_date is None:
        as_of_date = date.today()

    balance = Decimal("0")

    # 源1：CASH trades
    trades = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.product_code == "CASH",
        Trade.status == "confirmed",
        Trade.confirm_date <= as_of_date,
    )
    if platform_code:
        trades = trades.filter(Trade.platform_code == platform_code)

    for t in trades.all():
        if t.trade_type == "buy":
            balance += Decimal(str(t.amount or 0))
        elif t.trade_type == "sell":
            balance -= Decimal(str(t.amount or 0))

    # 源2：事件 cash_change
    events = db.query(ShareChangeEvent).filter(
        ShareChangeEvent.portfolio_code == portfolio_code,
        ShareChangeEvent.status == "confirmed",
        ShareChangeEvent.ex_date <= as_of_date,
        ShareChangeEvent.cash_change.isnot(None),
        ShareChangeEvent.cash_change != 0,
    )
    if platform_code:
        events = events.filter(ShareChangeEvent.platform_code == platform_code)

    for e in events.all():
        balance += Decimal(str(e.cash_change))

    return balance


# ---------------------------------------------------------------------------
# 持仓读侧派生（issue #99）：daily_profit / 现金累计收益 / 分红加回
#
# 口径（路线 C：分红加回基金 + 事件计入现金基数，与评审推演一致）：
# - 非现金行 profit_loss = 市值 − 份额×成本 + Σ 该产品/平台 confirmed 事件 cash_change
#   （现金分红加回 = 复权口径；拆分/合并事件 cash_change=0 天然无影响）
# - 非现金行 daily_profit = 当日市值 − 前一快照日市值 − 当日确认净买入额
#   + 当日事件 cash_change（分红日加回对冲除权，daily=0）
# - 现金行 profit_loss = 当前现金 − 现金基数（累计净存入 − 累计买入 + 累计卖出
#   + 转移净额 + 事件净额），≈ 0，仅吸收手动调平/利息等未记账差额
# - 现金行 daily_profit = 当日现金 − 前日现金 − 当日净流入（同基数五类流水按日过滤）
# - IN_TRANSIT_BUY/SELL 行、组合首个快照日均无收益概念 → None
# 全部为批量查询 + 内存映射，禁止 N+1。
# ---------------------------------------------------------------------------

# 在途资金虚拟产品：无收益概念
IN_TRANSIT_PRODUCT_CODES = ("IN_TRANSIT_BUY", "IN_TRANSIT_SELL")


def _classify_transfer_group(transfer_group: str) -> str:
    """transfer_group 前缀分类：sub_=申赎外部流；rebal_=调仓内部流；其余=跨平台转移"""
    if transfer_group.startswith("sub_"):
        return "sub"
    if transfer_group.startswith("rebal_"):
        return "rebal"
    return "transfer"


def _aggregate_position_flows(db: Session, items: Sequence[PortfolioPosition]) -> dict:
    """批量聚合 items 涉及组合的 trade/事件流水（派生计算共用，防 N+1）。

    返回：
      trade_daily: {(portfolio, product, market, platform): {confirm_date: 买入额−卖出额}}
        （基金腿，按确认日分组）
      cash_flows: {(portfolio, platform): [(confirm_date, 分类, trade_type, 金额), ...]}
        （CASH 腿，confirm_date <= 行快照日上限）
      event_rows: {(portfolio, product, platform): [(ex_date, cash_change), ...]}
        （confirmed 且 cash_change != 0，ex_date <= 行快照日上限）
    """
    portfolio_codes = {p.portfolio_code for p in items}
    max_date = max(p.snapshot_date for p in items)

    trade_daily = {}
    cash_flows: dict = {}
    trade_rows = (
        db.query(
            Trade.portfolio_code,
            Trade.product_code,
            Trade.market,
            Trade.platform_code,
            Trade.trade_type,
            Trade.transfer_group,
            Trade.confirm_date,
            func.sum(Trade.amount),
        )
        .filter(
            Trade.portfolio_code.in_(portfolio_codes),
            Trade.status == "confirmed",
            Trade.confirm_date <= max_date,
        )
        .group_by(
            Trade.portfolio_code,
            Trade.product_code,
            Trade.market,
            Trade.platform_code,
            Trade.trade_type,
            Trade.transfer_group,
            Trade.confirm_date,
        )
        .all()
    )
    for pc, prod, market, plat, ttype, tgroup, confirm_date, amount in trade_rows:
        amt = float(amount or 0)
        if prod == "CASH":
            category = _classify_transfer_group(tgroup)
            cash_flows.setdefault((pc, plat), []).append((confirm_date, category, ttype, amt))
        else:
            key = (pc, prod, market, plat)
            signed = amt if ttype == "buy" else -amt
            trade_daily.setdefault(key, {})
            trade_daily[key][confirm_date] = trade_daily[key].get(confirm_date, 0.0) + signed

    event_rows: dict = {}
    events = (
        db.query(
            ShareChangeEvent.portfolio_code,
            ShareChangeEvent.product_code,
            ShareChangeEvent.platform_code,
            ShareChangeEvent.ex_date,
            func.sum(ShareChangeEvent.cash_change),
        )
        .filter(
            ShareChangeEvent.portfolio_code.in_(portfolio_codes),
            ShareChangeEvent.status == "confirmed",
            ShareChangeEvent.cash_change.isnot(None),
            ShareChangeEvent.cash_change != 0,
            ShareChangeEvent.ex_date <= max_date,
        )
        .group_by(
            ShareChangeEvent.portfolio_code,
            ShareChangeEvent.product_code,
            ShareChangeEvent.platform_code,
            ShareChangeEvent.ex_date,
        )
        .all()
    )
    for pc, prod, plat, ex_date, cash_change in events:
        event_rows.setdefault((pc, prod, plat), []).append((ex_date, float(cash_change)))

    return {
        "trade_daily": trade_daily,
        "cash_flows": cash_flows,
        "event_rows": event_rows,
    }


def compute_daily_profits(
    db: Session,
    items: Sequence[PortfolioPosition],
    flows: dict,
) -> dict:
    """批量计算持仓行当日收益（读侧派生，不落库）。

    ``flows`` 由调用方经 ``compute_derived_fields`` 统一聚合后注入（issue #103，
    避免三函数各自重复聚合）。

    返回 {(portfolio_code, product_code, market, platform_code): float | None}。
    None 场景：组合首个快照日（无前日基准）、IN_TRANSIT 在途行。
    快照断层时取最近可得前一快照日为基准。
    """
    result = {}
    if not items:
        return result

    portfolio_codes = {p.portfolio_code for p in items}

    # 每个组合的快照日期集合：定位首个快照日与前一快照日
    date_rows = (
        db.query(PortfolioPosition.portfolio_code, PortfolioPosition.snapshot_date)
        .filter(PortfolioPosition.portfolio_code.in_(portfolio_codes))
        .distinct()
        .all()
    )
    dates_by_portfolio: dict = {}
    for pc, snap_date in date_rows:
        dates_by_portfolio.setdefault(pc, set()).add(snap_date)

    # 需要前日基准的行 → 批量查前一快照日持仓
    prev_needed = []  # (row, prev_date)
    for p in items:
        key = (p.portfolio_code, p.product_code, p.market, p.platform_code)
        if p.product_code in IN_TRANSIT_PRODUCT_CODES:
            result[key] = None
            continue
        dates = dates_by_portfolio.get(p.portfolio_code, set())
        prev_dates = [d for d in dates if d < p.snapshot_date]
        if not prev_dates:
            result[key] = None  # 首个快照日，无前日基准
            continue
        prev_needed.append((p, max(prev_dates)))

    prev_map = {}
    if prev_needed:
        prev_date_set = {d for _, d in prev_needed}
        prev_rows = (
            db.query(PortfolioPosition)
            .filter(
                PortfolioPosition.portfolio_code.in_(portfolio_codes),
                PortfolioPosition.snapshot_date.in_(prev_date_set),
            )
            .all()
        )
        for row in prev_rows:
            prev_map[
                (row.portfolio_code, row.product_code, row.market, row.platform_code, row.snapshot_date)
            ] = row

    trade_daily = flows["trade_daily"]
    cash_flows = flows["cash_flows"]
    event_rows = flows["event_rows"]

    for p, prev_date in prev_needed:
        key = (p.portfolio_code, p.product_code, p.market, p.platform_code)
        prev_row = prev_map.get(
            (p.portfolio_code, p.product_code, p.market, p.platform_code, prev_date)
        )
        d = p.snapshot_date
        if p.shares is not None:
            # 非现金行：市值差 − 当日确认净买入 + 当日事件分红加回（复权口径）
            mv_now = float(p.market_value or 0)
            mv_prev = float(prev_row.market_value or 0) if prev_row else 0.0
            net_buy = trade_daily.get(
                (p.portfolio_code, p.product_code, p.market, p.platform_code), {}
            ).get(d, 0.0)
            event_today = sum(
                v for ex, v in event_rows.get(
                    (p.portfolio_code, p.product_code, p.platform_code), []
                ) if ex == d
            )
            result[key] = round(mv_now - mv_prev - net_buy + event_today, 4)
        else:
            # 现金行：现金差 − 当日净流入（sub/rebal/转移/事件四类按日过滤）
            cash_now = float(p.cash_amount or 0)
            cash_prev = float(prev_row.cash_amount or 0) if prev_row else 0.0
            inflow = 0.0
            for confirm_date, category, ttype, amt in cash_flows.get(
                (p.portfolio_code, p.platform_code), []
            ):
                if confirm_date != d:
                    continue
                if category == "sub":
                    inflow += amt if ttype == "buy" else -amt
                elif category == "rebal":
                    inflow += amt if ttype == "buy" else -amt
                else:  # transfer：双腿同组同日分别计入各自平台
                    inflow += amt if ttype == "buy" else -amt
            event_today = sum(
                v
                for (epc, _prod, eplat), entries in event_rows.items()
                if epc == p.portfolio_code and eplat == p.platform_code
                for ex, v in entries
                if ex == d
            )
            inflow += event_today
            result[key] = round(cash_now - cash_prev - inflow, 4)

    return result


def compute_cash_cumulative_profits(
    db: Session,
    items: Sequence[PortfolioPosition],
    flows: dict,
) -> dict:
    """批量计算 CASH 行累计收益（读侧派生，路线 C 现金基数口径）。

    ``flows`` 由调用方经 ``compute_derived_fields`` 统一聚合后注入（issue #103）。

    现金累计收益 = 当前现金 −（累计净存入 − 累计买入 + 累计卖出 + 转移净额 + 事件净额），
    事件净额计入存入基数 → 收益 ≈ 0，仅吸收手动调平/利息等未记账差额。
    返回 {(portfolio_code, product_code, market, platform_code): float | None}，
    仅含 CASH 行；其他行不在返回中。
    """
    result = {}
    cash_items = [
        p for p in items
        if p.product_code == "CASH" and p.shares is None
    ]
    if not cash_items:
        return result

    cash_flows = flows["cash_flows"]
    event_rows = flows["event_rows"]

    for p in cash_items:
        key = (p.portfolio_code, p.product_code, p.market, p.platform_code)
        d = p.snapshot_date
        net_deposit = buys = sells = transfer_net = 0.0
        for confirm_date, category, ttype, amt in cash_flows.get(
            (p.portfolio_code, p.platform_code), []
        ):
            if confirm_date > d:
                continue
            if category == "sub":
                net_deposit += amt if ttype == "buy" else -amt
            elif category == "rebal":
                if ttype == "sell":
                    buys += amt  # 买产品花现金
                else:
                    sells += amt  # 卖产品收现金
            else:
                transfer_net += amt if ttype == "buy" else -amt
        event_net = sum(
            v
            for (epc, _prod, eplat), entries in event_rows.items()
            if epc == p.portfolio_code and eplat == p.platform_code
            for ex, v in entries
            if ex <= d
        )
        basis = net_deposit - buys + sells + transfer_net + event_net
        result[key] = round(float(p.cash_amount or 0) - basis, 4)

    return result


def compute_event_cash_addbacks(
    db: Session,
    items: Sequence[PortfolioPosition],
    flows: dict,
) -> dict:
    """批量计算非现金行累计分红加回（复权口径，读侧派生）。

    ``flows`` 由调用方经 ``compute_derived_fields`` 统一聚合后注入（issue #103）。

    返回 {(portfolio_code, product_code, market, platform_code): float}，
    值为 confirmed 事件 cash_change 按 (产品, 平台) 聚合（ex_date <= 行快照日）。
    拆分/合并事件 cash_change=0 天然无影响；无事件行不在返回中（调用方按 0 处理）。
    """
    result = {}
    fund_items = [p for p in items if p.shares is not None]
    if not fund_items:
        return result

    event_rows = flows["event_rows"]

    for p in fund_items:
        key = (p.portfolio_code, p.product_code, p.market, p.platform_code)
        total = sum(
            v
            for ex, v in event_rows.get(
                (p.portfolio_code, p.product_code, p.platform_code), []
            )
            if ex <= p.snapshot_date
        )
        if total:
            result[key] = round(total, 4)

    return result


def compute_derived_fields(
    db: Session,
    items: Sequence[PortfolioPosition],
) -> dict:
    """一次聚合，批量产出持仓读侧派生字段（issue #103：消除三函数重复聚合）。

    聚合 ``_aggregate_position_flows`` 仅执行 1 次（trade + event 共 2 条查询），
    结果注入 ``compute_daily_profits`` / ``compute_cash_cumulative_profits`` /
    ``compute_event_cash_addbacks`` 三个 helper，避免各自独立聚合导致的冗余查询。

    返回 ``{"daily_profits": {...}, "cash_profits": {...}, "event_addbacks": {...}}``；
    ``items`` 为空时返回三个空 dict。
    """
    empty = {"daily_profits": {}, "cash_profits": {}, "event_addbacks": {}}
    if not items:
        return empty
    flows = _aggregate_position_flows(db, items)
    return {
        "daily_profits": compute_daily_profits(db, items, flows),
        "cash_profits": compute_cash_cumulative_profits(db, items, flows),
        "event_addbacks": compute_event_cash_addbacks(db, items, flows),
    }


def get_cash_value(
    db: Session,
    portfolio_code: str,
    platform_code: Optional[str],
    target_date: Optional[date],
) -> Decimal:
    """
    获取指定日期的现金值（含 manual_market_value 绝对替换）。

    基础 = compute_cash_balance(target_date)
    若存在 manual_market_value 覆盖（value_date == target_date），则绝对替换。

    target_date 为 None 时：compute_cash_balance 降级为 today，
    且跳过 manual 覆盖查询（无快照日可匹配）。

    注意：calculate_available_cash 已改为直接读快照表基线（#52），
    本函数保留用于 cash-position 端点审计展示等辅助场景。
    """
    v = compute_cash_balance(db, portfolio_code, platform_code, target_date)
    if target_date is not None:
        manual = db.query(ManualMarketValue).filter(
            ManualMarketValue.portfolio_code == portfolio_code,
            ManualMarketValue.platform_code == platform_code,
            ManualMarketValue.product_code == "CASH",
            ManualMarketValue.value_date == target_date,
        ).first()
        if manual:
            v = Decimal(str(manual.market_value))
    return v


def calculate_available_cash(
    db: Session,
    portfolio_code: str,
    platform_code: Optional[str] = None,
    as_of_date: Optional[date] = None,
) -> Decimal:
    """
    组合可用现金实时计算（显式流水版）。

    基线 = 最新快照日 portfolio_position 快照表中 CASH 行的 cash_amount
    （与 _generate_portfolio_position 增量范式口径一致，manual_market_value
    覆盖已 baked in 快照，自然继承；无快照时降级为 compute_cash_balance 全量流水）
    + 快照后 confirmed CASH buys（流入）
    − 快照后 confirmed CASH sells（流出）
    − pending CASH sells（已承诺未执行）
    + 快照后 confirmed event cash_change

    时点口径（#70/#78）：CASH 流出（sell）的资金承诺锚定**下单日 trade_date**，
    不论 pending/confirmed——confirmed sell 的 as_of 上限按 trade_date（而非
    confirm_date）判定，pending sell 仅在 trade_date <= as_of_date 时计提，
    消除 pending→confirmed 翻转后"预留隐身"；CASH 流入（buy）仍须 confirmed
    且 confirm_date <= as_of_date 才计入。

    as_of_date 为 None 时取当前最新快照日为基线、快照后范围不设上限
    （confirmed buy/sell 均计入，pending sell 全额计提，与历史口径一致）；
    传入时基线取 <= as_of_date 的最新快照日，confirmed buys 计
    confirm_date ∈ (latest_date, as_of_date]，confirmed sells 计
    confirm_date > latest_date 且 trade_date <= as_of_date，
    pending sells 计 trade_date <= as_of_date。
    """
    if as_of_date is not None:
        latest_date = get_latest_snapshot_date_le(db, portfolio_code, as_of_date)
    else:
        latest_date = get_latest_snapshot_date(db, portfolio_code)

    # 基线：直接读快照表 CASH 持仓（与 _generate_portfolio_position 增量范式口径一致，
    # manual_market_value 覆盖已 baked in 快照，自然继承）
    if latest_date is not None:
        cash_query = db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == portfolio_code,
            PortfolioPosition.product_code == "CASH",
            PortfolioPosition.snapshot_date == latest_date,
        )
        if platform_code:
            cash_query = cash_query.filter(PortfolioPosition.platform_code == platform_code)
        cash = sum(
            Decimal(str(p.cash_amount or 0)) for p in cash_query.all()
        )
    else:
        cash = compute_cash_balance(db, portfolio_code, platform_code, as_of_date)

    if latest_date is None:
        latest_date = date(1970, 1, 1)  # 确保 > 条件对所有 trade 生效

    # 快照后 confirmed CASH buys（流入：confirm_date <= as_of_date 才计入）
    after_buys = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.product_code == "CASH",
        Trade.status == "confirmed",
        Trade.trade_type == "buy",
        Trade.confirm_date > latest_date,
    )
    if as_of_date is not None:
        after_buys = after_buys.filter(Trade.confirm_date <= as_of_date)
    if platform_code:
        after_buys = after_buys.filter(Trade.platform_code == platform_code)

    for t in after_buys.all():
        cash += Decimal(str(t.amount or 0))

    # 快照后 confirmed CASH sells（流出：承诺锚定下单日，上限按 trade_date 判定）
    after_sells = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.product_code == "CASH",
        Trade.status == "confirmed",
        Trade.trade_type == "sell",
        Trade.confirm_date > latest_date,
    )
    if as_of_date is not None:
        after_sells = after_sells.filter(Trade.trade_date <= as_of_date)
    if platform_code:
        after_sells = after_sells.filter(Trade.platform_code == platform_code)

    for t in after_sells.all():
        cash -= Decimal(str(t.amount or 0))

    # pending CASH sells（已承诺未执行，需预留；as_of 时点仅计提已下单的）
    pending_sells = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.product_code == "CASH",
        Trade.status == "pending",
        Trade.trade_type == "sell",
    )
    if as_of_date is not None:
        pending_sells = pending_sells.filter(Trade.trade_date <= as_of_date)
    if platform_code:
        pending_sells = pending_sells.filter(Trade.platform_code == platform_code)

    for t in pending_sells.all():
        cash -= Decimal(str(t.amount or 0))

    # 快照后 confirmed event cash_change
    after_events = db.query(ShareChangeEvent).filter(
        ShareChangeEvent.portfolio_code == portfolio_code,
        ShareChangeEvent.status == "confirmed",
        ShareChangeEvent.ex_date > latest_date,
        ShareChangeEvent.cash_change.isnot(None),
        ShareChangeEvent.cash_change != 0,
    )
    if as_of_date is not None:
        after_events = after_events.filter(ShareChangeEvent.ex_date <= as_of_date)
    if platform_code:
        after_events = after_events.filter(ShareChangeEvent.platform_code == platform_code)
    for e in after_events.all():
        cash += Decimal(str(e.cash_change))

    return cash


def calculate_available_shares(
    db: Session,
    portfolio_code: str,
    product_code: str,
    market: Optional[str] = None,
    as_of_date: Optional[date] = None,
) -> Decimal:
    """
    基金可用份额实时计算：
    基金可用份额 = 最新快照份额
                - SUM(pending卖出份额)
                - SUM(confirmed卖出份额 WHERE 快照未生成)
                + SUM(confirmed事件负向shares_change WHERE ex_date > 最新快照日 [≤ as_of])

    事件增量口径（issue #277）：与现金侧 cash_change 增量同窗口——
    只计平台级行（platform_code IS NOT NULL，基金级父记录持汇总值、防父子双计）；
    只计负向（正向变动入快照前保守低估，防事件被撤销后已放行的卖出成事实超卖）。

    as_of_date 为 None 时基线取当前最新快照日；传入时取 <= as_of_date 的最新快照日，
    confirmed 卖出仅计 confirm_date 在 (latest_date, as_of_date] 的。

    基线口径（#305）：最新快照日的全部持仓行份额求和（market 为空时跨市场、
    恒跨平台），与卖出/事件增量的汇总口径一致。
    """
    if as_of_date is not None:
        latest_date = get_latest_snapshot_date_le(db, portfolio_code, as_of_date)
    else:
        latest_date = get_latest_snapshot_date(db, portfolio_code)

    query = db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == portfolio_code,
        PortfolioPosition.product_code == product_code,
    )
    if market:
        query = query.filter(PortfolioPosition.market == market)
    if as_of_date is not None:
        query = query.filter(PortfolioPosition.snapshot_date <= as_of_date)
    # #305：基线 = 最新快照日全部行份额求和（跨市场/跨平台），与下方卖出/事件
    # 增量的汇总口径一致；旧实现单行 .first() 在 LOF 多市场/多平台场景少取行
    latest_pos_date = query.with_entities(
        func.max(PortfolioPosition.snapshot_date)
    ).scalar()
    shares = Decimal("0")
    if latest_pos_date is not None:
        baseline_total = query.filter(
            PortfolioPosition.snapshot_date == latest_pos_date,
            PortfolioPosition.shares.isnot(None),
        ).with_entities(func.sum(PortfolioPosition.shares)).scalar()
        if baseline_total is not None:
            shares = Decimal(str(baseline_total))

    pending_sells = (
        db.query(Trade)
        .filter(
            Trade.portfolio_code == portfolio_code,
            Trade.product_code == product_code,
            Trade.status == "pending",
            Trade.trade_type == "sell",
        )
        .all()
    )
    for t in pending_sells:
        shares -= Decimal(t.shares) if t.shares else Decimal("0")

    confirmed_sells = (
        db.query(Trade)
        .filter(
            Trade.portfolio_code == portfolio_code,
            Trade.product_code == product_code,
            Trade.status == "confirmed",
            Trade.trade_type == "sell",
        )
        .all()
    )
    for t in confirmed_sells:
        if latest_date is None or (
            t.confirm_date and t.confirm_date > latest_date
            and (as_of_date is None or t.confirm_date <= as_of_date)
        ):
            shares -= Decimal(t.shares) if t.shares else Decimal("0")

    # 快照后 confirmed event 负向 shares_change（issue #277，与现金侧 cash_change 增量同窗口口径）
    event_query = db.query(ShareChangeEvent).filter(
        ShareChangeEvent.portfolio_code == portfolio_code,
        ShareChangeEvent.product_code == product_code,
        ShareChangeEvent.status == "confirmed",
        ShareChangeEvent.platform_code.isnot(None),  # 跳过基金级父记录（持汇总值，防父子双计）
        ShareChangeEvent.shares_change < 0,  # 只计负向（正向保守低估，见 docstring）
    )
    if latest_date is not None:
        event_query = event_query.filter(ShareChangeEvent.ex_date > latest_date)
    if as_of_date is not None:
        event_query = event_query.filter(ShareChangeEvent.ex_date <= as_of_date)
    if market:
        event_query = event_query.filter(ShareChangeEvent.market == market)
    for e in event_query.all():
        shares += Decimal(str(e.shares_change))

    return shares


def calculate_investor_available_shares(
    db: Session,
    portfolio_code: str,
    investor_code: str,
    as_of_date: Optional[date] = None,
) -> Decimal:
    """
    投资人可用份额实时计算：
    投资人可用份额 = 最新快照份额
                  - SUM(pending赎回份额)
                  - SUM(confirmed赎回份额 WHERE 快照未生成)

    as_of_date 为 None 时基线取当前最新快照日；传入时取 <= as_of_date 的最新快照日，
    confirmed 赎回仅计 confirm_date 在 (latest_date, as_of_date] 的。

    issue #277：份额变动事件不并入本函数——组合份额仅因申赎变化，事件作用于
    基金/平台维度、不改投资人份额账本；其影响是确认至入快照窗口内赎回按旧净值
    计价的估值陈旧，属既有系统性特性，故不做近似换算扣减。
    """
    from app.models.investor_holding import InvestorHolding

    if as_of_date is not None:
        latest_date = get_latest_snapshot_date_le(db, portfolio_code, as_of_date)
    else:
        latest_date = get_latest_snapshot_date(db, portfolio_code)

    holding_query = db.query(InvestorHolding).filter(
        InvestorHolding.portfolio_code == portfolio_code,
        InvestorHolding.investor_code == investor_code,
    )
    if as_of_date is not None:
        holding_query = holding_query.filter(InvestorHolding.snapshot_date <= as_of_date)
    latest_holding = holding_query.order_by(InvestorHolding.snapshot_date.desc()).first()
    shares = Decimal(latest_holding.shares) if latest_holding else Decimal("0")

    pending_redeems = (
        db.query(Subscription)
        .filter(
            Subscription.portfolio_code == portfolio_code,
            Subscription.investor_code == investor_code,
            Subscription.sub_type == "redeem",
            Subscription.status == "pending",
        )
        .all()
    )
    for s in pending_redeems:
        shares -= Decimal(s.shares) if s.shares else Decimal("0")

    confirmed_redeems = (
        db.query(Subscription)
        .filter(
            Subscription.portfolio_code == portfolio_code,
            Subscription.investor_code == investor_code,
            Subscription.sub_type == "redeem",
            Subscription.status == "confirmed",
        )
        .all()
    )
    for s in confirmed_redeems:
        if latest_date is None or (
            s.confirm_date and s.confirm_date > latest_date
            and (as_of_date is None or s.confirm_date <= as_of_date)
        ):
            shares -= Decimal(s.shares) if s.shares else Decimal("0")

    return shares


def update_cash_position(
    db: Session,
    *,
    portfolio_code: str,
    platform_code: str,
    amount: Decimal,
    update_date: Optional[date] = None,
    created_by: Optional[str] = None,
) -> dict:
    """现金市值修正：写 manual_market_value（绝对替换），供 REST 与 CLI 共用。

    绝不直接写 portfolio_position（快照表受 ORM 事件保护）。
    写入后需重新生成快照才会反映到持仓。不 commit。

    Returns:
        dict：portfolio_code/platform_code/cash_amount/computed_value/update_date(date)
    """
    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise NotFoundError("PORTFOLIO_NOT_FOUND", f"组合 {portfolio_code} 不存在")

    platform = db.query(Platform).filter(Platform.code == platform_code).first()
    if not platform:
        raise NotFoundError("PLATFORM_NOT_FOUND", f"平台 {platform_code} 不存在")

    target_date = update_date or date.today()
    if not is_trading_day(db, target_date):
        raise BusinessError("NON_TRADING_DAY", "非交易日，请等待交易日再提交")

    # 覆盖金额为手动重估值，统一量化到 2 位（issue #94）
    amount_d = quantize_amount(amount)

    # 计算当前隐式值（用于审计）
    computed = compute_cash_balance(db, portfolio_code, platform_code, target_date)

    # 冲突提示（issue #88）：该日该平台存在已确认 CASH 交易时，
    # 覆盖层会绝对替换当日现金并作为后续快照增量基线，压制交易效果
    warnings = []
    conflict_count = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.platform_code == platform_code,
        Trade.product_code == "CASH",
        Trade.status == "confirmed",
        (Trade.trade_date == target_date) | (Trade.confirm_date == target_date),
    ).count()
    if conflict_count:
        warnings.append(
            f"该日存在 {conflict_count} 笔已确认现金交易，覆盖层将压制其效果"
            "（覆盖值会绝对替换当日现金并作为后续快照增量基线）"
        )

    manual = db.query(ManualMarketValue).filter(
        ManualMarketValue.portfolio_code == portfolio_code,
        ManualMarketValue.platform_code == platform_code,
        ManualMarketValue.product_code == "CASH",
        ManualMarketValue.value_date == target_date,
    ).first()
    if manual:
        audit_action = ACTION_UPDATE
        audit_old = {
            "market_value": manual.market_value,
            "computed_value": manual.computed_value,
        }
        manual.market_value = amount_d
        manual.computed_value = computed
    else:
        audit_action = ACTION_CREATE
        audit_old = None
        manual = ManualMarketValue(
            portfolio_code=portfolio_code,
            platform_code=platform_code,
            product_code="CASH",
            value_date=target_date,
            market_value=amount_d,
            computed_value=computed,
            created_by=created_by,
        )
        db.add(manual)
    db.flush()

    record_audit(
        db,
        action=audit_action,
        resource_type=RESOURCE_MANUAL_MARKET_VALUE,
        resource_id=str(manual.id),
        resource_name=f"{portfolio_code}/{platform_code}/CASH/{target_date.isoformat()}",
        old_value=audit_old,
        new_value={
            "market_value": manual.market_value,
            "computed_value": computed,
            "value_date": target_date,
            **({"warnings": warnings} if warnings else {}),
        },
    )

    return {
        "portfolio_code": portfolio_code,
        "platform_code": platform_code,
        "cash_amount": float(manual.market_value),
        "computed_value": float(computed) if computed is not None else None,
        "update_date": target_date,
        "warnings": warnings,
    }


def list_manual_cash_overrides(
    db: Session,
    portfolio_code: str,
    platform_code: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list:
    """查询现金手动覆盖记录（manual_market_value 的 CASH 行），供 REST 与 CLI 共用。"""
    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise NotFoundError("PORTFOLIO_NOT_FOUND", f"组合 {portfolio_code} 不存在")

    query = db.query(ManualMarketValue).filter(
        ManualMarketValue.portfolio_code == portfolio_code,
        ManualMarketValue.product_code == "CASH",
    )
    if platform_code:
        query = query.filter(ManualMarketValue.platform_code == platform_code)
    if start_date:
        query = query.filter(ManualMarketValue.value_date >= start_date)
    if end_date:
        query = query.filter(ManualMarketValue.value_date <= end_date)

    items = query.order_by(
        ManualMarketValue.value_date.desc(), ManualMarketValue.platform_code.asc()
    ).all()
    return [
        {
            "id": m.id,
            "portfolio_code": m.portfolio_code,
            "platform_code": m.platform_code,
            "value_date": m.value_date,
            "market_value": float(m.market_value),
            "computed_value": float(m.computed_value) if m.computed_value is not None else None,
            "created_by": m.created_by,
            "created_at": m.created_at,
        }
        for m in items
    ]


def delete_manual_cash_override(
    db: Session,
    *,
    portfolio_code: str,
    platform_code: str,
    value_date: date,
) -> dict:
    """删除现金手动覆盖记录（issue #88）。不 commit。

    删除后该日该平台回退到自然计算值；若覆盖已 baked in 快照
    （value_date <= 最新快照日），需重算快照才生效（requires_snapshot_regen）。
    """
    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise NotFoundError("PORTFOLIO_NOT_FOUND", f"组合 {portfolio_code} 不存在")

    manual = db.query(ManualMarketValue).filter(
        ManualMarketValue.portfolio_code == portfolio_code,
        ManualMarketValue.platform_code == platform_code,
        ManualMarketValue.product_code == "CASH",
        ManualMarketValue.value_date == value_date,
    ).first()
    if not manual:
        raise NotFoundError(
            "MANUAL_OVERRIDE_NOT_FOUND",
            f"未找到 {portfolio_code}/{platform_code} 在 {value_date} 的现金覆盖记录",
        )

    deleted_value = float(manual.market_value)

    record_audit(
        db,
        action=ACTION_DELETE,
        resource_type=RESOURCE_MANUAL_MARKET_VALUE,
        resource_id=str(manual.id),
        resource_name=f"{portfolio_code}/{platform_code}/CASH/{value_date.isoformat()}",
        old_value={
            "market_value": manual.market_value,
            "computed_value": manual.computed_value,
            "value_date": manual.value_date,
            "created_by": manual.created_by,
        },
    )

    db.delete(manual)
    db.flush()

    latest_snapshot_date = get_latest_snapshot_date(db, portfolio_code)
    requires_regen = bool(latest_snapshot_date and value_date <= latest_snapshot_date)

    return {
        "portfolio_code": portfolio_code,
        "platform_code": platform_code,
        "value_date": value_date,
        "deleted_value": deleted_value,
        "requires_snapshot_regen": requires_regen,
    }
