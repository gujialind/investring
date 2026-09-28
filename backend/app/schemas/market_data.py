from pydantic import BaseModel
from datetime import date
from typing import List, Optional


class PriceDataResponse(BaseModel):
    product_code: str
    market: str
    price_date: date
    unit_price: float

    class Config:
        from_attributes = True


class PriceDataSyncRequest(BaseModel):
    start_date: date
    end_date: date


# --- #595 产品详情页净值数据（§5.3）：历史净值分页 + 区间曲线/区间收益率 ---


class NavHistoryItem(BaseModel):
    """历史净值行：单位净值必有；累计净值/日涨跌可空（场内 ETF 行情源常无累计净值）。"""

    price_date: date
    unit_price: float
    accumulated_nav: Optional[float] = None
    pct_change: Optional[float] = None


class NavHistoryPage(BaseModel):
    """历史净值分页（按日期降序）。"""

    items: List[NavHistoryItem]
    total: int


class NavCurvePoint(BaseModel):
    """累计净值曲线点（仅含累计净值非空的记录）。"""

    date: date
    accumulated_nav: float


class ProductIntervalReturns(BaseModel):
    """区间收益率（百分数，4dp）。历史不足窗口期返回 None，前端显示占位。"""

    m1: Optional[float] = None
    m3: Optional[float] = None
    m6: Optional[float] = None
    y1: Optional[float] = None
    ytd: Optional[float] = None
    all: Optional[float] = None


class ProductNavAnalysis(BaseModel):
    """产品净值分析：所选区间累计净值曲线 + 六窗区间收益率（同一份序列算，口径一致）。"""

    curve: List[NavCurvePoint]
    interval_returns: ProductIntervalReturns
