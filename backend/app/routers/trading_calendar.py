from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_current_admin
from app.models.trading_calendar import TradingCalendar
from app.schemas.trading_calendar import (
    TradingCalendarResponse,
    TradingCalendarSyncRequest,
    TradingCalendarSyncResponse,
    TradingDayIsOpenResponse,
    TradingDayResponse,
)
from app.services import trading_utils
from app.services.exceptions import BusinessError
from app.services.trading_calendar_service import (
    sync_trading_calendar as sync_service,
    get_calendar_query,
    TushareNotConfiguredError,
    TushareAPIError,
)
from app.error_reporting import report_unexpected

router = APIRouter()

_CALENDAR_NOT_SYNCED = ("CALENDAR_NOT_SYNCED", "交易日历数据缺失，请先同步交易日历")


@router.get("/next", response_model=TradingDayResponse)
def get_next_trading_day(
    from_date: date = Query(..., description="起始日期"),
    days: int = Query(1, ge=1, le=365, description="向后第 N 个交易日"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """查询 from_date 之后第 days 个交易日"""
    # 日历不足由 helper 抛 CALENDAR_NOT_SYNCED（422 + details），不再在 router 判回退哨兵
    result = trading_utils.get_next_trading_day(db, from_date, days)
    return TradingDayResponse(from_date=from_date, trading_day=result)


@router.get("/prev", response_model=TradingDayResponse)
def get_prev_trading_day(
    from_date: date = Query(..., description="起始日期"),
    days: int = Query(1, ge=1, le=365, description="向前第 N 个交易日"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """查询 from_date 之前第 days 个交易日"""
    # 日历不足由 helper 抛 CALENDAR_NOT_SYNCED（422 + details）
    result = trading_utils.get_prev_trading_day(db, from_date, days)
    return TradingDayResponse(from_date=from_date, trading_day=result)


@router.get("/is-open", response_model=TradingDayIsOpenResponse)
def get_is_trading_day(
    query_date: date = Query(..., alias="date", description="查询日期"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """查询指定日期是否为交易日"""
    # 区分“非交易日”与“日历未同步”：无该日期记录视为日历数据缺失
    exists = (
        db.query(TradingCalendar)
        .filter(TradingCalendar.calendar_date == query_date)
        .first()
    )
    if exists is None:
        raise BusinessError(*_CALENDAR_NOT_SYNCED, http_status=422)
    return TradingDayIsOpenResponse(
        date=query_date, is_open=trading_utils.is_trading_day(db, query_date)
    )


@router.get("", response_model=List[TradingCalendarResponse])
def get_trading_calendar(
    year: Optional[int] = Query(None, description="按年份过滤"),
    start_date: Optional[date] = Query(None, description="开始日期"),
    end_date: Optional[date] = Query(None, description="结束日期"),
    is_open: Optional[bool] = Query(None, description="是否开盘"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取交易日历列表

    支持按年份、日期范围、是否开盘过滤
    """
    query = get_calendar_query(
        db=db,
        year=year,
        start_date=start_date,
        end_date=end_date,
        is_open=is_open,
    )
    return query.all()


@router.post("/sync", response_model=TradingCalendarSyncResponse)
def sync_trading_calendar(
    request: TradingCalendarSyncRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """
    同步指定年份的交易日历

    从 Tushare 获取交易日历数据并写入数据库
    """
    try:
        result = sync_service(db=db, year=request.year)
        # service 不 commit（backend/AGENTS.md「分层目录与职责」节），事务边界在 router
        db.commit()
        return TradingCalendarSyncResponse(
            synced_count=result["synced_count"],
            year=result["year"],
            message="交易日历同步成功",
        )
    except TushareNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "DATA_SOURCE_NOT_CONFIGURED", "message": str(e)},
        )
    except TushareAPIError as e:
        # 非 catch-all（不计入 #553 那 10 处），但同是「500 且零日志」形态：不出口的话
        # 上游数据源报错在服务侧查不到任何痕迹。一并接观测；响应契约不变
        report_unexpected(e, operation="sync_trading_calendar")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "SYNC_FAILED", "message": str(e)},
        )
    except Exception as e:
        report_unexpected(e, operation="sync_trading_calendar")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "SYNC_FAILED", "message": f"同步失败: {str(e)}"},
        )
