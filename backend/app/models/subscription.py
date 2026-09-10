from sqlalchemy import Column, String, Text, Numeric, Date, DateTime, Integer, ForeignKey, func
from app.models.base import Base


class Subscription(Base):
    __tablename__ = "subscription"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(String(20), ForeignKey("portfolio.code"), nullable=False)
    investor_code = Column(String(20), ForeignKey("investor.code"), nullable=False)
    platform_code = Column(String(20), ForeignKey("platform.code"), nullable=False)
    sub_type = Column(String(10), nullable=False)
    amount = Column(Numeric(15, 4))
    shares = Column(Numeric(15, 2))
    unit_price = Column(Numeric(10, 4))
    apply_date = Column(Date, nullable=False)
    confirm_date = Column(Date)
    status = Column(String(20), default="pending")
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
