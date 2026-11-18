"""台账型组合收益与绩效读侧（#905 PR2，D2 + P1=口径A）。

收益 = 当前市值 − 净入金（step-forward 取 ≤D 每平台最近一条市值）。
绩效口径A：只给 mwr（XIRR）与区间收益 return_1m/3m/6m/ytd/1y/3y（Modified Dietz）；
TWR/波动率/回撤/nav 类恒 None（台账无净值序列，不硬算）。复用 performance_service._xirr。
与净值型现金/在途口径隔离（D1）：本服务只读 ledger_entry/ledger_flow。
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.models.portfolio import Portfolio
from app.services.exceptions import BusinessError, NotFoundError
from app.utils.business_time import business_today
from app.utils.quantize import quantize_amount, quantize_nav

ANNUALIZATION_MIN_DAYS = 90


def _get_portfolio(db: Session, code: str) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.code == code).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", f"组合 {code} 不存在")
    return portfolio


def _step_forward_value(db: Session, code: str, as_of: date) -> Decimal:
    """≤as_of 每平台最近一条市值之和（step-forward，D3）；无记录计 0。"""
    entries = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.portfolio_code == code, LedgerEntry.value_date <= as_of)
        .order_by(LedgerEntry.value_date.asc())
        .all()
    )
    latest_per_platform = {}
    for e in entries:
        latest_per_platform[e.platform_code] = Decimal(str(e.market_value))
    return sum(latest_per_platform.values(), Decimal("0"))


def _net_inflow(db: Session, code: str, as_of: date) -> Decimal:
    flows = (
        db.query(LedgerFlow)
        .filter(LedgerFlow.portfolio_code == code, LedgerFlow.flow_date <= as_of)
        .all()
    )
    net = Decimal("0")
    for f in flows:
        amt = Decimal(str(f.amount))
        net += amt if f.direction == "inflow" else -amt
    return net


def compute_ledger_totals(db: Session, code: str, as_of_date: Optional[date] = None) -> dict:
    """收益 = 当前市值 − 净入金。draft → None；active 无记录 → 0。

    S2（#911 L2）：kind 闸门集中在此——净值型组合调台账收益查询拒
    PORTFOLIO_KIND_NOT_ALLOWED（与兄弟台账端点同口径），不返 200 全零。
    调用方 _derive_portfolio_totals / _flow_warnings 均已 guard ledger，不受影响。
    """
    portfolio = _get_portfolio(db, code)
    if portfolio.kind != "ledger":
        raise BusinessError(
            "PORTFOLIO_KIND_NOT_ALLOWED",
            f"组合 {code} 非平台市值台账型，不支持台账收益查询",
            details={"portfolio_code": code, "kind": portfolio.kind},
        )
    if portfolio.status == "draft":
        return {
            "portfolio_code": code, "as_of_date": as_of_date,
            "total_value": None, "net_inflow": None, "total_profit": None, "warnings": [],
        }
    D = as_of_date or business_today()
    total_value = quantize_nav(_step_forward_value(db, code, D))
    net = quantize_amount(_net_inflow(db, code, D))
    profit = quantize_nav(total_value - net)
    warnings = []
    if net < 0:
        warnings.append("累计撤资已超过累计注资，收益基数异常，请核对进出记录")
    return {
        "portfolio_code": code, "as_of_date": D,
        "total_value": float(total_value), "net_inflow": float(net),
        "total_profit": float(profit), "warnings": warnings,
    }


def _modified_dietz(db: Session, code: str, start: date, end: date) -> Optional[Decimal]:
    """区间收益率（百分数，Modified Dietz 口径②）。BMV=0/不足窗口/分母≤0 → None。"""
    earliest = (
        db.query(LedgerEntry.value_date)
        .filter(LedgerEntry.portfolio_code == code)
        .order_by(LedgerEntry.value_date.asc())
        .first()
    )
    # 历史不足窗口期（首条记录日晚于 start）→ None，不退化为「成立以来」虚高
    if earliest is None or earliest[0] > start:
        return None
    bmv = _step_forward_value(db, code, start)
    if bmv <= 0:
        return None
    emv = _step_forward_value(db, code, end)
    T = (end - start).days
    if T <= 0:
        return None
    flows = (
        db.query(LedgerFlow)
        .filter(LedgerFlow.portfolio_code == code,
                LedgerFlow.flow_date > start, LedgerFlow.flow_date <= end)
        .all()
    )
    cf_total = Decimal("0")
    weighted = Decimal("0")
    for f in flows:
        cf = Decimal(str(f.amount))
        if f.direction == "outflow":
            cf = -cf
        cf_total += cf
        t_i = (f.flow_date - start).days
        weighted += (Decimal(T - t_i) / Decimal(T)) * cf
    denom = bmv + weighted
    if denom <= 0:
        return None
    R = (emv - bmv - cf_total) / denom
    return quantize_nav(R * 100)


def compute_ledger_performance(db: Session, code: str) -> dict:
    """台账型绩效（口径A）：mwr(XIRR) + 区间收益(Modified Dietz)；其余 nav/逐日序列类 None。"""
    from app.services.performance_service import _xirr

    portfolio = _get_portfolio(db, code)
    D = business_today()
    result = {
        "portfolio_code": code,
        "twr": None, "twr_chained": None, "annualized_twr": None,
        "mwr": None, "initial_nav": None, "current_nav": None,
        "holding_days": None,
        "return_1m": None, "return_3m": None, "return_6m": None,
        "return_ytd": None, "return_1y": None, "return_3y": None,
        "max_drawdown": None, "max_drawdown_peak_date": None, "max_drawdown_trough_date": None,
        "annualized_volatility": None, "cash_flow_count": 0,
        "nav_series_consistent": None, "annualization_reliable": False,
    }
    if portfolio.status == "draft":
        return result

    flows = (
        db.query(LedgerFlow)
        .filter(LedgerFlow.portfolio_code == code)
        .order_by(LedgerFlow.flow_date.asc())
        .all()
    )
    result["cash_flow_count"] = len(flows)

    entries = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.portfolio_code == code)
        .order_by(LedgerEntry.value_date.asc())
        .all()
    )
    if entries:
        holding_days = (entries[-1].value_date - entries[0].value_date).days
        result["holding_days"] = holding_days
        result["annualization_reliable"] = holding_days >= ANNUALIZATION_MIN_DAYS

    # mwr（XIRR）：inflow 负流（投入）/ outflow 正流（取回）+ 期末市值正流
    end_value = _step_forward_value(db, code, D)
    xirr_flows = []
    for f in flows:
        amt = float(f.amount)
        xirr_flows.append((f.flow_date, -amt if f.direction == "inflow" else amt))
    if xirr_flows and end_value > 0:
        xirr_flows.append((D, float(end_value)))
        rate = _xirr(xirr_flows)
        if rate is not None:
            result["mwr"] = float(quantize_nav(Decimal(str(rate)) * 100))

    # 区间收益（Modified Dietz），锚点复用净值型 relativedelta 语义
    result["return_1m"] = _float_or_none(_modified_dietz(db, code, D - relativedelta(months=1), D))
    result["return_3m"] = _float_or_none(_modified_dietz(db, code, D - relativedelta(months=3), D))
    result["return_6m"] = _float_or_none(_modified_dietz(db, code, D - relativedelta(months=6), D))
    result["return_ytd"] = _float_or_none(_modified_dietz(db, code, date(D.year, 1, 1), D))
    result["return_1y"] = _float_or_none(_modified_dietz(db, code, D - relativedelta(years=1), D))
    result["return_3y"] = _float_or_none(_modified_dietz(db, code, D - relativedelta(years=3), D))
    return result


def _float_or_none(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None