from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text
from app.models.base import Base


class AssetClassification(Base):
    """资产分类维度值字典（issue #128 正交维度重构）。

    每行是一个维度值：dimension 区分所属维度（asset_class/region/style/size/segment），
    code 形如 `维度前缀_语义词`（ASSET_STOCK/REGION_CN/STYLE_GROWTH/SIZE_LARGE/SEG_GOLD）。
    name 为聚合展示名目（UI 分区/图例/二级分组用）；asset_class 维度的 sort_order
    同时是前端色板序位，变更即改色。维度值字典单一事实来源见
    app/constants/asset_dimensions.py。

    is_active（issue #135 软失效）：停用后产品表单下拉与新赋值校验过滤，
    存量产品引用照常显示与通过校验；无物理删除。
    """
    __tablename__ = "asset_classification"

    code = Column(String(30), primary_key=True, index=True)
    dimension = Column(String(20), nullable=False)
    name = Column(String(50), nullable=False)
    sort_order = Column(Integer, default=0)
    description = Column(Text)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")


class AssetDimensionApplicability(Base):
    """维度值 ↔ asset_class 适用关联（issue #135，值级适用性，多对多）。

    一个值可适用多个大类（如 REGION_CN 同时适用股票与债券）；asset_class 维度
    自身不参与关联。产品侧值级校验（validate_dimension_tags）以此表为准：
    产品所选维度值必须存在 (值, 产品 asset_class) 关联行。运行期事实来源为 DB，
    种子定义见 app/constants/asset_dimensions.py::DIMENSION_APPLICABILITY。
    """
    __tablename__ = "asset_dimension_applicability"

    dimension_value_code = Column(
        String(30), ForeignKey("asset_classification.code", ondelete="RESTRICT"),
        primary_key=True,
    )
    asset_class_code = Column(
        String(30), ForeignKey("asset_classification.code", ondelete="RESTRICT"),
        primary_key=True,
    )

    __table_args__ = (
        Index("ix_applicability_asset_class", "asset_class_code"),
    )


class AssetClassDimensionRule(Base):
    """asset_class 维度级适用规则（issue #135 矩阵落库，三态）。

    rule ∈ required/optional；某 (大类, 维度) 无行 = forbidden；某大类全表无行
    = 现金型语义（其余维度全 forbidden，新建大类默认态）。维度级矩阵管「字段要不
    要填」，值级关联表管「填的值属于哪个大类」，两层叠加。运行期事实来源为 DB，
    种子定义见 app/constants/asset_dimensions.py::DIMENSION_RULES。
    """
    __tablename__ = "asset_class_dimension_rule"

    asset_class_code = Column(
        String(30), ForeignKey("asset_classification.code", ondelete="RESTRICT"),
        primary_key=True,
    )
    dimension = Column(String(20), primary_key=True)
    rule = Column(String(10), nullable=False)
