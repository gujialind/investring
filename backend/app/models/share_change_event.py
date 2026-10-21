from sqlalchemy import Column, String, Text, Numeric, Date, DateTime, Integer, ForeignKey, ForeignKeyConstraint, case, func
from sqlalchemy.ext.hybrid import hybrid_property
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
    cash_pay_date = Column(Date, nullable=True)
    share_confirm_date = Column(Date, nullable=True)
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

    @hybrid_property
    def cash_effective_date(self):
        if self.event_type == "cash_dividend" and self.cash_pay_date:
            return self.cash_pay_date
        return self.ex_date

    @cash_effective_date.expression
    def cash_effective_date(cls):
        return case(
            (cls.event_type == "cash_dividend", func.coalesce(cls.cash_pay_date, cls.ex_date)),
            else_=cls.ex_date,
        )

    @hybrid_property
    def share_effective_date(self):
        """份额生效日（#743）：再投资设了确认日则份额在确认日到账，否则回落除息日。

        与 `cash_effective_date` 对仗，是快照份额窗口、pending 闸门与
        auto_confirm 判键的单一口径。同日登记/除息（`entitlement_date == ex_date`）
        只有设了 `share_confirm_date` 才合法，故同日事件的份额必然落在确认日。
        """
        if self.event_type == "reinvest_dividend" and self.share_confirm_date:
            return self.share_confirm_date
        return self.ex_date

    @share_effective_date.expression
    def share_effective_date(cls):
        return case(
            (
                cls.event_type == "reinvest_dividend",
                func.coalesce(cls.share_confirm_date, cls.ex_date),
            ),
            else_=cls.ex_date,
        )

    __table_args__ = (
        ForeignKeyConstraint(
            ["product_code", "market"],
            ["product.code", "product.market"]
        ),
    )
