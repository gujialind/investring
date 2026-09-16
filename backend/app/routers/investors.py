from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.models.investor import Investor
from app.schemas.investor import (
    InvestorCreate,
    InvestorUpdate,
    InvestorResponse,
    PaginatedInvestorResponse,
)
from app.dependencies import get_current_admin
from app.services import investor_service

router = APIRouter()


@router.get("", response_model=PaginatedInvestorResponse)
def get_investors(
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    query = db.query(Investor)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    # issue #487：必须经响应模型收窄——此前直接 return ORM 行，全列序列化泄漏 password_hash
    return PaginatedInvestorResponse(
        items=[InvestorResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=InvestorResponse)
def create_investor(
    investor: InvestorCreate,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    # REST 恒创建 viewer（不透传 role）；如需管理员请用 CLI
    new_investor = investor_service.create_investor(
        db,
        code=investor.code,
        name=investor.name,
        password=investor.password,
        phone=investor.phone,
        email=investor.email,
    )
    db.commit()
    db.refresh(new_investor)
    return new_investor


@router.get("/{code}", response_model=InvestorResponse)
def get_investor(
    code: str,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    investor = db.query(Investor).filter(Investor.code == code).first()
    if not investor:
        raise HTTPException(status_code=404, detail="Investor not found")
    return investor


@router.put("/{code}", response_model=InvestorResponse)
def update_investor(
    code: str,
    investor: InvestorUpdate,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    updates = investor.dict(exclude_unset=True)
    db_investor = investor_service.update_investor(db, code=code, updates=updates)
    db.commit()
    db.refresh(db_investor)
    return db_investor


@router.delete("/{code}")
def delete_investor(
    code: str,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    investor_service.delete_investor(db, code)
    db.commit()
    return {"message": "Investor deleted successfully"}
