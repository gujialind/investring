from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class InvestorBase(BaseModel):
    code: str
    name: str
    role: str = "viewer"
    phone: Optional[str] = None
    email: Optional[str] = None


class InvestorCreate(InvestorBase):
    password: str


class InvestorUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class InvestorResponse(InvestorBase):
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedInvestorResponse(BaseModel):
    """投资人列表分页响应（issue #487）：此前列表端点未声明响应模型，
    直吐 ORM 整行导致 password_hash 外泄；items 元素复用 InvestorResponse 收窄口径。"""

    items: List[InvestorResponse]
    total: int
    page: int
    page_size: int
