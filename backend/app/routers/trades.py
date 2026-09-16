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
    compute_confirm_plan,
    create_trade as create_trade_service,
    update_trade as update_trade_service,
    cancel_trade as cancel_trade_service,
    unconfirm_trade as unconfirm_trade_service,
    delete_trade as delete_trade_service,
    list_trades,
    build_paired_cash_leg_map,
)

router = APIRouter()


def _fill_derived_cash_fields(
    response: TradeResponse, trade: Trade, paired_legs: dict
) -> TradeResponse:
    """填充 #493 只读派生字段（现金平台/到账日）。

    只服务基金腿：从**实际**配对 CASH 腿读取（半确认组同样可见），无现金腿时
    保持 null；CASH 腿自身不回填。`paired_legs` 由
    `build_paired_cash_leg_map` 批量产出（列表）或单笔查询产出。
    """
    if trade.product_code == "CASH" or not trade.transfer_group:
        return response
    paired = paired_legs.get((trade.portfolio_code, trade.transfer_group))
    if paired is None:
        return response
    return response.model_copy(
        update={
            "cash_platform_code": paired.platform_code,
            "cash_confirm_date": paired.confirm_date,
        }
    )


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
    # #493 派生现金信息：一次批量查询（不受本页 status/platform 筛选截断）
    paired_legs = build_paired_cash_leg_map(db, items)
    enriched = [
        _fill_derived_cash_fields(
            TradeResponse.model_validate(t).model_copy(
                update={"product_name": name_map.get((t.product_code, t.market))}
            ),
            t,
            paired_legs,
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
    return _fill_derived_cash_fields(
        TradeResponse.model_validate(new_trade), new_trade,
        build_paired_cash_leg_map(db, [new_trade]),
    )


# 注意：必须注册在 GET /{id} 之前，避免路径 "preview" 被 /{id} 吞掉
@router.get("/{id}/preview", response_model=TradePreviewResponse)
def preview_trade_confirm(
    id: int,
    confirm_date: Optional[date] = None,
    price: Optional[float] = None,
    cash_confirm_date: Optional[date] = None,
    cash_platform_code: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """确认前预览：返回真实确认将写入的净值/份额/金额与有效现金平台/日期，不落库。

    与 confirm 共用 `compute_confirm_plan`（同一组日期/平台/快照保护/配对金额
    校验），**只查询与计算**：不修改 ORM、不构腿、不写审计；`sync_nav` 不适用
    于预览（净值同步是确认端点的显式动作）。
    """
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
    preview = compute_confirm_plan(
        db, trade, product, confirm_date=confirm_date, price=price_decimal,
        cash_confirm_date=cash_confirm_date, cash_platform_code=cash_platform_code,
    )
    return TradePreviewResponse(
        trade=_fill_derived_cash_fields(
            TradeResponse.model_validate(trade), trade,
            build_paired_cash_leg_map(db, [trade]),
        ),
        preview=TradePreviewResult(
            **{
                k: v for k, v in preview.items()
                if k not in ("paired_cash_amount", "cash_leg_action")
            }
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
    return _fill_derived_cash_fields(
        TradeResponse.model_validate(trade), trade,
        build_paired_cash_leg_map(db, [trade]),
    )


@router.post("/{id}/confirm")
def confirm_trade(
    id: int,
    confirm_date: Optional[date] = None,
    price: Optional[float] = None,
    sync_nav: bool = False,
    cash_confirm_date: Optional[date] = None,
    cash_platform_code: Optional[str] = None,
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
        sync_nav=sync_nav, cash_confirm_date=cash_confirm_date,
        cash_platform_code=cash_platform_code,
    )
    db.commit()
    db.refresh(trade)
    resp = _fill_derived_cash_fields(
        TradeResponse.from_orm(trade), trade,
        build_paired_cash_leg_map(db, [trade]),
    )
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
    return _fill_derived_cash_fields(
        TradeResponse.model_validate(db_trade), db_trade,
        build_paired_cash_leg_map(db, [db_trade]),
    )


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
