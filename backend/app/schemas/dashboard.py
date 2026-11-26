"""仪表盘汇总响应模型（#944）。"""
from typing import Literal, Optional

from pydantic import BaseModel


class DashboardAggregateXirr(BaseModel):
    value: Optional[float]
    reliable: bool
    reason: Optional[Literal["no_solution", "insufficient_flows", "short_history"]]
    cash_flow_count: int
    holding_days: Optional[int]


class DashboardPortfolioSummary(BaseModel):
    code: str
    name: str
    kind: str
    total_value: Optional[float]
    total_profit: Optional[float]
    cumulative_return: Optional[float]
    mwr: Optional[float]
    annualization_reliable: bool
    holding_days: Optional[int]


class DashboardSummary(BaseModel):
    total_value: float
    total_profit: float
    aggregate_xirr: DashboardAggregateXirr
    pending_subscription_count: int
    pending_trade_count: int
    portfolios: list[DashboardPortfolioSummary]
