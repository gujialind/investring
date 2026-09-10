from sqlalchemy import Column, String, Date, Boolean, DateTime, Integer, func
from app.models.base import Base


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    calendar_date = Column(Date, nullable=False, unique=True, index=True)
    is_open = Column(Boolean, default=True, nullable=False)
    exchange = Column(String(20), default="SSE")
    created_at = Column(DateTime, server_default=func.now())
