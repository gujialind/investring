from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import date, datetime


class PortfolioBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None


class PortfolioCreate(PortfolioBase):
    # 持仓明细二级分组维度覆盖（issue #144）：{"ASSET_STOCK": "style", ...}，
    # 仅存显式覆盖项；校验以 asset_class_dimension_rule 规则表为准
    display_config: Optional[Dict[str, str]] = None


class PortfolioUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # 显式传 null = 清空配置恢复默认；不传 = 不修改（exclude_unset 语义，
    # router 以 "display_config" in updates 区分后经哨兵传入 service）
    display_config: Optional[Dict[str, str]] = None
    # 自动快照开关（issue #156）：不传 = 不修改
    auto_snapshot_enabled: Optional[bool] = None


class PortfolioResponse(PortfolioBase):
    status: str = "draft"
    started_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # 组合级展示配置（issue #144）；ListItem 继承自动带出（列表页不消费）
    display_config: Optional[Dict[str, str]] = None
    # 自动快照开关（issue #156）：默认 False（opt-in），仅约束自动任务
    auto_snapshot_enabled: bool = False
    # 读侧派生（详情接口并入，issue #99）：最新快照总资产 / 累计收益（总资产 − 净投入）
    # 无快照（draft 等）时为 None
    total_value: Optional[float] = None
    total_profit: Optional[float] = None

    class Config:
        from_attributes = True


class PortfolioListItem(PortfolioResponse):
    """列表项：附聚合字段（issue #69），无快照时为 None/0。"""
    total_value: Optional[float] = None
    cumulative_return: Optional[float] = None
    investor_count: int = 0


class PaginatedPortfolioResponse(BaseModel):
    items: List[PortfolioListItem]
    total: int
    page: int
    page_size: int


class PortfolioValueSnapshotResponse(BaseModel):
    id: int
    portfolio_code: str
    snapshot_date: date
    total_value: float
    total_shares: float
    unit_price: float
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PortfolioInvestorItem(BaseModel):
    """组合投资人持仓行（#693 派生列 + #694 累计收益与清仓行）。

    派生列来自 investor_holding（快照生成时逐日落库）；#40 之前生成且从未重算的
    历史快照三列（market_value/total_cost/profit）可为 NULL，连同 frozen_shares/
    cost_per_share 一律 Optional。ratio 为占组合总份额比（0-1 小数）、
    profit_percent 为百分数（×100，total_cost 为 NULL/0 时 None），口径对齐
    HoldingProductAggregate（schemas/position.py）。

    cumulative_profit 为投资人累计收益（#694 方案 A：D 日市值 + Σconfirmed
    赎回 − Σconfirmed 申购，与 profit「持仓收益」口径不同）；缺省最新日路径
    无市值快照时降级 None，显式 snapshot_date 该日无市值快照为 404（#672）。
    is_liquidated 标记清仓行（当日无持仓行、份额/市值计 0、保留累计收益）。
    """
    investor_code: str
    name: str
    shares: float
    frozen_shares: Optional[float] = None
    cost_per_share: Optional[float] = None
    market_value: Optional[float] = None
    total_cost: Optional[float] = None
    profit: Optional[float] = None
    profit_percent: Optional[float] = None
    ratio: Optional[float] = None
    cumulative_profit: Optional[float] = None
    is_liquidated: bool = False


class NavHistoryRecord(BaseModel):
    snapshot_date: date
    unit_price: Optional[float] = None
    total_value: Optional[float] = None
    total_shares: Optional[float] = None


class PortfolioPerformance(BaseModel):
    """组合绩效指标（快照不足时相应字段为 None）。

    twr：时间加权收益率，消除资金进出影响（净值化系统下等于净值增长率）
    mwr：资金加权收益率（XIRR），反映实际投入资金的年化回报
    """
    portfolio_code: str
    # 收益率
    twr: Optional[float] = None
    twr_chained: Optional[float] = None
    annualized_twr: Optional[float] = None
    mwr: Optional[float] = None
    # 净值与持有期
    initial_nav: Optional[float] = None
    current_nav: Optional[float] = None
    holding_days: Optional[int] = None
    # 区间收益（组合成立不足窗口期时为 None）
    return_1m: Optional[float] = None
    return_3m: Optional[float] = None
    return_6m: Optional[float] = None
    return_ytd: Optional[float] = None
    return_1y: Optional[float] = None
    return_3y: Optional[float] = None
    # 风险指标
    max_drawdown: Optional[float] = None
    max_drawdown_peak_date: Optional[str] = None
    max_drawdown_trough_date: Optional[str] = None
    annualized_volatility: Optional[float] = None
    # 元信息
    cash_flow_count: int = 0
    nav_series_consistent: Optional[bool] = None
    # 持有期 < 90 天时为 False：年化属大幅外推，前端应标注仅供参考
    annualization_reliable: bool = False
