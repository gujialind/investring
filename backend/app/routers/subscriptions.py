from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date
from decimal import Decimal
from app.database import get_db
from app.models.subscription import Subscription
from app.models.portfolio import Portfolio
from app.models.investor import Investor
from app.models.platform import Platform
from app.services.subscription_service import (
    calculate_subscription_confirm_preview,
    confirm_single_subscription,
    unconfirm_single_subscription,
    create_subscription as create_subscription_service,
    update_subscription as update_subscription_service,
    cancel_subscription as cancel_subscription_service,
    delete_subscription as delete_subscription_service,
    list_subscriptions,
)
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionResponse,
    SubscriptionPreviewResult,
    SubscriptionPreviewResponse,
)
from app.dependencies import get_current_user, get_current_admin

router = APIRouter()


@router.get("")
def get_subscriptions(
    portfolio_code: Optional[str] = None,
    investor_code: Optional[str] = None,
    status: Optional[str] = None,
    sub_type: Optional[str] = None,
    platform_code: Optional[str] = None,
    apply_date_start: Optional[date] = None,
    apply_date_end: Optional[date] = None,
    confirm_date_start: Optional[date] = None,
    confirm_date_end: Optional[date] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    # viewer 只能查看自己的记录（权限归适配层，过滤归 service）
    if current_user.role != "admin":
        investor_code = current_user.code
    items, total = list_subscriptions(
        db,
        portfolio_code=portfolio_code,
        investor_code=investor_code,
        status=status,
        sub_type=sub_type,
        platform_code=platform_code,
        apply_date_start=apply_date_start,
        apply_date_end=apply_date_end,
        confirm_date_start=confirm_date_start,
        confirm_date_end=confirm_date_end,
        page=page,
        page_size=page_size,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=SubscriptionResponse)
def create_subscription(
    subscription: SubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    new_sub = create_subscription_service(
        db,
        portfolio_code=subscription.portfolio_code,
        investor_code=subscription.investor_code,
        platform_code=subscription.platform_code,
        sub_type=subscription.sub_type,
        apply_date=subscription.apply_date,
        amount=subscription.amount,
        shares=subscription.shares,
        notes=subscription.notes,
    )
    db.commit()
    db.refresh(new_sub)
    return new_sub


# 注意：必须注册在 GET /{id} 之前，避免路径 "preview" 被 /{id} 吞掉
@router.get("/{id}/preview", response_model=SubscriptionPreviewResponse)
def preview_subscription_confirm(
    id: int,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """确认前预览：返回真实确认将写入的净值/份额/金额/确认日，不落库（与 confirm 共用计算实现）"""
    subscription = db.query(Subscription).filter(Subscription.id == id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    preview = calculate_subscription_confirm_preview(db, subscription)
    preview.pop("portfolio", None)  # ORM 对象仅供 confirm 复用，不进响应 schema
    return SubscriptionPreviewResponse(
        subscription=SubscriptionResponse.from_orm(subscription),
        preview=SubscriptionPreviewResult(**preview),
    )


@router.get("/{id}", response_model=SubscriptionResponse)
def get_subscription(
    id: int,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    subscription = db.query(Subscription).filter(Subscription.id == id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if current_user.role != "admin" and subscription.investor_code != current_user.code:
        raise HTTPException(status_code=403, detail="Permission denied")
    return subscription



@router.post("/{id}/confirm")
def confirm_subscription(
    id: int,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    subscription = db.query(Subscription).filter(Subscription.id == id).with_for_update().first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    confirm_single_subscription(db, subscription)

    db.commit()
    db.refresh(subscription)
    resp = SubscriptionResponse.from_orm(subscription)
    return {
        "message": "Subscription confirmed successfully",
        "id": resp.id,
        "portfolio_code": resp.portfolio_code,
        "sub_type": resp.sub_type,
        "amount": resp.amount,
        "shares": resp.shares,
        "unit_price": resp.unit_price,
        "status": resp.status,
        "confirm_date": resp.confirm_date,
        "subscription": resp,
    }


@router.post("/{id}/cancel")
def cancel_subscription(
    id: int,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    subscription = db.query(Subscription).filter(Subscription.id == id).with_for_update().first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    cancel_subscription_service(db, subscription)
    db.commit()
    return {"message": "Subscription cancelled successfully"}


@router.post("/{id}/unconfirm")
def unconfirm_subscription(
    id: int,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    subscription = db.query(Subscription).filter(Subscription.id == id).with_for_update().first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    unconfirm_single_subscription(db, subscription)

    db.commit()
    return {"message": "Subscription unconfirmed successfully"}


@router.put("/{id}", response_model=SubscriptionResponse)
def update_subscription(
    id: int,
    subscription: SubscriptionUpdate,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    db_subscription = db.query(Subscription).filter(Subscription.id == id).first()
    if not db_subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    # 业务校验单一实现于 service 层（issue #202：apply_date 编辑支持）
    update_subscription_service(db, db_subscription, subscription.dict(exclude_unset=True))

    db.commit()
    db.refresh(db_subscription)
    return db_subscription


@router.delete("/{id}")
def delete_subscription(
    id: int,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    subscription = db.query(Subscription).filter(Subscription.id == id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    delete_subscription_service(db, subscription)
    db.commit()
    return {"message": "Subscription deleted successfully"}
