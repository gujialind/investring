from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_current_admin
from app.schemas.dividend_config import (
    DividendConfigCreate,
    DividendConfigResponse,
    PaginatedDividendConfigResponse,
)
from app.services import dividend_config_service

router = APIRouter()


@router.get("", response_model=PaginatedDividendConfigResponse)
def list_dividend_configs(
    portfolio_code: str,
    product_code: str,
    platform_code: str,
    market: Optional[str] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    items, total = dividend_config_service.list_configs(
        db, portfolio_code=portfolio_code, product_code=product_code, market=market,
        platform_code=platform_code, page=page, page_size=page_size,
    )
    return PaginatedDividendConfigResponse(
        items=[DividendConfigResponse.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/resolve", response_model=Optional[DividendConfigResponse])
def resolve_dividend_config(
    portfolio_code: str,
    product_code: str,
    platform_code: str,
    entitlement_date: date,
    market: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """确认弹窗预选解析（A12）：按权益登记日取生效行，不滤 source；无命中返回 null。"""
    row = dividend_config_service.resolve_config(
        db, portfolio_code=portfolio_code, product_code=product_code, market=market,
        platform_code=platform_code, entitlement_date=entitlement_date,
    )
    return DividendConfigResponse.model_validate(row) if row is not None else None


@router.post("", response_model=DividendConfigResponse)
def create_dividend_config(
    payload: DividendConfigCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    row = dividend_config_service.create_config(
        db, portfolio_code=payload.portfolio_code, product_code=payload.product_code,
        market=payload.market, platform_code=payload.platform_code,
        dividend_method=payload.dividend_method, effective_from=payload.effective_from,
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{config_id}")
def delete_dividend_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    dividend_config_service.delete_config(db, config_id)
    db.commit()
    return {"message": "分红方式配置删除成功"}