"""#522：现金到账日期的 API/服务写入契约与 SQL 生效日。"""

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.constants.audit_actions import ACTION_CREATE, ACTION_UPDATE, RESOURCE_SHARE_CHANGE_EVENT
from app.models.audit_log import AuditLog
from app.models.share_change_event import ShareChangeEvent
from app.services.exceptions import BusinessError
from app.services.share_change_event_service import (
    create_share_change_event,
    update_share_change_event,
)
from tests.factories import create_portfolio, ensure_trading_day

ENT = date(2025, 11, 10)
EX = date(2025, 11, 12)
PAY = date(2025, 11, 15)
NON_CASH_TYPES = ["reinvest_dividend", "share_split", "share_merge", "bonus_share", "forced_adjustment"]


@pytest.fixture(params=["api", "service"])
def writer(request, client, admin_headers, test_db):
    def write(payload, *, event=None, error=None):
        if request.param == "api":
            if event is None:
                response = client.post("/api/share-change-events", json=payload, headers=admin_headers)
            else:
                response = client.put(
                    f"/api/share-change-events/{event.id}", json=payload, headers=admin_headers
                )
            if error:
                assert response.status_code == 422, response.text
                assert response.json()["detail"]["error"] == error
                return
            assert response.status_code == 200, response.text
            assert "cash_pay_date" in response.json()
            return test_db.get(ShareChangeEvent, response.json()["id"])
        values = dict(payload)
        for field in ("ex_date", "entitlement_date", "cash_pay_date"):
            if values.get(field) is not None:
                values[field] = date.fromisoformat(values[field])

        def call():
            if event is None:
                return create_share_change_event(test_db, **values)
            return update_share_change_event(test_db, event, values)

        if error:
            with pytest.raises(BusinessError) as exc:
                call()
            assert exc.value.code == error
            return
        result = call()
        test_db.flush()
        return result
    return write


@pytest.fixture
def payload(test_db):
    create_portfolio(test_db, code="PAY_DATE", status="active")
    for day in (ENT, EX, date(2025, 11, 13), date(2025, 11, 14), date(2025, 11, 17)):
        ensure_trading_day(test_db, day, is_open=True)
    ensure_trading_day(test_db, PAY, is_open=False)
    return {
        "portfolio_code": "PAY_DATE", "product_code": "510300.SH", "market": "CN_EXCHANGE",
        "platform_code": "MYCF", "event_type": "cash_dividend",
        "ex_date": EX.isoformat(), "entitlement_date": ENT.isoformat(), "div_cash": 0.5,
    }


def _state(db):
    return (
        db.execute(select(ShareChangeEvent.__table__).order_by(ShareChangeEvent.id)).all(),
        db.execute(select(AuditLog.__table__).order_by(AuditLog.id)).all(),
    )


def _audit(db, event, action):
    return db.query(AuditLog).filter_by(
        resource_type=RESOURCE_SHARE_CHANGE_EVENT, resource_id=str(event.id), action=action
    ).order_by(AuditLog.id.desc()).first()


@pytest.mark.parametrize("fields", [
    {}, {"cash_pay_date": None}, {"cash_pay_date": EX.isoformat()},
    {"cash_pay_date": PAY.isoformat()}, {"cash_pay_date": "2099-01-01"},
])
def test_create_dates_round_trip_and_audit(writer, payload, fields, test_db, client, admin_headers):
    event = writer({**payload, **fields})
    expected = date.fromisoformat(fields["cash_pay_date"]) if fields.get("cash_pay_date") else None
    test_db.refresh(event)
    assert event.cash_pay_date == expected
    assert event.cash_effective_date == (expected or EX)
    assert json.loads(_audit(test_db, event, ACTION_CREATE).new_value)["cash_pay_date"] == fields.get("cash_pay_date")
    got = client.get(f"/api/share-change-events/{event.id}", headers=admin_headers)
    assert got.status_code == 200
    assert got.json()["cash_pay_date"] == fields.get("cash_pay_date")
    listed = client.get("/api/share-change-events?portfolio_code=PAY_DATE", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["cash_pay_date"] == fields.get("cash_pay_date")


@pytest.mark.parametrize("event_type", NON_CASH_TYPES)
def test_non_cash_date_rejected_but_null_allowed(writer, payload, event_type, test_db):
    payload.update(event_type=event_type, shares_change=1, cash_change=2)
    if event_type in {"share_split", "share_merge", "bonus_share"}:
        payload.pop("platform_code")
    before = _state(test_db)
    writer({**payload, "cash_pay_date": PAY.isoformat()}, error="INVALID_PARAM")
    assert _state(test_db) == before
    event = writer({**payload, "cash_pay_date": None})
    before = _state(test_db)
    writer({"notes": "must not persist", "cash_pay_date": PAY.isoformat()}, event=event, error="INVALID_PARAM")
    assert event.notes is None
    assert _state(test_db) == before
    writer({"cash_pay_date": None}, event=event)
    assert event.cash_effective_date == EX


def test_create_date_before_ex_rejected_without_writes(writer, payload, test_db):
    before = _state(test_db)
    writer({**payload, "cash_pay_date": "2025-11-11"}, error="INVALID_DATE_ORDER")
    test_db.flush()
    assert _state(test_db) == before


def test_update_omission_clear_and_audit(writer, payload, test_db):
    event = writer({**payload, "cash_pay_date": PAY.isoformat()})
    writer({"notes": "keep date"}, event=event)
    assert event.cash_pay_date == PAY
    before = _state(test_db)
    writer({"cash_pay_date": PAY.isoformat()}, event=event)
    assert _state(test_db) == before
    writer({"cash_pay_date": None}, event=event)
    test_db.refresh(event)
    assert event.cash_pay_date is None
    assert event.cash_effective_date == EX
    audit = _audit(test_db, event, ACTION_UPDATE)
    assert json.loads(audit.old_value) == {"cash_pay_date": PAY.isoformat()}
    assert json.loads(audit.new_value) == {"cash_pay_date": None}


@pytest.mark.parametrize("updates, expected_ex, expected_pay", [
    ({"cash_pay_date": EX.isoformat()}, EX, EX),
    ({"cash_pay_date": PAY.isoformat()}, EX, PAY),
    ({"ex_date": "2025-11-17", "cash_pay_date": "2025-11-17"}, date(2025, 11, 17), date(2025, 11, 17)),
    ({"ex_date": "2025-11-17", "cash_pay_date": None}, date(2025, 11, 17), None),
])
def test_update_uses_merged_final_dates(writer, payload, updates, expected_ex, expected_pay, test_db):
    event = writer({**payload, "cash_pay_date": "2025-11-13"})
    writer(updates, event=event)
    test_db.refresh(event)
    assert event.ex_date == expected_ex
    assert event.cash_pay_date == expected_pay


@pytest.mark.parametrize("updates, error", [
    ({"cash_pay_date": "2025-11-11"}, "INVALID_DATE_ORDER"),
    ({"ex_date": "2025-11-17"}, "INVALID_DATE_ORDER"),
    ({"ex_date": "2025-11-17", "cash_pay_date": "2025-11-14"}, "INVALID_DATE_ORDER"),
    ({"entitlement_date": "2025-11-13"}, "INVALID_DATE_ORDER"),
    ({"ex_date": PAY.isoformat()}, "INVALID_EX_DATE"),
    ({"ex_date": None}, "INVALID_PARAM"),
    ({"entitlement_date": None}, "INVALID_PARAM"),
])
def test_invalid_update_is_zero_change(writer, payload, updates, error, test_db):
    event = writer({**payload, "cash_pay_date": PAY.isoformat()})
    before = _state(test_db)
    writer({"notes": "must not persist", **updates}, event=event, error=error)
    assert event.notes is None
    assert event.cash_pay_date == PAY
    assert event.ex_date == EX
    test_db.flush()
    assert _state(test_db) == before


@pytest.mark.parametrize("updates", [
    {"cash_pay_date": None}, {"cash_pay_date": "2025-11-11"},
    {"cash_pay_date": "2025-11-17"}, {"ex_date": None, "cash_pay_date": None},
])
def test_confirmed_guard_has_priority(writer, payload, updates, test_db):
    event = writer({**payload, "cash_pay_date": PAY.isoformat()})
    event.status = "confirmed"
    test_db.flush()
    before = _state(test_db)
    writer(updates, event=event, error="CANNOT_MODIFY_CONFIRMED")
    assert _state(test_db) == before


@pytest.mark.parametrize("event_type", ["cash_dividend", *NON_CASH_TYPES])
@pytest.mark.parametrize("cash_pay_date", [None, EX, PAY])
def test_hybrid_python_sql_and_aliased_window_agree(payload, test_db, event_type, cash_pay_date):
    event = ShareChangeEvent(
        **{key: value for key, value in payload.items() if key not in {"event_type", "ex_date", "entitlement_date"}},
        event_type=event_type, ex_date=EX, entitlement_date=ENT, cash_pay_date=cash_pay_date,
        event_source="manual", cash_change=Decimal("10"),
    )
    test_db.add(event)
    test_db.flush()
    expected = cash_pay_date if event_type == "cash_dividend" and cash_pay_date else EX
    assert event.cash_effective_date == expected
    assert test_db.scalar(select(ShareChangeEvent.cash_effective_date).where(ShareChangeEvent.id == event.id)) == expected
    row = aliased(ShareChangeEvent)
    in_window = test_db.scalars(select(row.id).where(
        row.id == event.id, row.cash_effective_date > EX, row.cash_effective_date <= PAY,
    )).all()
    assert in_window == ([event.id] if expected == PAY else [])
