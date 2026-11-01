"""#775：待定分红逐笔选择、原子确认与完整双层账本回归。

经济基线沿用 dividend_transit 的三条交易腿 + 申购 + 三类快照，不用孤立持仓
冒充完整账本。1000 份 @2.1，红利 0.1，除息净值 2：现金 100 或再投 50 份。
"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, dataclass
from datetime import date
from decimal import Decimal
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event as sqlalchemy_event, inspect
from sqlalchemy.orm import Session

from app.constants.share_change_events import CAPITAL_FREE_SHARE_EVENT_TYPES
from app.models import (
    InvestorHolding, Platform, Portfolio, PortfolioPosition, PortfolioValueSnapshot,
    PriceRecord, Product, ShareChangeEvent, Subscription, Trade,
)
from app.models.audit_log import AuditLog
from app.services import share_change_event_service as service
from app.services.exceptions import BusinessError
from app.services.position_service import calculate_available_cash, compute_cash_balance
from app.services.reinvest_receivable import aggregate_receivable_flows, list_reinvest_receivables
from app.services.snapshot_service import (
    _check_share_change_events, _compute_in_transit_amounts,
    auto_confirm_after_snapshot, generate_daily_snapshots,
)
from app.services.task_runner import (
    _derive_log_fields, _detect_dividends, _generate_snapshots_for_date,
)
from tests.factories import (
    create_investor_holding, create_platform, create_portfolio, create_position_snapshot,
    create_price_record, create_product, create_share_change_event as create_raw_event,
    create_subscription, create_trade, create_value_snapshot,
)
from tests.integration.calendar_helpers import mirror_hkex_calendar, remove_hkex_calendar

BASE = date(2025, 6, 5)
ENT = date(2025, 6, 6)
EX = date(2025, 6, 9)
MID = date(2025, 6, 10)
APPLY = date(2025, 6, 11)
WEEKEND = date(2025, 6, 14)
PENDING = "dividend_pending"
CASH = "cash_dividend"
REINVEST = "reinvest_dividend"
METHODS = [CASH, REINVEST]
CALCULATED = ("entitlement_shares", "shares_before", "shares_change", "shares_after", "cash_change")


@dataclass(frozen=True)
class Ledger:
    port: str = "DM775"
    plat: str = "DMP775"
    fund: str = "DMF775"
    market: str = "CN_OTC"


def _seed_ledger(db, env=Ledger()):
    create_portfolio(db, code=env.port, status="active")
    create_platform(db, code=env.plat)
    create_product(db, code=env.fund, market=env.market, product_type="OEF", confirm_days=0)
    create_position_snapshot(
        db, env.port, env.fund, env.market, snapshot_date=BASE, platform_code=env.plat,
        shares=1000, unit_price=2.1, cost_price=2.1, market_value=2100,
    )
    create_position_snapshot(
        db, env.port, "CASH", "", snapshot_date=BASE, platform_code=env.plat,
        cash_amount=0, market_value=0,
    )
    create_value_snapshot(db, env.port, BASE, total_value=2100, total_shares=2100, unit_price=1)
    create_investor_holding(db, env.port, "VIEWER", BASE, shares=2100)
    create_subscription(
        db, env.port, "VIEWER", amount=2100, shares=2100, unit_price=1,
        platform_code=env.plat, apply_date=date(2025, 6, 4), confirm_date=BASE,
        status="confirmed",
    )
    for product, market, direction, group in (
        ("CASH", "", "buy", f"sub_{env.port}"),
        ("CASH", "", "sell", f"rebal_{env.port}"),
        (env.fund, env.market, "buy", f"rebal_{env.port}"),
    ):
        create_trade(
            db, env.port, product, market, trade_type=direction, platform_code=env.plat,
            trade_date=BASE, confirm_date=BASE, status="confirmed", amount=2100,
            actual_amount=2100, price=2.1 if product == env.fund else 1,
            shares=1000 if product == env.fund else None, transfer_group=group,
        )
    for day in (BASE, ENT, EX, MID, APPLY):
        create_price_record(db, env.fund, env.market, day, 2.1 if day < EX else 2)
    db.commit()
    return env


@pytest.fixture
def ledger(test_db):
    return _seed_ledger(test_db)


@pytest.fixture
def base(test_db, ledger):
    generate_daily_snapshots(test_db, ledger.port, ENT)
    test_db.commit()
    return ledger


def _event(db, env, **overrides):
    args = dict(
        portfolio_code=env.port, product_code=env.fund, market=env.market,
        platform_code=env.plat, event_type=PENDING, entitlement_date=ENT, ex_date=EX,
        div_cash=Decimal("0.1"),
    )
    args.update(overrides)
    event = service.create_share_change_event(db, **args)
    db.commit()
    return event


def _raw(db, env, **overrides):
    args = dict(
        portfolio_code=env.port, product_code=env.fund, market=env.market,
        platform_code=env.plat, event_type=PENDING, entitlement_date=ENT, ex_date=EX,
        div_cash=Decimal("0.1"),
    )
    args.update(overrides)
    return create_raw_event(db, **args)


def _body(env, **overrides):
    body = dict(
        portfolio_code=env.port, product_code=env.fund, market=env.market,
        platform_code=env.plat, event_type=PENDING, entitlement_date=ENT.isoformat(),
        ex_date=EX.isoformat(), div_cash=0.1,
    )
    body.update(overrides)
    return body


def _state(event):
    return {column.key: getattr(event, column.key) for column in ShareChangeEvent.__table__.columns}


def _audits(db, event_id, action=None):
    query = db.query(AuditLog).filter_by(resource_type="share_change_event", resource_id=str(event_id))
    return query.filter_by(action=action).all() if action else query.all()


def _error(response, code=None):
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    if code is not None:
        assert detail["error"] == code, detail
    return detail


def _request(client, headers, event_id, operation, selection=None):
    url = f"/api/share-change-events/{event_id}/{operation}"
    if operation == "preview":
        return client.get(url, headers=headers, params=selection)
    if selection is None:
        return client.post(url, headers=headers)
    return client.post(url, headers=headers, json=selection)


def _generate(client, headers, env, day):
    response = client.post(
        "/api/snapshots/generate", headers=headers,
        json={"portfolio_code": env.port, "target_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True, response.text


def _assert_economics(db, env, day, method, effective):
    rows = {
        row.product_code: row for row in db.query(PortfolioPosition).filter_by(
            portfolio_code=env.port, snapshot_date=day, platform_code=env.plat,
        )
    }
    arrived = day >= effective
    cash = Decimal("100") if method == CASH and arrived else Decimal("0")
    shares = Decimal("1050") if method == REINVEST and arrived else Decimal("1000")
    fund = rows[env.fund]
    assert fund.shares == shares
    assert fund.cost_price == (Decimal("2") if shares == 1050 else Decimal("2.1"))
    assert fund.shares * fund.cost_price == Decimal("2100")
    assert fund.market_value == shares * Decimal("2")
    assert rows["CASH"].cash_amount == cash
    assert calculate_available_cash(db, env.port, env.plat, day) == cash
    assert compute_cash_balance(db, env.port, env.plat, day) == cash
    if arrived:
        assert "IN_TRANSIT_DIVIDEND" not in rows
    else:
        assert rows["IN_TRANSIT_DIVIDEND"].cash_amount == Decimal("100")
        assert rows["IN_TRANSIT_DIVIDEND"].shares is None
        assert rows["IN_TRANSIT_DIVIDEND"].frozen_amount == 0
    value = db.query(PortfolioValueSnapshot).filter_by(portfolio_code=env.port, snapshot_date=day).one()
    assert value.total_value == value.total_shares == Decimal("2100")
    assert value.unit_price == Decimal("1")
    assert value.in_transit_total == (Decimal("0") if arrived else Decimal("100"))
    holding = db.query(InvestorHolding).filter_by(portfolio_code=env.port, snapshot_date=day).one()
    assert holding.investor_code == "VIEWER"
    assert holding.shares == Decimal("2100")
    assert holding.cost_per_share == Decimal("1")
    assert holding.total_cost == holding.market_value == Decimal("2100")
    assert holding.profit == 0


def test_selection_is_frozen_and_pending_dates_match_sql(test_db, base):
    selection = service.DividendSelection(CASH)
    with pytest.raises(FrozenInstanceError):
        selection.cash_pay_date = APPLY
    event = _event(test_db, base)
    assert event.share_effective_date == event.cash_effective_date == EX
    dates = test_db.query(ShareChangeEvent.share_effective_date, ShareChangeEvent.cash_effective_date).filter_by(
        id=event.id,
    ).one()
    assert tuple(dates) == (EX, EX)
    assert PENDING not in CAPITAL_FREE_SHARE_EVENT_TYPES


def test_pure_calculation_requires_method():
    with pytest.raises(BusinessError) as exc:
        service.compute_event_fields(PENDING, Decimal("1000"), div_cash=Decimal("0.1"))
    assert exc.value.code == "DIVIDEND_METHOD_REQUIRED"


@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_service_without_selection_rejects_without_mutation(test_db, base, operation):
    event = _event(test_db, base)
    before, audits = _state(event), len(_audits(test_db, event.id))
    call = service.compute_share_change_event_preview if operation == "preview" else service.confirm_share_change_event
    with pytest.raises(BusinessError) as exc:
        call(test_db, event)
    assert exc.value.code == "DIVIDEND_METHOD_REQUIRED"
    assert "选择" in exc.value.message
    assert _state(event) == before
    assert not test_db.dirty
    assert len(_audits(test_db, event.id)) == audits


@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_http_without_selection_rejects(client, admin_headers, test_db, base, operation):
    event = _event(test_db, base)
    event_id = event.id
    detail = _error(_request(client, admin_headers, event_id, operation), "DIVIDEND_METHOD_REQUIRED")
    assert "选择" in detail["message"]
    assert _audits(test_db, event_id, "confirm") == []
    assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=base.port, status="confirmed").count() == 0
    assert test_db.query(ShareChangeEvent).filter_by(parent_event_id=event_id).count() == 0


def test_gate_message_caps_undecided_ids_in_id_order(test_db, ledger):
    """积压超过 10 笔时消息只按 id 序列出前 10 笔并附总数（#782 复审 Nit）。

    error_message 有 1000 字符上限，截断抗性依赖「前 10 + 共 M 笔」；定序让
    重复运行提示的 ID 子集可复现，完整清单交给事件列表页筛选。
    """
    events = [_raw(test_db, ledger) for _ in range(11)]
    blocked = _check_share_change_events(test_db, ledger.port, EX)
    assert blocked["status"] == "failed"
    message = blocked["message"]
    assert "（共 11 笔）" in message
    for event in events[:10]:
        assert f"#{event.id}" in message
    assert f"#{events[10].id}" not in message


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("delayed", [False, True], ids=["ex-date", "later-date"])
def test_api_choice_resumes_snapshots_and_recalculation_is_idempotent(
    client, admin_headers, test_db, ledger, method, delayed,
):
    response = client.post("/api/share-change-events", headers=admin_headers, json=_body(ledger))
    assert response.status_code == 200, response.text
    event_id = response.json()["id"]
    assert response.json()["event_type"] == PENDING
    assert all(response.json()[field] is None for field in CALCULATED)
    assert _check_share_change_events(test_db, ledger.port, ENT)["status"] == "passed"
    _generate(client, admin_headers, ledger, ENT)
    blocked = _check_share_change_events(test_db, ledger.port, EX)
    assert blocked["status"] == "failed"
    assert f"#{event_id}" in blocked["message"] and "先选择分红方式并确认" in blocked["message"]
    assert list_reinvest_receivables(test_db, ledger.port, EX) == []
    assert aggregate_receivable_flows(test_db, [ledger.port], APPLY) == {}
    assert _compute_in_transit_amounts(test_db, ledger.port, EX) == {}
    assert calculate_available_cash(test_db, ledger.port, ledger.plat, EX) == 0

    selection = {"event_type": method}
    date_field = "cash_pay_date" if method == CASH else "share_confirm_date"
    effective = APPLY if delayed else EX
    if delayed:
        selection[date_field] = APPLY.isoformat()
    response = _request(client, admin_headers, event_id, "preview", selection)
    assert response.status_code == 200, response.text
    preview = response.json()["preview"]
    assert preview["event_type"] == method
    assert preview["entitlement_shares"] == 1000
    assert preview["dividend_amount"] == 100
    assert preview["shares_change"] == (0 if method == CASH else 50)
    assert preview["cash_change"] == (100 if method == CASH else 0)
    assert preview["cash_effective_date"] == (effective if method == CASH else EX).isoformat()
    assert preview["share_effective_date"] == (effective if method == REINVEST else EX).isoformat()
    test_db.expire_all()
    event = test_db.get(ShareChangeEvent, event_id)
    assert event.event_type == PENDING and event.status == "pending"
    assert all(getattr(event, field) is None for field in CALCULATED)
    assert _audits(test_db, event_id, "confirm") == []

    response = _request(client, admin_headers, event_id, "confirm", selection)
    assert response.status_code == 200, response.text
    confirmed = response.json()["event"]
    for field in ("event_type", "entitlement_shares", "shares_change", "shares_after", "cash_change", "reinvest_nav"):
        assert confirmed[field] == preview[field]
    audit = _audits(test_db, event_id, "confirm")
    assert len(audit) == 1
    old, new = json.loads(audit[0].old_value), json.loads(audit[0].new_value)
    assert old["event_type"] == PENDING and new["event_type"] == method
    assert old["status"] == "pending" and new["status"] == "confirmed"
    if delayed:
        assert old[date_field] is None and new[date_field] == APPLY.isoformat()

    before = _state(event)
    with pytest.raises(BusinessError) as exc:
        service.confirm_share_change_event(test_db, event, selection=service.DividendSelection(method))
    assert exc.value.code == "INVALID_STATUS"
    assert _state(event) == before
    assert len(_audits(test_db, event_id, "confirm")) == 1
    assert test_db.query(Trade).filter_by(portfolio_code=ledger.port).count() == 3
    for day in (EX, MID, APPLY):
        _generate(client, admin_headers, ledger, day)
        _assert_economics(test_db, ledger, day, method, effective)
    for _ in range(2):
        response = client.post(
            "/api/snapshots/recalculate", headers=admin_headers,
            json={"portfolio_code": ledger.port, "start_date": EX.isoformat(), "end_date": APPLY.isoformat()},
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True, response.text
        assert all(not result["errors"] for result in response.json()["results"]), response.text
        for day in (EX, MID, APPLY):
            _assert_economics(test_db, ledger, day, method, effective)
    assert len(_audits(test_db, event_id, "confirm")) == 1


@pytest.mark.parametrize("method", METHODS)
def test_real_detector_to_confirmation_preserves_selection_on_redetection(
    client, admin_headers, test_db, base, method,
):
    product = test_db.query(Product).filter_by(code=base.fund, market=base.market).one()
    announcement = {
        "record_date": ENT.strftime("%Y%m%d"), "ex_date": EX.strftime("%Y%m%d"),
        "div_cash": 0.1, "div_proc": "实施",
    }
    with patch("app.services.tushare_client.get_fund_div", return_value=[announcement]) as source:
        assert _detect_dividends(test_db, [product]) == (1, [])
        source.assert_called_once_with(f"{base.fund}.OF")
        event = test_db.query(ShareChangeEvent).filter_by(portfolio_code=base.port).one()
        assert (event.event_type, event.status, event.event_source) == (PENDING, "pending", "tushare")
        assert _detect_dividends(test_db, [product]) == (0, [])
        assert auto_confirm_after_snapshot(test_db, base.port, ENT)[0]["code"] == "DIVIDEND_METHOD_REQUIRED"
        response = _request(client, admin_headers, event.id, "confirm", {"event_type": method})
        assert response.status_code == 200, response.text
        before = _state(event)
        assert _detect_dividends(test_db, [product]) == (0, [])
        assert _state(event) == before
        assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=base.port).count() == 1
    _generate(client, admin_headers, base, EX)
    _assert_economics(test_db, base, EX, method, EX)


@pytest.mark.parametrize("method", METHODS)
def test_repeated_preview_uses_transient_candidates_without_flush_or_audit(test_db, base, monkeypatch, method):
    event = _event(test_db, base)
    before, audit_count = _state(event), len(_audits(test_db, event.id))
    candidates = []
    real_resolve = service.resolve_entitlement_shares

    def inspect_candidate(db, candidate):
        assert candidate is not event
        assert inspect(candidate).transient and inspect(candidate).session is None
        candidates.append(candidate)
        return real_resolve(db, candidate)

    monkeypatch.setattr(service, "resolve_entitlement_shares", inspect_candidate)
    # 与生产 SessionLocal 一样关闭查询隐式 autoflush；显式 flush 仍被下方桩禁止。
    monkeypatch.setattr(test_db, "autoflush", False)
    with patch.object(test_db, "flush", side_effect=AssertionError("preview must not flush")):
        previews = [
            service.compute_share_change_event_preview(test_db, event, selection=service.DividendSelection(method))
            for _ in range(3)
        ]
        assert previews[0] == previews[1] == previews[2]
        assert _state(event) == before
        assert not test_db.dirty and not test_db.new and not test_db.deleted
        assert len(_audits(test_db, event.id)) == audit_count
    assert len({id(candidate) for candidate in candidates}) == 3
    test_db.expire_all()
    assert _state(test_db.get(ShareChangeEvent, before["id"])) == before


@pytest.mark.parametrize("initial,target", [(CASH, CASH), (CASH, REINVEST), (REINVEST, CASH), (REINVEST, REINVEST)])
@pytest.mark.parametrize("explicit_null", [False, True], ids=["omitted", "null"])
def test_selection_is_complete_candidate_not_patch(client, admin_headers, test_db, base, initial, target, explicit_null):
    event = _event(
        test_db, base, event_type=initial,
        cash_pay_date=APPLY if initial == CASH else None,
        share_confirm_date=APPLY if initial == REINVEST else None,
        reinvest_nav=Decimal("2") if initial == REINVEST else Decimal("99"),
    )
    body = {"event_type": target}
    if explicit_null:
        body.update(cash_pay_date=None, share_confirm_date=None)
    response = _request(client, admin_headers, event.id, "preview", {"event_type": target})
    assert response.status_code == 200, response.text
    preview = response.json()["preview"]
    assert preview["cash_effective_date"] == preview["share_effective_date"] == EX.isoformat()
    response = _request(client, admin_headers, event.id, "confirm", body)
    assert response.status_code == 200, response.text
    row = response.json()["event"]
    assert row["event_type"] == target
    assert row["cash_pay_date"] is None and row["share_confirm_date"] is None
    assert row["reinvest_nav"] == (None if target == CASH else 2)
    assert row["shares_change"] == preview["shares_change"] == (0 if target == CASH else 50)
    assert row["cash_change"] == preview["cash_change"] == (100 if target == CASH else 0)
    audit = _audits(test_db, event.id, "confirm")[0]
    old, new = json.loads(audit.old_value), json.loads(audit.new_value)
    date_field = "cash_pay_date" if initial == CASH else "share_confirm_date"
    assert old[date_field] == APPLY.isoformat() and new[date_field] is None
    if initial != target:
        assert old["event_type"] == initial and new["event_type"] == target


@pytest.mark.parametrize("method", METHODS)
def test_no_body_legacy_confirmation_keeps_dates_and_null_amount(client, admin_headers, test_db, base, method):
    event = _event(
        test_db, base, event_type=method, div_cash=None,
        cash_pay_date=APPLY if method == CASH else None,
        share_confirm_date=APPLY if method == REINVEST else None,
    )
    response = _request(client, admin_headers, event.id, "confirm")
    assert response.status_code == 200, response.text
    row = response.json()["event"]
    assert row["event_type"] == method
    assert row["cash_pay_date" if method == CASH else "share_confirm_date"] == APPLY.isoformat()
    assert row["shares_change"] == row["cash_change"] == 0
    assert row["div_cash"] is None


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("amount", [None, Decimal("0"), Decimal("-0.1")], ids=["null", "zero", "negative"])
def test_pending_amount_must_be_positive_before_selection(test_db, base, method, amount):
    event = _raw(test_db, base, div_cash=amount)
    before = _state(event)
    for call in (service.compute_share_change_event_preview, service.confirm_share_change_event):
        with pytest.raises(BusinessError) as exc:
            call(test_db, event, selection=service.DividendSelection(method))
        assert exc.value.code == "INVALID_PARAM"
        assert exc.value.details["field"] == "div_cash"
        assert _state(event) == before
        assert not test_db.dirty
    assert _audits(test_db, event.id, "confirm") == []


@pytest.mark.parametrize("method", METHODS)
def test_positive_subcent_amount_is_not_a_new_result_side_rejection(test_db, base, method):
    event = _event(test_db, base, div_cash=Decimal("0.000001"))
    service.confirm_share_change_event(test_db, event, selection=service.DividendSelection(method))
    assert event.status == "confirmed"
    assert event.shares_change == event.cash_change == 0


@pytest.mark.parametrize("operation", ["preview", "confirm"])
@pytest.mark.parametrize("overrides,selection,code", [
    ({"status": "confirmed"}, {"event_type": CASH}, "INVALID_STATUS"),
    ({"status": "cancelled"}, {"event_type": CASH}, "INVALID_STATUS"),
    ({"event_type": "share_split"}, {"event_type": CASH}, "INVALID_PARAM"),
    ({"event_type": "share_merge"}, {"event_type": CASH}, "INVALID_PARAM"),
    ({"event_type": "bonus_share"}, {"event_type": REINVEST}, "INVALID_PARAM"),
    ({"event_type": "forced_adjustment", "cash_change": Decimal("1")}, {"event_type": CASH}, "INVALID_PARAM"),
    ({"platform_code": None}, {"event_type": CASH}, "INVALID_PARAM"),
    ({}, {"event_type": PENDING}, "INVALID_PARAM"),
    ({}, {"event_type": "share_split"}, "INVALID_PARAM"),
    ({}, {"event_type": CASH, "cash_pay_date": ENT}, "INVALID_DATE_ORDER"),
    ({}, {"event_type": REINVEST, "share_confirm_date": EX}, "INVALID_DATE_ORDER"),
    ({}, {"event_type": REINVEST, "share_confirm_date": ENT}, "INVALID_DATE_ORDER"),
    ({}, {"event_type": REINVEST, "share_confirm_date": WEEKEND}, "INVALID_SHARE_CONFIRM_DATE"),
    ({}, {"event_type": CASH, "share_confirm_date": APPLY}, "INVALID_PARAM"),
    ({}, {"event_type": REINVEST, "cash_pay_date": APPLY}, "INVALID_PARAM"),
    ({"event_type": REINVEST, "entitlement_date": EX, "share_confirm_date": APPLY}, {"event_type": CASH}, "INVALID_DATE_ORDER"),
    ({"event_type": REINVEST, "reinvest_nav": Decimal("1.9")}, {"event_type": REINVEST}, "PRICE_NAV_MISMATCH"),
    ({"event_type": REINVEST, "reinvest_nav": Decimal("0")}, {"event_type": REINVEST}, "INVALID_PARAM"),
])
def test_selection_validation_is_shared_and_atomic(test_db, base, operation, overrides, selection, code):
    event = _raw(test_db, base, **overrides)
    before = _state(event)
    call = service.compute_share_change_event_preview if operation == "preview" else service.confirm_share_change_event
    with pytest.raises(BusinessError) as exc:
        call(test_db, event, selection=service.DividendSelection(**selection))
    assert exc.value.code == code
    assert _state(event) == before
    assert not test_db.dirty
    assert _audits(test_db, event.id, "confirm") == []


def test_child_cannot_select_a_dividend_method(test_db, base):
    parent = _raw(test_db, base, event_type="share_split", platform_code=None)
    child = _raw(test_db, base, event_type=REINVEST, parent_event_id=parent.id)
    before = _state(child)
    for call in (service.compute_share_change_event_preview, service.confirm_share_change_event):
        with pytest.raises(BusinessError) as exc:
            call(test_db, child, selection=service.DividendSelection(CASH))
        assert exc.value.code == "INVALID_PARAM"
    assert _state(child) == before
    assert _audits(test_db, child.id, "confirm") == []


@pytest.mark.parametrize("market", ["CN_OTC", "HK_MUTUAL", "CN_EXCHANGE"])
@pytest.mark.parametrize("method", METHODS)
def test_selection_respects_market(test_db, base, market, method):
    if market != base.market:
        create_product(test_db, code=base.fund, market=market, product_type="LOF")
        create_position_snapshot(
            test_db, base.port, base.fund, market, snapshot_date=ENT, platform_code=base.plat,
            shares=1000, unit_price=2.1, cost_price=2.1, market_value=2100,
        )
        create_price_record(test_db, base.fund, market, EX, 2)
    # #784：HK_MUTUAL 的日期校验走产品市场日历（HKEX）——镜像 ENT..APPLY 的
    # SSE 开闭形态生成 HKEX 行，**用后必须删除**（calendar_helpers 共享库契约）。
    if market == "HK_MUTUAL":
        mirror_hkex_calendar(test_db, ENT, APPLY)
    try:
        event = _event(test_db, base, market=market)
        before = _state(event)
        selection = service.DividendSelection(method)
        if market == "CN_EXCHANGE" and method == REINVEST:
            for call in (service.compute_share_change_event_preview, service.confirm_share_change_event):
                with pytest.raises(BusinessError) as exc:
                    call(test_db, event, selection=selection)
                assert exc.value.code == "INVALID_PARAM"
                assert "场内" in exc.value.message
            assert _state(event) == before
        else:
            preview = service.compute_share_change_event_preview(test_db, event, selection=selection)
            service.confirm_share_change_event(test_db, event, selection=selection)
            assert preview["entitlement_shares"] == event.entitlement_shares == 1000
            assert event.shares_change == (0 if method == CASH else 50)
    finally:
        if market == "HK_MUTUAL":
            remove_hkex_calendar(test_db, ENT, APPLY)


@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_missing_nav_leaves_original_type_dates_and_derived_fields(test_db, base, operation):
    event = _event(test_db, base, event_type=CASH, cash_pay_date=APPLY)
    test_db.query(PriceRecord).filter_by(product_code=base.fund, market=base.market, price_date=EX).delete()
    test_db.commit()
    before = _state(event)
    call = service.compute_share_change_event_preview if operation == "preview" else service.confirm_share_change_event
    with pytest.raises(BusinessError) as exc:
        call(test_db, event, selection=service.DividendSelection(REINVEST, share_confirm_date=APPLY))
    assert exc.value.code == "MISSING_NAV"
    assert _state(event) == before
    assert not test_db.dirty
    assert _audits(test_db, event.id, "confirm") == []


@pytest.mark.parametrize("overrides", [
    {"platform_code": None}, {"platform_code": ""},
    {"cash_pay_date": APPLY.isoformat()}, {"share_confirm_date": APPLY.isoformat()},
    {"reinvest_nav": 2}, {"ratio": 2}, {"entitlement_shares": 1000},
    {"shares_before": 1000}, {"shares_change": 50}, {"shares_after": 1050},
    {"cash_change": 100}, {"cash_product_code": "CASH"},
])
def test_pending_create_rejects_missing_platform_or_method_fields(client, admin_headers, test_db, base, overrides):
    response = client.post("/api/share-change-events", headers=admin_headers, json=_body(base, **overrides))
    code = "PLATFORM_REQUIRED" if "platform_code" in overrides else "INVALID_PARAM"
    _error(response, code)
    assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=base.port).count() == 0
    assert test_db.query(AuditLog).filter_by(resource_type="share_change_event").count() == 0


def test_pending_create_parent_field_ignored_per_global_contract(client, admin_headers, test_db, base):
    """`parent_event_id` 不在 create 端点的 service 传参内——**全部类型**一律静默丢弃
    （既有全局契约），子记录只能由确认拆分产生。本用例钉住待定沿用该契约而非新造拒绝。"""
    parent = _raw(test_db, base, event_type="share_split", platform_code=None)
    response = client.post(
        "/api/share-change-events", headers=admin_headers,
        json=_body(base, parent_event_id=parent.id),
    )
    assert response.status_code in (200, 201), response.text
    created = test_db.query(ShareChangeEvent).filter_by(
        portfolio_code=base.port, event_type=PENDING
    ).one()
    assert created.parent_event_id is None


@pytest.mark.parametrize("field", [
    "cash_pay_date", "share_confirm_date", "reinvest_nav", "ratio",
    "shares_before", "shares_change", "shares_after", "cash_change",
])
@pytest.mark.parametrize("null", [False, True], ids=["value", "null"])
def test_pending_put_rejects_method_fields_even_null(client, admin_headers, test_db, base, field, null):
    event = _event(test_db, base)
    value = None if null else (APPLY.isoformat() if field.endswith("date") else 1)
    before = _state(event)
    _error(client.put(f"/api/share-change-events/{event.id}", headers=admin_headers, json={field: value}), "INVALID_PARAM")
    assert _audits(test_db, before["id"], "update") == []
    assert _state(test_db.get(ShareChangeEvent, before["id"])) == before


@pytest.mark.parametrize("field", [
    "event_type", "status", "platform_code", "parent_event_id", "entitlement_shares", "cash_product_code",
])
def test_pending_put_unknown_update_fields_dropped_not_persisted(client, admin_headers, test_db, base, field):
    """这些键不在 `ShareChangeEventUpdate` 里，Pydantic 在**所有类型**上统一丢弃（全局
    既有契约，含 event_type——它刻意不开放直改）。待定侧真正需要拒绝的是 update schema
    内的方式专属/计算字段，由上一条用例钉住；本用例钉「丢弃不落库」而非虚构 422。"""
    event = _event(test_db, base)
    before = _state(event)
    response = client.put(
        f"/api/share-change-events/{event.id}", headers=admin_headers, json={field: None},
    )
    assert response.status_code == 200, response.text
    assert _audits(test_db, event.id, "update") == []
    assert _state(test_db.get(ShareChangeEvent, before["id"])) == before


def test_pending_put_accepts_only_public_announcement_edits(client, admin_headers, test_db, base):
    event = _event(test_db, base)
    updates = {"ex_date": MID.isoformat(), "entitlement_date": ENT.isoformat(), "div_cash": 0.2, "notes": "核对公告"}
    response = client.put(f"/api/share-change-events/{event.id}", headers=admin_headers, json=updates)
    assert response.status_code == 200, response.text
    assert all(response.json()[key] == value for key, value in updates.items())
    assert response.json()["event_type"] == PENDING
    assert all(response.json()[field] is None for field in CALCULATED)
    response = client.put(f"/api/share-change-events/{event.id}", headers=admin_headers, json={"div_cash": None, "notes": None})
    assert response.status_code == 200, response.text
    assert response.json()["div_cash"] is None and response.json()["notes"] is None


@pytest.mark.parametrize("body", [
    {}, {"cash_pay_date": APPLY.isoformat()}, {"event_type": PENDING},
    {"event_type": "share_split"}, {"event_type": None},
    {"event_type": CASH, "shares_change": 50},
    {"event_type": CASH, "cash_pay_date": "not-a-date"},
])
def test_confirm_body_contract_rejects_malformed_selection(client, admin_headers, test_db, base, body):
    event = _event(test_db, base)
    _error(_request(client, admin_headers, event.id, "confirm", body))
    assert _audits(test_db, event.id, "confirm") == []
    assert event.event_type == PENDING and event.status == "pending"


@pytest.mark.parametrize("query", [
    {"cash_pay_date": APPLY.isoformat()}, {"share_confirm_date": APPLY.isoformat()},
    {"event_type": PENDING}, {"event_type": "share_split"},
    {"event_type": REINVEST, "share_confirm_date": "not-a-date"},
])
def test_preview_query_contract_rejects_partial_or_malformed_selection(client, admin_headers, test_db, base, query):
    event = _event(test_db, base)
    _error(_request(client, admin_headers, event.id, "preview", query))
    assert _audits(test_db, event.id, "confirm") == []
    assert event.event_type == PENDING and event.status == "pending"


@pytest.mark.parametrize("selection", [
    service.DividendSelection(CASH),
    service.DividendSelection(CASH, cash_pay_date=APPLY),
    service.DividendSelection(REINVEST),
    service.DividendSelection(REINVEST, share_confirm_date=MID),
])
def test_old_receivable_snapshot_cannot_be_bypassed_by_clearing_dates(client, admin_headers, test_db, base, selection):
    event = _event(test_db, base, event_type=REINVEST, share_confirm_date=APPLY)
    _generate(client, admin_headers, base, EX)
    assert [row.amount for row in list_reinvest_receivables(test_db, base.port, EX)] == [Decimal("100")]
    before = _state(event)
    for call in (service.compute_share_change_event_preview, service.confirm_share_change_event):
        with pytest.raises(BusinessError) as exc:
            call(test_db, event, selection=selection)
        assert exc.value.code == "SNAPSHOT_DEPENDENCY"
        assert exc.value.details["from_date"] == EX.isoformat()
        assert _state(event) == before
        assert not test_db.dirty
    assert _audits(test_db, event.id, "confirm") == []


@pytest.mark.parametrize("same_day", [False, True])
def test_unchanged_reinvest_candidate_can_confirm_after_receivable_snapshot(client, admin_headers, test_db, base, same_day):
    event = _event(
        test_db, base, event_type=REINVEST, share_confirm_date=APPLY,
        entitlement_date=EX if same_day else ENT,
    )
    _generate(client, admin_headers, base, EX)
    selection = {"event_type": REINVEST, "share_confirm_date": APPLY.isoformat()}
    response = _request(client, admin_headers, event.id, "preview", selection)
    assert response.status_code == 200, response.text
    assert response.json()["preview"]["shares_change"] == 50
    response = _request(client, admin_headers, event.id, "confirm", selection)
    assert response.status_code == 200, response.text
    for day in (MID, APPLY):
        _generate(client, admin_headers, base, day)
        _assert_economics(test_db, base, day, REINVEST, APPLY)


@pytest.mark.parametrize("method", METHODS)
def test_unconfirm_and_snapshot_cascade_keep_selected_method(client, admin_headers, test_db, base, method):
    date_field = "cash_pay_date" if method == CASH else "share_confirm_date"
    event = _event(test_db, base)
    selection = service.DividendSelection(method, **{date_field: APPLY})
    service.confirm_share_change_event(test_db, event, selection=selection)
    test_db.commit()
    service.unconfirm_share_change_event(test_db, event)
    test_db.commit()
    assert event.status == "pending" and event.event_type == method
    assert getattr(event, date_field) == APPLY
    assert all(getattr(event, field) is None for field in CALCULATED)
    assert event.reinvest_nav == (Decimal("2") if method == REINVEST else None)
    service.confirm_share_change_event(test_db, event)
    test_db.commit()
    for day in (EX, MID, APPLY):
        _generate(client, admin_headers, base, day)
    before = _state(event)
    with pytest.raises(BusinessError) as exc:
        service.unconfirm_share_change_event(test_db, event)
    assert exc.value.code == "SNAPSHOT_DEPENDENCY"
    assert _state(event) == before
    for day in (APPLY, MID, EX, ENT):
        response = client.delete(f"/api/snapshots/{base.port}/{day.isoformat()}", headers=admin_headers)
        assert response.status_code == 200, response.text
    test_db.refresh(event)
    assert event.status == "pending" and event.event_type == method
    assert getattr(event, date_field) == APPLY
    assert event.reinvest_nav == (Decimal("2") if method == REINVEST else None)
    assert all(getattr(event, field) is None for field in CALCULATED)


def test_auto_confirm_skip_has_identity_and_never_attempts_confirmation(test_db, base):
    event = _event(test_db, base)
    before = _state(event)
    with patch.object(service, "confirm_share_change_event", side_effect=AssertionError("must select first")):
        entries = auto_confirm_after_snapshot(test_db, base.port, ENT)
    assert len(entries) == 1
    assert entries[0]["id"] == event.id and entries[0]["type"] == "event"
    assert entries[0]["action"] == "auto_confirm_skipped"
    assert entries[0]["code"] == "DIVIDEND_METHOD_REQUIRED"
    assert "选择" in entries[0]["hint"]
    assert _state(event) == before and not test_db.dirty
    assert _audits(test_db, event.id, "confirm") == []


def test_scheduling_skip_reaches_warning_but_actual_block_is_a_failure(test_db, ledger):
    event = _event(test_db, ledger)
    event_id = event.id
    test_db.query(Portfolio).filter_by(code=ledger.port).one().auto_snapshot_enabled = True
    test_db.commit()
    outcome = _generate_snapshots_for_date(test_db, ENT)
    skips = [warning for warning in outcome["warnings"] if warning["type"] == "auto_confirm_skipped"]
    assert len(skips) == 1
    assert skips[0]["event_id"] == event_id and skips[0]["portfolio_code"] == ledger.port
    assert skips[0]["date"] == ENT.isoformat() and "选择" in skips[0]["message"]
    assert outcome["auto_confirm_failed"] == outcome["failed_portfolios"] == []
    status, total, success, failed, message = _derive_log_fields("snapshot_generate", outcome)
    assert (status, total, success, failed) == ("success", 1, 1, 0)
    assert message == f"warnings: auto_confirm_skipped({ledger.port}#{event_id})"

    blocked = _generate_snapshots_for_date(test_db, EX)
    assert blocked["generated"] == 0 and blocked["auto_confirm_failed"] == []
    assert len(blocked["failed_portfolios"]) == 1
    failure = blocked["failed_portfolios"][0]
    assert failure["portfolio_code"] == ledger.port and failure["date"] == EX.isoformat()
    assert f"#{event_id}" in failure["error"]
    status, total, success, failed, _ = _derive_log_fields("snapshot_generate", blocked)
    assert status != "success" and (total, success, failed) == (1, 0, 1)
    assert test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code=ledger.port, snapshot_date=EX).count() == 0


@pytest.mark.parametrize("event_type,inputs,change", [
    ("share_split", {"ratio": Decimal("2")}, Decimal("1000")),
    ("share_merge", {"ratio": Decimal("2")}, Decimal("-500")),
    ("bonus_share", {"ratio": Decimal("0.1")}, Decimal("100")),
    ("forced_adjustment", {"shares_change": Decimal("5")}, Decimal("5")),
])
def test_other_manual_types_keep_no_body_confirmation(client, admin_headers, test_db, base, event_type, inputs, change):
    event = _event(
        test_db, base, event_type=event_type,
        platform_code=base.plat if event_type == "forced_adjustment" else None,
        **inputs,
    )
    response = _request(client, admin_headers, event.id, "preview")
    assert response.status_code == 200, response.text
    assert response.json()["preview"]["shares_change"] == change
    response = _request(client, admin_headers, event.id, "confirm")
    assert response.status_code == 200, response.text
    assert response.json()["event"]["event_type"] == event_type
    assert response.json()["event"]["shares_change"] == change


@pytest.mark.parametrize("overrides,code", [
    ({"ex_date": ENT.isoformat()}, "INVALID_DATE_ORDER"),
    ({"ex_date": BASE.isoformat()}, "INVALID_DATE_ORDER"),
    ({"ex_date": WEEKEND.isoformat()}, "INVALID_EX_DATE"),
    ({"entitlement_date": date(2025, 6, 7).isoformat()}, "INVALID_ENTITLEMENT_DATE"),
    ({"div_cash": 0}, "INVALID_PARAM"),
    ({"div_cash": -0.1}, "INVALID_PARAM"),
])
def test_pending_creation_keeps_date_and_amount_validation(client, admin_headers, test_db, base, overrides, code):
    response = client.post("/api/share-change-events", headers=admin_headers, json=_body(base, **overrides))
    _error(response, code)
    assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=base.port).count() == 0


@pytest.mark.parametrize("failure,code", [
    ("nav", "MISSING_NAV"), ("snapshot", "MISSING_POSITION_SNAPSHOT"),
    ("date", "INVALID_DATE_ORDER"),
])
def test_http_business_failure_has_no_confirmation_writes(client, admin_headers, test_db, base, failure, code):
    event = _event(test_db, base)
    event_id = event.id
    if failure == "nav":
        test_db.query(PriceRecord).filter_by(product_code=base.fund, market=base.market, price_date=EX).delete()
    elif failure == "snapshot":
        test_db.query(PortfolioPosition).filter_by(portfolio_code=base.port, snapshot_date=ENT).delete()
    test_db.commit()
    body = {"event_type": REINVEST, "share_confirm_date": (ENT if failure == "date" else APPLY).isoformat()}
    _error(_request(client, admin_headers, event_id, "confirm", body), code)
    # HTTP 失败可能回滚 fixture 的业务数据，按既有习惯检查零产出，不依赖失败后的对象存活。
    assert _audits(test_db, event_id, "confirm") == []
    assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=base.port, status="confirmed").count() == 0
    assert test_db.query(ShareChangeEvent).filter_by(parent_event_id=event_id).count() == 0
    assert test_db.query(ShareChangeEvent).filter(
        ShareChangeEvent.portfolio_code == base.port,
        ShareChangeEvent.event_type != PENDING,
    ).count() == 0


def test_selected_reinvest_sync_nav_retry_keeps_candidate_atomic(client, admin_headers, test_db, base, monkeypatch):
    event = _event(test_db, base)
    test_db.query(PriceRecord).filter_by(product_code=base.fund, market=base.market, price_date=EX).delete()
    test_db.commit()
    before = _state(event)
    calls = []

    def sync(db, product_code, market, start_date, end_date):
        calls.append((product_code, market, start_date))
        assert _state(event) == before, "同步净值期间不得先把候选方式写回原事件"
        create_price_record(db, product_code, market, EX, 2)
        return {"success": True}

    monkeypatch.setattr("app.services.market_data_service.sync_price_data", sync)
    response = client.post(
        f"/api/share-change-events/{event.id}/confirm", headers=admin_headers,
        params={"sync_nav": True},
        json={"event_type": REINVEST, "share_confirm_date": APPLY.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert calls == [(base.fund, base.market, EX)]
    assert response.json()["event"]["event_type"] == REINVEST
    assert response.json()["event"]["shares_change"] == 50
    assert len(_audits(test_db, event.id, "confirm")) == 1


def test_cash_weekend_date_is_not_rolled_to_a_trading_day(test_db, base):
    event = _event(test_db, base)
    selection = service.DividendSelection(CASH, cash_pay_date=WEEKEND)
    preview = service.compute_share_change_event_preview(test_db, event, selection=selection)
    assert preview["cash_effective_date"] == WEEKEND
    assert preview["share_effective_date"] == EX
    service.confirm_share_change_event(test_db, event, selection=selection)
    test_db.commit()
    assert event.cash_pay_date == WEEKEND
    assert calculate_available_cash(test_db, base.port, base.plat, APPLY) == 0
    assert calculate_available_cash(test_db, base.port, base.plat, WEEKEND) == 100


@pytest.mark.dialect
@pytest.mark.parametrize("contender", ["confirm", "edit", "auto-confirm"])
def test_mysql_two_sessions_recheck_locked_event_before_writing(test_engine, _seed_base_data, contender):
    """两个真实连接竞争同一事件；只提交/清理本用例 UUID 资源，不借 test_db 的外层事务。

    第二会话预先缓存 pending，然后等第一会话的行锁；第一会话确认后，第二会话
    必须重新读状态，不能用缓存做第二次确认、修改公告或自动确认。
    """
    if test_engine.dialect.name != "mysql":
        pytest.skip("需要可用的隔离 MySQL，SQLite 不能证明行锁竞争")
    from app.routers import share_change_events as router
    from app.schemas.share_change_event import ShareChangeEventConfirmSelection, ShareChangeEventUpdate

    suffix = uuid4().hex[:10]
    env = Ledger(port=f"DM_{suffix}", plat=f"DP_{suffix}", fund=f"DF_{suffix}")
    reached_lock = Event()
    event_id = None
    with Session(test_engine, expire_on_commit=False) as winner:
        try:
            _seed_ledger(winner, env)
            generate_daily_snapshots(winner, env.port, ENT)
            winner.commit()
            event = _event(winner, env)
            event_id = event.id
            winner.query(ShareChangeEvent).filter_by(id=event_id).with_for_update().one()

            def compete():
                with Session(test_engine, expire_on_commit=False) as db:
                    stale = db.get(ShareChangeEvent, event_id)
                    assert stale.status == "pending" and stale.event_type == PENDING
                    connection = db.connection()

                    def before_execute(conn, cursor, statement, parameters, context, executemany):
                        if "FOR UPDATE" in statement.upper() and "share_change_event" in statement:
                            reached_lock.set()

                    sqlalchemy_event.listen(connection, "before_cursor_execute", before_execute)
                    try:
                        if contender == "confirm":
                            router.confirm_share_change_event(
                                event_id, selection=ShareChangeEventConfirmSelection(event_type=CASH),
                                sync_nav=False, db=db, current_user=None,
                            )
                        elif contender == "edit":
                            router.update_share_change_event(
                                event_id, event=ShareChangeEventUpdate(div_cash=0.2), db=db, current_user=None,
                            )
                        else:
                            entries = auto_confirm_after_snapshot(db, env.port, ENT)
                            db.commit()
                            return entries
                        return "unexpected success"
                    except BusinessError as exc:
                        db.rollback()
                        return exc.code
                    finally:
                        sqlalchemy_event.remove(connection, "before_cursor_execute", before_execute)

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(compete)
                try:
                    assert reached_lock.wait(timeout=10), "第二会话没有进入真实行锁查询"
                    router.confirm_share_change_event(
                        event_id,
                        selection=ShareChangeEventConfirmSelection(event_type=REINVEST, share_confirm_date=APPLY),
                        sync_nav=False, db=winner, current_user=None,
                    )
                finally:
                    # 即使主断言失败也先释放行锁，再等待线程退出，不遗留阻塞连接。
                    winner.rollback()
                outcome = future.result(timeout=10)
            assert outcome == {
                "confirm": "INVALID_STATUS", "edit": "CANNOT_MODIFY_CONFIRMED", "auto-confirm": [],
            }[contender]
            winner.expire_all()
            row = winner.get(ShareChangeEvent, event_id)
            assert (row.event_type, row.status, row.share_confirm_date) == (REINVEST, "confirmed", APPLY)
            assert row.cash_pay_date is None and row.div_cash == Decimal("0.1")
            assert row.shares_change == 50 and row.cash_change == 0
            assert len(_audits(winner, event_id, "confirm")) == 1
            assert _audits(winner, event_id, "update") == []
            for day in (EX, MID, APPLY):
                generate_daily_snapshots(winner, env.port, day)
                winner.commit()
                _assert_economics(winner, env, day, REINVEST, APPLY)
        finally:
            winner.rollback()
            if event_id is not None:
                winner.query(AuditLog).filter_by(resource_type="share_change_event", resource_id=str(event_id)).delete()
            # generate_daily_snapshots 会提交 snapshot 审计（resource_name=组合码）；不按本用例
            # UUID 组合删净，会毒化 test_audit_service / test_system_logs 的空表与计数断言（仅 MySQL 显形）。
            winner.query(AuditLog).filter_by(resource_type="snapshot", resource_name=env.port).delete(synchronize_session=False)
            for model in (ShareChangeEvent, InvestorHolding, PortfolioPosition, PortfolioValueSnapshot, Trade, Subscription):
                winner.query(model).filter_by(portfolio_code=env.port).delete(synchronize_session=False)
            winner.query(PriceRecord).filter_by(product_code=env.fund).delete(synchronize_session=False)
            winner.query(Product).filter_by(code=env.fund).delete(synchronize_session=False)
            winner.query(Portfolio).filter_by(code=env.port).delete(synchronize_session=False)
            winner.query(Platform).filter_by(code=env.plat).delete(synchronize_session=False)
            winner.commit()
