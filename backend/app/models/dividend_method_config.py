from sqlalchemy import (
    Column, String, Date, DateTime, Integer, ForeignKey, ForeignKeyConstraint,
    UniqueConstraint, func,
)
from app.models.base import Base


class DividendMethodConfig(Base):
    """分红方式配置（#887）：append-only 生效区间表，键 (组合, 产品, 市场, 平台)。

    解析键是权益登记日 entitlement_date（SPEC §2）：同键 effective_from <= 权益登记日
    的最大一行生效，区间隐式 [from_i, from_{i+1})。source 分级（丙，D7/R9）：
    - default：迁移为存量建的预选值，只供确认弹窗预选，检测器视同无配置（A9/A12）；
    - explicit：用户显式写入/沉淀，PR2 起检测器据此自动入账（A1/A2）。
    写侧市场恒 CN_OTC（R10/A14）；HK_MUTUAL/CN_EXCHANGE 不回填、不写入。
    账务路径（确认/自动确认/快照重放/应收派生）一律不读本表（A3/A7/A10）。
    """

    __tablename__ = "dividend_method_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_code = Column(
        String(20),
        ForeignKey("portfolio.code", name="fk_dividend_config_portfolio", ondelete="RESTRICT"),
        nullable=False,
    )
    product_code = Column(String(20), nullable=False)
    market = Column(String(20), nullable=False)
    platform_code = Column(
        String(20),
        ForeignKey("platform.code", name="fk_dividend_config_platform", ondelete="RESTRICT"),
        nullable=False,
    )
    dividend_method = Column(String(20), nullable=False)
    effective_from = Column(Date, nullable=False)
    source = Column(String(10), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["product_code", "market"],
            ["product.code", "product.market"],
            name="fk_dividend_config_product",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "portfolio_code", "product_code", "market", "platform_code", "effective_from",
            name="uq_dividend_config_key_from",
        ),
    )