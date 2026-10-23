from pydantic import BaseModel, Field, field_serializer
from datetime import date, datetime
from typing import Optional

# exchange 入参校验单点（#777 评审 n-2）：router 查询参数与 sync 请求体共用；
# 合法值与 trading_calendar_service.SUPPORTED_EXCHANGES 同步扩展
EXCHANGE_PATTERN = "^(SSE|HKEX)$"


class TradingCalendarBase(BaseModel):
    calendar_date: date
    is_open: bool = True


class TradingCalendarCreate(TradingCalendarBase):
    pass


class TradingCalendarResponse(TradingCalendarBase):
    # 双日历后消费方需要区分行的市场归属（#777）；存量 SSE 行语义不变
    exchange: str = "SSE"
    created_at: Optional[datetime] = None

    @field_serializer("created_at")
    def serialize_created_at(self, value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.strftime("%Y-%m-%d %H:%M:%S")

    class Config:
        from_attributes = True


class TradingCalendarSyncRequest(BaseModel):
    """交易日历同步请求

    exchange 缺省 None = SSE + HKEX 两套都同步（#777，与 trading_calendar_sync
    任务口径一致）；显式传 SSE / HKEX 只同步该市场。
    """
    year: int
    # 非法市场在 schema 层 422（pydantic pattern），不落 service 的 ValueError → 500
    exchange: Optional[str] = Field(None, pattern=EXCHANGE_PATTERN)


class TradingCalendarSyncResponse(BaseModel):
    """交易日历同步响应"""
    synced_count: int
    year: int
    message: str


class TradingDayResponse(BaseModel):
    """交易日偏移查询响应（next/prev）"""
    from_date: date
    trading_day: date


class TradingDayIsOpenResponse(BaseModel):
    """是否交易日查询响应"""
    date: date
    is_open: bool
