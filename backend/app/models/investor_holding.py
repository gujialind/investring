from sqlalchemy import Column, String, Numeric, Date, DateTime, Integer, ForeignKey, func, UniqueConstraint, event
from app.models.base import Base


class InvestorHolding(Base):
    __tablename__ = "investor_holding"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(String(20), ForeignKey("portfolio.code"), nullable=False)
    investor_code = Column(String(20), ForeignKey("investor.code"), nullable=False)
    shares = Column(Numeric(15, 2), nullable=False)
    frozen_shares = Column(Numeric(15, 2), default=0)
    cost_per_share = Column(Numeric(10, 4))
    # 派生字段（#40 改进1）：快照生成时回填，历史快照可为 NULL
    market_value = Column(Numeric(15, 4))  # shares * 组合净值
    total_cost = Column(Numeric(15, 4))    # shares * cost_per_share
    profit = Column(Numeric(15, 4))         # market_value - total_cost
    snapshot_date = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint('portfolio_code', 'investor_code', 'snapshot_date', name='uix_holding_snapshot'),
    )


# #59 加固：ORM 层兜底，禁止 instance-level update/delete（快照只增不改，与 PortfolioPosition 对齐）
# bulk delete（db.execute(delete(...))）不触发 instance event，
# _delete_existing_snapshots 经此绕过（明确表达内部删除意图）。
@event.listens_for(InvestorHolding, "before_update")
def _prevent_holding_update(mapper, connection, target):
    raise RuntimeError(
        "investor_holding 快照不可更新，请使用 recalculate_snapshots 重算"
    )


@event.listens_for(InvestorHolding, "before_delete")
def _prevent_holding_delete(mapper, connection, target):
    raise RuntimeError(
        "investor_holding 快照不可直接删除，请使用 DELETE /snapshots/{portfolio}/{date}"
    )
