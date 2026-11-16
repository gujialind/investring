from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class DividendConfigCreate(BaseModel):
    """分红方式配置写入（#887，SPEC §6）。market 省略按 resolve_product_market 补全；
    写侧恒 CN_OTC（R10）。必填字段显式 null 由 pydantic 校验拒绝（422，#579 口径）。"""

    portfolio_code: str
    product_code: str
    market: Optional[str] = None
    platform_code: str
    dividend_method: str
    effective_from: date
    # 确认弹窗「一次确认即沉淀」入口标记（#887 S4 方案 B）。true 时 service 把
    # effective_from 取 max(入参, 业务当日)，使沉淀行不早于今天、消除 default 遮蔽；
    # 配置页/CLI 用户自选日期（含补录过去，D3）默认 false、不受影响。
    from_confirmation: bool = False


class DividendConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class PaginatedDividendConfigResponse(BaseModel):
    items: List[DividendConfigResponse]
    total: int
    page: int
    page_size: int