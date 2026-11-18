from sqlalchemy import (
    Column, String, Numeric, Date, DateTime, Integer, Text, ForeignKey, UniqueConstraint, func,
)
from app.models.base import Base


class LedgerEntry(Base):
    """平台市值台账（#905 PR2）：台账型组合按平台日记绝对市值。

    与净值型三表（portfolio_position / portfolio_value_snapshot / investor_holding）完全隔离：
    本表不被现金/在途/快照口径消费（D1 隔离断言见 test_ledger_cash_isolation）。
    每平台每日一行（唯一键），可 update/delete（D5），无 ORM 禁改 hook。
    市值是手动绝对值、无隐式计算值，变更留痕靠 audit_log（不另设计算值列）。
    """
    __tablename__ = "ledger_entry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(
        String(20), ForeignKey("portfolio.code", name="fk_ledger_entry_portfolio"), nullable=False
    )
    platform_code = Column(
        String(20), ForeignKey("platform.code", name="fk_ledger_entry_platform"), nullable=False
    )
    value_date = Column(Date, nullable=False)  # 任意自然日，不校交易日（D3）
    market_value = Column(Numeric(15, 4), nullable=False)  # 绝对市值，≥0，金额闸门
    asset_kind = Column(String(20), nullable=False)  # cash / non_cash（展示分类，D1）
    note = Column(Text)
    created_by = Column(String(50))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "portfolio_code", "platform_code", "value_date",
            name="uq_ledger_entry_portfolio_platform_date",
        ),
    )