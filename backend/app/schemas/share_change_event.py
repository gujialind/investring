from pydantic import BaseModel, ConfigDict, model_validator
from typing import List, Literal, Optional
from datetime import date, datetime

from app.services.reinvest_receivable import reinvest_dividend_amount


class ShareChangeEventBase(BaseModel):
    portfolio_code: str
    event_type: str  # dividend_pending/cash_dividend/reinvest_dividend/share_split/share_merge/bonus_share/forced_adjustment
    ex_date: date
    entitlement_date: date
    cash_pay_date: Optional[date] = None
    share_confirm_date: Optional[date] = None
    platform_code: Optional[str] = None
    parent_event_id: Optional[int] = None
    product_code: Optional[str] = None
    market: Optional[str] = None
    event_source: str = "manual"
    entitlement_shares: Optional[float] = None
    shares_before: Optional[float] = None
    shares_change: Optional[float] = None
    shares_after: Optional[float] = None
    cash_change: Optional[float] = None
    cash_product_code: Optional[str] = None
    div_cash: Optional[float] = None
    reinvest_nav: Optional[float] = None
    ratio: Optional[float] = None
    status: str = "pending"
    tushare_event_id: Optional[str] = None
    notes: Optional[str] = None


class ShareChangeEventCreate(ShareChangeEventBase):
    pass


class ShareChangeEventConfirmSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal["cash_dividend", "reinvest_dividend"]
    cash_pay_date: Optional[date] = None
    share_confirm_date: Optional[date] = None


class ShareChangeEventUpdate(BaseModel):
    # status 不开放直改：状态流转走 confirm/cancel/unconfirm 端点
    ex_date: Optional[date] = None
    entitlement_date: Optional[date] = None
    cash_pay_date: Optional[date] = None
    share_confirm_date: Optional[date] = None
    shares_before: Optional[float] = None
    shares_change: Optional[float] = None
    shares_after: Optional[float] = None
    cash_change: Optional[float] = None
    div_cash: Optional[float] = None
    reinvest_nav: Optional[float] = None
    ratio: Optional[float] = None
    notes: Optional[str] = None


class ShareChangeEventResponse(ShareChangeEventBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # product_name 读侧派生（非 DB 列）：仅 list 端点批量 join 产品表填充，
    # create/get/update/confirm/unconfirm 响应恒为 None；同 positions 模式（#175/#342）
    product_name: Optional[str] = None
    # 显示码读侧派生（#705，非 DB 列）：与 product_name 同批填充（仅 list 端点）
    display_code: Optional[str] = None
    # 确认时间（#754）：DB 列已存在（确认写入、取消确认清空），from_attributes
    # 下全端点自动填充；pending/cancelled 为 null
    confirmed_at: Optional[datetime] = None
    # 红利金额（#754）：读侧派生（非 DB 列），口径与确认/预览同一单点
    # reinvest_receivable.reinvest_dividend_amount（= quantize_amount(基数份额 ×
    # 每份分红)）。confirmed cash_dividend 取落库 cash_change（确认时即同式量化，
    # 恒等且天然覆盖存量行）；confirmed reinvest_dividend 现算；其余状态/类型
    # （pending、cancelled、非分红类、confirmed 但 es 或 div_cash 为 null 的存量
    # 脏行）为 null——前端显示 --，不折 0
    dividend_amount: Optional[float] = None

    class Config:
        from_attributes = True

    @model_validator(mode="after")
    def _derive_dividend_amount(self):
        if self.status != "confirmed":
            return self
        if self.event_type == "cash_dividend":
            self.dividend_amount = self.cash_change
        elif (
            self.event_type == "reinvest_dividend"
            and self.entitlement_shares is not None
            and self.div_cash is not None
        ):
            self.dividend_amount = float(
                reinvest_dividend_amount(
                    self.entitlement_shares, self.div_cash
                )
            )
        return self


class ShareChangeEventPreviewResult(BaseModel):
    """事件确认前预览的计算结果（与真实确认共用同一计算实现，#424）。

    四个字段与确认后落库值逐一相等，例外都在 forced_adjustment：
    shares_change/cash_change 是用户直填值的回显；shares_after 恒为 null——
    确认路径刻意保留用户直填的「调整后余额」、不计算写回（见 apply_event_fields），
    预览照实回 null，不编造确认不会产生的值。

    分红明细（#744）：div_cash（每份分红）与 dividend_amount（到分的红利金额）仅
    cash_dividend / reinvest_dividend 有值；reinvest_nav 为确认将回写的解析净值、
    nav_date 为其取价日（= 除息日），仅 reinvest_dividend 有值。
    """

    event_type: str
    cash_effective_date: date
    share_effective_date: date
    entitlement_shares: Optional[float] = None
    shares_change: Optional[float] = None
    shares_after: Optional[float] = None
    cash_change: Optional[float] = None
    div_cash: Optional[float] = None
    dividend_amount: Optional[float] = None
    reinvest_nav: Optional[float] = None
    nav_date: Optional[date] = None


class ShareChangeEventPreviewResponse(BaseModel):
    """事件确认预览响应（#424）。与 trades / subscriptions 的 preview 形状对齐：
    记录字段取自列表行，计算值取自 preview。"""

    preview: ShareChangeEventPreviewResult


class PaginatedShareEventResponse(BaseModel):
    """份额变动事件列表分页响应（#342）。items 元素复用 ShareChangeEventResponse：
    product_name 仅 list 端点填充，单对象端点恒为 None（见 ShareChangeEventResponse 注释）。"""
    items: List[ShareChangeEventResponse]
    total: int
    page: int
    page_size: int
