from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal


class LedgerEntryCreate(BaseModel):
    platform_code: str
    value_date: date
    market_value: Decimal
    asset_kind: str = "non_cash"  # cash / non_cash
    note: Optional[str] = None


class LedgerEntryUpdate(BaseModel):
    # D5：可改某日市值/分类/备注；platform_code/value_date 为唯一键组成，不改（改日期请删后重建）
    market_value: Optional[Decimal] = None
    asset_kind: Optional[str] = None
    note: Optional[str] = None


class LedgerEntryResponse(BaseModel):
    id: int
    portfolio_code: str
    platform_code: str
    value_date: date
    market_value: float
    asset_kind: str
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LedgerFlowCreate(BaseModel):
    platform_code: str
    flow_date: date
    direction: str  # inflow / outflow
    amount: Decimal
    note: Optional[str] = None


class LedgerFlowUpdate(BaseModel):
    flow_date: Optional[date] = None
    direction: Optional[str] = None
    amount: Optional[Decimal] = None
    note: Optional[str] = None


class LedgerFlowResponse(BaseModel):
    id: int
    portfolio_code: str
    platform_code: str
    flow_date: date
    direction: str
    amount: float
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LedgerTotalsResponse(BaseModel):
    portfolio_code: str
    as_of_date: Optional[date] = None
    total_value: Optional[float] = None
    net_inflow: Optional[float] = None
    total_profit: Optional[float] = None
    warnings: List[str] = []


class LedgerFlowWriteResponse(LedgerFlowResponse):
    # 写入响应附告警（S4：净入金为负时提示，不硬拒不静默）
    warnings: List[str] = []