from sqlalchemy import Column, String, Numeric, Date, DateTime, Integer, ForeignKey, func, UniqueConstraint, event
from app.models.base import Base


class PortfolioValueSnapshot(Base):
    __tablename__ = "portfolio_value_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(String(20), ForeignKey("portfolio.code"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    total_value = Column(Numeric(15, 4), nullable=False)
    total_shares = Column(Numeric(15, 2), nullable=False)
    unit_price = Column(Numeric(10, 4), nullable=False)
    frozen_shares = Column(Numeric(15, 2), default=0)
    unit_price_change_pct = Column(Numeric(8, 4))
    in_transit_total = Column(Numeric(15, 4), server_default='0')
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint('portfolio_code', 'snapshot_date', name='uix_snapshot_portfolio_date'),
    )


# #59 加固：ORM 层兜底，禁止 instance-level update/delete（快照只增不改，与 PortfolioPosition 对齐）
# bulk delete（db.execute(delete(...))）不触发 instance event，
# _delete_existing_snapshots 经此绕过（明确表达内部删除意图）。
@event.listens_for(PortfolioValueSnapshot, "before_update")
def _prevent_value_snapshot_update(mapper, connection, target):
    raise RuntimeError(
        "portfolio_value_snapshot 快照不可更新，请使用 recalculate_snapshots 重算"
    )


@event.listens_for(PortfolioValueSnapshot, "before_delete")
def _prevent_value_snapshot_delete(mapper, connection, target):
    raise RuntimeError(
        "portfolio_value_snapshot 快照不可直接删除，请使用 DELETE /snapshots/{portfolio}/{date}"
    )
