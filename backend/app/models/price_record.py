from sqlalchemy import Column, String, Numeric, Date, DateTime, Integer, ForeignKeyConstraint, func, UniqueConstraint
from app.models.base import Base


class PriceRecord(Base):
    __tablename__ = "price_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_code = Column(String(20), nullable=False)
    market = Column(String(20), nullable=False)
    price_date = Column(Date, nullable=False)
    # #580：单价必须非空。NULL 单价会让 `float(r.unit_price)` 炸成 500，且快照取价
    # 把它当「有价的一天」、让 nav_coverage 误计为已覆盖——缺一行好过藏一颗雷，
    # 写入侧（`_bulk_upsert_prices`）先滤掉无价行，这里再以 NOT NULL 收口。
    unit_price = Column(Numeric(10, 4), nullable=False)
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
