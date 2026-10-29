"""#754：详情回查读侧派生字段——`confirmed_at` 与 `dividend_amount` 的响应契约。

- `confirmed_at`：DB 列已存在（确认写入、取消确认清空），响应首次暴露；
  确认后有值 → unconfirm 后 null → 再确认再有值；pending / cancelled 恒 null。
- `dividend_amount`：读侧派生（非 DB 列），口径与确认/预览同一单点
  `reinvest_receivable.reinvest_dividend_amount`（= quantize_amount(基数份额 × 每份分红)）：
  confirmed cash_dividend 取落库 `cash_change`（恒等且覆盖存量行）；
  confirmed reinvest_dividend 现算；其余（pending / cancelled / 非分红类 /
  confirmed 但 es 或 div_cash 为 null 的存量脏行）恒 null，不折 0。
- list 与单条 GET 响应同时携带两新字段（单条 GET 的派生不依赖 list 接线）。
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.share_change_event import ShareChangeEvent
from app.services.reinvest_receivable import reinvest_dividend_amount
from tests.factories import (
    create_platform, create_portfolio, create_position_snapshot, create_price_record,
    create_product, create_share_change_event, create_value_snapshot, ensure_trading_day,
)

PORT = "P754D"
FUND = "000754.OF"
PLAT = "PL754"
ENT = date(2026, 3, 4)
EX = date(2026, 3, 6)
SHARES = 1000.0
DIV_CASH = 0.125
NAV_EX = 1.2345
# 与 reinvest_dividend_amount 同源：quantize_amount(1000 × 0.125) = 125.00
EXPECTED_DIVIDEND = float(reinvest_dividend_amount(Decimal(str(SHARES)), Decimal(str(DIV_CASH))))


@pytest.fixture
def base(test_db):
    create_portfolio(test_db, code=PORT, status="active")
    create_product(test_db, code=FUND, market="CN_OTC", product_type="OEF")
    create_platform(test_db, code=PLAT)
    ensure_trading_day(test_db, ENT, is_open=True)
    ensure_trading_day(test_db, date(2026, 3, 5), is_open=True)
    ensure_trading_day(test_db, EX, is_open=True)
    create_position_snapshot(
        test_db, PORT, FUND, "CN_OTC", snapshot_date=ENT, shares=SHARES,
        unit_price=1.2, cost_price=1.2, market_value=SHARES * 1.2, platform_code=PLAT,
    )
    create_value_snapshot(test_db, PORT, ENT, total_value=SHARES * 1.2,
                          total_shares=SHARES, unit_price=1.2)
    create_price_record(test_db, FUND, "CN_OTC", EX, NAV_EX)
    test_db.commit()
    return test_db


def _create_body(event_type, **overrides):
    body = {
        "portfolio_code": PORT, "event_type": event_type,
        "ex_date": EX.isoformat(), "entitlement_date": ENT.isoformat(),
        "product_code": FUND, "market": "CN_OTC", "platform_code": PLAT,
        "div_cash": DIV_CASH,
    }
    body.update(overrides)
    return body


def _create_via_api(client, admin_headers, event_type, **overrides):
    resp = client.post("/api/share-change-events", json=_create_body(event_type, **overrides),
                       headers=admin_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _get(client, headers, event_id):
    resp = client.get(f"/api/share-change-events/{event_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _confirm(client, admin_headers, event_id):
    resp = client.post(f"/api/share-change-events/{event_id}/confirm", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["event"]


# ---------------- confirmed_at 三态生命周期 ----------------

def test_confirmed_at_lifecycle_confirm_unconfirm_reconfirm(base, client, admin_headers):
    created = _create_via_api(client, admin_headers, "cash_dividend")
    assert created["confirmed_at"] is None
    event_id = created["id"]

    confirmed = _confirm(client, admin_headers, event_id)
    assert confirmed["confirmed_at"] is not None

    got = _get(client, admin_headers, event_id)
    assert got["status"] == "confirmed"
    assert got["confirmed_at"] is not None

    resp = client.post(f"/api/share-change-events/{event_id}/unconfirm", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    got = _get(client, admin_headers, event_id)
    assert got["status"] == "pending"
    assert got["confirmed_at"] is None

    confirmed = _confirm(client, admin_headers, event_id)
    assert confirmed["confirmed_at"] is not None
    assert _get(client, admin_headers, event_id)["confirmed_at"] is not None


def test_confirmed_at_null_for_pending_and_cancelled(base, client, admin_headers):
    pending = _create_via_api(client, admin_headers, "cash_dividend")
    assert pending["confirmed_at"] is None

    resp = client.post(f"/api/share-change-events/{pending['id']}/cancel", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    cancelled = _get(client, admin_headers, pending["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["confirmed_at"] is None


# ---------------- dividend_amount 派生矩阵 ----------------

def test_confirmed_cash_dividend_equals_cash_change(base, client, admin_headers):
    created = _create_via_api(client, admin_headers, "cash_dividend")
    assert created["dividend_amount"] is None  # pending 不折 0

    confirmed = _confirm(client, admin_headers, created["id"])
    assert confirmed["cash_change"] == pytest.approx(EXPECTED_DIVIDEND)
    # 验收断言 4：现金分红的红利金额 == 现金变化（同一 quantize(es × div_cash)）
    assert confirmed["dividend_amount"] == confirmed["cash_change"]

    got = _get(client, admin_headers, created["id"])
    assert got["dividend_amount"] == pytest.approx(EXPECTED_DIVIDEND)


def test_confirmed_reinvest_dividend_matches_single_source(base, client, admin_headers):
    created = _create_via_api(client, admin_headers, "reinvest_dividend")
    confirmed = _confirm(client, admin_headers, created["id"])
    # 与 reinvest_dividend_amount（应收/确认折算金额同一单点）同源
    assert confirmed["dividend_amount"] == pytest.approx(EXPECTED_DIVIDEND)
    # 再投资份额按两步量化折算，红利金额 ≠ 现金变化（现金腿为 0/None）
    assert confirmed["shares_change"] is not None

    got = _get(client, admin_headers, created["id"])
    assert got["dividend_amount"] == pytest.approx(EXPECTED_DIVIDEND)


@pytest.mark.parametrize("event_type", [
    "cash_dividend", "reinvest_dividend", "share_split", "share_merge",
    "bonus_share", "forced_adjustment", "dividend_pending",
])
def test_pending_dividend_amount_null_for_all_types(base, test_db, client, admin_headers, event_type):
    kwargs = {"div_cash": DIV_CASH}
    if event_type in {"share_split", "share_merge", "bonus_share"}:
        kwargs.update(platform_code=None, ratio=2.0)
    elif event_type == "forced_adjustment":
        kwargs.update(shares_change=10.0, cash_change=-25.0)
    else:
        kwargs.update(platform_code=PLAT)
    event = create_share_change_event(
        test_db, PORT, FUND, "CN_OTC", event_type=event_type,
        ex_date=EX, entitlement_date=ENT, **kwargs,
    )
    got = _get(client, admin_headers, event.id)
    assert got["status"] == "pending"
    assert got["dividend_amount"] is None
    assert got["confirmed_at"] is None


@pytest.mark.parametrize("event_type, dirty_fields", [
    # confirmed 但计算输入缺失的存量脏行：派生 null，不折 0、不抛错
    ("reinvest_dividend", {"div_cash": None}),
    ("reinvest_dividend", {"entitlement_shares": None}),
    ("cash_dividend", {"cash_change": None}),
    # confirmed 非分红类：恒 null
    ("share_split", {"ratio": 2.0}),
    ("forced_adjustment", {"shares_change": 10.0, "cash_change": -25.0}),
])
def test_confirmed_dirty_and_non_dividend_rows_null(base, test_db, client, admin_headers,
                                                    event_type, dirty_fields):
    fields = {"div_cash": DIV_CASH, "entitlement_shares": SHARES,
              "platform_code": PLAT, "status": "confirmed",
              "confirmed_at": datetime(2026, 3, 6, 10, 0, 0)}
    if event_type == "share_split":
        fields["platform_code"] = None
    fields.update(dirty_fields)
    event = create_share_change_event(
        test_db, PORT, FUND, "CN_OTC", event_type=event_type,
        ex_date=EX, entitlement_date=ENT, **fields,
    )
    got = _get(client, admin_headers, event.id)
    assert got["status"] == "confirmed"
    assert got["dividend_amount"] is None
    assert got["confirmed_at"] is not None


# ---------------- list 与单条 GET 同携两字段 ----------------

def test_list_and_get_both_carry_new_fields(base, test_db, client, admin_headers):
    confirmed = _create_via_api(client, admin_headers, "cash_dividend")
    _confirm(client, admin_headers, confirmed["id"])
    pending = _create_via_api(client, admin_headers, "reinvest_dividend")

    listed = client.get(f"/api/share-change-events?portfolio_code={PORT}", headers=admin_headers)
    assert listed.status_code == 200
    items = {item["id"]: item for item in listed.json()["items"]}
    for event_id, expected_amount in [
        (confirmed["id"], EXPECTED_DIVIDEND), (pending["id"], None),
    ]:
        row = items[event_id]
        assert "confirmed_at" in row and "dividend_amount" in row
        if expected_amount is None:
            assert row["dividend_amount"] is None
            assert row["confirmed_at"] is None
        else:
            assert row["dividend_amount"] == pytest.approx(expected_amount)
            assert row["confirmed_at"] is not None

    # 单条 GET 的派生不依赖 list 接线（model_validator 行级填充）
    single = _get(client, admin_headers, confirmed["id"])
    assert single["dividend_amount"] == pytest.approx(EXPECTED_DIVIDEND)
    assert single["confirmed_at"] is not None


def test_orm_row_serializes_without_api_touch(base, test_db):
    """守门回归：新字段不改变 ORM 表结构（无迁移、无新列）。"""
    event = create_share_change_event(
        test_db, PORT, FUND, "CN_OTC", event_type="cash_dividend",
        ex_date=EX, entitlement_date=ENT, platform_code=PLAT, div_cash=DIV_CASH,
    )
    columns = {c.name for c in ShareChangeEvent.__table__.columns}
    assert "dividend_amount" not in columns  # 读侧派生、不落库
    assert "confirmed_at" in columns          # DB 列已存在（#754 仅暴露到响应）
    assert event.confirmed_at is None
