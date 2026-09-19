from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class NotificationBase(BaseModel):
    type: str
    level: str = "info"
    title: str
    content: Optional[str] = None
    recipient: Optional[str] = None
    channel: str = "in_app"


class NotificationCreate(NotificationBase):
    pass


class NotificationUpdate(BaseModel):
    status: Optional[str] = None


class NotificationResponse(NotificationBase):
    id: int
    status: str = "pending"
    sent_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedNotificationResponse(BaseModel):
    """通知列表分页响应（issue #512）：此前未声明响应模型，ORM 整行直吐；
    items 元素复用 NotificationResponse 收窄口径。"""

    items: List[NotificationResponse]
    total: int
    page: int
    page_size: int
