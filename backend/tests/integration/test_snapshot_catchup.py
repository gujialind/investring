# ============================================================================
# 集成测试：快照追平与批量删除 dry-run (test_snapshot_catchup.py)
# ============================================================================
# 覆盖 issue #84：POST /catch-up（多日追平、幂等、无基线、中途失败 checkpoint）
#                 POST /generate-next（单日顺延、无基线）
# 覆盖 issue #75：delete-bulk dry_run=true 纯预览零副作用
# 日期基于 conftest 交易日历（工作日均为交易日）
# ============================================================================

from datetime import date

import pytest

from app.models import PortfolioValueSnapshot, Trade, TradingCalendar
from app.services import snapshot_service
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
)


def _make_last_open_day(db, last_day: date):
    """事务内把日历裁成「last_day 之后无任何开市日」（且 last_day 本身开市）。

    用于日历耗尽场景；不 commit，SAVEPOINT 回滚复原 session 级种子。
    """
    row = db.query(TradingCalendar).filter_by(calendar_date=last_day).first()
    if row is None:
        db.add(TradingCalendar(calendar_date=last_day, is_open=True, exchange="SSE"))
    else:
        row.is_open = True
    db.query(TradingCalendar).filter(
        TradingCalendar.calendar_date > last_day
    ).delete(synchronize_session=False)
    db.flush()


def _setup_cash_snapshot(db, portfolio_code: str, snapshot_date: date, amount: float = 10000.0):
    """为组合制造指定日的完整三表快照（仅 CASH 持仓，无需行情数据）"""
    create_position_snapshot(
        db, portfolio_code, "CASH", "",
        snapshot_date=snapshot_date,
        cash_amount=amount, unit_price=None, cost_price=None,
        market_value=amount, platform_code="MYCF",
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=amount, total_shares=amount, unit_price=1.0,
    )
    create_investor_holding(
        db, portfolio_code, "VIEWER", snapshot_date, shares=amount,
    )


def _snapshot_dates(db, portfolio_code):
    return sorted(
        row[0] for row in db.query(PortfolioValueSnapshot.snapshot_date).filter(
            PortfolioValueSnapshot.portfolio_code == portfolio_code
        ).all()
    )


class TestCatchUp:
    """POST /api/snapshots/catch-up（issue #84）

    - D0 = 2025-06-06（周五），后续交易日 06-09/06-10/06-11
    """

    D0 = date(2025, 6, 6)
    DAY1 = date(2025, 6, 9)
    DAY2 = date(2025, 6, 10)
    DAY3 = date(2025, 6, 11)

    def test_catch_up_multi_days(self, client, admin_headers, test_db):
        """多日追平成功：从最新快照日的下一交易日逐日生成至 to_date"""
        port = create_portfolio(test_db, code="CATCHUP_OK", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": port.code, "to_date": self.DAY3.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["generated_count"] == 3
        assert data["generated_dates"] == [
            self.DAY1.isoformat(), self.DAY2.isoformat(), self.DAY3.isoformat(),
        ]
        assert data["latest_snapshot_date"] == self.DAY3.isoformat()
        assert data.get("failed_date") is None
        assert data.get("error") is None

        test_db.expire_all()
        assert _snapshot_dates(test_db, port.code) == [
            self.D0, self.DAY1, self.DAY2, self.DAY3,
        ]

    def test_catch_up_idempotent(self, client, admin_headers, test_db):
        """幂等：latest >= to_date → generated_count == 0，零副作用"""
        port = create_portfolio(test_db, code="CATCHUP_IDEM", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": port.code, "to_date": self.D0.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["generated_count"] == 0
        assert data["generated_dates"] == []
        assert data["latest_snapshot_date"] == self.D0.isoformat()
        assert data["message"] == "已追平"

        test_db.expire_all()
        assert _snapshot_dates(test_db, port.code) == [self.D0]

    def test_catch_up_no_baseline(self, client, admin_headers, test_db):
        """组合无任何快照 → 422 NO_SNAPSHOT_BASELINE"""
        port = create_portfolio(test_db, code="CATCHUP_NOBASE", status="active")

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": port.code, "to_date": self.DAY1.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "NO_SNAPSHOT_BASELINE"

    def test_catch_up_portfolio_not_found(self, client, admin_headers, test_db):
        """组合不存在 → 404 PORTFOLIO_NOT_FOUND"""
        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": "NO_SUCH_PORT", "to_date": self.DAY1.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "PORTFOLIO_NOT_FOUND"

    def test_catch_up_midway_failure_keeps_succeeded_days(
        self, client, admin_headers, test_db, monkeypatch
    ):
        """中途失败停止且已成功日保留（逐日 checkpoint）

        monkeypatch 让第二日 generate_daily_snapshots 抛异常：
        第一日已 commit 落库保留，响应附 failed_date/error。
        """
        port = create_portfolio(test_db, code="CATCHUP_MID", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)

        real_generate = snapshot_service.generate_daily_snapshots

        def flaky_generate(db, portfolio_code, target_date, check_continuity=True):
            if target_date == self.DAY2:
                raise RuntimeError("模拟第二日生成失败")
            return real_generate(db, portfolio_code, target_date, check_continuity)

        monkeypatch.setattr(snapshot_service, "generate_daily_snapshots", flaky_generate)

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": port.code, "to_date": self.DAY3.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["generated_count"] == 1
        assert data["generated_dates"] == [self.DAY1.isoformat()]
        assert data["failed_date"] == self.DAY2.isoformat()
        assert "模拟第二日生成失败" in data["error"]
        assert data["latest_snapshot_date"] == self.DAY1.isoformat()

        # 第一日 checkpoint 已落库保留，失败日及之后未生成
        test_db.expire_all()
        assert _snapshot_dates(test_db, port.code) == [self.D0, self.DAY1]

    def test_viewer_cannot_catch_up(self, client, viewer_headers, test_db):
        """viewer 无权限 → 403"""
        port = create_portfolio(test_db, code="CATCHUP_PERM", status="active")
        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": port.code, "to_date": self.DAY1.isoformat()},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestGenerateNext:
    """POST /api/snapshots/generate-next（issue #84）"""

    D0 = date(2025, 6, 6)
    NEXT_DAY = date(2025, 6, 9)

    def test_generate_next_creates_one_snapshot(self, client, admin_headers, test_db):
        """生成最新快照日的下一交易日一张快照"""
        port = create_portfolio(test_db, code="GENNEXT_OK", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)

        resp = client.post(
            "/api/snapshots/generate-next",
            json={"portfolio_code": port.code},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["generated_date"] == self.NEXT_DAY.isoformat()

        test_db.expire_all()
        assert _snapshot_dates(test_db, port.code) == [self.D0, self.NEXT_DAY]

    def test_generate_next_no_baseline(self, client, admin_headers, test_db):
        """组合无任何快照 → 422 NO_SNAPSHOT_BASELINE"""
        port = create_portfolio(test_db, code="GENNEXT_NOBASE", status="active")

        resp = client.post(
            "/api/snapshots/generate-next",
            json={"portfolio_code": port.code},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "NO_SNAPSHOT_BASELINE"


class TestBulkDeleteDryRun:
    """delete-bulk dry_run=true 纯预览（issue #75）"""

    D0 = date(2025, 6, 6)
    DAY1 = date(2025, 6, 9)

    def test_dry_run_returns_dates_without_deleting(self, client, admin_headers, test_db):
        """dry_run=true（不带 confirm）→ 200 返回日期列表，快照计数不变"""
        port = create_portfolio(test_db, code="DRYRUN_OK", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _setup_cash_snapshot(test_db, port.code, self.DAY1)
        dates_before = _snapshot_dates(test_db, port.code)

        resp = client.delete(
            f"/api/snapshots/{port.code}/bulk/{self.D0.isoformat()}",
            params={"dry_run": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["portfolio_code"] == port.code
        assert data["from_date"] == self.D0.isoformat()
        assert data["count"] == 2
        assert sorted(data["snapshot_dates"]) == [
            self.D0.isoformat(), self.DAY1.isoformat(),
        ]

        # 零副作用：调用前后快照完全一致
        test_db.expire_all()
        assert _snapshot_dates(test_db, port.code) == dates_before

    def test_dry_run_with_confirm_still_previews(self, client, admin_headers, test_db):
        """dry_run=true 优先于 confirm=true，仍为纯预览"""
        port = create_portfolio(test_db, code="DRYRUN_PRI", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)

        resp = client.delete(
            f"/api/snapshots/{port.code}/bulk/{self.D0.isoformat()}",
            params={"dry_run": True, "confirm": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True

        test_db.expire_all()
        assert _snapshot_dates(test_db, port.code) == [self.D0]

    def test_confirm_required_message_mentions_dry_run(self, client, admin_headers, test_db):
        """CONFIRM_REQUIRED message 提示可先加 dry_run=true 预览"""
        port = create_portfolio(test_db, code="DRYRUN_MSG", status="active")

        resp = client.delete(
            f"/api/snapshots/{port.code}/bulk/{self.D0.isoformat()}",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "CONFIRM_REQUIRED"
        assert "dry_run=true" in detail["message"]


class TestCalendarExhaustion:
    """#591：日历到头是合法终止条件，不得静默编造日期、也不得把部分成功判成失败。"""

    D0 = date(2025, 6, 6)
    DAY1 = date(2025, 6, 9)
    DAY2 = date(2025, 6, 10)

    def test_catch_up_beyond_calendar_end_keeps_partial_success(
        self, client, admin_headers, test_db
    ):
        """to_date 越过最后开市日 → 200 + 已生成日保留 + 告警说明是日历到头"""
        port = create_portfolio(test_db, code="CATCH_EXH", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _make_last_open_day(test_db, self.DAY1)  # DAY1 之后再无开市日

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": port.code, "to_date": self.DAY2.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["generated_dates"] == [self.DAY1.isoformat()]
        assert data.get("failed_date") is None  # 日历到头不是失败
        assert "日历" in data["message"], data["message"]
        assert "追平完成" not in data["message"]  # 不得宣称已追平到 to_date
        assert any(
            w.get("type") == "calendar_exhausted" for w in (data.get("warnings") or [])
        ), data.get("warnings")

    def test_catch_up_entry_exhaustion_is_422(self, client, admin_headers, test_db):
        """最新快照日已是最后开市日 → 入口即 422 CALENDAR_NOT_SYNCED"""
        port = create_portfolio(test_db, code="CATCH_EXH0", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _make_last_open_day(test_db, self.D0)

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": port.code, "to_date": self.DAY2.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"

    def test_generate_next_exhaustion_is_422(self, client, admin_headers, test_db):
        port = create_portfolio(test_db, code="GEN_EXH", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _make_last_open_day(test_db, self.D0)

        resp = client.post(
            "/api/snapshots/generate-next",
            json={"portfolio_code": port.code},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"

    def test_auto_confirm_skips_when_no_next_trading_day(self, test_db):
        """D 之后无交易日 → 跨天/事件两段跳过；错日匹配的隐患被消除（腿保持 pending）。

        旧回退把 next_confirm_date 设成 D 本身，会把 confirm_date == D 的腿**按错日
        确认**。现契约：不确认它（可证明合法——confirm_date/ex_date 创建期即被校验为
        日历交易日，D 之后无交易日 ⟹ 不存在应在此日确认的记录）。
        """
        port = create_portfolio(test_db, code="AC_EXH", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        # 一条 confirm_date == D 的 pending CASH 跨天腿（非 rebal_ 组）
        leg = Trade(
            portfolio_code=port.code, product_code="CASH", market="",
            trade_type="buy", amount=100.0, actual_amount=100.0,
            trade_date=self.D0, confirm_date=self.D0, status="pending",
            platform_code="MYCF", transfer_group="xfer_exh",
        )
        test_db.add(leg)
        test_db.flush()
        _make_last_open_day(test_db, self.D0)

        results = snapshot_service.auto_confirm_after_snapshot(test_db, port.code, self.D0)

        assert not [r for r in results if r.get("transfer_group") == "xfer_exh"]
        test_db.refresh(leg)
        assert leg.status == "pending"
