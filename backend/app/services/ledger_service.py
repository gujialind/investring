"""台账型组合市值台账与本金进出读写服务（#905 PR2）。

只服务 kind='ledger' 组合；写侧要求 active（draft 拒绝并提示先激活，S3）。
与净值型现金/在途/快照口径完全隔离（D1）：本服务不被 calculate_available_cash 等消费。
D5：entry/flow 可 update/delete，每次写入经 record_audit 留痕（old/new）。
service 只抛领域异常、不 commit（可 flush）。
"""
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from sqlalchemy.orm import Session

from app.constants.audit_actions import (
    ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE,
    RESOURCE_LEDGER_ENTRY, RESOURCE_LEDGER_FLOW,
)
from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.models.platform import Platform
from app.models.portfolio import Portfolio
from app.services.audit_service import record_audit
from app.services.exceptions import BusinessError, NotFoundError
from app.utils.quantize import quantize_amount, quantize_nav

logger = logging.getLogger(__name__)

UNSET = object()
# Numeric(15,4) 列容量（11 位整数 + 4 位小数），与 manual_market_value 同口径
_MAX_LEDGER_VALUE = Decimal("99999999999.9999")
_MAX_LEDGER_ACCEPTED = Decimal("99999999999.99")
_ASSET_KINDS = ("cash", "non_cash")
_DIRECTIONS = ("inflow", "outflow")


def _get_ledger_portfolio(db: Session, code: str, *, require_active: bool = False) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.code == code).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", f"组合 {code} 不存在")
    if portfolio.kind != "ledger":
        raise BusinessError(
            "PORTFOLIO_KIND_NOT_ALLOWED",
            f"组合 {code} 非平台市值台账型，不支持台账读写",
            details={"portfolio_code": code, "kind": portfolio.kind},
        )
    if require_active and portfolio.status != "active":
        raise BusinessError(
            "PORTFOLIO_NOT_ACTIVE",
            f"组合 {code} 未激活，请先激活后再记账" if portfolio.status == "draft"
            else f"组合 {code} 非 active 状态，不可记账",
            details={"status": portfolio.status},
        )
    return portfolio


def _validate_platform(db: Session, platform_code: str) -> None:
    if not db.query(Platform).filter(Platform.code == platform_code).first():
        raise NotFoundError("PLATFORM_NOT_FOUND", f"平台 {platform_code} 不存在")


def _validate_market_value(value) -> Decimal:
    try:
        orig = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BusinessError("INVALID_AMOUNT", "市值必须是有限十进制数")
    if not orig.is_finite():
        raise BusinessError("INVALID_AMOUNT", "市值必须是有限十进制数")
    if orig < 0:
        raise BusinessError("INVALID_AMOUNT", "市值不能为负")
    if orig > _MAX_LEDGER_VALUE:
        raise BusinessError("INVALID_AMOUNT", f"市值不能超过列容量 {_MAX_LEDGER_VALUE}")
    quantized = quantize_nav(orig)
    if quantized > _MAX_LEDGER_VALUE:
        raise BusinessError(
            "INVALID_AMOUNT",
            f"市值量化到 4 位后为 {quantized}，超过列容量，可接受上限按 {_MAX_LEDGER_ACCEPTED}",
        )
    return quantized


def _validate_flow_amount(value) -> Decimal:
    try:
        orig = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BusinessError("INVALID_AMOUNT", "进出金额必须是有限十进制数")
    if not orig.is_finite():
        raise BusinessError("INVALID_AMOUNT", "进出金额必须是有限十进制数")
    quantized = quantize_amount(orig)
    if quantized is None or quantized <= 0:
        raise BusinessError("INVALID_AMOUNT", "进出金额必须大于 0")
    if quantized > _MAX_LEDGER_ACCEPTED:
        raise BusinessError("INVALID_AMOUNT", f"进出金额不能超过 {_MAX_LEDGER_ACCEPTED}")
    return quantized


def _validate_asset_kind(asset_kind: str) -> None:
    if asset_kind not in _ASSET_KINDS:
        raise BusinessError("INVALID_PARAM", "asset_kind 仅支持 cash / non_cash", details={"field": "asset_kind"})


def _validate_direction(direction: str) -> None:
    if direction not in _DIRECTIONS:
        raise BusinessError("INVALID_PARAM", "direction 仅支持 inflow / outflow", details={"field": "direction"})


def _entry_payload(entry: LedgerEntry) -> dict:
    return {
        "platform_code": entry.platform_code,
        "value_date": entry.value_date.isoformat(),
        "market_value": str(entry.market_value),
        "asset_kind": entry.asset_kind,
        "note": entry.note,
    }


def _flow_payload(flow: LedgerFlow) -> dict:
    return {
        "platform_code": flow.platform_code,
        "flow_date": flow.flow_date.isoformat(),
        "direction": flow.direction,
        "amount": str(flow.amount),
        "note": flow.note,
    }


# ---- ledger_entry ----

def create_entry(
    db: Session, *, portfolio_code: str, platform_code: str, value_date: date,
    market_value, asset_kind: str = "non_cash", note: Optional[str] = None,
    created_by: Optional[str] = None,
) -> LedgerEntry:
    _get_ledger_portfolio(db, portfolio_code, require_active=True)
    _validate_platform(db, platform_code)
    _validate_asset_kind(asset_kind)
    mv = _validate_market_value(market_value)
    existing = db.query(LedgerEntry).filter(
        LedgerEntry.portfolio_code == portfolio_code,
        LedgerEntry.platform_code == platform_code,
        LedgerEntry.value_date == value_date,
    ).first()
    if existing:
        raise BusinessError(
            "ALREADY_EXISTS",
            f"组合 {portfolio_code} 平台 {platform_code} 在 {value_date} 已有市值记录，请改用更新",
            http_status=400,
        )
    entry = LedgerEntry(
        portfolio_code=portfolio_code, platform_code=platform_code,
        value_date=value_date, market_value=mv, asset_kind=asset_kind,
        note=note, created_by=created_by,
    )
    db.add(entry)
    db.flush()
    record_audit(
        db, action=ACTION_CREATE, resource_type=RESOURCE_LEDGER_ENTRY,
        resource_id=str(entry.id), resource_name=f"{portfolio_code}/{platform_code}/{value_date}",
        new_value=_entry_payload(entry),
    )
    return entry


def update_entry(
    db: Session, *, entry_id: int, portfolio_code: str,
    market_value=None, asset_kind: Optional[str] = None, note=UNSET,
) -> LedgerEntry:
    _get_ledger_portfolio(db, portfolio_code, require_active=True)
    entry = db.query(LedgerEntry).filter(
        LedgerEntry.id == entry_id, LedgerEntry.portfolio_code == portfolio_code
    ).first()
    if not entry:
        raise NotFoundError("NOT_FOUND", "台账市值记录不存在")
    old = _entry_payload(entry)
    if market_value is not None:
        entry.market_value = _validate_market_value(market_value)
    if asset_kind is not None:
        _validate_asset_kind(asset_kind)
        entry.asset_kind = asset_kind
    if note is not UNSET:
        entry.note = note
    db.flush()
    record_audit(
        db, action=ACTION_UPDATE, resource_type=RESOURCE_LEDGER_ENTRY,
        resource_id=str(entry.id), resource_name=f"{portfolio_code}/{entry.platform_code}/{entry.value_date}",
        old_value=old, new_value=_entry_payload(entry),
    )
    return entry


def delete_entry(db: Session, *, entry_id: int, portfolio_code: str) -> None:
    _get_ledger_portfolio(db, portfolio_code, require_active=True)
    entry = db.query(LedgerEntry).filter(
        LedgerEntry.id == entry_id, LedgerEntry.portfolio_code == portfolio_code
    ).first()
    if not entry:
        raise NotFoundError("NOT_FOUND", "台账市值记录不存在")
    record_audit(
        db, action=ACTION_DELETE, resource_type=RESOURCE_LEDGER_ENTRY,
        resource_id=str(entry.id), resource_name=f"{portfolio_code}/{entry.platform_code}/{entry.value_date}",
        old_value=_entry_payload(entry),
    )
    db.delete(entry)


def list_entries(db: Session, portfolio_code: str, platform_code: Optional[str] = None) -> List[LedgerEntry]:
    _get_ledger_portfolio(db, portfolio_code)
    query = db.query(LedgerEntry).filter(LedgerEntry.portfolio_code == portfolio_code)
    if platform_code:
        query = query.filter(LedgerEntry.platform_code == platform_code)
    return query.order_by(LedgerEntry.value_date.asc(), LedgerEntry.platform_code.asc()).all()


# ---- ledger_flow ----

def _flow_warnings(db: Session, portfolio_code: str) -> List[str]:
    from app.services.ledger_profit_service import compute_ledger_totals
    return compute_ledger_totals(db, portfolio_code).get("warnings", [])


def create_flow(
    db: Session, *, portfolio_code: str, platform_code: str, flow_date: date,
    direction: str, amount, note: Optional[str] = None, created_by: Optional[str] = None,
) -> dict:
    _get_ledger_portfolio(db, portfolio_code, require_active=True)
    _validate_platform(db, platform_code)
    _validate_direction(direction)
    amt = _validate_flow_amount(amount)
    flow = LedgerFlow(
        portfolio_code=portfolio_code, platform_code=platform_code,
        flow_date=flow_date, direction=direction, amount=amt,
        note=note, created_by=created_by,
    )
    db.add(flow)
    db.flush()
    record_audit(
        db, action=ACTION_CREATE, resource_type=RESOURCE_LEDGER_FLOW,
        resource_id=str(flow.id), resource_name=f"{portfolio_code}/{platform_code}/{flow_date}",
        new_value=_flow_payload(flow),
    )
    return {"flow": flow, "warnings": _flow_warnings(db, portfolio_code)}


def update_flow(
    db: Session, *, flow_id: int, portfolio_code: str,
    flow_date: Optional[date] = None, direction: Optional[str] = None,
    amount=None, note=UNSET,
) -> dict:
    _get_ledger_portfolio(db, portfolio_code, require_active=True)
    flow = db.query(LedgerFlow).filter(
        LedgerFlow.id == flow_id, LedgerFlow.portfolio_code == portfolio_code
    ).first()
    if not flow:
        raise NotFoundError("NOT_FOUND", "台账进出记录不存在")
    old = _flow_payload(flow)
    if flow_date is not None:
        flow.flow_date = flow_date
    if direction is not None:
        _validate_direction(direction)
        flow.direction = direction
    if amount is not None:
        flow.amount = _validate_flow_amount(amount)
    if note is not UNSET:
        flow.note = note
    db.flush()
    record_audit(
        db, action=ACTION_UPDATE, resource_type=RESOURCE_LEDGER_FLOW,
        resource_id=str(flow.id), resource_name=f"{portfolio_code}/{flow.platform_code}/{flow.flow_date}",
        old_value=old, new_value=_flow_payload(flow),
    )
    return {"flow": flow, "warnings": _flow_warnings(db, portfolio_code)}


def delete_flow(db: Session, *, flow_id: int, portfolio_code: str) -> None:
    _get_ledger_portfolio(db, portfolio_code, require_active=True)
    flow = db.query(LedgerFlow).filter(
        LedgerFlow.id == flow_id, LedgerFlow.portfolio_code == portfolio_code
    ).first()
    if not flow:
        raise NotFoundError("NOT_FOUND", "台账进出记录不存在")
    record_audit(
        db, action=ACTION_DELETE, resource_type=RESOURCE_LEDGER_FLOW,
        resource_id=str(flow.id), resource_name=f"{portfolio_code}/{flow.platform_code}/{flow.flow_date}",
        old_value=_flow_payload(flow),
    )
    db.delete(flow)


def list_flows(db: Session, portfolio_code: str, platform_code: Optional[str] = None) -> List[LedgerFlow]:
    _get_ledger_portfolio(db, portfolio_code)
    query = db.query(LedgerFlow).filter(LedgerFlow.portfolio_code == portfolio_code)
    if platform_code:
        query = query.filter(LedgerFlow.platform_code == platform_code)
    return query.order_by(LedgerFlow.flow_date.asc(), LedgerFlow.id.asc()).all()