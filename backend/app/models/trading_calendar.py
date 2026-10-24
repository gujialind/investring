from sqlalchemy import Column, String, Date, Boolean, DateTime, Integer, UniqueConstraint, func
from app.models.base import Base


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"
    # issue #777：多市场日历（SSE / HKEX）同日期各一行，唯一键为 (calendar_date, exchange)。
    # exchange 语义单点在 services/trading_utils.py 的 exchange_for_market。
    __table_args__ = (
        UniqueConstraint("calendar_date", "exchange", name="uix_trading_calendar_date_exchange"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    calendar_date = Column(Date, nullable=False, index=True)
    is_open = Column(Boolean, default=True, nullable=False)
    exchange = Column(String(20), nullable=False, server_default="SSE", default="SSE")
    created_at = Column(DateTime, server_default=func.now())
