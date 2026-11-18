from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date

from app.database import get_db
from app.dependencies import get_current_user, get_current_admin
from app.models.investor import Investor
from app.schemas.ledger import (
    LedgerEntryCreate, LedgerEntryUpdate, LedgerEntryResponse,
    LedgerFlowCreate, LedgerFlowUpdate, LedgerFlowResponse, LedgerFlowWriteResponse,
    LedgerTotalsResponse,
)
from app.services import ledger_service
from app.services.ledger_profit_service import compute_ledger_totals
from app.services.null_guard import reject_explicit_nulls

router = APIRouter()


# ---- 市值台账 ledger_entry ----

@router.post("/{code}/ledger/entries", response_model=LedgerEntryResponse)
def create_entry(
    code: str, body: LedgerEntryCreate,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_admin),
):
    entry = ledger_service.create_entry(
        db, portfolio_code=code, platform_code=body.platform_code,
        value_date=body.value_date, market_value=body.market_value,
        asset_kind=body.asset_kind, note=body.note, created_by=current_user.code,
    )
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/{code}/ledger/entries", response_model=List[LedgerEntryResponse])
def list_entries(
    code: str, platform_code: Optional[str] = None,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_user),
):
    return ledger_service.list_entries(db, code, platform_code=platform_code)


@router.put("/{code}/ledger/entries/{entry_id}", response_model=LedgerEntryResponse)
def update_entry(
    code: str, entry_id: int, body: LedgerEntryUpdate,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_admin),
):
    updates = body.dict(exclude_unset=True)
    reject_explicit_nulls(updates, allow={"note"})
    entry = ledger_service.update_entry(
        db, entry_id=entry_id, portfolio_code=code,
        market_value=updates.get("market_value"),
        asset_kind=updates.get("asset_kind"),
        note=updates["note"] if "note" in updates else ledger_service.UNSET,
    )
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{code}/ledger/entries/{entry_id}")
def delete_entry(
    code: str, entry_id: int,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_admin),
):
    ledger_service.delete_entry(db, entry_id=entry_id, portfolio_code=code)
    db.commit()
    return {"message": "台账市值记录删除成功"}


# ---- 本金进出 ledger_flow ----

@router.post("/{code}/ledger/flows", response_model=LedgerFlowWriteResponse)
def create_flow(
    code: str, body: LedgerFlowCreate,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_admin),
):
    result = ledger_service.create_flow(
        db, portfolio_code=code, platform_code=body.platform_code,
        flow_date=body.flow_date, direction=body.direction, amount=body.amount,
        note=body.note, created_by=current_user.code,
    )
    db.commit()
    db.refresh(result["flow"])
    return {**LedgerFlowResponse.model_validate(result["flow"]).model_dump(), "warnings": result["warnings"]}


@router.get("/{code}/ledger/flows", response_model=List[LedgerFlowResponse])
def list_flows(
    code: str, platform_code: Optional[str] = None,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_user),
):
    return ledger_service.list_flows(db, code, platform_code=platform_code)


@router.put("/{code}/ledger/flows/{flow_id}", response_model=LedgerFlowWriteResponse)
def update_flow(
    code: str, flow_id: int, body: LedgerFlowUpdate,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_admin),
):
    updates = body.dict(exclude_unset=True)
    reject_explicit_nulls(updates, allow={"note"})
    result = ledger_service.update_flow(
        db, flow_id=flow_id, portfolio_code=code,
        flow_date=updates.get("flow_date"), direction=updates.get("direction"),
        amount=updates.get("amount"),
        note=updates["note"] if "note" in updates else ledger_service.UNSET,
    )
    db.commit()
    db.refresh(result["flow"])
    return {**LedgerFlowResponse.model_validate(result["flow"]).model_dump(), "warnings": result["warnings"]}


@router.delete("/{code}/ledger/flows/{flow_id}")
def delete_flow(
    code: str, flow_id: int,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_admin),
):
    ledger_service.delete_flow(db, flow_id=flow_id, portfolio_code=code)
    db.commit()
    return {"message": "台账进出记录删除成功"}


# ---- 收益汇总 ----

@router.get("/{code}/ledger/totals", response_model=LedgerTotalsResponse)
def get_totals(
    code: str, as_of_date: Optional[date] = None,
    db: Session = Depends(get_db), current_user: Investor = Depends(get_current_user),
):
    # S1（#911 L2）：日期参数用 Optional[date]，非法日期由 FastAPI 统一 422，不手工解析逃 500。
    # S2：kind 闸门在 compute_ledger_totals 集中校验（净值型 → 422 PORTFOLIO_KIND_NOT_ALLOWED）。
    return compute_ledger_totals(db, code, as_of_date=as_of_date)