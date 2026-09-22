# ============================================================================
# 集成测试：快照管理 (test_snapshots.py)
# ============================================================================
# 覆盖批量删除端点的 CONFIRM_REQUIRED 守卫与基本分支。
# 覆盖单日生成的快照连续性校验（#55 SNAPSHOT_NOT_CONTINUOUS）。
# 覆盖重算单事务原子性（#58：预校验拦截、中途失败整体回滚）。
# ============================================================================

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models import (
    InvestorHolding, Portfolio, PortfolioPosition, PortfolioValueSnapshot,
    ShareChangeEvent, Subscription, Trade,
)
from app.services import snapshot_service, subscription_service
from app.services.exceptions import BusinessError
from app.services.share_change_event_service import (
    create_share_change_event, confirm_share_change_event,
)
from tests.integration.snapshot_helpers import capture_portfolio_state
from tests.integration.test_snapshot_forced_adjustment import (
    EX_DAY, FUND, _ensure_price, _setup_real_history,
)
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
    create_product,
    create_trade,
)


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


class TestSnapshotContinuity:
    """单日生成快照的连续性校验（#55）

    日期基于 conftest 交易日历（工作日均为交易日）：
    - D0 = 2025-06-06（周五）
    - 下一交易日 = 2025-06-09（周一）
    - 跳日目标 = 2025-06-10（周二）
    """

    D0 = date(2025, 6, 6)
    NEXT_DAY = date(2025, 6, 9)
    SKIP_DAY = date(2025, 6, 10)

    def _portfolio(self, db, code="SNAP_CONT"):
        return create_portfolio(db, code=code, status="active")

    def test_generate_skip_day_rejected(self, client, admin_headers, test_db):
        """跳过紧邻交易日直接生成 → 422 SNAPSHOT_NOT_CONTINUOUS，不产生空洞"""
        port = self._portfolio(test_db)
        _setup_cash_snapshot(test_db, port.code, self.D0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": self.SKIP_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "SNAPSHOT_NOT_CONTINUOUS"

        # 未生成跳日快照
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == self.SKIP_DAY,
        ).first() is None

    def test_generate_next_trading_day_ok(self, client, admin_headers, test_db):
        """顺延生成下一交易日 → 成功"""
        port = self._portfolio(test_db, code="SNAP_NEXT")
        _setup_cash_snapshot(test_db, port.code, self.D0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": self.NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == self.NEXT_DAY,
        ).first() is not None

    def test_generate_rebuild_latest_ok(self, client, admin_headers, test_db):
        """重建最新一日（target_date == 最新快照日）→ 成功"""
        port = self._portfolio(test_db, code="SNAP_LATEST")
        _setup_cash_snapshot(test_db, port.code, self.D0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": self.D0.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_generate_mid_day_rejected(self, client, admin_headers, test_db):
        """重建其后仍有快照的中间日 → 422 SNAPSHOT_NOT_CONTINUOUS（应走 recalculate）"""
        port = self._portfolio(test_db, code="SNAP_MID")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _setup_cash_snapshot(test_db, port.code, self.NEXT_DAY)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": self.D0.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "SNAPSHOT_NOT_CONTINUOUS"

    def test_first_snapshot_no_continuity_restriction(self, client, admin_headers, test_db):
        """无任何快照时首次生成 → 不因连续性被拒（无持仓时返回跳过）"""
        port = self._portfolio(test_db, code="SNAP_FIRST")

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": self.D0.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_recalculate_bypasses_continuity(self, client, admin_headers, test_db):
        """重算覆盖已有快照区间（逐日重建时其后快照仍存在）不受连续性校验阻断"""
        port = self._portfolio(test_db, code="SNAP_RECALC")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _setup_cash_snapshot(test_db, port.code, self.NEXT_DAY)

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": port.code,
                "start_date": self.D0.isoformat(),
                "end_date": self.NEXT_DAY.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        errors = data["results"][0]["errors"]
        assert not any("SNAPSHOT_NOT_CONTINUOUS" in str(e) for e in errors)


class TestSnapshotBulkDeleteGuard:
    """批量删除快照的确认守卫测试"""

    def test_bulk_delete_without_confirm_rejected(self, client, admin_headers, active_portfolio):
        """不带 confirm 参数 → 422 CONFIRM_REQUIRED，不执行删除"""
        resp = client.delete(
            f"/api/snapshots/{active_portfolio.code}/bulk/2025-01-06",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "CONFIRM_REQUIRED"

    def test_bulk_delete_confirm_false_rejected(self, client, admin_headers, active_portfolio):
        """显式传 confirm=false 同样拒绝"""
        resp = client.delete(
            f"/api/snapshots/{active_portfolio.code}/bulk/2025-01-06",
            params={"confirm": False},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CONFIRM_REQUIRED"

    def test_bulk_delete_with_confirm_no_snapshots(self, client, admin_headers, active_portfolio):
        """带 confirm=true 且无快照 → 200，deleted_count == 0"""
        resp = client.delete(
            f"/api/snapshots/{active_portfolio.code}/bulk/2025-01-06",
            params={"confirm": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["deleted_count"] == 0

    def test_bulk_delete_portfolio_not_found(self, client, admin_headers):
        """组合不存在 → 404"""
        resp = client.delete(
            "/api/snapshots/NO_SUCH_PORT/bulk/2025-01-06",
            params={"confirm": True},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "PORTFOLIO_NOT_FOUND"

    def test_viewer_cannot_bulk_delete(self, client, viewer_headers, active_portfolio):
        """viewer 无权限 → 403"""
        resp = client.delete(
            f"/api/snapshots/{active_portfolio.code}/bulk/2025-01-06",
            params={"confirm": True},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestRecalculateAtomicity:
    """重算单事务原子性（issue #58）

    - 预校验失败（NAV 缺失）→ 422 VALIDATION_FAILED，不删任何快照
    - 循环中途失败 → 200 + errors，整体回滚，快照与重算前完全一致
    - 正常重算 → 统一 commit，快照重建落库
    """

    D0 = date(2025, 6, 6)
    NEXT_DAY = date(2025, 6, 9)

    def _snapshot_ids(self, db, portfolio_code):
        return sorted(
            row[0] for row in db.query(PortfolioValueSnapshot.id).filter(
                PortfolioValueSnapshot.portfolio_code == portfolio_code
            ).all()
        )

    def test_precheck_failure_keeps_snapshots(self, client, admin_headers, test_db):
        """NAV 缺失 → 预校验 422，三张快照表与重算前完全一致"""
        port = create_portfolio(test_db, code="ATOM_PRE", status="active")
        create_product(test_db, code="ATOMX.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        # 最新持仓含无任何价格记录的基金 → price_data 预校验必失败
        create_position_snapshot(
            test_db, port.code, "ATOMX.OF", "CN_OTC",
            snapshot_date=self.D0, shares=100.0, market_value=100.0,
            platform_code="MYCF",
        )
        state_before = capture_portfolio_state(test_db, port.code)

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": port.code,
                "start_date": self.D0.isoformat(),
                "end_date": self.D0.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "VALIDATION_FAILED"
        assert "预校验失败" in resp.json()["detail"]["message"]

        assert capture_portfolio_state(test_db, port.code) == state_before

    def test_midloop_failure_rolls_back_all(self, client, admin_headers, test_db):
        """到期 pending 调仓阻断后续日，router 回滚整个重算事务。"""
        port = create_portfolio(test_db, code="ATOM_MID", status="active")
        create_product(test_db, code="ATOMY.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _setup_cash_snapshot(test_db, port.code, self.NEXT_DAY)
        create_trade(
            test_db, port.code, "ATOMY.OF", "CN_OTC",
            trade_type="buy", amount=1000.0, price=None,
            trade_date=self.D0, confirm_date=self.NEXT_DAY,
            status="pending",
        )
        state_before = capture_portfolio_state(test_db, port.code)
        assert len(state_before["portfolio_value_snapshot"]) == 2

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": port.code,
                "start_date": self.D0.isoformat(),
                "end_date": self.NEXT_DAY.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["errors"], "NEXT_DAY 应因 pending 交易校验失败"

        assert capture_portfolio_state(test_db, port.code) == state_before

    def test_success_recalculate_commits(self, client, admin_headers, test_db):
        """无失败的重算 → 统一 commit，快照重建落库（新行 id）

        需提供 start_date 前一日基线快照，否则重建时 CASH 增量基线为 0，
        重建结果为无持仓跳过（合法但非本用例目标）。
        """
        prev_day = date(2025, 6, 5)  # D0 前一交易日（周四）
        port = create_portfolio(test_db, code="ATOM_OK", status="active")
        _setup_cash_snapshot(test_db, port.code, prev_day)
        _setup_cash_snapshot(test_db, port.code, self.D0)
        _setup_cash_snapshot(test_db, port.code, self.NEXT_DAY)
        ids_before = self._snapshot_ids(test_db, port.code)
        baseline_id = ids_before[0]

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": port.code,
                "start_date": self.D0.isoformat(),
                "end_date": self.NEXT_DAY.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["results"][0]["errors"] == []
        assert data["results"][0]["total_processed"] == 2

        test_db.expire_all()
        ids_after = self._snapshot_ids(test_db, port.code)
        assert len(ids_after) == 3
        assert baseline_id in ids_after, "区间外基线快照不受影响"
        assert set(ids_after) != set(ids_before), "重建应产生新快照行并已提交"


REBUILD_END = date(2025, 6, 10)


def _setup_dependent_history(db, code):
    _setup_real_history(db, code, "ATOM538_INV")
    assert snapshot_service.generate_daily_snapshots(db, code, EX_DAY)["success"]
    db.flush()
    sub = subscription_service.create_subscription(
        db, portfolio_code=code, investor_code="ATOM538_INV", platform_code="MYCF",
        sub_type="subscribe", apply_date=EX_DAY, amount=Decimal("200.00"),
    )
    subscription_service.confirm_single_subscription(db, sub)
    event = create_share_change_event(
        db, portfolio_code=code, product_code=FUND, market="CN_OTC",
        event_type="share_split", entitlement_date=EX_DAY, ex_date=REBUILD_END,
        ratio=Decimal("2"),
    )
    db.flush()
    confirm_share_change_event(db, event)
    db.flush()
    _ensure_price(db, REBUILD_END)
    assert snapshot_service.generate_daily_snapshots(db, code, REBUILD_END)["success"]
    db.commit()
    return sub.id, event.id


class TestCompleteBusinessAtomicity:
    @pytest.mark.parametrize("autoflush", [True, False])
    def test_second_day_failure_restores_all_business_fields(
        self, client, admin_headers, test_db, monkeypatch, autoflush,
    ):
        code = "ATOM538_REBUILD"
        _setup_dependent_history(test_db, code)
        before = capture_portfolio_state(test_db, code)
        original = snapshot_service._generate_investor_holding
        visited = []
        intermediate = []
        monkeypatch.setattr(test_db, "autoflush", autoflush)

        def fail_after_values(db, portfolio_code, target_date, value_snapshot):
            visited.append(target_date)
            if target_date == REBUILD_END:
                db.flush()
                intermediate.append(capture_portfolio_state(db, portfolio_code))
                raise BusinessError("POSITION_NOT_FOUND", "injected rebuild failure")
            return original(db, portfolio_code, target_date, value_snapshot)

        monkeypatch.setattr(snapshot_service, "_generate_investor_holding", fail_after_values)
        response = client.post(
            "/api/snapshots/recalculate",
            json={"portfolio_code": code, "start_date": EX_DAY.isoformat(),
                  "end_date": REBUILD_END.isoformat()},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        assert result["processed_dates"] == [EX_DAY.isoformat()]
        assert result["errors"][0]["code"] == "POSITION_NOT_FOUND"
        assert visited == [EX_DAY, REBUILD_END]
        changed = intermediate[0]
        for table in ("portfolio_position", "portfolio_value_snapshot", "investor_holding"):
            assert any(row["snapshot_date"] == EX_DAY for row in changed[table])
        for table in ("portfolio_position", "portfolio_value_snapshot"):
            assert any(row["snapshot_date"] == REBUILD_END for row in changed[table])
        assert not any(row["snapshot_date"] == REBUILD_END for row in changed["investor_holding"])
        assert capture_portfolio_state(test_db, code) == before

    @pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk-checkpoint"])
    def test_cascade_failure_restores_rows_and_children(
        self, client, admin_headers, test_db, monkeypatch, bulk,
    ):
        code = "ATOM538_CASCADE"
        sub_id, event_id = _setup_dependent_history(test_db, code)
        before = capture_portfolio_state(test_db, code)
        original = snapshot_service._cascade_unconfirm_share_change_events
        intermediate = []
        monkeypatch.setattr(test_db, "autoflush", False)

        def fail_after_cascade(db, portfolio_code, target_date):
            result = original(db, portfolio_code, target_date)
            if target_date == EX_DAY:
                db.flush()
                intermediate.append(capture_portfolio_state(db, portfolio_code))
                raise BusinessError("SNAPSHOT_DEPENDENCY", "injected cascade failure")
            return result

        monkeypatch.setattr(
            snapshot_service, "_cascade_unconfirm_share_change_events", fail_after_cascade,
        )
        suffix = f"bulk/{EX_DAY}" if bulk else str(EX_DAY)
        response = client.delete(
            f"/api/snapshots/{code}/{suffix}", params={"confirm": True}, headers=admin_headers,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"
        assert len(intermediate) == 1
        changed = intermediate[0]
        sub = next(row for row in changed["subscription"] if row["id"] == sub_id)
        assert sub["status"] == "pending" and sub["unit_price"] is None
        assert not any(row["transfer_group"] == f"sub_{sub_id}" for row in changed["trade"])
        parent = next(row for row in changed["share_change_event"] if row["id"] == event_id)
        assert parent["status"] == "pending" and parent["entitlement_shares"] is None
        assert any(row["parent_event_id"] == event_id for row in before["share_change_event"])
        assert not any(row["parent_event_id"] == event_id for row in changed["share_change_event"])
        expected = deepcopy(before)
        if bulk:
            for table in ("portfolio_position", "portfolio_value_snapshot", "investor_holding"):
                expected[table] = [row for row in expected[table] if row["snapshot_date"] != REBUILD_END]
        assert capture_portfolio_state(test_db, code) == expected

    @pytest.mark.parametrize("model, field, value", [
        (PortfolioPosition, "market_value", Decimal("999.1111")),
        (PortfolioValueSnapshot, "in_transit_total", Decimal("123.4567")),
        (InvestorHolding, "cost_per_share", Decimal("1.2345")),
        (Subscription, "amount", Decimal("222.22")),
        (Trade, "actual_amount", Decimal("333.33")),
        (ShareChangeEvent, "entitlement_shares", Decimal("444.44")),
        (Portfolio, "started_at", datetime(2025, 6, 4)),
    ])
    def test_state_detects_changed_fields_with_unchanged_ids(self, test_db, model, field, value):
        code = "ATOM538_FIELDS"
        _setup_dependent_history(test_db, code)
        before = capture_portfolio_state(test_db, code)
        table = model.__table__
        row = before[table.name][0]
        assert set(row) == set(table.columns.keys())
        key = list(table.primary_key.columns)[0]
        test_db.execute(table.update().where(key == row[key.name]).values({field: value}))
        after = capture_portfolio_state(test_db, code)
        assert [r[key.name] for r in after[table.name]] == [r[key.name] for r in before[table.name]]
        assert after[table.name][0][field] == value
        assert row[field] != value
        assert after != before
