from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models.platform import Platform
from app.schemas.platform import (
    PlatformCreate,
    PlatformUpdate,
    PlatformResponse,
    PaginatedPlatformResponse,
)
from app.dependencies import get_current_user, get_current_admin

router = APIRouter()


@router.get("", response_model=PaginatedPlatformResponse)
def get_platforms(
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(Platform)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    # issue #512：必须经响应模型收窄——此前直接 return ORM 行，全列序列化
    return PaginatedPlatformResponse(
        items=[PlatformResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=PlatformResponse)
def create_platform(
    platform: PlatformCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    db_platform = db.query(Platform).filter(Platform.code == platform.code).first()
    if db_platform:
        raise HTTPException(status_code=400, detail="Platform already exists")

    new_platform = Platform(**platform.dict())
    db.add(new_platform)
    db.commit()
    db.refresh(new_platform)
    return new_platform


@router.get("/{code}", response_model=PlatformResponse)
def get_platform(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    platform = db.query(Platform).filter(Platform.code == code).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")
    return platform


@router.put("/{code}", response_model=PlatformResponse)
def update_platform(
    code: str,
    platform: PlatformUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    db_platform = db.query(Platform).filter(Platform.code == code).first()
    if not db_platform:
        raise HTTPException(status_code=404, detail="Platform not found")

    for field, value in platform.dict(exclude_unset=True).items():
        setattr(db_platform, field, value)

    db.commit()
    db.refresh(db_platform)
    return db_platform


@router.delete("/{code}")
def delete_platform(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    platform = db.query(Platform).filter(Platform.code == code).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")

    db.delete(platform)
    db.commit()
    return {"message": "Platform deleted successfully"}
