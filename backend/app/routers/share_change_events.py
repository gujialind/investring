from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from datetime import date
from typing import Optional
from app.database import get_db
from app.models.share_change_event import ShareChangeEvent
from app.schemas.share_change_event import (
    ShareChangeEventCreate,
    ShareChangeEventUpdate,
    ShareChangeEventResponse,
    PaginatedShareEventResponse,
)
from app.dependencies import get_current_user, get_current_admin
from app.services.exceptions import BusinessError
from app.services.product_service import build_product_name_map
from app.services.share_change_event_service import (
    create_share_change_event as create_event_service,
    update_share_change_event as update_event_service,
    confirm_share_change_event as confirm_event_service,
    cancel_share_change_event as cancel_event_service,
    unconfirm_share_change_event as unconfirm_event_service,
)

router = APIRouter()


@router.get("", response_model=PaginatedShareEventResponse)
def get_share_change_events(
    portfolio_code: Optional[str] = None,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    product_code: Optional[str] = None,
    products: Optional[str] = None,
    platform_code: Optional[str] = None,
    ex_date_start: Optional[date] = None,
    ex_date_end: Optional[date] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # 筛选形态对齐调仓列表（#126/#155/#274）：日期闭区间；
    # products 为逗号分隔 `code|market` 复合多选，与 product_code 互斥
    if ex_date_start and ex_date_end and ex_date_start > ex_date_end:
        raise BusinessError(
            "INVALID_DATE_RANGE",
            f"ex_date_start ({ex_date_start}) 不能晚于 ex_date_end ({ex_date_end})",
            http_status=422,
        )
    product_pairs: list[tuple[str, str]] = []
    if products:
        for part in products.split(","):
            part = part.strip()
            if not part:
                continue
            code, _, mkt = part.partition("|")
            if code:
                product_pairs.append((code, mkt))
    if product_pairs and product_code:
        raise BusinessError(
            "PRODUCTS_PARAM_CONFLICT",
            "products 与 product_code 互斥，不能同时传参",
            http_status=422,
        )

    query = db.query(ShareChangeEvent)
    if portfolio_code:
        query = query.filter(ShareChangeEvent.portfolio_code == portfolio_code)
    if status:
        query = query.filter(ShareChangeEvent.status == status)
    if event_type:
        query = query.filter(ShareChangeEvent.event_type == event_type)
    if product_pairs:
        query = query.filter(or_(*(
            and_(ShareChangeEvent.product_code == code, ShareChangeEvent.market == mkt)
            for code, mkt in product_pairs
        )))
    if product_code:
        query = query.filter(ShareChangeEvent.product_code == product_code)
    if platform_code:
        query = query.filter(ShareChangeEvent.platform_code == platform_code)
    if ex_date_start:
        query = query.filter(ShareChangeEvent.ex_date >= ex_date_start)
    if ex_date_end:
        query = query.filter(ShareChangeEvent.ex_date <= ex_date_end)
    total = query.count()
    items = query.order_by(ShareChangeEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    # 读侧派生 product_name（#342，同 trades #175 模式）
    pairs = {(e.product_code, e.market) for e in items}
    name_map = build_product_name_map(db, pairs)
    enriched = [
        ShareChangeEventResponse.model_validate(e).model_copy(
            update={"product_name": name_map.get((e.product_code, e.market))}
        ) for e in items
    ]
    return PaginatedShareEventResponse(items=enriched, total=total, page=page, page_size=page_size)


@router.post("", response_model=ShareChangeEventResponse)
def create_share_change_event(
    event: ShareChangeEventCreate,
    force_cover: bool = Query(False, description="平台覆盖不全时降为 warning（默认阻断）"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    new_event = create_event_service(
        db,
        portfolio_code=event.portfolio_code,
        event_type=event.event_type,
        ex_date=event.ex_date,
        entitlement_date=event.entitlement_date,
        product_code=event.product_code,
        market=event.market,
        platform_code=event.platform_code,
        entitlement_shares=event.entitlement_shares,
        shares_before=event.shares_before,
        shares_change=event.shares_change,
        shares_after=event.shares_after,
        cash_change=event.cash_change,
        cash_product_code=event.cash_product_code,
        div_cash=event.div_cash,
        reinvest_nav=event.reinvest_nav,
        ratio=event.ratio,
        event_source=event.event_source,
        tushare_event_id=event.tushare_event_id,
        notes=event.notes,
        force_cover=force_cover,
    )
    db.commit()
    db.refresh(new_event)
    return new_event


@router.get("/{id}", response_model=ShareChangeEventResponse)
def get_share_change_event(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    event = db.query(ShareChangeEvent).filter(ShareChangeEvent.id == id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Share change event not found")
    return event


@router.post("/{id}/confirm")
def confirm_share_change_event(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    event = db.query(ShareChangeEvent).filter(ShareChangeEvent.id == id).with_for_update().first()
    if not event:
        raise HTTPException(status_code=404, detail="Share change event not found")
    confirm_event_service(db, event)
    db.commit()
    db.refresh(event)
    return {"message": "Share change event confirmed successfully", "event": ShareChangeEventResponse.from_orm(event)}


@router.post("/{id}/cancel")
def cancel_share_change_event(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    event = db.query(ShareChangeEvent).filter(ShareChangeEvent.id == id).with_for_update().first()
    if not event:
        raise HTTPException(status_code=404, detail="Share change event not found")
    cancel_event_service(db, event)
    db.commit()
    return {"message": "Share change event cancelled successfully"}


@router.post("/{id}/unconfirm")
def unconfirm_share_change_event(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """取消确认份额变动事件。

    - 仅 confirmed 状态可 unconfirm，否则 422 INVALID_STATUS
    - 快照保护：ex_date 及之后已有快照则拒绝 422 SNAPSHOT_DEPENDENCY
    - 基金级父记录（platform_code 为空）：级联删除所有子记录后置 pending
    - 平台级记录（platform_code 非空）：直接置 pending
    - 子记录（parent_event_id 非空）单独 unconfirm 拒绝：422 CANNOT_UNCONFIRM_CHILD
    """
    event = db.query(ShareChangeEvent).filter(ShareChangeEvent.id == id).with_for_update().first()
    if not event:
        raise HTTPException(status_code=404, detail="Share change event not found")
    unconfirm_event_service(db, event)
    db.commit()
    db.refresh(event)
    return {"message": "Share change event unconfirmed successfully", "event": ShareChangeEventResponse.from_orm(event)}


@router.put("/{id}", response_model=ShareChangeEventResponse)
def update_share_change_event(
    id: int,
    event: ShareChangeEventUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    db_event = db.query(ShareChangeEvent).filter(ShareChangeEvent.id == id).first()
    if not db_event:
        raise HTTPException(status_code=404, detail="Share change event not found")

    # confirmed 阻断、日期重校验均在 service 单点实现（REST/CLI 共用）
    update_event_service(db, db_event, event.dict(exclude_unset=True))

    db.commit()
    db.refresh(db_event)
    return db_event


@router.delete("/{id}")
def delete_share_change_event(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    event = db.query(ShareChangeEvent).filter(ShareChangeEvent.id == id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Share change event not found")

    # 父记录：先删除所有子记录
    db.query(ShareChangeEvent).filter(
        ShareChangeEvent.parent_event_id == event.id
    ).delete(synchronize_session=False)

    db.delete(event)
    db.commit()
    return {"message": "Share change event deleted successfully"}
