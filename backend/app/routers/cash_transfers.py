"""
平台间现金转移 API

薄适配器：委托 cash_transfer_service（非对称状态模型），router 仅负责鉴权/序列化/commit。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.schemas.cash_transfer import (
    CashTransferCreate,
    CashTransferResponse,
    CashTransferListItem,
)
from app.dependencies import get_current_admin
from app.services.cash_transfer_service import (
    create_cash_transfer as create_transfer_service,
    confirm_cash_transfer as confirm_transfer_service,
    list_cash_transfers as list_transfers_service,
)


router = APIRouter()


@router.post("/{portfolio_code}/cash-transfer", response_model=CashTransferResponse)
def create_cash_transfer(
    portfolio_code: str,
    request: CashTransferCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """创建平台间现金转移（当天两腿立即 confirm；跨天时转出方当日 confirm，转入方 pending 至下一交易日确认）。"""
    result = create_transfer_service(
        db,
        portfolio_code=portfolio_code,
        from_platform=request.from_platform,
        to_platform=request.to_platform,
        amount=request.amount,
        transfer_date=request.transfer_date,
        cross_day=request.cross_day,
        notes=request.notes,
    )
    db.commit()
    return CashTransferResponse(**result)


@router.post("/{portfolio_code}/cash-transfer/{transfer_group}/confirm")
def confirm_cash_transfer(
    portfolio_code: str,
    transfer_group: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """确认跨天转移中所有仍为 pending 的 CASH legs（新模型下通常仅转入腿）。"""
    result = confirm_transfer_service(
        db, portfolio_code=portfolio_code, transfer_group=transfer_group
    )
    db.commit()
    confirm_date = result["confirm_date"]
    return {
        "message": "跨天转移确认成功",
        "transfer_group": result["transfer_group"],
        "confirmed_count": result["confirmed_count"],
        "confirm_date": confirm_date.isoformat() if confirm_date else None,
    }


@router.get("/{portfolio_code}/cash-transfers")
def list_cash_transfers(
    portfolio_code: str,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    transfer_group: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """查询平台间现金转移记录（按 transfer_group 分组，分页）。"""
    items = list_transfers_service(db, portfolio_code, transfer_group=transfer_group)
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = [CashTransferListItem(**item).model_dump() for item in items[start:end]]
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
