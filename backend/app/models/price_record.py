from sqlalchemy import Column, String, Numeric, Date, DateTime, Integer, ForeignKeyConstraint, func, UniqueConstraint
from app.models.base import Base


class PriceRecord(Base):
    __tablename__ = "price_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_code = Column(String(20), nullable=False)
    market = Column(String(20), nullable=False)
    price_date = Column(Date, nullable=False)
    unit_price = Column(Numeric(10, 4))
    accumulated_nav = Column(Numeric(10, 4))
    pre_close = Column(Numeric(10, 4))
    pct_change = Column(Numeric(8, 4))
    net_asset = Column(Numeric(15, 4))
    source = Column(String(20))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["product_code", "market"],
            ["product.code", "product.market"]
        ),
        UniqueConstraint('product_code', 'market', 'price_date', name='uix_price_record'),
    )
