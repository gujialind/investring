from datetime import date
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot, ShareChangeEvent
from app.services.position_service import calculate_available_cash, compute_cash_balance
from app.services.share_change_event_service import (
    confirm_share_change_event,
    create_share_change_event,
)
from app.services.snapshot_service import _compute_in_transit_amounts
from tests.factories import (
    create_investor_holding,
    create_platform,
    create_portfolio,
    create_position_snapshot,
    create_price_record,
    create_product,
    create_subscription,
    create_trade,
    create_value_snapshot,
)

BASE = date(2025, 6, 5)
EX = date(2025, 6, 6)
WEEKEND = date(2025, 6, 7)
MONDAY = date(2025, 6, 9)
TUESDAY = date(2025, 6, 10)
PORTFOLIO = "DIV_TRANSIT"
PLATFORM = "DIV_PLATFORM"
FUND = "DIV_FUND"
MARKET = "CN_EXCHANGE"


@pytest.fixture
def dividend_portfolio(test_db):
    create_portfolio(test_db, code=PORTFOLIO, status="active")
    create_platform(test_db, code=PLATFORM)
    create_product(test_db, code=FUND, market=MARKET, product_type="ETF", confirm_days=0)
    create_position_snapshot(
        test_db, PORTFOLIO, FUND, MARKET, snapshot_date=BASE,
        platform_code=PLATFORM, shares=100, market_value=1000, unit_price=10, cost_price=10,
    )
    create_position_snapshot(
        test_db, PORTFOLIO, "CASH", "", snapshot_date=BASE,
        platform_code=PLATFORM, cash_amount=0, market_value=0,
    )
    create_value_snapshot(test_db, PORTFOLIO, BASE, total_value=1000, total_shares=1000, unit_price=1)
    create_investor_holding(test_db, PORTFOLIO, "VIEWER", BASE, shares=1000)
    create_subscription(
        test_db, PORTFOLIO, "VIEWER", amount=1000, shares=1000, unit_price=1,
        platform_code=PLATFORM, apply_date=date(2025, 6, 4), confirm_date=BASE, status="confirmed",
    )
    create_price_record(test_db, FUND, MARKET, BASE, 10)
    for product, market, direction, group in (
        ("CASH", "", "buy", "sub_dividend"),
        ("CASH", "", "sell", "rebal_dividend"),
        (FUND, MARKET, "buy", "rebal_dividend"),
    ):
        create_trade(
            test_db, PORTFOLIO, product, market, trade_type=direction,
            platform_code=PLATFORM, trade_date=BASE, confirm_date=BASE,
            status="confirmed", amount=1000, actual_amount=1000,
            price=10 if product == FUND else 1, shares=100 if product == FUND else None,
            transfer_group=group,
        )
    for day in (EX, MONDAY, TUESDAY):
        create_price_record(test_db, FUND, MARKET, day, 9.9)
    return PORTFOLIO


def _dividend(db, *, pay_date=WEEKEND, platform=PLATFORM, div_cash="0.1"):
    event = create_share_change_event(
        db, portfolio_code=PORTFOLIO, product_code=FUND, market=MARKET,
        platform_code=platform, event_type="cash_dividend", ex_date=EX,
        entitlement_date=BASE, cash_pay_date=pay_date, div_cash=Decimal(div_cash),
        force_cover=True,
    )
    confirm_share_change_event(db, event)
    db.commit()
    return event


def _generate(client, headers, day):
    response = client.post(
        "/api/snapshots/generate", headers=headers,
        json={"portfolio_code": PORTFOLIO, "target_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True


def _positions(db, day):
    return {
        (p.product_code, p.platform_code): p
        for p in db.query(PortfolioPosition).filter_by(
            portfolio_code=PORTFOLIO, snapshot_date=day,
        ).all()
    }


def _derived(client, headers, day):
    response = client.get(
        "/api/positions", headers=headers,
        params={"portfolio_code": PORTFOLIO, "snapshot_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    return {p["product_code"]: p for p in response.json()["items"]}


@pytest.mark.parametrize("pay_date", [None, EX, WEEKEND, MONDAY, TUESDAY])
def test_share_event_cash_dates_preserve_value_and_cash_profits(
    client, admin_headers, test_db, dividend_portfolio, pay_date,
):
    event = _dividend(test_db, pay_date=pay_date)
    assert event.cash_change == Decimal("10")
    effective = pay_date or EX
    for day in (EX, MONDAY, TUESDAY):
        expected_cash = Decimal("10") if day >= effective else Decimal("0")
        assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, day) == expected_cash
        assert compute_cash_balance(test_db, PORTFOLIO, PLATFORM, day) == expected_cash
        _generate(client, admin_headers, day)
        positions = _positions(test_db, day)
        assert positions[("CASH", PLATFORM)].cash_amount == expected_cash
        if day < effective:
            transit = positions[("IN_TRANSIT_DIVIDEND", PLATFORM)]
            assert transit.cash_amount == Decimal("10")
            assert transit.shares is None
            assert transit.frozen_amount == transit.frozen_shares == 0
        else:
            assert ("IN_TRANSIT_DIVIDEND", PLATFORM) not in positions
        snapshot = test_db.query(PortfolioValueSnapshot).filter_by(
            portfolio_code=PORTFOLIO, snapshot_date=day,
        ).one()
        assert snapshot.total_value == Decimal("1000")
        assert snapshot.in_transit_total == Decimal("10") - expected_cash
        derived = _derived(client, admin_headers, day)
        assert derived["CASH"]["daily_profit"] == 0
        assert derived["CASH"]["profit_loss"] == 0
        assert derived[FUND]["daily_profit"] == 0
        assert derived[FUND]["profit_loss"] == 0
        if day < effective:
            assert derived["IN_TRANSIT_DIVIDEND"]["daily_profit"] is None
            assert derived["IN_TRANSIT_DIVIDEND"]["profit_loss"] is None
        assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, day) == expected_cash
    assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, BASE) == 0
    assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, EX) == (
        Decimal("10") if effective == EX else Decimal("0")
    )


def test_share_event_unpaid_dividend_cannot_fund_buy(
    client, admin_headers, test_db, dividend_portfolio,
):
    _dividend(test_db, pay_date=TUESDAY)
    payload = {
        "portfolio_code": PORTFOLIO, "product_code": FUND, "market": MARKET,
        "platform_code": PLATFORM, "trade_type": "buy", "amount": 5, "price": 9.9,
    }
    for day in (EX, MONDAY):
        response = client.post("/api/trades", headers=admin_headers, json={
            **payload, "trade_date": day.isoformat(),
        })
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"] == "INSUFFICIENT_CASH"
        if day == EX:
            _generate(client, admin_headers, EX)
    response = client.post("/api/trades", headers=admin_headers, json={
        **payload, "trade_date": TUESDAY.isoformat(),
    })
    assert response.status_code == 200, response.text


def test_share_event_arrives_after_fund_liquidated(
    client, admin_headers, test_db, dividend_portfolio,
):
    _dividend(test_db, pay_date=TUESDAY)
    for product, market, direction in ((FUND, MARKET, "sell"), ("CASH", "", "buy")):
        create_trade(
            test_db, PORTFOLIO, product, market, trade_type=direction,
            platform_code=PLATFORM, trade_date=EX, confirm_date=EX,
            status="confirmed", amount=990, actual_amount=990,
            shares=100 if product == FUND else None, price=9.9,
            transfer_group="rebal_dividend_sell",
        )
    for day in (EX, MONDAY, TUESDAY):
        _generate(client, admin_headers, day)
        rows = _positions(test_db, day)
        assert (FUND, PLATFORM) not in rows
        assert rows[("CASH", PLATFORM)].cash_amount == (1000 if day == TUESDAY else 990)
        assert (("IN_TRANSIT_DIVIDEND", PLATFORM) in rows) == (day < TUESDAY)


def test_share_event_rebuild_recalculate_and_entitlement_cascade(
    client, admin_headers, test_db, dividend_portfolio,
):
    event = _dividend(test_db)
    for day in (EX, MONDAY):
        _generate(client, admin_headers, day)
    _generate(client, admin_headers, MONDAY)
    assert _positions(test_db, MONDAY)[("CASH", PLATFORM)].cash_amount == 10
    for _ in range(2):
        response = client.post("/api/snapshots/recalculate", headers=admin_headers, json={
            "portfolio_code": PORTFOLIO, "start_date": EX.isoformat(), "end_date": MONDAY.isoformat(),
        })
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True, response.text
        assert all(not result["errors"] for result in response.json()["results"]), response.text
        assert _positions(test_db, EX)[("IN_TRANSIT_DIVIDEND", PLATFORM)].cash_amount == 10
        assert _positions(test_db, MONDAY)[("CASH", PLATFORM)].cash_amount == 10
    for day in (MONDAY, EX, BASE):
        response = client.delete(
            f"/api/snapshots/{PORTFOLIO}/{day.isoformat()}", headers=admin_headers,
        )
        assert response.status_code == 200, response.text
    test_db.refresh(event)
    assert event.status == "pending"
    assert event.cash_change is None
    assert event.cash_pay_date == WEEKEND
    assert _compute_in_transit_amounts(test_db, PORTFOLIO, EX) == {}
    assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, MONDAY) == 0
    response = client.post("/api/snapshots/recalculate", headers=admin_headers, json={
        "portfolio_code": PORTFOLIO, "start_date": BASE.isoformat(), "end_date": MONDAY.isoformat(),
    })
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True, response.text
    test_db.refresh(event)
    assert event.status == "confirmed"
    assert event.cash_pay_date == WEEKEND
    assert _positions(test_db, MONDAY)[("CASH", PLATFORM)].cash_amount == 10


def test_share_event_multiple_dividends_platforms_and_statuses(
    client, admin_headers, test_db, dividend_portfolio,
):
    other = "DIV_OTHER"
    create_platform(test_db, code=other)
    create_position_snapshot(
        test_db, PORTFOLIO, FUND, MARKET, snapshot_date=BASE,
        platform_code=other, shares=50, market_value=500, unit_price=10,
    )
    _dividend(test_db, pay_date=MONDAY)
    _dividend(test_db, pay_date=TUESDAY, div_cash="0.2")
    _dividend(test_db, pay_date=TUESDAY, platform=other)
    for status in ("pending", "cancelled"):
        test_db.add(ShareChangeEvent(
            portfolio_code=PORTFOLIO, product_code=FUND, market=MARKET,
            platform_code=other, event_type="cash_dividend", status=status, event_source="manual",
            entitlement_date=BASE, ex_date=EX, cash_pay_date=TUESDAY,
            cash_change=Decimal("999"),
        ))
    test_db.flush()
    assert _compute_in_transit_amounts(test_db, PORTFOLIO, BASE) == {}
    assert _compute_in_transit_amounts(test_db, PORTFOLIO, EX) == {
        (PLATFORM, "dividend"): Decimal("30"), (other, "dividend"): Decimal("5"),
    }
    assert _compute_in_transit_amounts(test_db, PORTFOLIO, MONDAY) == {
        (PLATFORM, "dividend"): Decimal("20"), (other, "dividend"): Decimal("5"),
    }
    assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, MONDAY) == 10
    assert calculate_available_cash(test_db, PORTFOLIO, other, MONDAY) == 0
    assert calculate_available_cash(test_db, PORTFOLIO, None, TUESDAY) == 35
    assert calculate_available_cash(test_db, PORTFOLIO) == 35


@pytest.mark.parametrize("status", ["confirmed", "pending", "cancelled"])
def test_share_event_cash_without_snapshot_counts_once(test_db, status):
    create_portfolio(test_db, code=PORTFOLIO, status="active")
    create_platform(test_db, code=PLATFORM)
    create_product(test_db, code=FUND, market=MARKET, product_type="ETF", confirm_days=0)
    test_db.add(ShareChangeEvent(
        portfolio_code=PORTFOLIO, product_code=FUND, market=MARKET,
        platform_code=PLATFORM, event_type="cash_dividend", status=status, event_source="manual",
        entitlement_date=BASE, ex_date=EX, cash_pay_date=WEEKEND, cash_change=Decimal("10"),
    ))
    test_db.flush()
    assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, EX) == 0
    expected = 10 if status == "confirmed" else 0
    assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM, WEEKEND) == expected
    assert compute_cash_balance(test_db, PORTFOLIO, PLATFORM, WEEKEND) == expected
    assert calculate_available_cash(test_db, PORTFOLIO, PLATFORM) == expected


def test_share_event_cash_addback_keeps_market_boundary(
    client, admin_headers, test_db, dividend_portfolio,
):
    create_product(test_db, code=FUND, market="CN_OTC", product_type="LOF")
    create_position_snapshot(
        test_db, PORTFOLIO, FUND, "CN_OTC", snapshot_date=BASE,
        platform_code=PLATFORM, shares=10, unit_price=10, market_value=100, cost_price=10,
    )
    _dividend(test_db)
    create_price_record(test_db, FUND, "CN_OTC", EX, 10)
    _generate(client, admin_headers, EX)
    response = client.get("/api/positions", headers=admin_headers, params={
        "portfolio_code": PORTFOLIO, "snapshot_date": EX.isoformat(), "product_code": FUND,
    })
    assert response.status_code == 200, response.text
    rows = {p["market"]: p for p in response.json()["items"]}
    assert rows["CN_EXCHANGE"]["daily_profit"] == 0
    assert rows["CN_OTC"]["daily_profit"] == 0
    assert rows["CN_OTC"]["profit_loss"] == 0
