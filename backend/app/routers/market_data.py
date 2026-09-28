from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Literal, Optional
from datetime import date
from app.database import get_db
from app.dependencies import get_current_user, get_current_admin
from app.models.investor import Investor
from app.schemas.market_data import (
    NavHistoryItem,
    NavHistoryPage,
    NavCurvePoint,
    PriceDataResponse,
    PriceDataSyncRequest,
    ProductIntervalReturns,
    ProductNavAnalysis,
)
from app.services.market_data_service import (
    get_price_records,
    get_latest_price,
    get_nav_coverage,
    get_nav_analysis,
    get_nav_history_page,
    sync_price_data,
)
from app.error_reporting import report_unexpected

router = APIRouter()


@router.get("/products/{code}/{market}/price-data", response_model=List[PriceDataResponse])
def get_price_data(
    code: str,
    market: str,
    start_date: Optional[date] = Query(None, description="开始日期"),
    end_date: Optional[date] = Query(None, description="结束日期"),
    limit: Optional[int] = Query(30, ge=1, le=1000, description="限制返回数量（默认30，最大1000，按日期降序）"),
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    try:
        records = get_price_records(db, code, market, start_date, end_date, limit)
        return [
            PriceDataResponse(
                product_code=r.product_code,
                market=r.market,
                price_date=r.price_date,
                unit_price=float(r.unit_price),
            )
            for r in records
        ]
    except HTTPException:
        raise
    except Exception as e:
        report_unexpected(e, operation="get_price_data")
        raise HTTPException(status_code=500, detail=f"查询价格数据失败: {str(e)}")


@router.get("/products/{code}/{market}/nav-history", response_model=NavHistoryPage)
def get_nav_history_endpoint(
    code: str,
    market: str,
    start_date: Optional[date] = Query(None, description="开始日期"),
    end_date: Optional[date] = Query(None, description="结束日期"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(5, ge=1, le=100, description="每页条数（首屏 5 行）"),
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    """历史净值分页（#595 §5.3）：日期降序，累计净值/日涨跌可空照传。"""
    try:
        items, total = get_nav_history_page(
            db, code, market,
            start_date=start_date, end_date=end_date,
            page=page, page_size=page_size,
        )
        return NavHistoryPage(
            items=[
                NavHistoryItem(
                    price_date=r.price_date,
                    unit_price=float(r.unit_price),
                    accumulated_nav=(
                        float(r.accumulated_nav) if r.accumulated_nav is not None else None
                    ),
                    pct_change=(
                        float(r.pct_change) if r.pct_change is not None else None
                    ),
                )
                for r in items
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
    except HTTPException:
        raise
    except Exception as e:
        report_unexpected(e, operation="get_nav_history")
        raise HTTPException(status_code=500, detail=f"查询历史净值失败: {str(e)}")


@router.get("/products/{code}/{market}/nav-analysis", response_model=ProductNavAnalysis)
def get_nav_analysis_endpoint(
    code: str,
    market: str,
    range_code: Literal["1m", "3m", "6m", "1y"] = Query(
        ..., alias="range", description="曲线区间：近1月/近3月/近6月/近1年"
    ),
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    """产品净值分析（#595 §5.3）：区间累计净值曲线 + 六窗区间收益率（同一序列同口径）。"""
    try:
        result = get_nav_analysis(db, code, market, range_code)
        return ProductNavAnalysis(
            curve=[NavCurvePoint(**p) for p in result["curve"]],
            interval_returns=ProductIntervalReturns(
                **result["interval_returns"]
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        report_unexpected(e, operation="get_nav_analysis")
        raise HTTPException(status_code=500, detail=f"查询净值分析失败: {str(e)}")


@router.get("/products/{code}/{market}/nav-coverage")
def get_nav_coverage_endpoint(
    code: str,
    market: str,
    start_date: date = Query(..., description="开始日期"),
    end_date: Optional[date] = Query(None, description="结束日期（默认今天）"),
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    """校验区间内净值同步覆盖情况"""
    return get_nav_coverage(db, code, market, start_date, end_date or date.today())


@router.post("/products/{code}/{market}/sync-price-data")
def sync_price_data_endpoint(
    code: str,
    market: str,
    request: Optional[PriceDataSyncRequest] = None,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    try:
        result = sync_price_data(
            db,
            code,
            market,
            start_date=request.start_date if request else None,
            end_date=request.end_date if request else None,
        )
        # service 不 commit（backend/AGENTS.md「分层目录与职责」节）；在 success 判断前提交，
        # 保证 success=False 时 _mark_failed 写入的失败状态仍持久化
        db.commit()
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["message"])
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        report_unexpected(e, operation="sync_price_data_endpoint")
        raise HTTPException(status_code=500, detail=f"同步价格数据失败: {str(e)}")


@router.post("/products/{code}/{market}/sync-history")
def sync_history(
    code: str,
    market: str,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    end_date = date.today()

    try:
        result = sync_price_data(db, code, market, None, end_date)
        # 同 sync_price_data_endpoint：先提交再判 success，保留失败标记
        db.commit()
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["message"])
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        report_unexpected(e, operation="sync_history")
        raise HTTPException(status_code=500, detail=f"同步历史数据失败: {str(e)}")
