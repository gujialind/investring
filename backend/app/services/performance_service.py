"""
组合绩效指标服务

提供两类互补的收益率口径与风险指标：

- TWR（时间加权收益率）：消除资金进出影响，衡量组合本身的投资水平。
  本系统为净值化记账（申赎按当日净值折算份额，总市值与总份额同比例变动，
  净值不受资金流影响），故 `期末净值 / 期初净值 - 1` 天然等于教科书的
  分段几何连乘 TWR。`compute_twr` 同时给出两种算法结果，差异可作为
  净值序列完整性的自检信号（正常应在 1e-9 量级内）。
- MWR（资金加权收益率 / XIRR）：把每笔申赎按发生时点计入现金流，
  求解使净现值为 0 的年化贴现率，衡量"实际投入的钱赚了多少"。
  TWR 高而 MWR 低通常意味着加仓时点不佳（大部分资金买在高位）。

风险指标：最大回撤、年化波动率、区间收益率。

纯计算函数（_xirr / _max_drawdown 等）不依赖 ORM，便于单测。
"""
from datetime import date, timedelta
from typing import List, Optional, Sequence, Tuple

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.subscription import Subscription

# 年化换算基准：日历日 365（与既有 get_returns 口径一致，不用交易日 252）
DAYS_PER_YEAR = 365

# 年化可靠性门槛：持有期不足此天数时，年化（尤其 XIRR）属于大幅外推，
# 短期大额申购可能换算出数百上千的失真值，需在 UI 上标注仅供参考
ANNUALIZATION_MIN_DAYS = 90


def _annualize(total_growth: float, days: int) -> Optional[float]:
    """把区间总增长倍数换算为年化收益率（百分数）。

    total_growth 为期末/期初倍数（如 1.4376），days <= 0 时返回 None。
    """
    if days <= 0 or total_growth <= 0:
        return None
    return ((total_growth ** (DAYS_PER_YEAR / days)) - 1) * 100


def compute_twr(navs: Sequence[float]) -> dict:
    """计算 TWR（时间加权收益率，百分数）。

    返回 simple（净值比法）与 chained（逐期几何连乘）两个结果及其差异，
    净值化体系下两者应相等，差异过大说明净值序列存在异常（如 0 值或断层）。
    """
    valid = [n for n in navs if n is not None and n > 0]
    if len(valid) < 2:
        return {"twr": None, "twr_chained": None, "twr_diff": None}

    simple = (valid[-1] / valid[0] - 1) * 100

    chained_growth = 1.0
    for prev, cur in zip(valid, valid[1:]):
        chained_growth *= cur / prev
    chained = (chained_growth - 1) * 100

    return {
        "twr": round(simple, 4),
        "twr_chained": round(chained, 4),
        "twr_diff": abs(simple - chained),
    }


def _npv(rate: float, flows: Sequence[Tuple[date, float]], base: date) -> float:
    """按年化贴现率计算现金流净现值（XNPV）。"""
    total = 0.0
    for when, amount in flows:
        years = (when - base).days / DAYS_PER_YEAR
        total += amount / ((1 + rate) ** years)
    return total


def _xirr(flows: Sequence[Tuple[date, float]]) -> Optional[float]:
    """求解不规则现金流的年化内部收益率（XIRR，返回小数而非百分数）。

    约定：流出（投入组合）为负，流入（赎回/期末市值）为正。
    先用二分法在 [-0.9999, 10] 区间定位符号变化，再迭代收敛；
    无解（现金流同号）或不收敛时返回 None。
    """
    if len(flows) < 2:
        return None

    ordered = sorted(flows, key=lambda f: f[0])
    base = ordered[0][0]
    amounts = [a for _, a in ordered]
    # 现金流全同号时 NPV 无零点，IRR 无定义
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return None

    low, high = -0.9999, 10.0
    npv_low = _npv(low, ordered, base)
    npv_high = _npv(high, ordered, base)
    # 端点同号：真实收益率超出搜索区间（如亏损殆尽或数十倍收益），不强行外推
    if npv_low * npv_high > 0:
        return None

    for _ in range(200):
        mid = (low + high) / 2
        npv_mid = _npv(mid, ordered, base)
        if abs(npv_mid) < 1e-9:
            return mid
        if npv_low * npv_mid < 0:
            high = mid
            npv_high = npv_mid
        else:
            low = mid
            npv_low = npv_mid
    return (low + high) / 2


def _max_drawdown(navs: Sequence[float], dates: Sequence[date]) -> dict:
    """计算最大回撤（百分数，正值表示回撤幅度）及其峰谷日期。"""
    valid = [(d, n) for d, n in zip(dates, navs) if n is not None and n > 0]
    if len(valid) < 2:
        return {"max_drawdown": None, "max_drawdown_peak_date": None, "max_drawdown_trough_date": None}

    peak_date, peak_nav = valid[0]
    worst = 0.0
    worst_peak: Optional[date] = None
    worst_trough: Optional[date] = None

    for cur_date, cur_nav in valid[1:]:
        if cur_nav > peak_nav:
            peak_date, peak_nav = cur_date, cur_nav
            continue
        drawdown = (peak_nav - cur_nav) / peak_nav * 100
        if drawdown > worst:
            worst, worst_peak, worst_trough = drawdown, peak_date, cur_date

    return {
        "max_drawdown": round(worst, 4),
        "max_drawdown_peak_date": worst_peak.isoformat() if worst_peak else None,
        "max_drawdown_trough_date": worst_trough.isoformat() if worst_trough else None,
    }


def _annualized_volatility(navs: Sequence[float]) -> Optional[float]:
    """年化波动率（百分数）：日收益率标准差 × sqrt(252)。

    波动率按交易日频率年化（每年约 252 个交易日），
    与收益率的 365 日历日年化口径不同，这是行业惯例。
    """
    valid = [n for n in navs if n is not None and n > 0]
    if len(valid) < 3:
        return None

    daily = [(cur / prev - 1) for prev, cur in zip(valid, valid[1:])]
    n = len(daily)
    mean = sum(daily) / n
    # 样本方差（n-1）：快照序列是总体的样本
    variance = sum((r - mean) ** 2 for r in daily) / (n - 1)
    return round((variance ** 0.5) * (252 ** 0.5) * 100, 4)


def _period_return(
    snapshots: Sequence[PortfolioValueSnapshot],
    start: date,
) -> Optional[float]:
    """区间收益率（百分数）：以 start 当日或之后首个快照为基准，至最新快照。

    历史未覆盖 start（首个快照日晚于 start，即组合成立不足窗口期）或基准与
    最新同日时返回 None。
    """
    if len(snapshots) < 2:
        return None
    # 历史不足窗口期：退化为「成立以来」会虚高/失真，统一返回 None
    if snapshots[0].snapshot_date > start:
        return None
    base = next((s for s in snapshots if s.snapshot_date >= start), None)
    latest = snapshots[-1]
    if base is None or base.snapshot_date >= latest.snapshot_date:
        return None
    base_nav = float(base.unit_price)
    if base_nav <= 0:
        return None
    return round((float(latest.unit_price) / base_nav - 1) * 100, 4)


def compute_mwr(
    subscriptions: Sequence[Subscription],
    end_date: date,
    end_value: float,
) -> dict:
    """计算 MWR（资金加权收益率 / XIRR，百分数）。

    现金流口径：
    - 申购按 confirm_date 记为负流（投入），赎回记为正流（取回）；
    - 期末市值记为一笔正流（视为全部赎回）；
    - 仅取 confirmed 且 amount 有效的记录。
    """
    flows: List[Tuple[date, float]] = []
    for sub in subscriptions:
        if sub.status != "confirmed" or sub.amount is None:
            continue
        when = sub.confirm_date or sub.apply_date
        if when is None:
            continue
        amount = float(sub.amount)
        if amount <= 0:
            continue
        if sub.sub_type == "subscribe":
            flows.append((when, -amount))
        elif sub.sub_type == "redeem":
            flows.append((when, amount))

    if not flows or end_value <= 0:
        return {"mwr": None, "cash_flow_count": len(flows)}

    flows.append((end_date, end_value))
    rate = _xirr(flows)
    return {
        "mwr": round(rate * 100, 4) if rate is not None else None,
        "cash_flow_count": len(flows) - 1,  # 不计期末市值这笔虚拟流
    }


def get_performance(db: Session, code: str) -> dict:
    """组合绩效全指标（快照不足时相关字段为 None，不报错）。"""
    # 延迟导入：避免与 portfolio_service 形成模块级循环依赖
    from app.services.portfolio_service import _get_portfolio_or_404

    portfolio = _get_portfolio_or_404(db, code)

    snapshots = (
        db.query(PortfolioValueSnapshot)
        .filter(PortfolioValueSnapshot.portfolio_code == code)
        .order_by(PortfolioValueSnapshot.snapshot_date.asc())
        .all()
    )

    empty = {
        "portfolio_code": code,
        "twr": None,
        "twr_chained": None,
        "annualized_twr": None,
        "mwr": None,
        "initial_nav": None,
        "current_nav": None,
        "holding_days": None,
        "return_1m": None,
        "return_3m": None,
        "return_6m": None,
        "return_ytd": None,
        "return_1y": None,
        "return_3y": None,
        "max_drawdown": None,
        "max_drawdown_peak_date": None,
        "max_drawdown_trough_date": None,
        "annualized_volatility": None,
        "cash_flow_count": 0,
        "nav_series_consistent": None,
        "annualization_reliable": False,
    }
    # issue #905 PR2：台账型绩效按类型分流（口径A：mwr XIRR + 区间收益 Modified Dietz，
    # nav/逐日序列类恒 None）。净值型走下方原有逻辑，逐字不变。
    if portfolio.kind == "ledger":
        from app.services.ledger_profit_service import compute_ledger_performance
        return compute_ledger_performance(db, code)
    if not snapshots:
        return empty

    navs = [float(s.unit_price) for s in snapshots]
    dates = [s.snapshot_date for s in snapshots]
    first, last = snapshots[0], snapshots[-1]
    holding_days = (last.snapshot_date - first.snapshot_date).days

    twr_result = compute_twr(navs)
    annualized_twr = (
        _annualize(navs[-1] / navs[0], holding_days) if navs[0] > 0 else None
    )

    subs = db.query(Subscription).filter(Subscription.portfolio_code == code).all()
    mwr_result = compute_mwr(subs, last.snapshot_date, float(last.total_value))

    drawdown = _max_drawdown(navs, dates)

    latest_date = last.snapshot_date
    return {
        "portfolio_code": code,
        "twr": twr_result["twr"],
        "twr_chained": twr_result["twr_chained"],
        "annualized_twr": round(annualized_twr, 4) if annualized_twr is not None else None,
        "mwr": mwr_result["mwr"],
        "initial_nav": navs[0],
        "current_nav": navs[-1],
        "holding_days": holding_days,
        "return_1m": _period_return(snapshots, latest_date - timedelta(days=30)),
        "return_3m": _period_return(snapshots, latest_date - timedelta(days=90)),
        # 6m/1y/3y 用 relativedelta 保证月份/年份锚点语义正确（如 1y 遇闰年）
        "return_6m": _period_return(snapshots, latest_date - relativedelta(months=6)),
        "return_ytd": _period_return(snapshots, date(latest_date.year, 1, 1)),
        "return_1y": _period_return(snapshots, latest_date - relativedelta(years=1)),
        "return_3y": _period_return(snapshots, latest_date - relativedelta(years=3)),
        **drawdown,
        "annualized_volatility": _annualized_volatility(navs),
        "cash_flow_count": mwr_result["cash_flow_count"],
        # 两种 TWR 算法一致性自检：净值化体系下应恒等，超差说明净值序列异常
        "nav_series_consistent": (
            twr_result["twr_diff"] < 1e-6 if twr_result["twr_diff"] is not None else None
        ),
        # 持有期过短时年化属大幅外推（尤其 MWR 可能轻松超过 100%），UI 需标注仅供参考
        "annualization_reliable": holding_days >= ANNUALIZATION_MIN_DAYS,
    }
