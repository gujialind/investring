# ============================================================================
# 集成测试：快照停更读侧信号 catchup_pending_dates（issue #906 SPEC §4）
# ============================================================================
# trailing 缺口 = 最新快照日之后至 business_yesterday() 应生成而未生成的 SSE
# 交易日，与 missing_dates（首末快照日闭区间内部空洞，#146）互补不重叠。
# 修复 A 挡住的是最新快照日之后的推进，missing_dates 检测不到——本字段让
# 「数字停更」在读侧可解释，前端详情页据此渲染停更 Alert。
#
# 口径：无快照基线（latest=None）恒空（与 missing_dates 无基线同构）；
# 台账型（kind='ledger'）恒空（status 端点闸门，与 PR1 对齐）；
# 业务时钟经 monkeypatch snapshot_service.business_yesterday 冻结（#746 单点）。
# ============================================================================

from datetime import date

import pytest

from app.models import TradingCalendar
from app.services.snapshot_service import compute_catchup_pending_dates
from tests.factories import create_portfolio, create_value_snapshot

D0 = date(2025, 6, 6)    # 周五
YESTERDAY = date(2025, 6, 12)  # 周四（冻结的业务昨日）
# (D0, YESTERDAY] 的 SSE 交易日：6/9 周一、6/10 周二、6/11 周三、6/12 周四
GAP_DAYS = [date(2025, 6, d) for d in (9, 10, 11, 12)]


@pytest.fixture
def frozen_yesterday(monkeypatch):
    monkeypatch.setattr(
        "app.services.snapshot_service.business_yesterday", lambda: YESTERDAY
    )
    return YESTERDAY


def _snap(db, code, d: date) -> None:
    create_value_snapshot(db, code, d, total_value=100.0, total_shares=100.0, unit_price=1.0)


class TestComputeCatchupPendingDates:
    def test_trailing_gap_lists_open_sse_days(self, test_db, frozen_yesterday):
        port = create_portfolio(test_db, code="CATCHUP_T1", status="active")
        _snap(test_db, port.code, D0)
        assert compute_catchup_pending_dates(test_db, port.code, D0) == GAP_DAYS

    def test_no_baseline_returns_empty(self, test_db, frozen_yesterday):
        port = create_portfolio(test_db, code="CATCHUP_T2", status="active")
        assert compute_catchup_pending_dates(test_db, port.code, None) == []

    def test_up_to_date_returns_empty(self, test_db, frozen_yesterday):
        port = create_portfolio(test_db, code="CATCHUP_T3", status="active")
        _snap(test_db, port.code, YESTERDAY)
        assert compute_catchup_pending_dates(test_db, port.code, YESTERDAY) == []

    def test_latest_after_yesterday_returns_empty(self, test_db, frozen_yesterday):
        """最新快照日晚于业务昨日（时钟回拨等异常态）不产生负区间。"""
        port = create_portfolio(test_db, code="CATCHUP_T4", status="active")
        later = date(2025, 6, 13)
        _snap(test_db, port.code, later)
        assert compute_catchup_pending_dates(test_db, port.code, later) == []

    def test_hkex_days_excluded(self, test_db, frozen_yesterday):
        """双日历口径（#777）：快照日序列是 SSE，HKEX 开市日不混入。"""
        port = create_portfolio(test_db, code="CATCHUP_T5", status="active")
        _snap(test_db, port.code, D0)
        # 6/10 SSE 闭市但 HKEX 开市 → 该日不应出现在 trailing 缺口
        sse_row = test_db.query(TradingCalendar).filter_by(
            calendar_date=date(2025, 6, 10), exchange="SSE"
        ).one()
        sse_row.is_open = False
        test_db.add(TradingCalendar(
            calendar_date=date(2025, 6, 10), is_open=True, exchange="HKEX"
        ))
        test_db.flush()
        assert compute_catchup_pending_dates(test_db, port.code, D0) == [
            date(2025, 6, 9), date(2025, 6, 11), date(2025, 6, 12),
        ]

    def test_closed_sse_days_excluded(self, test_db, frozen_yesterday):
        port = create_portfolio(test_db, code="CATCHUP_T6", status="active")
        _snap(test_db, port.code, D0)
        row = test_db.query(TradingCalendar).filter_by(
            calendar_date=date(2025, 6, 11), exchange="SSE"
        ).one()
        row.is_open = False
        test_db.flush()
        assert compute_catchup_pending_dates(test_db, port.code, D0) == [
            date(2025, 6, 9), date(2025, 6, 10), date(2025, 6, 12),
        ]


class TestStatusEndpointCatchupField:
    def test_trailing_gap_exposed_and_complementary_to_missing_dates(
        self, client, admin_headers, test_db, frozen_yesterday
    ):
        """SPEC §4 断言 1/4：trailing 缺口暴露，与内部空洞互补不重叠。"""
        port = create_portfolio(test_db, code="CATCHUP_S1", status="active")
        _snap(test_db, port.code, date(2025, 6, 5))
        _snap(test_db, port.code, date(2025, 6, 9))  # 内部空洞 6/6
        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["missing_dates"] == ["2025-06-06"]
        assert body["catchup_pending_dates"] == ["2025-06-10", "2025-06-11", "2025-06-12"]
        assert not set(body["missing_dates"]) & set(body["catchup_pending_dates"])

    def test_up_to_date_portfolio_empty(
        self, client, admin_headers, test_db, frozen_yesterday
    ):
        """SPEC §4 断言 2：最新快照日 == 业务昨日 → 空。"""
        port = create_portfolio(test_db, code="CATCHUP_S2", status="active")
        _snap(test_db, port.code, YESTERDAY)
        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["catchup_pending_dates"] == []

    def test_ledger_portfolio_always_empty(
        self, client, admin_headers, test_db, frozen_yesterday
    ):
        """SPEC §4 断言 3：台账型恒空——即便人为存在快照行也不报停更。"""
        port = create_portfolio(
            test_db, code="CATCHUP_S3", status="active",
            kind="ledger", owner_investor_code="ADMIN",
        )
        # 台账型正常无净值快照；人为落一行验证闸门独立于「无基线」路径生效
        _snap(test_db, port.code, D0)
        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["catchup_pending_dates"] == []

    def test_draft_no_baseline_empty(
        self, client, admin_headers, test_db, frozen_yesterday
    ):
        """无快照基线（draft 未激活）→ 空，不误报停更。"""
        port = create_portfolio(test_db, code="CATCHUP_S4", status="draft")
        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["catchup_pending_dates"] == []
        assert body["missing_dates"] == []