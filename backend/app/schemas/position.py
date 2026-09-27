from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime


class PositionBase(BaseModel):
    portfolio_code: str
    product_code: str
    market: Optional[str] = None
    platform_code: Optional[str] = None
    shares: Optional[float] = None
    frozen_shares: Optional[float] = None
    cost_price: Optional[float] = None
    unit_price: Optional[float] = None
    market_value: Optional[float] = None
    cash_amount: Optional[float] = None
    frozen_amount: Optional[float] = None
    snapshot_date: date


class PositionCreate(PositionBase):
    pass


class PositionUpdate(BaseModel):
    platform_code: Optional[str] = None
    shares: Optional[float] = None
    frozen_shares: Optional[float] = None
    cost_price: Optional[float] = None
    unit_price: Optional[float] = None
    market_value: Optional[float] = None
    cash_amount: Optional[float] = None
    frozen_amount: Optional[float] = None
    snapshot_date: Optional[date] = None


class PositionResponse(PositionBase):
    id: int
    created_at: Optional[datetime] = None
    # 读侧派生字段（issue：前端持仓表产品名称/盈亏/收益率三列）：
    # product_name 来自 product 表；盈亏 = market_value − shares×cost_price（仅净值型资产）
    product_name: Optional[str] = None
    profit_loss: Optional[float] = None
    profit_loss_percent: Optional[float] = None
    # 读侧派生字段（issue #128）：五维度标签 code+name 成对，来自 product 表
    # join 维度字典（快照不存分类，portfolio_position.asset_type 列已删除）；
    # CASH 行 asset_class=现金；IN_TRANSIT 行五维度全 NULL（前端按 product_code 判在途）
    asset_class_code: Optional[str] = None
    asset_class_name: Optional[str] = None
    region_code: Optional[str] = None
    region_name: Optional[str] = None
    style_code: Optional[str] = None
    style_name: Optional[str] = None
    size_code: Optional[str] = None
    size_name: Optional[str] = None
    segment_code: Optional[str] = None
    segment_name: Optional[str] = None
    # daily_profit 为当日收益（首个快照日 / IN_TRANSIT 在途行 → None）；
    # nav_lag_days 来自 product 表（issue #228）：0=当日取价，N>0=取前第 N 个交易日净值，
    # 前端据此提示「日收益滞后 N 天」；is_qdii 仅为展示标签，不参与取价判断
    daily_profit: Optional[float] = None
    is_qdii: Optional[bool] = None
    nav_lag_days: Optional[int] = None
    # platform_name 来自 platform 表（批量 enrich，防 N+1，issue #106）
    platform_name: Optional[str] = None

    class Config:
        from_attributes = True


class PaginatedPositionResponse(BaseModel):
    """持仓列表分页响应（issue #512）：此前未声明响应模型，ORM 行经 enrich 后直吐；
    items 元素复用 PositionResponse（读侧派生字段已在该模型内）。"""

    items: List[PositionResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# #595 组合详情页双视图：按产品 / 按平台聚合响应
# ---------------------------------------------------------------------------


class HoldingProductPlatformSlice(BaseModel):
    """产品聚合内的平台分布切片（产品详情页平台分布卡数据源）。

    market_value 为该平台切片市值（现金切片按 cash_amount 计）；
    ratio_in_product 为占产品比（0–1 小数）。
    """

    platform_code: Optional[str] = None
    platform_name: Optional[str] = None
    market_value: float
    shares: Optional[float] = None
    cash_amount: Optional[float] = None
    holding_profit: Optional[float] = None
    cumulative_profit: Optional[float] = None
    ratio_in_product: Optional[float] = None


class HoldingProductAggregate(BaseModel):
    """按产品聚合的持仓卡（跨平台合计）。

    现金（CASH）：shares=None、cash_amount 为跨平台合计；market_value 为跨平台
    合计市值（现金行按 cash_amount 计，与快照行市值口径一致）。行级缺份额/成本时
    holding_profit / holding_profit_percent 整体为 None（不发布部分和）。
    ratio 为占组合比（0–1 小数）；holding_profit_percent 为百分数（×100）。
    cumulative_profit 为 #598 全历史净流量口径，无市值快照时 None
    （前端按可空占位渲染）。
    """

    product_code: str
    market: str
    product_name: Optional[str] = None
    # 五维度标签（前端大类/维度分组元数据，与 PositionResponse 同构）
    asset_class_code: Optional[str] = None
    asset_class_name: Optional[str] = None
    region_code: Optional[str] = None
    region_name: Optional[str] = None
    style_code: Optional[str] = None
    style_name: Optional[str] = None
    size_code: Optional[str] = None
    size_name: Optional[str] = None
    segment_code: Optional[str] = None
    segment_name: Optional[str] = None
    market_value: float
    ratio: Optional[float] = None
    shares: Optional[float] = None
    cash_amount: Optional[float] = None
    holding_profit: Optional[float] = None
    holding_profit_percent: Optional[float] = None
    daily_profit: Optional[float] = None
    cumulative_profit: Optional[float] = None
    platforms: List[HoldingProductPlatformSlice]


class HoldingsByProductResponse(BaseModel):
    portfolio_code: str
    snapshot_date: Optional[date] = None
    total_market_value: float
    products: List[HoldingProductAggregate]


class HoldingPlatformAggregate(BaseModel):
    """按平台聚合的持仓卡。

    market_value 含该平台现金与在途（现金行按 cash_amount 计）；
    product_count 只计非现金、非在途产品。行级缺份额/成本时 holding_profit
    为 None（不发布部分和）。ratio 为占组合比（0–1 小数）。
    cumulative_profit 为 #598 平台粒度口径，无市值快照时 None。
    """

    platform_code: Optional[str] = None
    platform_name: Optional[str] = None
    platform_type: Optional[str] = None
    market_value: float
    cash_balance: float
    product_count: int
    holding_profit: Optional[float] = None
    cumulative_profit: Optional[float] = None
    ratio: Optional[float] = None


class HoldingsByPlatformResponse(BaseModel):
    portfolio_code: str
    snapshot_date: Optional[date] = None
    total_market_value: float
    platforms: List[HoldingPlatformAggregate]


class CashPositionUpdate(BaseModel):
    """非净值型资产（现金）更新请求"""
    cash_amount: float
    platform_code: str  # 必填：平台代码
    update_date: Optional[date] = None  # 可选，默认为今天
