from sqlalchemy import Column, String, Text, Numeric, Date, DateTime, Integer, ForeignKey, ForeignKeyConstraint, UniqueConstraint, func
from app.models.base import Base


class Trade(Base):
    __tablename__ = "trade"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(String(20), ForeignKey("portfolio.code"), nullable=False)
    platform_code = Column(String(20), ForeignKey("platform.code"))
    product_code = Column(String(20), nullable=False)
    market = Column(String(20))
    trade_type = Column(String(10), nullable=False)
    # 现金流水配对标识：关联同一业务操作产生的多条 trade（基金腿 + CASH 腿），
    # 用于 confirm/unconfirm/cancel 时对配对 CASH 腿做原子翻转。编码规则：
    #   - 申购确认（CASH buy）/赎回确认（CASH sell）：sub_{subscription.id}
    #   - 基金买入/卖出（基金腿 + 配对 CASH 腿）：rebal_{uuid}
    #   - 平台间现金转移（CASH sell + CASH buy）：uuid（裸 uuid）
    # 每笔 trade 均隶属一个业务组，不存在裸 trade（NOT NULL 保证数据规范）
    transfer_group = Column(String(36), nullable=False)
    shares = Column(Numeric(15, 2))
    amount = Column(Numeric(15, 4), nullable=False)
    price = Column(Numeric(10, 4))
    fee = Column(Numeric(15, 4), default=0)
    actual_amount = Column(Numeric(15, 4))
    trade_date = Column(Date, nullable=False)
    confirm_date = Column(Date)
    status = Column(String(20), default="pending")
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["product_code", "market"],
            ["product.code", "product.market"]
        ),
        # transfer_group 唯一约束：防止重复确认生成重复 CASH trade。
        # transfer_group NOT NULL，每笔 trade 均属一个业务组；基金腿与 CASH 腿按
        # product_code 区分、现金转移两腿按 trade_type 区分、申赎为单腿 sub_{id}，故无碰撞
        UniqueConstraint('transfer_group', 'product_code', 'trade_type', name='uq_trade_transfer_group'),
    )
