from sqlalchemy import Column, String, Numeric, Date, DateTime, Integer, ForeignKey, ForeignKeyConstraint, func, CheckConstraint, UniqueConstraint, event
from app.models.base import Base


class PortfolioPosition(Base):
    __tablename__ = "portfolio_position"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(String(20), ForeignKey("portfolio.code"), nullable=False)
    platform_code = Column(String(20), ForeignKey("platform.code"))
    product_code = Column(String(20), nullable=False)
    market = Column(String(20), nullable=False)
    shares = Column(Numeric(15, 2))
    frozen_shares = Column(Numeric(15, 2), default=0)
    cost_price = Column(Numeric(10, 4))
    unit_price = Column(Numeric(10, 4))
    market_value = Column(Numeric(15, 4), default=0)
    cash_amount = Column(Numeric(15, 4))
    frozen_amount = Column(Numeric(15, 4), default=0)
    snapshot_date = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["product_code", "market"],
            ["product.code", "product.market"]
        ),
        CheckConstraint(
            '(shares IS NOT NULL AND cash_amount IS NULL) OR (shares IS NULL AND cash_amount IS NOT NULL)',
            name='check_nav_or_non_nav'
        ),
        UniqueConstraint('portfolio_code', 'product_code', 'market', 'platform_code', 'snapshot_date', name='uix_position_snapshot'),
    )


# #40 改进2：ORM 层兜底，禁止 instance-level update/delete（HTTP API 已禁，此处防内部代码）
# bulk delete（db.query(...).delete() / db.execute(delete(...))）不触发 instance event，
# _delete_existing_snapshots 通过 db.execute(delete()) 绕过（明确表达内部删除意图）。
@event.listens_for(PortfolioPosition, "before_update")
def _prevent_position_update(mapper, connection, target):
    raise RuntimeError(
        "portfolio_position 快照不可更新，请使用 recalculate_snapshots 重算"
    )


@event.listens_for(PortfolioPosition, "before_delete")
def _prevent_position_delete(mapper, connection, target):
    raise RuntimeError(
        "portfolio_position 快照不可直接删除，请使用 DELETE /snapshots/{portfolio}/{date}"
    )
