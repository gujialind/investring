from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class PlatformBase(BaseModel):
    code: str
    name: str
    platform_type: Optional[str] = None


class PlatformCreate(PlatformBase):
    pass


class PlatformUpdate(BaseModel):
    name: Optional[str] = None
    platform_type: Optional[str] = None


class PlatformResponse(PlatformBase):
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedPlatformResponse(BaseModel):
    """平台列表分页响应（issue #512）：此前未声明响应模型，ORM 整行直吐；
    items 元素复用 PlatformResponse 收窄口径（同 PR #511 的 investors/products 写法）。"""

    items: List[PlatformResponse]
    total: int
    page: int
    page_size: int
