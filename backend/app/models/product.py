from sqlalchemy import Column, String, Text, DateTime, Boolean, Integer, ForeignKey, func, text
from app.models.base import Base


class Product(Base):
    __tablename__ = "product"

    code = Column(String(20), primary_key=True)
    market = Column(String(20), primary_key=True, nullable=True)
    name = Column(String(100), nullable=False)
    product_type = Column(String(20), nullable=False)
    # 港互认 6 位内地销售代码（issue #705，仅 HK_MUTUAL 有值）：`code` 存 10 位
    # 香港基金代码（取数主键），展示码由它派生；映射不可得为 NULL，读侧回退原码
    hk_sale_code = Column(String(10))
    # 正交维度标签（issue #128）：asset_class 必填；region 股票/债券必填；
    # style/size 仅股票；segment 按大类解释（股票→行业/债券→期限/商品→品种）
    asset_class_code = Column(String(30), ForeignKey("asset_classification.code"))
    region_code = Column(String(30), ForeignKey("asset_classification.code"))
    style_code = Column(String(30), ForeignKey("asset_classification.code"))
    size_code = Column(String(30), ForeignKey("asset_classification.code"))
    segment_code = Column(String(30), ForeignKey("asset_classification.code"))
    confirm_days = Column(Integer)
    # issue #228：快照估值取价滞后交易日数（0=当日、N=前第 N 个交易日）；
    # 普通产品 0，场外 QDII / 互认基金 1。滞后估值一律由此列驱动，
    # is_qdii 降级为纯展示标签（不再参与取价业务分支）。
    nav_lag_days = Column(Integer, nullable=False, default=0, server_default=text('0'))
    is_qdii = Column(Boolean, default=False)
    data_source = Column(String(20), default="tushare")
    fallback_source = Column(String(20))
    data_source_status = Column(String(20), default="pending")
    last_sync_at = Column(DateTime)
    sync_error = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
