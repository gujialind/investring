"""HKEX 日历镜像 helper（#777）。

溯源：HK_MUTUAL 的快照取价与调仓确认改在 HKEX 日历上解析 T-N，存量 HK 用例
需要库里有对应行才能复现原语义。
现状：种子（tests/seed_base.py）刻意只写 SSE——全仓单日历契约不变；HK 用例经
本 helper 按需镜像一段 SSE 开闭形态生成 HKEX 行，**用后必须删除**：共享测试库
里遗留的 HKEX 行会破坏其他用例对日历的无 exchange 过滤查询
（如 filter_by(calendar_date=d).one() 撞多行）。
"""
from datetime import date

from sqlalchemy.orm import Session

from app.models.trading_calendar import TradingCalendar


def mirror_hkex_calendar(db: Session, start: date, end: date) -> int:
    """按 [start, end] 内 SSE 行的开闭形态生成 HKEX 行，返回新建行数（幂等）。"""
    sse_rows = db.query(TradingCalendar).filter(
        TradingCalendar.exchange == "SSE",
        TradingCalendar.calendar_date >= start,
        TradingCalendar.calendar_date <= end,
    ).all()
    existing = {
        row.calendar_date
        for row in db.query(TradingCalendar).filter(
            TradingCalendar.exchange == "HKEX",
            TradingCalendar.calendar_date >= start,
            TradingCalendar.calendar_date <= end,
        ).all()
    }
    created = 0
    for row in sse_rows:
        if row.calendar_date in existing:
            continue
        db.add(TradingCalendar(
            calendar_date=row.calendar_date, is_open=row.is_open, exchange="HKEX",
        ))
        created += 1
    db.commit()
    return created


def remove_hkex_calendar(db: Session, start: date, end: date) -> None:
    """删除 [start, end] 内的 HKEX 行（teardown 用；SSE 行不碰）。"""
    db.query(TradingCalendar).filter(
        TradingCalendar.exchange == "HKEX",
        TradingCalendar.calendar_date >= start,
        TradingCalendar.calendar_date <= end,
    ).delete(synchronize_session=False)
    db.commit()
