from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date
from app.database import get_db
from app.dependencies import get_current_user, get_current_admin
from app.models.investor import Investor
from app.schemas.market_data import PriceDataResponse, PriceDataSyncRequest
from app.services.market_data_service import (
    get_price_records,
    get_latest_price,
    get_nav_coverage,
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
