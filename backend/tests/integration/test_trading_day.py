# ============================================================================
# 集成测试：交易日查询端点 (test_trading_day.py)
# ============================================================================
# 覆盖 issue #74 新增的三个端点：
# - GET /api/trading-calendar/next
# - GET /api/trading-calendar/prev
# - GET /api/trading-calendar/is-open
#
# 测试日历背景（conftest 种子数据）：
# 2025-01-01 起、终点滚动到 today+1 年（issue #468），周一至周五为交易日（is_open=True）。
# 超出该范围的日期视为"日历数据缺失"，应返回 CALENDAR_NOT_SYNCED 的 422。
# 终点侧哨兵取 today+400 天（恒在滚动终点之外，语义不随时间漂移）；起点固定
# 2025-01-01，故早于起点的哨兵可用固定日期。
#
# 另含两类拒绝的归因对照（issue #591）：TestClosedWeekWrites 证明"这天不开放"
# 走 NON_TRADING_DAY，TestExhaustedCalendarWrites 证明"日历没覆盖到这天之后"走
# CALENDAR_NOT_SYNCED——同一入口、同一断言形状，只有错误码与原因不同。
# ============================================================================

from datetime import date, timedelta

import pytest

from app.models import ShareChangeEvent, Subscription, Trade, TradingCalendar
from tests.factories import (
    create_investor_holding,
    create_portfolio,
    create_position_snapshot,
    create_product,
    create_value_snapshot,
)
from tests.integration.calendar_exhaustion_helpers import make_last_open_day

# 终点侧「日历未同步」哨兵：seed_base 段 4 的日历终点 = today + 365 天，
# today + 400 天恒在日历外。
CALENDAR_GAP_DATE = date.today() + timedelta(days=400)
CLOSED_WEEK = tuple(date(2025, 6, day) for day in range(9, 14))


@pytest.fixture
def closed_week_calendar(test_db):
    """仅在本用例事务内，把已存在的周一至周五改为模拟长假。"""
    for day in CLOSED_WEEK:
        row = test_db.query(TradingCalendar).filter_by(calendar_date=day).one()
        row.is_open = False
    test_db.flush()
    assert dict(test_db.query(
        TradingCalendar.calendar_date, TradingCalendar.is_open,
    ).filter(TradingCalendar.calendar_date.in_(CLOSED_WEEK)).all()) == {
        day: False for day in CLOSED_WEEK
    }


@pytest.fixture
def calendar_write_portfolio(test_db, closed_week_calendar):
    port = create_portfolio(test_db, code="CAL_WRITE", status="active")
    create_product(test_db, code="CALENDAR.OF", market="CN_OTC", confirm_days=1)
    baseline = date(2025, 6, 5)
    create_position_snapshot(
        test_db, port.code, "CASH", "", baseline,
        cash_amount=10000.0, unit_price=None, cost_price=None,
        market_value=10000.0, platform_code="MYCF",
    )
    create_position_snapshot(
        test_db, port.code, "CALENDAR.OF", "CN_OTC", baseline,
        shares=100.0, unit_price=2.0, cost_price=2.0,
        market_value=200.0, platform_code="MYCF",
    )
    create_value_snapshot(
        test_db, port.code, baseline,
        total_value=10200.0, total_shares=10200.0, unit_price=1.0,
    )
    create_investor_holding(test_db, port.code, "VIEWER", baseline, shares=10200.0)
    return port


def _business_rows(db, portfolio_code):
    return {
        model.__tablename__: db.query(*model.__table__.columns).filter(
            model.portfolio_code == portfolio_code,
        ).order_by(model.id).all()
        for model in (Subscription, Trade, ShareChangeEvent)
    }


def _assert_closed_date_rejected(
    client, headers, db, portfolio_code, endpoint, payload, date_field, operation,
    error_code="NON_TRADING_DAY", invalid_date="2025-06-09",
):
    """拒绝 + 零业务残留 + （create 时）正向对照。

    error_code / invalid_date 可换，使「这天不开放」与「日历没覆盖到这天之后」
    两种拒绝走同一形状断言（#591），不因形状不同而互相掩盖。
    """
    if operation == "update":
        created = client.post(endpoint, json=payload, headers=headers)
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "pending"
        endpoint = f"{endpoint}/{created.json()['id']}"
        invalid_payload = {date_field: invalid_date, "notes": "must not persist"}
        request = client.put
    else:
        invalid_payload = {**payload, date_field: invalid_date}
        request = client.post

    before = _business_rows(db, portfolio_code)
    resp = request(endpoint, json=invalid_payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == error_code
    db.flush()
    assert _business_rows(db, portfolio_code) == before

    if operation == "create":
        control = client.post(endpoint, json=payload, headers=headers)
        assert control.status_code == 200, control.text

    return resp


class TestNextTradingDay:
    """GET /api/trading-calendar/next"""

    def test_next_trading_day_default_days(self, client, viewer_headers):
        """2025-01-06 是周一，T+1 应为周二 2025-01-07"""
        resp = client.get(
            "/api/trading-calendar/next",
            params={"from_date": "2025-01-06"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["from_date"] == "2025-01-06"
        assert data["trading_day"] == "2025-01-07"

    def test_next_trading_day_skips_weekend(self, client, viewer_headers):
        """2025-01-03 是周五，下一交易日跳过周末为周一 2025-01-06"""
        resp = client.get(
            "/api/trading-calendar/next",
            params={"from_date": "2025-01-03"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["trading_day"] == "2025-01-06"

    def test_next_trading_day_multiple_days(self, client, viewer_headers):
        """days=3：周一 2025-01-06 向后第 3 个交易日为周四 2025-01-09"""
        resp = client.get(
            "/api/trading-calendar/next",
            params={"from_date": "2025-01-06", "days": 3},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["trading_day"] == "2025-01-09"

    def test_next_trading_day_calendar_not_synced(self, client, viewer_headers):
        """超出日历终点范围（滚动终点之后未同步）应返回 CALENDAR_NOT_SYNCED 422"""
        resp = client.get(
            "/api/trading-calendar/next",
            params={"from_date": CALENDAR_GAP_DATE.isoformat()},
            headers=viewer_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"

    def test_next_trading_day_invalid_days(self, client, viewer_headers):
        """days=0 违反 ge=1 校验"""
        resp = client.get(
            "/api/trading-calendar/next",
            params={"from_date": "2025-01-06", "days": 0},
            headers=viewer_headers,
        )
        assert resp.status_code == 422

    def test_next_trading_day_requires_auth(self, client):
        """未认证应返回 401"""
        resp = client.get(
            "/api/trading-calendar/next", params={"from_date": "2025-01-06"}
        )
        assert resp.status_code == 401


class TestPrevTradingDay:
    """GET /api/trading-calendar/prev"""

    def test_prev_trading_day_default_days(self, client, viewer_headers):
        """2025-01-07 是周二，T-1 应为周一 2025-01-06"""
        resp = client.get(
            "/api/trading-calendar/prev",
            params={"from_date": "2025-01-07"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["from_date"] == "2025-01-07"
        assert data["trading_day"] == "2025-01-06"

    def test_prev_trading_day_skips_weekend(self, client, viewer_headers):
        """2025-01-06 是周一，前一交易日跳过周末为周五 2025-01-03"""
        resp = client.get(
            "/api/trading-calendar/prev",
            params={"from_date": "2025-01-06"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["trading_day"] == "2025-01-03"

    def test_prev_trading_day_multiple_days(self, client, viewer_headers):
        """days=2：周四 2025-01-09 向前第 2 个交易日为周二 2025-01-07"""
        resp = client.get(
            "/api/trading-calendar/prev",
            params={"from_date": "2025-01-09", "days": 2},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["trading_day"] == "2025-01-07"

    def test_prev_trading_day_calendar_not_synced(self, client, viewer_headers):
        """早于日历起点（2024 年未同步）应返回 CALENDAR_NOT_SYNCED 422"""
        resp = client.get(
            "/api/trading-calendar/prev",
            params={"from_date": "2024-06-01"},
            headers=viewer_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"


class TestPartialExhaustionIsNow422:
    """#591 契约收紧：部分耗尽（能走到中间日但凑不满 days）从 200 翻成 422。

    旧 helper 会返回它走到的最后一个交易日（把「凑不满」当成功），现按 resolved_days
    如实拒绝。端点 days 参数 ge=1，故只测 days≥1 的部分耗尽。
    """

    def _last_open_days(self, test_db, n):
        rows = test_db.query(TradingCalendar.calendar_date).filter(
            TradingCalendar.is_open == True
        ).order_by(TradingCalendar.calendar_date.desc()).limit(n).all()
        return [r[0] for r in rows]

    def test_next_partial_exhaustion_is_422(self, client, viewer_headers, test_db):
        _last, second = self._last_open_days(test_db, 2)[:2]
        # 从倒数第二个开市日要 T+3，但日历只剩最后一个 → 部分耗尽
        resp = client.get(
            "/api/trading-calendar/next",
            params={"from_date": second.isoformat(), "days": 3},
            headers=viewer_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "CALENDAR_NOT_SYNCED"
        assert detail["details"]["requested_days"] == 3
        assert detail["details"]["resolved_days"] == 1
        assert detail["details"]["direction"] == "next"

    def test_prev_partial_exhaustion_is_422(self, client, viewer_headers, test_db):
        _first, second = (
            test_db.query(TradingCalendar.calendar_date)
            .filter(TradingCalendar.is_open == True)
            .order_by(TradingCalendar.calendar_date.asc()).limit(2).all()
        )
        resp = client.get(
            "/api/trading-calendar/prev",
            params={"from_date": second[0].isoformat(), "days": 3},
            headers=viewer_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "CALENDAR_NOT_SYNCED"
        assert detail["details"]["resolved_days"] == 1
        assert detail["details"]["direction"] == "prev"


class TestIsOpen:
    """GET /api/trading-calendar/is-open"""

    def test_is_open_trading_day(self, client, viewer_headers):
        """2025-01-06 是周一（交易日）"""
        resp = client.get(
            "/api/trading-calendar/is-open",
            params={"date": "2025-01-06"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == "2025-01-06"
        assert data["is_open"] is True

    def test_is_open_non_trading_day(self, client, viewer_headers):
        """2025-01-04 是周六（非交易日），应返回 200 而非 422"""
        resp = client.get(
            "/api/trading-calendar/is-open",
            params={"date": "2025-01-04"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == "2025-01-04"
        assert data["is_open"] is False

    def test_is_open_calendar_not_synced(self, client, viewer_headers):
        """日历中无该日期记录（超出滚动终点）应返回 CALENDAR_NOT_SYNCED 422"""
        resp = client.get(
            "/api/trading-calendar/is-open",
            params={"date": CALENDAR_GAP_DATE.isoformat()},
            headers=viewer_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"

    def test_is_open_requires_auth(self, client):
        """未认证应返回 401"""
        resp = client.get(
            "/api/trading-calendar/is-open", params={"date": "2025-01-06"}
        )
        assert resp.status_code == 401


@pytest.mark.usefixtures("closed_week_calendar")
class TestClosedWeekCalendar:
    @pytest.mark.parametrize("closed_date", CLOSED_WEEK)
    def test_weekday_can_be_closed(self, client, viewer_headers, closed_date):
        resp = client.get(
            "/api/trading-calendar/is-open",
            params={"date": closed_date.isoformat()},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == {"date": closed_date.isoformat(), "is_open": False}

    @pytest.mark.parametrize("direction, from_date, days, expected", [
        ("next", "2025-06-06", 1, "2025-06-16"),
        ("next", "2025-06-06", 2, "2025-06-17"),
        ("prev", "2025-06-16", 1, "2025-06-06"),
        ("prev", "2025-06-16", 2, "2025-06-05"),
    ])
    def test_next_prev_skip_closed_week(
        self, client, viewer_headers, direction, from_date, days, expected,
    ):
        resp = client.get(
            f"/api/trading-calendar/{direction}",
            params={"from_date": from_date, "days": days},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == {"from_date": from_date, "trading_day": expected}

    def test_missing_calendar_row_is_not_a_closed_day(
        self, client, viewer_headers, test_db,
    ):
        missing_date = date(2025, 6, 10)
        row = test_db.query(TradingCalendar).filter_by(calendar_date=missing_date).one()
        test_db.delete(row)
        test_db.flush()
        assert test_db.query(TradingCalendar).filter_by(
            calendar_date=missing_date,
        ).first() is None

        resp = client.get(
            "/api/trading-calendar/is-open",
            params={"date": missing_date.isoformat()},
            headers=viewer_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"


class TestClosedWeekWrites:
    @pytest.mark.parametrize("operation", ["create", "update"])
    @pytest.mark.parametrize("sub_type", ["subscribe", "redeem"])
    def test_subscription_rejects_closed_apply_date(
        self, client, admin_headers, test_db, calendar_write_portfolio,
        operation, sub_type,
    ):
        port = calendar_write_portfolio
        payload = {
            "portfolio_code": port.code,
            "investor_code": "VIEWER",
            "platform_code": "MYCF",
            "sub_type": sub_type,
            "apply_date": "2025-06-06",
            "amount" if sub_type == "subscribe" else "shares": 100.0,
        }
        _assert_closed_date_rejected(
            client, admin_headers, test_db, port.code, "/api/subscriptions",
            payload, "apply_date", operation,
        )

    @pytest.mark.parametrize("operation", ["create", "update"])
    @pytest.mark.parametrize("trade_type", ["buy", "sell"])
    def test_trade_rejects_closed_trade_date(
        self, client, admin_headers, test_db, calendar_write_portfolio,
        operation, trade_type,
    ):
        port = calendar_write_portfolio
        payload = {
            "portfolio_code": port.code,
            "product_code": "CALENDAR.OF",
            "market": "CN_OTC",
            "platform_code": "MYCF",
            "trade_type": trade_type,
            "trade_date": "2025-06-06",
            "price": 2.0,
            "amount" if trade_type == "buy" else "shares": 50.0,
        }
        _assert_closed_date_rejected(
            client, admin_headers, test_db, port.code, "/api/trades",
            payload, "trade_date", operation,
        )

    @pytest.mark.parametrize("cross_day", [False, True])
    def test_cash_transfer_rejects_closed_transfer_date(
        self, client, admin_headers, test_db, calendar_write_portfolio, cross_day,
    ):
        port = calendar_write_portfolio
        payload = {
            "from_platform": "MYCF",
            "to_platform": "HBZQ",
            "amount": 100.0,
            "transfer_date": "2025-06-06",
            "cross_day": cross_day,
        }
        _assert_closed_date_rejected(
            client, admin_headers, test_db, port.code,
            f"/api/portfolios/{port.code}/cash-transfer",
            payload, "transfer_date", "create",
        )

    @pytest.mark.parametrize("operation", ["create", "update"])
    @pytest.mark.parametrize("date_field, error_code", [
        ("entitlement_date", "INVALID_ENTITLEMENT_DATE"),
        ("ex_date", "INVALID_EX_DATE"),
    ])
    def test_event_rejects_closed_dates(
        self, client, admin_headers, test_db, calendar_write_portfolio,
        operation, date_field, error_code,
    ):
        port = calendar_write_portfolio
        payload = {
            "portfolio_code": port.code,
            "product_code": "CALENDAR.OF",
            "market": "CN_OTC",
            "platform_code": "MYCF",
            "event_type": "cash_dividend",
            "entitlement_date": "2025-06-05",
            "ex_date": "2025-06-16",
            "div_cash": 0.1,
        }
        _assert_closed_date_rejected(
            client, admin_headers, test_db, port.code, "/api/share-change-events",
            payload, date_field, operation, error_code,
        )


class TestExhaustedCalendarWrites:
    """#591-B：日期本身是开市日，但日历在其后到头——与 TestClosedWeekWrites 同形状、不同归因。

    同一条申购入口在同一次用例里先后给出两种拒绝码，证明「这天不开放」
    （NON_TRADING_DAY）与「日历没覆盖到这天之后」（CALENDAR_NOT_SYNCED）没有被互相掩盖：
    前者由 is_trading_day 挡在入口，后者由确认日推导抛出。
    """

    LAST_OPEN = date(2025, 6, 6)

    @pytest.mark.parametrize("operation", ["create", "update"])
    @pytest.mark.parametrize("sub_type", ["subscribe", "redeem"])
    def test_subscription_distinguishes_closed_from_exhausted(
        self, client, admin_headers, test_db, calendar_write_portfolio,
        operation, sub_type,
    ):
        port = calendar_write_portfolio
        # 基准日保持在截断后的日历内可用（06-05 的确认日 = 06-06），
        # 否则正向对照会先被耗尽挡下，测的就不是「两种归因」而是「全都失败」。
        payload = {
            "portfolio_code": port.code,
            "investor_code": "VIEWER",
            "platform_code": "MYCF",
            "sub_type": sub_type,
            "apply_date": "2025-06-05",
            "amount" if sub_type == "subscribe" else "shares": 100.0,
        }
        _assert_closed_date_rejected(
            client, admin_headers, test_db, port.code, "/api/subscriptions",
            payload, "apply_date", operation,
        )

        make_last_open_day(test_db, self.LAST_OPEN)
        resp = _assert_closed_date_rejected(
            client, admin_headers, test_db, port.code, "/api/subscriptions",
            payload, "apply_date", operation,
            error_code="CALENDAR_NOT_SYNCED", invalid_date=self.LAST_OPEN.isoformat(),
        )
        assert resp.json()["detail"]["details"]["direction"] == "next"
