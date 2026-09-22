from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_current_admin
from app.services.market_data_service import submit_price_sync_job, ConflictError
from app.models.sync_job import SyncJob
from app.models.nav_sync_detail import NavSyncDetail
from app.schemas.sync_job import PriceSyncRequest, SyncJobResponse, NavSyncDetailResponse
from typing import List

router = APIRouter()


@router.post("/price")
def submit_price_sync(
    payload: PriceSyncRequest,
    current_user=Depends(get_current_admin),
):
    try:
        params = {
            "job_type": "price_history_sync" if payload.start_date else "price_incremental_sync",
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "scope": payload.scope or "all",
            "products": payload.products or [],
            "data_source": payload.data_source,
        }
        # 任务记录的事务归投递入口自持会话（#592），本端点不注入请求会话
        job_id = submit_price_sync_job(params, triggered_by="manual")
        return {"job_id": job_id, "status": "pending", "message": "任务已提交"}
    except ConflictError:
        raise HTTPException(status_code=409, detail="已有价格同步任务在运行中")


@router.get("/{job_id}", response_model=SyncJobResponse)
def get_job_status(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("/{job_id}/details")
def get_job_details(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    details = db.query(NavSyncDetail).filter(NavSyncDetail.job_id == job_id).all()
    return {
        "job": SyncJobResponse.model_validate(job),
        "details": [NavSyncDetailResponse.model_validate(d) for d in details],
    }
