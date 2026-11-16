from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class DividendConfigCreate(BaseModel):
    """分红方式配置写入（#887，SPEC §6）。market 省略按 resolve_product_market 补全；
    写侧恒 CN_OTC（R10）。必填字段显式 null 由 pydantic 校验拒绝（422，#579 口径）。"""

    portfolio_code: str
    product_code: str
    market: Optional[str] = None
    platform_code: str
    dividend_method: str
    effective_from: date


class DividendConfigResponse(BaseModel):
    id: int
    portfolio_code: str
    product_code: str
    market: str
    platform_code: str
    dividend_method: str
    effective_from: date
    source: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedDividendConfigResponse(BaseModel):
    items: List[DividendConfigResponse]
    total: int
    page: int
    page_size: int