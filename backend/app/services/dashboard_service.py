"""仪表盘汇总读侧（#944）。

仅统计 active 组合；市值/收益复用组合列表与台账批量聚合，绩效外部现金流与
各组合期末市值并集后只求解一次聚合 XIRR。所有多组合读取均为固定批量 IN 查询，
不在组合循环内读库，也不 commit。
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.models.portfolio import Portfolio
from app.models.subscription import Subscription
from app.models.trade import Trade
from app.services import performance_service, portfolio_service
from app.services.ledger_profit_service import compute_ledger_totals_batch, ledger_xirr_flows
from app.utils.business_time import business_today
from app.utils.quantize import quantize_nav

Flow = Tuple[date, float]


def _aggregate_xirr(
    external_flows: Sequence[Flow],
    terminal_flows: Sequence[Flow],
    *,
    incomplete: bool = False,
) -> dict:
    """对外部现金流与各组合期末现金流求并集 XIRR（纯函数）。"""
    all_flows = [*external_flows, *terminal_flows]
    flow_dates = [when for when, _ in all_flows]
    holding_days = (max(flow_dates) - min(flow_dates)).days if flow_dates else None
    if not external_flows or incomplete:
        return {
            "value": None,
            "reliable": False,
            "reason": "insufficient_flows",
            "cash_flow_count": len(external_flows),
            "holding_days": holding_days,
        }

    rate = performance_service._xirr(all_flows)
    if rate is None:
        return {
            "value": None,
            "reliable": False,
            "reason": "no_solution",
            "cash_flow_count": len(external_flows),
            "holding_days": holding_days,
        }

    reliable = holding_days >= performance_service.ANNUALIZATION_MIN_DAYS
    return {
        "value": round(rate * 100, 4),
        "reliable": reliable,
        "reason": None if reliable else "short_history",
        "cash_flow_count": len(external_flows),
        "holding_days": holding_days,
    }


def _confirmed_subscriptions_by_code(db: Session, nav_codes: List[str]) -> dict:
    """一次 IN 查询加载全部净值型组合的已确认申赎，供 MWR 与聚合共用。"""
    if not nav_codes:
        return {}
    grouped: dict = {}
    rows = (
        db.query(Subscription)
        .filter(
            Subscription.portfolio_code.in_(nav_codes),
            Subscription.status == "confirmed",
        )
        .order_by(Subscription.portfolio_code.asc(), Subscription.id.asc())
        .all()
    )
    for row in rows:
        grouped.setdefault(row.portfolio_code, []).append(row)
    return grouped


def _ledger_entry_date_ranges(db: Session, ledger_codes: List[str]) -> dict:
    """一次 IN 查询求每个台账组合首末记录日，供持有期与可靠性判断。"""
    if not ledger_codes:
        return {}
    rows = (
        db.query(
            LedgerEntry.portfolio_code,
            func.min(LedgerEntry.value_date),
            func.max(LedgerEntry.value_date),
        )
        .filter(LedgerEntry.portfolio_code.in_(ledger_codes))
        .group_by(LedgerEntry.portfolio_code)
        .all()
    )
    return {code: [first, last] for code, first, last in rows}


def _ledger_flows_by_code(db: Session, ledger_codes: List[str]) -> dict:
    """一次 IN 查询加载全部台账外部现金流，供逐行 MWR 与聚合共用。"""
    if not ledger_codes:
        return {}
    grouped: dict = {}
    rows = (
        db.query(LedgerFlow)
        .filter(LedgerFlow.portfolio_code.in_(ledger_codes))
        .order_by(LedgerFlow.portfolio_code.asc(), LedgerFlow.id.asc())
        .all()
    )
    for row in rows:
        grouped.setdefault(row.portfolio_code, []).append(row)
    return grouped


def _holding_days(first: Optional[date], last: Optional[date]) -> Optional[int]:
    return (last - first).days if first is not None and last is not None else None


def _annualization_reliable(holding_days: Optional[int]) -> bool:
    return holding_days is not None and holding_days >= performance_service.ANNUALIZATION_MIN_DAYS


def get_dashboard_summary(db: Session) -> dict:
    """汇总 active 组合资产、收益、待处理数量与资金加权收益率。"""
    portfolios = (
        db.query(Portfolio)
        .filter(Portfolio.status == "active")
        .order_by(Portfolio.code.asc())
        .all()
    )
    nav_codes = [p.code for p in portfolios if p.kind != "ledger"]
    ledger_codes = [p.code for p in portfolios if p.kind == "ledger"]

    # nav 市值/累计收益与 list_portfolios 同源；first 为 #944 追加的只读元数据。
    nav_agg = portfolio_service._nav_list_aggregates(db, nav_codes)
    subscriptions_by_code = _confirmed_subscriptions_by_code(db, nav_codes)

    # 台账市值/收益必须复用批量入口；期末值即该入口的 step-forward 总市值。
    D = business_today()
    ledger_totals = compute_ledger_totals_batch(db, ledger_codes, as_of_date=D)
    ledger_date_ranges = _ledger_entry_date_ranges(db, ledger_codes)
    ledger_flows_by_code = _ledger_flows_by_code(db, ledger_codes)

    pending_subscription_count = (
        db.query(Subscription.id).filter(Subscription.status == "pending").count()
    )
    pending_trade_count = db.query(Trade.id).filter(Trade.status == "pending").count()

    external_flows: List[Flow] = []
    terminal_flows: List[Flow] = []
    aggregate_complete = True
    items = []
    for portfolio in portfolios:
        if portfolio.kind == "ledger":
            totals = ledger_totals.get(
                portfolio.code, {"total_value": None, "total_profit": None}
            )
            total_value = totals["total_value"]
            total_profit = totals["total_profit"]
            first, last = ledger_date_ranges.get(portfolio.code, (None, None))
            holding_days = _holding_days(first, last)

            rows = ledger_flows_by_code.get(portfolio.code, [])
            row_external_flows = ledger_xirr_flows(rows, D, 0.0)
            end_value = total_value if total_value is not None else 0.0
            row_all_flows = ledger_xirr_flows(rows, D, end_value)
            mwr = None
            # 与 compute_ledger_performance 相同：无外部流或期末市值非正时不求解。
            if row_external_flows and end_value > 0:
                rate = performance_service._xirr(row_all_flows)
                if rate is not None:
                    mwr = float(quantize_nav(Decimal(str(rate)) * 100))
            external_flows.extend(row_external_flows)
            if end_value > 0:
                terminal_flows.extend(row_all_flows[len(row_external_flows):])
            elif row_external_flows:
                aggregate_complete = False
            cumulative_return = None
        else:
            aggregate = nav_agg.get(portfolio.code, {})
            latest = aggregate.get("latest")
            first = aggregate.get("first")
            total_value = (
                float(latest.total_value)
                if latest is not None and latest.total_value is not None
                else None
            )
            totals = portfolio_service._nav_totals(
                total_value, aggregate.get("net_invested", 0.0)
            )
            total_value = totals["total_value"]
            total_profit = totals["total_profit"]
            cumulative_return = aggregate.get("cumulative_return")
            holding_days = _holding_days(
                first.snapshot_date if first is not None else None,
                latest.snapshot_date if latest is not None else None,
            )

            subscriptions = subscriptions_by_code.get(portfolio.code, [])
            row_external_flows = performance_service.subscription_flows(subscriptions)
            mwr = None
            if latest is not None and latest.total_value is not None:
                # 纯函数复用保证逐行 MWR 与 compute_mwr/get_performance 逐字一致。
                mwr = performance_service.compute_mwr(
                    subscriptions, latest.snapshot_date, float(latest.total_value)
                )["mwr"]
            external_flows.extend(row_external_flows)
            if (
                latest is not None
                and latest.total_value is not None
                and float(latest.total_value) > 0
            ):
                terminal_flows.append((latest.snapshot_date, float(latest.total_value)))
            elif row_external_flows:
                aggregate_complete = False

        items.append({
            "code": portfolio.code,
            "name": portfolio.name,
            "kind": portfolio.kind,
            "total_value": total_value,
            "total_profit": total_profit,
            "cumulative_return": cumulative_return,
            "mwr": mwr,
            "annualization_reliable": _annualization_reliable(holding_days),
            "holding_days": holding_days,
        })

    return {
        # 与前端 R8 一致：active 行的 None 按 0 合并，空集合同样返回 0。
        "total_value": float(sum((item["total_value"] or 0.0) for item in items)),
        "total_profit": float(sum((item["total_profit"] or 0.0) for item in items)),
        "aggregate_xirr": _aggregate_xirr(
            external_flows, terminal_flows, incomplete=not aggregate_complete
        ),
        "pending_subscription_count": pending_subscription_count,
        "pending_trade_count": pending_trade_count,
        "portfolios": items,
    }
