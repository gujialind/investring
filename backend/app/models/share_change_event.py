from sqlalchemy import Column, String, Text, Numeric, Date, DateTime, Integer, ForeignKey, ForeignKeyConstraint, func
from app.models.base import Base


class ShareChangeEvent(Base):
    __tablename__ = "share_change_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(String(20), ForeignKey("portfolio.code"), nullable=False)
    product_code = Column(String(20), nullable=False)
    market = Column(String(20), nullable=False)
    event_type = Column(String(30), nullable=False)
    ex_date = Column(Date, nullable=False)
    entitlement_date = Column(Date, nullable=False)
    platform_code = Column(String(20), ForeignKey("platform.code"), nullable=True)
    parent_event_id = Column(Integer, ForeignKey("share_change_event.id"), nullable=True)
    event_source = Column(String(20), nullable=False)
    entitlement_shares = Column(Numeric(15, 2))
    shares_before = Column(Numeric(15, 2))
    shares_change = Column(Numeric(15, 2))
    shares_after = Column(Numeric(15, 2))
    ratio = Column(Numeric(10, 4))
    div_cash = Column(Numeric(10, 4))
    reinvest_nav = Column(Numeric(10, 4))
    cash_change = Column(Numeric(15, 4))
    cash_product_code = Column(String(20))
    status = Column(String(20), default="pending")
    tushare_event_id = Column(String(50))
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    confirmed_at = Column(DateTime)

    __table_args__ = (
        ForeignKeyConstraint(
            ["product_code", "market"],
            ["product.code", "product.market"]
        ),
    )
