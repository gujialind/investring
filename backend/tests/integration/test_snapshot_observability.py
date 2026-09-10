# ============================================================================
# 集成测试：快照链路可观测性 (test_snapshot_observability.py)
# ============================================================================
# issue #305：快照生成/重算链路的失败与告警在 API 边界静默丢失。
# 覆盖验收断言：
# 1. 重算响应含逐日 auto_confirmed（含 auto_confirm_failed 条目与 code）
# 2. catch_up 响应含 warnings（至少 event_zeroed_position）；调度路径负现金
#    硬阻断（#203，原 negative_cash warning 已升级为 NEGATIVE_CASH 失败）
# 3. 逐日错误条目携带 code 与 details（POSITION_NOT_FOUND）
# 4. auto_confirm 循环单条 DB 级失败不毒化 session、不级联误导记录
#    （#419：savepoint 须为 session 级，用例让 ORM flush 真失败；session 不可恢复时
#     记一条 SESSION_ABORTED 根因终止本段，重算仍 200 + errors 且整体 rollback）
# 5. calculate_available_shares market=None 基线跨市场/跨平台汇总
# 日期基于 conftest 交易日历（工作日均为交易日）
# ============================================================================

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError, IntegrityError, PendingRollbackError
from sqlalchemy.orm.session import SessionTransaction, SessionTransactionState

from app.models import Portfolio, PortfolioValueSnapshot, Trade
from app.services import snapshot_service
from app.services import subscription_service
from app.services.exceptions import BusinessError
from app.services.position_service import calculate_available_shares
from app.services.share_change_event_service import (
    create_share_change_event as svc_create_event,
    confirm_share_change_event,
)
from app.services.snapshot_service import auto_confirm_after_snapshot
from app.services.task_runner import run_snapshot_generate
from tests.factories import (
    create_portfolio,
    create_product,
    create_position_snapshot,
    create_price_record,
    create_share_change_event,
    create_subscription,
    create_trade,
    create_value_snapshot,
    create_investor_holding,
)
from tests.integration.test_snapshot_forced_adjustment import (
    D0,
    EX_DAY,
    FUND,
    _ensure_price,
    _setup,
    _setup_real_history,
)


class TestRecalculateObservability:
    """验收 1/3：重算响应透传 auto_confirmed 与结构化错误"""

    def test_response_contains_auto_confirmed_and_cascaded(self, client, admin_headers, test_db):
        """重算响应含逐日 auto_confirmed 与级联回退字段（不再被 schema 剥离）

        级联回退只命中 apply_date == 快照日 的申赎，故补一笔 apply_date=D0
        的申购：删 D0 快照时被回退为 pending，重建后由 auto_confirm 重确认。
        """
        _setup_real_history(test_db, "OBS_RC1", "OBS_RC1_INV")
        sub2 = create_subscription(
            test_db, portfolio_code="OBS_RC1", investor_code="OBS_RC1_INV",
            platform_code="MYCF", sub_type="subscribe",
            amount=Decimal("200.00"), apply_date=D0,
        )
        test_db.flush()
        subscription_service.confirm_single_subscription(test_db, sub2)
        test_db.commit()
        sub2_id = sub2.id

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": "OBS_RC1",
                "start_date": D0.isoformat(),
                "end_date": D0.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        entry = data["results"][0]
        assert entry["errors"] == []
        # 级联回退后申购被自动重确认 → auto_confirmed 含成功条目
        auto = entry["auto_confirmed"]
        assert isinstance(auto, list) and auto, "auto_confirmed 应透传至响应"
        assert any(
            r.get("action") == "auto_confirmed" and r.get("sub_type") == "subscribe"
            for r in auto
        )
        # 删旧快照级联回退了该申购
        cascaded = entry["cascaded_unconfirmed"]
        assert cascaded, "cascaded_unconfirmed 应透传至响应"
        assert any(c["id"] == sub2_id for c in cascaded)

    def test_dirty_event_auto_confirm_failed_with_code(self, client, admin_headers, test_db):
        """脏事件（#279 双空）自动确认失败：响应可见 auto_confirm_failed 与 code，
        不阻断重算（errors 为空）"""
        _setup_real_history(test_db, "OBS_RC2", "OBS_RC2_INV")
        # 直造存量脏数据（#279 校验前创建的双空 forced_adjustment）
        create_share_change_event(
            test_db, "OBS_RC2", FUND, "CN_OTC",
            event_type="forced_adjustment", ex_date=EX_DAY,
            entitlement_date=D0, status="pending",
            platform_code="MYCF",
        )

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": "OBS_RC2",
                "start_date": D0.isoformat(),
                "end_date": D0.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        entry = resp.json()["results"][0]
        assert entry["errors"] == []
        failed = [
            r for r in entry["auto_confirmed"]
            if r.get("action") == "auto_confirm_failed"
        ]
        assert len(failed) == 1
        assert failed[0]["code"] == "EMPTY_ADJUSTMENT"
        assert failed[0]["type"] == "event"
        assert failed[0]["error"]

    def test_recalculate_survives_real_flush_failure(self, client, admin_headers, test_db, monkeypatch):
        """自动确认中真撞唯一约束（#419 验收 5）：重算仍 200、errors 为空、
        根因 code 落在 auto_confirmed，全响应无 PendingRollbackError——
        连接级 savepoint 时代这里会冒泡成 500 RECALCULATION_FAILED"""
        _setup_real_history(test_db, "OBS_RC5", "OBS_RC5_INV")
        # 撞约束的预置行挂在另一个组合上，不干扰被测组合的快照数据
        create_portfolio(test_db, code="OBS_RC5_HOLDER", status="active")
        create_trade(
            test_db, portfolio_code="OBS_RC5_HOLDER", product_code=FUND,
            market="CN_OTC", trade_type="buy", transfer_group="sub_conflict",
            trade_date=D0,
        )
        # 级联回退只命中 apply_date == 快照日 的申赎，补一笔 apply_date=D0 的申购
        sub2 = create_subscription(
            test_db, portfolio_code="OBS_RC5", investor_code="OBS_RC5_INV",
            platform_code="MYCF", sub_type="subscribe",
            amount=Decimal("200.00"), apply_date=D0,
        )
        test_db.flush()
        subscription_service.confirm_single_subscription(test_db, sub2)
        test_db.commit()

        calls = []

        def fake_confirm(db_, sub, *, auto_flush=False, skip_cash_check=False):
            calls.append(sub.id)
            db_.add(Trade(
                portfolio_code="OBS_RC5", platform_code="MYCF",
                product_code=FUND, market="CN_OTC", trade_type="buy",
                transfer_group="sub_conflict", amount=Decimal("1.00"),
                trade_date=D0, status="pending",
            ))
            db_.flush()  # 真 flush 失败：撞 uq_trade_transfer_group
            sub.status = "confirmed"

        monkeypatch.setattr(
            subscription_service, "confirm_single_subscription", fake_confirm
        )

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": "OBS_RC5",
                "start_date": D0.isoformat(),
                "end_date": D0.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "PendingRollbackError" not in resp.text
        assert calls == [sub2.id], "守护确实被走到"
        entry = resp.json()["results"][0]
        assert entry["errors"] == [], "单条 DB 级失败不该中止重算"
        failed = [
            r for r in entry["auto_confirmed"]
            if r.get("action") == "auto_confirm_failed"
        ]
        assert len(failed) == 1
        assert failed[0]["code"] == "IntegrityError"

    def test_recalculate_aborts_with_root_cause_when_session_dies(
        self, client, admin_headers, test_db, monkeypatch
    ):
        """session 不可恢复（#419 兜底③）：重算 200 + errors 携带 SESSION_ABORTED
        根因、中止日不计入 processed_dates，router 整体 rollback 后无半截快照——
        不再冒泡成 500 RECALCULATION_FAILED，也不产出误导性的 PendingRollbackError 条目"""
        _setup_real_history(test_db, "OBS_RC6", "OBS_RC6_INV")
        sub2 = create_subscription(
            test_db, portfolio_code="OBS_RC6", investor_code="OBS_RC6_INV",
            platform_code="MYCF", sub_type="subscribe",
            amount=Decimal("200.00"), apply_date=D0,
        )
        test_db.flush()
        subscription_service.confirm_single_subscription(test_db, sub2)
        test_db.commit()
        dates_before = [
            r.snapshot_date for r in test_db.query(PortfolioValueSnapshot).filter(
                PortfolioValueSnapshot.portfolio_code == "OBS_RC6"
            ).order_by(PortfolioValueSnapshot.snapshot_date).all()
        ]

        monkeypatch.setattr(snapshot_service, "_session_aborted", lambda db_: True)

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": "OBS_RC6",
                "start_date": D0.isoformat(),
                "end_date": D0.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "PendingRollbackError" not in resp.text
        entry = resp.json()["results"][0]
        assert entry["errors"][0]["code"] == "SESSION_ABORTED"
        assert entry["errors"][0]["date"] == D0.isoformat()
        assert entry["processed_dates"] == [], "中止日不该记为已处理"
        # 恰有一条终止根因条目，其后不再有该段条目
        aborted = [
            r for r in entry["auto_confirmed"] if r.get("code") == "SESSION_ABORTED"
        ]
        assert len(aborted) == 1
        assert len(entry["auto_confirmed"]) == 1
        # 整体 rollback：快照与重算前一致（「要么完整成功、要么无变化」）
        dates_after = [
            r.snapshot_date for r in test_db.query(PortfolioValueSnapshot).filter(
                PortfolioValueSnapshot.portfolio_code == "OBS_RC6"
            ).order_by(PortfolioValueSnapshot.snapshot_date).all()
        ]
        assert dates_after == dates_before == [D0]

    def test_error_entries_carry_code_and_details(self, client, admin_headers, test_db):
        """幽灵持仓事件致逐日失败：错误条目携带 code=POSITION_NOT_FOUND 与 details

        权益登记日取重算区间之前（不触发级联回退），事件保持 confirmed
        进入 EX_DAY 快照生成，命中生成侧持仓存在性硬拒绝（携 details）。
        """
        _setup_real_history(test_db, "OBS_RC3", "OBS_RC3_INV")
        create_product(test_db, code="GHOST3.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_share_change_event(
            test_db, "OBS_RC3", "GHOST3.OF", "CN_OTC",
            event_type="forced_adjustment", ex_date=EX_DAY,
            entitlement_date=date(2025, 6, 4), status="confirmed",
            platform_code="MYCF", shares_change=Decimal("10.00"),
            entitlement_shares=Decimal("0"), shares_before=Decimal("0"),
        )

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": "OBS_RC3",
                "start_date": D0.isoformat(),
                "end_date": EX_DAY.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        entry = resp.json()["results"][0]
        assert entry["errors"], "幽灵事件应使重算失败"
        err = entry["errors"][-1]
        assert err["date"] == EX_DAY.isoformat()
        assert err["code"] == "POSITION_NOT_FOUND"
        assert err["details"]["product_code"] == "GHOST3.OF"
        assert err["error"]  # 消息文本保留（向后兼容）


class TestCatchUpObservability:
    """验收 2：追平响应透传 warnings 与结构化中断错误"""

    def test_warnings_and_auto_confirmed_passthrough(self, client, admin_headers, test_db):
        """清零告警随追平响应可见（带 date 键），auto_confirmed 字段存在"""
        _setup(test_db, "OBS_CU1")
        event = svc_create_event(
            test_db, portfolio_code="OBS_CU1", event_type="forced_adjustment",
            product_code=FUND, market="CN_OTC", platform_code="MYCF",
            ex_date=EX_DAY, entitlement_date=D0,
            shares_change=Decimal("-100.00"),  # 100 − 100 = 0 → 清零告警
        )
        test_db.flush()
        confirm_share_change_event(test_db, event)
        test_db.commit()

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": "OBS_CU1", "to_date": EX_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["generated_count"] == 1
        assert data.get("failed_date") is None

        warnings = data["warnings"]
        assert warnings, "清零告警应透传至追平响应"
        zeroed = [w for w in warnings if w["type"] == "event_zeroed_position"]
        assert len(zeroed) == 1
        assert zeroed[0]["date"] == EX_DAY.isoformat()
        assert zeroed[0]["event_id"] == event.id
        # 无可确认项时为 None（字段存在、语义与单日 generate 一致）
        assert data.get("auto_confirmed") is None

    def test_failed_day_has_structured_error(self, client, admin_headers, test_db, monkeypatch):
        """中断日错误携带 error_code 与 error_details（error 仍为消息文本）"""
        _setup(test_db, "OBS_CU2")

        def boom(db, portfolio_code, target_date, check_continuity=True):
            from app.services.exceptions import BusinessError
            raise BusinessError(
                code="MISSING_NAV",
                message="模拟净值缺失",
                details={"target_date": target_date.isoformat()},
            )

        monkeypatch.setattr(snapshot_service, "generate_daily_snapshots", boom)

        resp = client.post(
            "/api/snapshots/catch-up",
            json={"portfolio_code": "OBS_CU2", "to_date": EX_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed_date"] == EX_DAY.isoformat()
        assert data["error"] == "模拟净值缺失"
        assert data["error_code"] == "MISSING_NAV"
        assert data["error_details"] == {"target_date": EX_DAY.isoformat()}


class TestSchedulerObservability:
    """验收 2：调度路径（run_snapshot_generate）失败语义（#203：负现金硬阻断）"""

    def test_negative_cash_blocks_scheduler_task(self, test_db):
        """负现金阻断调度回补（#203）：首个回补日即失败，
        snapshots_generated == 0、无 negative_cash warning（已从告警升级为硬阻断），
        基线日快照不受影响。

        用现金型数据而非清零告警：清零后基金行不入快照，后续回补日不缺净值，
        会逐日生成到任务目标日（约 300 日）；保留基金持仓则次日缺净值中断。
        """
        _setup(test_db, "OBS_TASK1")
        # 调度任务仅处理开启自动快照的组合
        port = test_db.query(Portfolio).filter_by(code="OBS_TASK1").first()
        port.auto_snapshot_enabled = True
        test_db.commit()

        event = svc_create_event(
            test_db, portfolio_code="OBS_TASK1", event_type="forced_adjustment",
            product_code=FUND, market="CN_OTC", platform_code="MYCF",
            ex_date=EX_DAY, entitlement_date=D0,
            cash_change=Decimal("-2000.00"),  # 现金 1000 − 2000 → 负现金阻断
        )
        test_db.flush()
        confirm_share_change_event(test_db, event)
        test_db.commit()

        result = run_snapshot_generate(test_db)
        assert result["snapshots_generated"] == 0
        assert result["warnings"] == []
        assert result["auto_confirm_failed"] == []

        # 阻断日无快照落库：最新快照日仍为基线 D0
        latest = test_db.query(func.max(PortfolioValueSnapshot.snapshot_date)).filter(
            PortfolioValueSnapshot.portfolio_code == "OBS_TASK1"
        ).scalar()
        assert latest == D0


class TestAutoConfirmSavepointIsolation:
    """验收 4：单条 DB 级失败不毒化 session、不级联误导性记录

    #419：savepoint 必须是 **session 级**（`db.begin_nested()`）。原用例的 fake 在做任何
    ORM 写入前直接 raise，session 从未经历一次失败的 flush，属假绿；本类主用例让 flush
    **真失败**（撞 `uq_trade_transfer_group`），并直接断言 ORM 事务状态复位。
    """

    CONFLICT_GROUP = "sub_conflict"

    def _portfolio_with_pending_subs(self, test_db, code: str, count: int = 2):
        create_portfolio(test_db, code=code, status="active")
        subs = [
            create_subscription(
                test_db, portfolio_code=code, investor_code="VIEWER",
                sub_type="subscribe", amount=100.0, apply_date=D0,
            )
            for _ in range(count)
        ]
        return subs

    def _conflicting_trade(self, portfolio_code: str) -> Trade:
        """与预置行同 (transfer_group, product_code, trade_type) → flush 必撞唯一约束"""
        return Trade(
            portfolio_code=portfolio_code, platform_code="MYCF",
            product_code=FUND, market="CN_OTC", trade_type="buy",
            transfer_group=self.CONFLICT_GROUP, amount=Decimal("1.00"),
            trade_date=D0, status="pending",
        )

    def _seed_conflict_row(self, test_db, holder_code: str):
        """预置撞约束的那一行。挂在另一个组合上，避免干扰被测组合的快照数据"""
        create_portfolio(test_db, code=holder_code, status="active")
        create_product(test_db, code=FUND, market="CN_OTC", product_type="OEF")
        create_trade(
            test_db, portfolio_code=holder_code, product_code=FUND, market="CN_OTC",
            trade_type="buy", transfer_group=self.CONFLICT_GROUP, trade_date=D0,
        )

    def test_real_flush_failure_does_not_poison_following_items(self, test_db, monkeypatch):
        """真实 ORM flush 失败：session 级 savepoint 回滚后循环继续，第二条正常确认，
        外层已 flush 的成果保留，全程无 PendingRollbackError（#419 验收 1-4）"""
        subs = self._portfolio_with_pending_subs(test_db, "OBS_SP1")
        self._seed_conflict_row(test_db, "OBS_SP1_HOLDER")

        # 失败前已 flush 的外层成果（模拟重算前几日重建的快照），须留在同一事务内
        outer = Trade(
            portfolio_code="OBS_SP1", platform_code="MYCF",
            product_code=FUND, market="CN_OTC", trade_type="sell",
            transfer_group="outer_work", amount=Decimal("1.00"),
            trade_date=D0, status="pending",
        )
        test_db.add(outer)
        test_db.flush()

        calls = []

        def fake_confirm(db_, sub, *, auto_flush=False, skip_cash_check=False):
            calls.append(sub.id)
            if len(calls) == 1:
                # 真写入 + 真 flush：撞 uq_trade_transfer_group
                db_.add(self._conflicting_trade("OBS_SP1"))
                db_.flush()
            sub.status = "confirmed"
            return sub

        monkeypatch.setattr(
            subscription_service, "confirm_single_subscription", fake_confirm
        )

        results = auto_confirm_after_snapshot(test_db, "OBS_SP1", EX_DAY)
        sub_results = [r for r in results if r.get("sub_type")]
        assert calls == [subs[0].id, subs[1].id], "两条申购都应被处理（循环未中断）"
        assert len(sub_results) == 2, "两条申购都应有结果（循环未被毒化中断）"
        assert sub_results[0]["action"] == "auto_confirm_failed"
        assert sub_results[0]["code"] == "IntegrityError"
        assert sub_results[1]["action"] == "auto_confirmed"
        for r in results:
            assert "PendingRollbackError" not in str(r.get("error", ""))

        # ORM 事务状态复位（连接级 savepoint 做不到：DEACTIVE + _rollback_exception）
        assert test_db._transaction._state is SessionTransactionState.ACTIVE
        assert test_db._transaction._rollback_exception is None
        # 撞约束那行已回滚到 savepoint（只剩预置的一行），外层成果仍在同一事务内
        assert test_db.query(Trade).filter_by(
            transfer_group=self.CONFLICT_GROUP
        ).count() == 1
        assert test_db.query(Trade).filter_by(transfer_group="outer_work").count() == 1
        # 后续 flush / commit 均不抛
        test_db.flush()
        test_db.commit()

    def test_connection_invalidated_aborts_segment_once(self, test_db, monkeypatch):
        """连接级失效：记一条 SESSION_ABORTED 根因后终止本段，
        后续条目不再产生误导性记录"""
        subs = self._portfolio_with_pending_subs(test_db, "OBS_SP2", count=2)

        def fake_confirm(db_, sub, *, auto_flush=False, skip_cash_check=False):
            raise DBAPIError(
                "SELECT ...", {}, RuntimeError("connection gone"),
                connection_invalidated=True,
            )

        monkeypatch.setattr(
            subscription_service, "confirm_single_subscription", fake_confirm
        )

        results = auto_confirm_after_snapshot(test_db, "OBS_SP2", EX_DAY)
        sub_results = [r for r in results if r.get("sub_type")]
        assert len(sub_results) == 1, "应终止于第一条，不再处理后续条目"
        assert sub_results[0]["code"] == "SESSION_ABORTED"
        assert sub_results[0]["id"] == subs[0].id

    def test_pending_rollback_error_aborts_segment_once(self, test_db, monkeypatch):
        """兜底③：守护内冒出 PendingRollbackError（session 已废的信号，非 DBAPIError）
        → 同样按 SESSION_ABORTED 终止本段，不再只认 connection_invalidated"""
        subs = self._portfolio_with_pending_subs(test_db, "OBS_SP3", count=2)

        def fake_confirm(db_, sub, *, auto_flush=False, skip_cash_check=False):
            raise PendingRollbackError(
                "This Session's transaction has been rolled back due to a "
                "previous exception during flush.",
                code="7s2a",
            )

        monkeypatch.setattr(
            subscription_service, "confirm_single_subscription", fake_confirm
        )

        results = auto_confirm_after_snapshot(test_db, "OBS_SP3", EX_DAY)
        sub_results = [r for r in results if r.get("sub_type")]
        assert len(sub_results) == 1
        assert sub_results[0]["code"] == "SESSION_ABORTED"
        assert sub_results[0]["id"] == subs[0].id

    def test_session_dead_after_failure_aborts_segment(self, test_db, monkeypatch):
        """兜底③：失败后探测到 session 仍不可用（如 savepoint 回滚自身失败）→
        按 SESSION_ABORTED 终止本段，条目保留真实根因文案。

        用 fake 探测而非真毒化：真毒化会连夹具连接一起废掉（#419 实测 teardown 抛
        「Can't reconnect until invalid savepoint transaction is rolled back」），
        破坏后续用例隔离；机制本身已由 test_real_flush_failure_* 证明。
        """
        subs = self._portfolio_with_pending_subs(test_db, "OBS_SP4", count=2)
        probes = {"n": 0}

        def fake_aborted(db_):
            probes["n"] += 1
            return probes["n"] > 1  # 进入守护时可用，失败之后已废

        monkeypatch.setattr(snapshot_service, "_session_aborted", fake_aborted)

        def fake_confirm(db_, sub, *, auto_flush=False, skip_cash_check=False):
            raise IntegrityError("INSERT ...", {}, RuntimeError("fake dup"))

        monkeypatch.setattr(
            subscription_service, "confirm_single_subscription", fake_confirm
        )

        results = auto_confirm_after_snapshot(test_db, "OBS_SP4", EX_DAY)
        sub_results = [r for r in results if r.get("sub_type")]
        assert len(sub_results) == 1
        assert sub_results[0]["code"] == "SESSION_ABORTED"
        assert sub_results[0]["id"] == subs[0].id
        assert "fake dup" in sub_results[0]["error"], "终止条目须保留真实根因文案"

    def test_dead_session_detected_before_entering_guard(self, test_db, monkeypatch):
        """兜底③（#419 附带①）：进入守护前 session 已废 → 直接终止本段，
        不再尝试 begin_nested（那只会逐条再抛同因 PendingRollbackError）"""
        self._portfolio_with_pending_subs(test_db, "OBS_SP5", count=2)
        monkeypatch.setattr(snapshot_service, "_session_aborted", lambda db_: True)

        results = auto_confirm_after_snapshot(test_db, "OBS_SP5", EX_DAY)
        sub_results = [r for r in results if r.get("sub_type")]
        assert len(sub_results) == 1
        assert sub_results[0]["action"] == "auto_confirm_failed"
        assert sub_results[0]["code"] == "SESSION_ABORTED"

    def test_unrollbackable_savepoint_terminates_segment_loudly(
        self, test_db, monkeypatch, caplog
    ):
        """savepoint 回滚自身失败 → 外层事务状态不可信：响亮记 ERROR、
        以 SESSION_ABORTED 终止本段，且条目保留真实根因（不静默当成功）"""
        subs = self._portfolio_with_pending_subs(test_db, "OBS_SP6", count=2)

        def fake_confirm(db_, sub, *, auto_flush=False, skip_cash_check=False):
            raise BusinessError(code="NAV_NOT_AVAILABLE", message="净值缺失")

        monkeypatch.setattr(
            subscription_service, "confirm_single_subscription", fake_confirm
        )

        def broken_rollback(self, *args, **kwargs):
            raise RuntimeError("savepoint gone")

        monkeypatch.setattr(SessionTransaction, "rollback", broken_rollback)

        with caplog.at_level(logging.ERROR, logger="app.services.snapshot_service"):
            results = auto_confirm_after_snapshot(test_db, "OBS_SP6", EX_DAY)

        sub_results = [r for r in results if r.get("sub_type")]
        assert len(sub_results) == 1, "回滚失败后不得继续处理后续条目"
        assert sub_results[0]["code"] == "SESSION_ABORTED"
        assert sub_results[0]["id"] == subs[0].id
        assert "净值缺失" in sub_results[0]["error"], "终止条目须保留真实根因文案"
        assert any(
            "savepoint 回滚失败" in r.getMessage() for r in caplog.records
        ), "回滚失败必须留下响亮 ERROR 留痕"


class TestAvailableSharesBaseline:
    """验收 5：market=None 基线跨市场/跨平台汇总（与增量口径一致）"""

    D = date(2025, 6, 6)

    def test_market_none_sums_across_markets_and_platforms(self, test_db):
        create_portfolio(test_db, code="OBS_ASH", status="active")
        create_product(test_db, code="LOF305", market="CN_EXCHANGE", product_type="LOF")
        create_product(test_db, code="LOF305", market="CN_OTC", product_type="LOF")
        # 同产品跨市场 + 同市场跨平台三行持仓
        create_position_snapshot(
            test_db, "OBS_ASH", "LOF305", "CN_EXCHANGE", snapshot_date=self.D,
            shares=100.0, market_value=100.0, platform_code="MYCF",
        )
        create_position_snapshot(
            test_db, "OBS_ASH", "LOF305", "CN_OTC", snapshot_date=self.D,
            shares=50.0, market_value=50.0, platform_code="MYCF",
        )
        create_position_snapshot(
            test_db, "OBS_ASH", "LOF305", "CN_OTC", snapshot_date=self.D,
            shares=30.0, market_value=30.0, platform_code="HBZQ",
        )

        assert calculate_available_shares(
            test_db, "OBS_ASH", "LOF305", market=None
        ) == Decimal("180.00"), "market=None 应为全部行基线之和"
        assert calculate_available_shares(
            test_db, "OBS_ASH", "LOF305", market="CN_OTC"
        ) == Decimal("80.00"), "指定 market 时同市场多平台也应汇总"
        assert calculate_available_shares(
            test_db, "OBS_ASH", "LOF305", market="CN_EXCHANGE"
        ) == Decimal("100.00")
