from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models.notification import Notification
from app.schemas.notification import (
    NotificationResponse,
    NotificationUpdate,
    PaginatedNotificationResponse,
)
from app.dependencies import get_current_user

router = APIRouter()


@router.get("", response_model=PaginatedNotificationResponse)
def get_notifications(
    status: Optional[str] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(Notification)
    if current_user.role != "admin":
        query = query.filter(
            (Notification.recipient == current_user.code)
            | (Notification.recipient.is_(None))
        )
    if status is not None:
        query = query.filter(Notification.status == status)
    total = query.count()
    items = query.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    # issue #512：必须经响应模型收窄——此前直接 return ORM 行，全列序列化
    return PaginatedNotificationResponse(
        items=[NotificationResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{id}/read")
def mark_notification_as_read(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    notification = db.query(Notification).filter(Notification.id == id).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if current_user.role != "admin":
        if notification.recipient is not None and notification.recipient != current_user.code:
            raise HTTPException(status_code=403, detail="Permission denied")

    notification.status = "read"
    from datetime import datetime
    notification.read_at = datetime.utcnow()
    db.commit()
    return {"message": "Notification marked as read"}


@router.post("/read-all")
def mark_all_notifications_as_read(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(Notification).filter(Notification.status != "read")
    if current_user.role != "admin":
        query = query.filter(
            (Notification.recipient == current_user.code)
            | (Notification.recipient.is_(None))
        )
    from datetime import datetime
    query.update({"status": "read", "read_at": datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return {"message": "All notifications marked as read"}
