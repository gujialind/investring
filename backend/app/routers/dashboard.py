from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.investor import Investor
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard_service

router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    """只读仪表盘汇总；事务边界不在此提交。"""
    return dashboard_service.get_dashboard_summary(db)
