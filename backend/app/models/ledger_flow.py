from sqlalchemy import (
    Column, String, Numeric, Date, DateTime, Integer, Text, ForeignKey, func,
)
from app.models.base import Base


class LedgerFlow(Base):
    """台账本金进出（#905 PR2）：台账型组合的本金流入/流出流水。

    与净值型现金口径隔离（D1）：本表不被 calculate_available_cash / 在途口径消费。
    同平台同日可多笔（无唯一约束），可 update/delete + 审计（D5）。
    direction=inflow（注资）/ outflow（撤资）；amount>0 本金，量化到分。
    """
    __tablename__ = "ledger_flow"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(
        String(20), ForeignKey("portfolio.code", name="fk_ledger_flow_portfolio"), nullable=False
    )
    platform_code = Column(
        String(20), ForeignKey("platform.code", name="fk_ledger_flow_platform"), nullable=False
    )
    flow_date = Column(Date, nullable=False)  # 任意自然日（D3）
    direction = Column(String(10), nullable=False)  # inflow / outflow
    amount = Column(Numeric(15, 2), nullable=False)  # >0 本金，量化到分
    note = Column(Text)
    created_by = Column(String(50))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())