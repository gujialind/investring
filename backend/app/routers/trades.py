from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from decimal import Decimal
from app.database import get_db
from app.models.trade import Trade
from app.models.product import Product
from app.schemas.trade import (
    TradeCreate,
    TradeUpdate,
    TradeResponse,
    PaginatedTradeResponse,
    TradePreviewResult,
    TradePreviewResponse,
)
from app.dependencies import get_current_user, get_current_admin
from app.services.product_service import build_product_name_map
from app.services.trade_service import (
    confirm_single_trade,
    calculate_confirm_preview,
    create_trade as create_trade_service,
    update_trade as update_trade_service,
    cancel_trade as cancel_trade_service,
    unconfirm_trade as unconfirm_trade_service,
    delete_trade as delete_trade_service,
    list_trades,
)

router = APIRouter()


@router.get("", response_model=PaginatedTradeResponse)
def get_trades(
    portfolio_code: Optional[str] = None,
    status: Optional[str] = None,
    trade_type: Optional[str] = None,
    product_code: Optional[str] = None,
    market: Optional[str] = None,
    products: Optional[str] = None,
    platform_code: Optional[str] = None,
    trade_date_start: Optional[date] = None,
    trade_date_end: Optional[date] = None,
    confirm_date_start: Optional[date] = None,
    confirm_date_end: Optional[date] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    items, total = list_trades(
        db,
        portfolio_code=portfolio_code,
        status=status,
        trade_type=trade_type,
        product_code=product_code,
        market=market,
        products=products,
        platform_code=platform_code,
        trade_date_start=trade_date_start,
        trade_date_end=trade_date_end,
        confirm_date_start=confirm_date_start,
        confirm_date_end=confirm_date_end,
        page=page,
        page_size=page_size,
    )
    # 读侧派生 product_name（#175）：(code, market) 双键天然覆盖 LOF 与 CASH 虚拟产品
    pairs = {(t.product_code, t.market) for t in items}
    name_map = build_product_name_map(db, pairs)
    enriched = [
        TradeResponse.model_validate(t).model_copy(
            update={"product_name": name_map.get((t.product_code, t.market))}
        )
        for t in items
    ]
    return PaginatedTradeResponse(
        items=enriched, total=total, page=page, page_size=page_size
    )


@router.post("", response_model=TradeResponse)
def create_trade(
    trade: TradeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    new_trade = create_trade_service(
        db,
        portfolio_code=trade.portfolio_code,
        product_code=trade.product_code,
        market=trade.market,
        trade_type=trade.trade_type,
        trade_date=trade.trade_date,
        amount=trade.amount,
        actual_amount=trade.actual_amount,
        fee=trade.fee,
        price=trade.price,
        shares=trade.shares,
        platform_code=trade.platform_code,
        notes=trade.notes,
        allow_duplicate=trade.allow_duplicate,
        cash_platform_code=trade.cash_platform_code,
        cash_confirm_date=trade.cash_confirm_date,
    )
    db.commit()
    db.refresh(new_trade)
    return new_trade


# 注意：必须注册在 GET /{id} 之前，避免路径 "preview" 被 /{id} 吞掉
@router.get("/{id}/preview", response_model=TradePreviewResponse)
def preview_trade_confirm(
    id: int,
    confirm_date: Optional[date] = None,
    price: Optional[float] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """确认前预览：返回真实确认将写入的净值/份额/金额，不落库（与 confirm 共用计算实现）"""
    trade = db.query(Trade).filter(Trade.id == id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "INVALID_STATUS", "message": "仅 pending 状态可预览确认结果"},
        )

    product = (
        db.query(Product)
        .filter(Product.code == trade.product_code, Product.market == trade.market)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    price_decimal = Decimal(str(price)) if price is not None else None
    preview = calculate_confirm_preview(
        db, trade, product, confirm_date=confirm_date, price=price_decimal
    )
    return TradePreviewResponse(
        trade=TradeResponse.from_orm(trade),
        preview=TradePreviewResult(
            **{k: v for k, v in preview.items() if k != "paired_cash_amount"}
        ),
        paired_cash_amount=preview["paired_cash_amount"],
    )


@router.get("/{id}", response_model=TradeResponse)
def get_trade(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    trade = db.query(Trade).filter(Trade.id == id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.post("/{id}/confirm")
def confirm_trade(
    id: int,
    confirm_date: Optional[date] = None,
    price: Optional[float] = None,
    sync_nav: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    trade = db.query(Trade).filter(Trade.id == id).with_for_update().first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "INVALID_STATUS", "message": "仅 pending 状态可确认"},
        )

    product = (
        db.query(Product)
        .filter(Product.code == trade.product_code, Product.market == trade.market)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    price_decimal = Decimal(str(price)) if price is not None else None
    confirm_single_trade(
        db, trade, product, confirm_date=confirm_date, price=price_decimal,
        sync_nav=sync_nav,
    )
    db.commit()
    db.refresh(trade)
    resp = TradeResponse.from_orm(trade)
    return {
        "message": "Trade confirmed successfully",
        "id": resp.id,
        "portfolio_code": resp.portfolio_code,
        "trade_type": resp.trade_type,
        "status": resp.status,
        "confirm_date": resp.confirm_date,
        "trade": resp,
    }


@router.post("/{id}/cancel")
def cancel_trade(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    trade = db.query(Trade).filter(Trade.id == id).with_for_update().first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    cancel_trade_service(db, trade)
    db.commit()
    return {"message": "Trade cancelled successfully"}


@router.post("/{id}/unconfirm")
def unconfirm_trade(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    trade = db.query(Trade).filter(Trade.id == id).with_for_update().first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    unconfirm_trade_service(db, trade)
    db.commit()
    return {"message": "Trade unconfirmed successfully"}


@router.put("/{id}", response_model=TradeResponse)
def update_trade(
    id: int,
    trade: TradeUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    db_trade = db.query(Trade).filter(Trade.id == id).with_for_update().first()
    if not db_trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    update_trade_service(db, db_trade, trade.dict(exclude_unset=True))
    db.commit()
    db.refresh(db_trade)
    return db_trade


@router.delete("/{id}")
def delete_trade(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    trade = db.query(Trade).filter(Trade.id == id).with_for_update().first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    delete_trade_service(db, trade)
    db.commit()
    return {"message": "Trade deleted successfully"}
