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
# ============================================================================

from datetime import date, timedelta

import pytest

# 终点侧「日历未同步」哨兵：seed_base 段 4 的日历终点 = today + 365 天，
# today + 400 天恒在日历外。
CALENDAR_GAP_DATE = date.today() + timedelta(days=400)


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
