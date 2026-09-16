from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime


class ProductBase(BaseModel):
    code: str
    market: Optional[str] = None
    name: str
    product_type: str
    # 正交维度标签（issue #128）：适用矩阵由服务端校验（股票必填 region/style/size；
    # 债券必填 region+segment；商品/现金的 region/style/size 必须为 NULL）
    asset_class_code: Optional[str] = None
    region_code: Optional[str] = None
    style_code: Optional[str] = None
    size_code: Optional[str] = None
    segment_code: Optional[str] = None
    # issue #240 跟进 #6：约束由服务层单一实现（product_service.validate_confirm_days，
    # INVALID_CONFIRM_DAYS）——>=0、显式 null 拒绝、场内（CN_EXCHANGE）必须 0；
    # schema 层不加 ge=0，避免同一业务规则产生两种 422 形状（与 #5 决策一致）。
    # 创建路径显式优先、缺省推导（#231/#236/#241），见 ProductCreate 覆盖
    confirm_days: int = 1
    # issue #228：快照估值取价滞后交易日数（0=当日、N=前第 N 个交易日）；
    # 场外 QDII / 互认基金取 1。is_qdii 仅为展示标签，不参与取价
    # issue #235/#240：约束由服务层单一实现（product_service.validate_nav_lag_days，
    # INVALID_NAV_LAG_DAYS）——>=0、显式 null 拒绝、场内（CN_EXCHANGE）必须 0；
    # schema 层不加 ge=0，避免同一业务规则两种 422 形状（pydantic 列表形状无 error 码）
    nav_lag_days: int = 0
    is_qdii: bool = False
    data_source: Optional[str] = "tushare"


class ProductCreate(ProductBase):
    # issue #90：创建后立即回填历史净值（同步失败不阻断创建）
    sync_history: Optional[bool] = False
    # issue #231/#236/#241：覆盖继承的 int = 1——None=未传、后端按 market+is_qdii 推导；
    # 显式传值优先（校验走 product_service.validate_confirm_days，显式 null 拒绝），
    # 与 update 路径纯显式语义对齐
    confirm_days: Optional[int] = None


class ProductUpdate(BaseModel):
    # issue #232：未知字段直接 422（此前 pydantic 静默丢弃，调用方误以为修改生效）
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    # issue #232：身份字段可纠错（枚举/守卫见 product_service），market 仅限无引用产品
    product_type: Optional[str] = None
    market: Optional[str] = None
    asset_class_code: Optional[str] = None
    region_code: Optional[str] = None
    style_code: Optional[str] = None
    size_code: Optional[str] = None
    segment_code: Optional[str] = None
    # issue #240 跟进 #6：显式更新走服务层校验（validate_confirm_days，纯显式不传不改；
    # 唯一例外：market 变化且未传时按新市场重推导）
    confirm_days: Optional[int] = None
    # issue #235/#240：PartialUpdate 缺省=不修改；取值校验（含显式 null 拒绝）由服务层
    # validate_nav_lag_days 单一实现
    nav_lag_days: Optional[int] = None
    is_qdii: Optional[bool] = None


class ProductResponse(ProductBase):
    data_source: Optional[str] = None
    # 列可空（models/product.py 无 server_default）：迁移 0006 裸 SQL 种入的
    # IN_TRANSIT_BUY/SELL 该列为 NULL，声明成非空会让这些行的响应校验 500（#487 评审）
    data_source_status: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # issue #90：sync_history=True 时的回填结果（success/message/synced_count）
    sync_result: Optional[dict] = None
    # issue #232：market 随行迁移后的提示（建议重新 sync-history 等）
    market_change_hint: Optional[str] = None

    class Config:
        from_attributes = True


class PaginatedProductResponse(BaseModel):
    """产品列表分页响应（issue #487 同因排查）：此前列表端点未声明响应模型，
    直吐 ORM 整行多带 fallback_source / sync_error；items 元素复用 ProductResponse 口径。
    sync_result / market_change_hint 为创建/更新路径专属字段，列表恒为 null。"""

    items: List[ProductResponse]
    total: int
    page: int
    page_size: int
