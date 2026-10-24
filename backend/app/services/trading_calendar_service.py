from datetime import date, datetime
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.trading_calendar import TradingCalendar
from app.services.trading_utils import SSE_EXCHANGE, HKEX_EXCHANGE
from app.services.tushare_client import (
    get_trade_calendar,
    get_hk_trade_calendar,
    TushareNotConfiguredError,
    TushareAPIError,
)

# 双日历同步的合法市场（#777）：SSE 走通用 trade_cal，HKEX 走专用 hk_tradecal
SUPPORTED_EXCHANGES = (SSE_EXCHANGE, HKEX_EXCHANGE)


def sync_trading_calendar(db: Session, year: int, exchange: str = SSE_EXCHANGE) -> Dict[str, Any]:
    """
    同步指定年份、指定市场的交易日历到数据库（#777 起支持 HKEX）

    Args:
        db: 数据库会话
        year: 年份，如 2026
        exchange: SSE(上交所，默认) 或 HKEX(港交所)

    Returns:
        同步结果，包含 synced_count、year 和 exchange
        例如: {"synced_count": 244, "year": 2026, "exchange": "SSE"}

    Raises:
        ValueError: exchange 不在 SUPPORTED_EXCHANGES 内
        TushareNotConfiguredError: Tushare token 未配置
        TushareAPIError: API 调用失败

    Note:
        本函数不 commit（backend/AGENTS.md「分层目录与职责」节），事务边界交调用方。
    """
    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(f"不支持的日历市场: {exchange}（可选 {SUPPORTED_EXCHANGES}）")

    # 从 Tushare 获取交易日历数据（HKEX 走专用接口 hk_tradecal，#777）
    if exchange == HKEX_EXCHANGE:
        calendar_data = get_hk_trade_calendar(year)
    else:
        calendar_data = get_trade_calendar(year, exchange)

    if not calendar_data:
        return {"synced_count": 0, "year": year, "exchange": exchange}

    # 查询数据库中该市场已存在的日期（双日历后必须按 exchange 过滤，否则
    # 另一市场的行会被误判为已存在而整年跳过，#777）
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    existing_dates = {
        row[0]
        for row in db.query(TradingCalendar.calendar_date)
        .filter(and_(
            TradingCalendar.calendar_date >= start_date,
            TradingCalendar.calendar_date <= end_date,
            TradingCalendar.exchange == exchange,
        ))
        .all()
    }

    # 过滤出需要新增的日期
    new_records = []
    for item in calendar_data:
        item_date = date.fromisoformat(item["date"])
        if item_date not in existing_dates:
            new_records.append({
                "calendar_date": item_date,
                "is_open": item["is_open"],
                "exchange": exchange,
            })

    # 批量插入新记录（commit 交调用方）
    if new_records:
        db.bulk_insert_mappings(TradingCalendar, new_records)
        db.flush()

    return {
        "synced_count": len(new_records),
        "year": year,
        "exchange": exchange,
    }


def get_calendar_query(
    db: Session,
    year: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_open: Optional[bool] = None,
    exchange: Optional[str] = None,
):
    """
    构建交易日历查询

    Args:
        db: 数据库会话
        year: 按年份过滤
        start_date: 起始日期
        end_date: 结束日期
        is_open: 是否开盘
        exchange: 按市场过滤（None = 不过滤，返回全部市场；#777）

    Returns:
        SQLAlchemy Query 对象
    """
    query = db.query(TradingCalendar)

    if year is not None:
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        query = query.filter(
            and_(TradingCalendar.calendar_date >= year_start, TradingCalendar.calendar_date <= year_end)
        )

    if start_date is not None:
        query = query.filter(TradingCalendar.calendar_date >= start_date)

    if end_date is not None:
        query = query.filter(TradingCalendar.calendar_date <= end_date)

    if is_open is not None:
        query = query.filter(TradingCalendar.is_open == is_open)

    if exchange is not None:
        query = query.filter(TradingCalendar.exchange == exchange)

    return query.order_by(TradingCalendar.calendar_date)


def is_trading_day(db: Session, target_date: date, exchange: str = SSE_EXCHANGE) -> bool:
    """
    判断指定日期是否为 exchange 日历的交易日

    Args:
        db: 数据库会话
        target_date: 目标日期
        exchange: 日历市场（默认 SSE；#777）

    Returns:
        True 如果是交易日，False  otherwise
    """
    cal = db.query(TradingCalendar).filter(
        TradingCalendar.calendar_date == target_date,
        TradingCalendar.exchange == exchange,
    ).first()
    if not cal:
        return False
    return cal.is_open
