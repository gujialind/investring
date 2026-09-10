from sqlalchemy import (
    Column, String, Numeric, Date, DateTime, Integer, ForeignKey, UniqueConstraint, func
)
from app.models.base import Base


class ManualMarketValue(Base):
    __tablename__ = "manual_market_value"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(String(20), ForeignKey("portfolio.code"), nullable=False)
    platform_code = Column(String(20), ForeignKey("platform.code"), nullable=False)
    product_code = Column(String(20), nullable=False)
    value_date = Column(Date, nullable=False)
    market_value = Column(Numeric(15, 4), nullable=False)
    computed_value = Column(Numeric(15, 4))  # 隐式计算值（审计用）
    created_by = Column(String(50))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            'portfolio_code', 'platform_code', 'product_code', 'value_date',
            name='uq_manual_market_value'
        ),
    )
