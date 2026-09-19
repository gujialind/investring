from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models.login_log import LoginLog
from app.models.audit_log import AuditLog
from app.models.system_error_log import SystemErrorLog
from app.schemas.log import (
    LoginLogResponse,
    AuditLogResponse,
    SystemErrorLogResponse,
    PaginatedLoginLogResponse,
    PaginatedAuditLogResponse,
    PaginatedSystemErrorLogResponse,
)
from app.dependencies import get_current_admin

router = APIRouter()


def _paginated_response(query, page: int, page_size: int, item_model, response_model):
    """分页响应统一构造：显式经响应模型收窄字段，防 ORM 整行直吐（issue #512）"""
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return response_model(
        items=[item_model.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/login", response_model=PaginatedLoginLogResponse)
def get_login_logs(
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    query = db.query(LoginLog).order_by(LoginLog.created_at.desc())
    return _paginated_response(query, page, page_size, LoginLogResponse, PaginatedLoginLogResponse)


@router.get("/audit", response_model=PaginatedAuditLogResponse)
def get_audit_logs(
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    query = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    return _paginated_response(query, page, page_size, AuditLogResponse, PaginatedAuditLogResponse)


@router.get("/error", response_model=PaginatedSystemErrorLogResponse)
def get_error_logs(
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    query = db.query(SystemErrorLog).order_by(SystemErrorLog.created_at.desc())
    return _paginated_response(query, page, page_size, SystemErrorLogResponse, PaginatedSystemErrorLogResponse)
