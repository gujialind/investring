"""
交易相关公共工具函数

从 routers 层提取的共享函数，供 CLI 和 router 共用。

交易日解析有两类入口，刻意分开（#591）：
- `get_next_trading_day` / `get_prev_trading_day`：**日历不足即抛 `CALENDAR_NOT_SYNCED`**，
  绝不回退返回入参或伪造自然日。用于所有「结果要被写进财务数据」的消费点
  （confirm_date、取价日、快照日），静默回退会把编造的日期持久化。
- `try_get_next_trading_day` / `try_get_prev_trading_day`：**日历不足返回 `None`**，
  仅供编排把「日历到头」当合法控制流（循环推进、逐组合跳过），调用点显式表达该意图。
"""
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.trading_calendar import TradingCalendar
from app.services.exceptions import BusinessError


def is_trading_day(db: Session, target_date: date) -> bool:
    """判断指定日期是否为交易日"""
    cal = db.query(TradingCalendar).filter(TradingCalendar.calendar_date == target_date).first()
    if not cal:
        return False
    return cal.is_open


_CALENDAR_MSG = "交易日历数据缺失，请先同步交易日历"


def _step_next(db: Session, cursor: date) -> Optional[date]:
    return (
        db.query(func.min(TradingCalendar.calendar_date))
        .filter(
            TradingCalendar.calendar_date > cursor,
            TradingCalendar.is_open == True,
        )
        .scalar()
    )


def _step_prev(db: Session, cursor: date) -> Optional[date]:
    return (
        db.query(func.max(TradingCalendar.calendar_date))
        .filter(
            TradingCalendar.calendar_date < cursor,
            TradingCalendar.is_open == True,
        )
        .scalar()
    )


def _advance(db: Session, from_date: date, steps: int, step) -> Optional[date]:
    """逐日走 steps 步；任一步走不动即返回 None（steps==0 → 零查询、返回 from_date）。"""
    cursor = from_date
    for _ in range(steps):
        cursor = step(db, cursor)
        if cursor is None:
            return None
    return cursor


def _walk_with_count(
    db: Session, from_date: date, steps: int, step
) -> tuple[Optional[date], int]:
    """与 _advance 同，但额外回报**已成功走到的步数**——部分耗尽时错误 details
    要如实写 `resolved_days`（days=3 只剩 1 天 ≠ 完全无日历）。"""
    cursor = from_date
    for resolved in range(steps):
        nxt = step(db, cursor)
        if nxt is None:
            return None, resolved
        cursor = nxt
    return cursor, steps


def _exhausted_details(
    from_date: date, direction: str, requested: int, resolved: int
) -> dict:
    return {
        "from_date": from_date.isoformat(),
        "direction": direction,
        "requested_days": requested,
        "resolved_days": resolved,
    }


def try_get_next_trading_day(
    db: Session, from_date: date, days: int = 1
) -> Optional[date]:
    """from_date 之后第 days 个交易日；日历不足返回 None，绝不编造。仅供编排控制流。"""
    return _advance(db, from_date, max(days, 0), _step_next)


def try_get_prev_trading_day(
    db: Session, from_date: date, days: int = 1
) -> Optional[date]:
    """from_date 之前第 days 个交易日；日历不足返回 None，绝不编造。仅供编排控制流。"""
    return _advance(db, from_date, max(days, 0), _step_prev)


def get_next_trading_day(db: Session, from_date: date, days: int = 1) -> date:
    """
    获取 from_date 之后第 days 个交易日（days=0 表示当天，零查询、返回 from_date）。

    日历覆盖不足（含只走到中间某日就断档）→ 抛 CALENDAR_NOT_SYNCED，
    不回退返回 from_date、不伪造自然日（#591）。需要「到头即停」的编排请改用
    try_get_next_trading_day。
    """
    steps = max(days, 0)
    result, resolved = _walk_with_count(db, from_date, steps, _step_next)
    if result is None:
        raise BusinessError(
            "CALENDAR_NOT_SYNCED",
            f"{_CALENDAR_MSG}：{from_date} 之后第 {steps} 个交易日不在日历内",
            details=_exhausted_details(from_date, "next", steps, resolved),
        )
    return result


def get_prev_trading_day(db: Session, from_date: date, days: int = 1) -> date:
    """获取前 N 个交易日；日历不足抛 CALENDAR_NOT_SYNCED（同 get_next_trading_day）。"""
    steps = max(days, 0)
    result, resolved = _walk_with_count(db, from_date, steps, _step_prev)
    if result is None:
        raise BusinessError(
            "CALENDAR_NOT_SYNCED",
            f"{_CALENDAR_MSG}：{from_date} 之前第 {steps} 个交易日不在日历内",
            details=_exhausted_details(from_date, "prev", steps, resolved),
        )
    return result


def get_latest_snapshot_date(db: Session, portfolio_code: str) -> Optional[date]:
    """获取组合最新快照日期"""
    result = (
        db.query(func.max(PortfolioValueSnapshot.snapshot_date))
        .filter(PortfolioValueSnapshot.portfolio_code == portfolio_code)
        .scalar()
    )
    return result


def get_latest_snapshot_date_le(
    db: Session, portfolio_code: str, as_of_date: date
) -> Optional[date]:
    """获取组合不超过 as_of_date 的最新快照日期（用于 as_of_date 截止计算）。"""
    result = (
        db.query(func.max(PortfolioValueSnapshot.snapshot_date))
        .filter(
            PortfolioValueSnapshot.portfolio_code == portfolio_code,
            PortfolioValueSnapshot.snapshot_date <= as_of_date,
        )
        .scalar()
    )
    return result
