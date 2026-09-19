from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime


class SubscriptionBase(BaseModel):
    portfolio_code: str
    investor_code: str
    platform_code: str  # 交易平台
    sub_type: str  # subscribe/redeem
    amount: Optional[float] = None
    shares: Optional[float] = None
    unit_price: Optional[float] = None
    apply_date: date
    confirm_date: Optional[date] = None
    status: str = "pending"
    notes: Optional[str] = None


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    # confirm_date 不开放直改：创建/unconfirm 时由服务层按 T+1 自动维护
    # （编辑 apply_date 时服务层同步重算预计确认日，issue #202）
    # status 不开放直改：状态流转走 confirm/cancel/unconfirm 端点
    platform_code: Optional[str] = None
    amount: Optional[float] = None
    shares: Optional[float] = None
    unit_price: Optional[float] = None
    apply_date: Optional[date] = None
    notes: Optional[str] = None


class SubscriptionResponse(SubscriptionBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedSubscriptionResponse(BaseModel):
    """申赎列表分页响应（issue #512）：此前未声明响应模型，ORM 整行直吐；
    items 元素复用 SubscriptionResponse 收窄口径。"""

    items: List[SubscriptionResponse]
    total: int
    page: int
    page_size: int


class SubscriptionPreviewResult(BaseModel):
    """申赎确认前预览的计算结果（与真实确认共用同一计算实现）"""

    nav: float
    shares: Optional[float] = None
    amount: Optional[float] = None
    confirm_date: date
    is_first: bool = False


class SubscriptionPreviewResponse(BaseModel):
    subscription: SubscriptionResponse
    preview: SubscriptionPreviewResult
