"""data_source_switch_job 单测（#882，SPEC v1.1 Block 3 / V4）。

探测全 mock（不触网）：逐产品 checkpoint 台账、探测失败 skipped 其余继续、
同值 no-op、终态 success/partial/failed、单 active 锁、投递失败无孤儿、
跨会话可见、request_id 传播、孤儿回收、整批预校验 fail-fast 零写入、源级快检早拒。
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.context import get_request_id
from app.models.audit_log import AuditLog
from app.models.product import Product
from app.models.sync_job import SyncJob
from app.services.data_source_switch_job import (
    BATCH_MAX_PRODUCTS,
    JOB_TYPE,
    _run_data_source_switch_job_impl,
    submit_data_source_switch_job,
)
from app.services.exceptions import BusinessError
from app.services.market_data_service import ConflictError, recover_orphan_jobs
from tests.factories import create_product
from tests.session_helpers import patch_non_closing_session_local


def _probe_error(reason, message="探测失败"):
    return BusinessError(
        "DATA_SOURCE_PROBE_FAILED", message,
        details={"reason": reason, "data_source": "akshare"},
    )


def _make_job(db, pairs, target="akshare"):
    job = SyncJob(
        job_type=JOB_TYPE,
        status="pending",
        params={"products": [list(p) for p in pairs], "data_source": target, "results": []},
        total=len(pairs),
        triggered_by="manual",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _product(db, code, market="CN_OTC", source="tushare", **kw):
    p = create_product(db, code=code, market=market, **kw)
    p.data_source = source
    db.commit()
    return p


class TestExecutorCheckpoint:
    def test_success_writes_source_status_audit_and_ledger(self, test_db):
        p = _product(test_db, "F882A")
        p.last_sync_at = datetime(2026, 1, 1, 8, 0)
        p.sync_error = "旧错误"
        p.data_source_status = "success"
        test_db.commit()
        job = _make_job(test_db, [("F882A", "CN_OTC")])

        with patch("app.services.data_source_probe.probe_data_source", return_value=None) as probe:
            _run_data_source_switch_job_impl(job.id, db=test_db)

        probe.assert_called_once()
        test_db.refresh(p)
        assert p.data_source == "akshare"
        assert p.data_source_status == "pending"
        assert p.sync_error is None
        # V5：last_sync_at 是旧源历史事实，保留不清空
        assert p.last_sync_at == datetime(2026, 1, 1, 8, 0)
        audit = test_db.query(AuditLog).filter(AuditLog.resource_id == "F882A(CN_OTC)").all()
        assert len(audit) == 1
        assert audit[0].action == "update"
        assert audit[0].resource_type == "product"
        assert '"tushare"' in audit[0].old_value and '"akshare"' in audit[0].new_value
        # 后台执行体无请求主体 → 审计 actor 落 SYSTEM 哨兵
        assert audit[0].investor_code == "SYSTEM"

        test_db.refresh(job)
        assert job.status == "success"
        assert (job.total, job.done, job.success_count, job.skipped_count, job.failed_count) == (1, 1, 1, 0, 0)
        assert job.error_message is None
        results = job.params["results"]
        assert results == [{
            "code": "F882A", "market": "CN_OTC", "old_source": "tushare",
            "new_source": "akshare", "status": "success", "reason": None,
        }]

    def test_probe_failure_skips_only_that_product(self, test_db):
        bad = _product(test_db, "F882B1")
        good = _product(test_db, "F882B2")
        job = _make_job(test_db, [("F882B1", "CN_OTC"), ("F882B2", "CN_OTC")])

        with patch(
            "app.services.data_source_probe.probe_data_source",
            side_effect=[_probe_error("no_data", "目标数据源未返回该产品的净值，请核对代码与市场"), None],
        ):
            _run_data_source_switch_job_impl(job.id, db=test_db)

        test_db.refresh(bad)
        test_db.refresh(good)
        assert bad.data_source == "tushare"  # skipped 不写
        assert good.data_source == "akshare"
        test_db.refresh(job)
        assert job.status == "partial"
        assert (job.success_count, job.skipped_count, job.failed_count) == (1, 1, 0)
        assert "F882B1" in job.error_message and "no_data" in job.error_message
        statuses = {r["code"]: r["status"] for r in job.params["results"]}
        assert statuses == {"F882B1": "skipped", "F882B2": "success"}
        # 被跳过产品无审计行
        assert test_db.query(AuditLog).filter(AuditLog.resource_id == "F882B1(CN_OTC)").count() == 0

    def test_all_skipped_marks_failed_loudly(self, test_db):
        _product(test_db, "F882C")
        job = _make_job(test_db, [("F882C", "CN_OTC")])
        with patch("app.services.data_source_probe.probe_data_source", side_effect=_probe_error("unreachable")):
            _run_data_source_switch_job_impl(job.id, db=test_db)
        test_db.refresh(job)
        assert job.status == "failed"
        assert job.success_count == 0 and job.skipped_count == 1
        assert job.error_message

    def test_same_value_is_noop_without_probe_audit_or_reset(self, test_db):
        p = _product(test_db, "F882D", source="akshare")
        p.data_source_status = "success"
        test_db.commit()
        job = _make_job(test_db, [("F882D", "CN_OTC")], target="akshare")
        with patch("app.services.data_source_probe.probe_data_source") as probe:
            _run_data_source_switch_job_impl(job.id, db=test_db)
        probe.assert_not_called()
        test_db.refresh(p)
        assert p.data_source_status == "success"  # 不重置
        assert test_db.query(AuditLog).filter(AuditLog.resource_id == "F882D(CN_OTC)").count() == 0
        test_db.refresh(job)
        assert job.status == "success"
        assert job.params["results"][0]["reason"] == "同值无变更"
        assert job.params["results"][0]["old_source"] == "akshare"

    def test_product_deleted_after_submit_marks_failed_entry(self, test_db):
        _product(test_db, "F882E")
        job = _make_job(test_db, [("F882GONE", "CN_OTC"), ("F882E", "CN_OTC")])
        with patch("app.services.data_source_probe.probe_data_source", return_value=None):
            _run_data_source_switch_job_impl(job.id, db=test_db)
        test_db.refresh(job)
        assert job.status == "partial"
        assert job.failed_count == 1 and job.success_count == 1
        assert job.params["results"][0]["status"] == "failed"
        assert "不存在" in job.params["results"][0]["reason"]

    def test_unexpected_exception_rolls_back_and_continues(self, test_db):
        p1 = _product(test_db, "F882F1")
        _product(test_db, "F882F2")
        job = _make_job(test_db, [("F882F1", "CN_OTC"), ("F882F2", "CN_OTC")])
        with patch(
            "app.services.data_source_probe.probe_data_source",
            side_effect=[RuntimeError("连接炸了"), None],
        ):
            _run_data_source_switch_job_impl(job.id, db=test_db)
        test_db.refresh(p1)
        assert p1.data_source == "tushare"
        test_db.refresh(job)
        assert job.status == "partial"
        assert job.failed_count == 1 and job.success_count == 1
        assert "连接炸了" in job.params["results"][0]["reason"]

    def test_error_message_truncated_to_1000(self, test_db):
        pairs = []
        for i in range(30):
            code = f"F882T{i}"
            _product(test_db, code)
            pairs.append((code, "CN_OTC"))
        job = _make_job(test_db, pairs)
        with patch(
            "app.services.data_source_probe.probe_data_source",
            side_effect=_probe_error("unreachable", "数据源暂时不可达，请稍后重试" * 5),
        ):
            _run_data_source_switch_job_impl(job.id, db=test_db)
        test_db.refresh(job)
        assert job.status == "failed"
        assert len(job.error_message) <= 1000

    def test_request_id_propagates_to_probe_context(self, test_db):
        _product(test_db, "F882R")
        job = _make_job(test_db, [("F882R", "CN_OTC")])
        seen = {}

        def _capture(*args, **kwargs):
            seen["request_id"] = get_request_id()
            return None

        with patch("app.services.data_source_probe.probe_data_source", side_effect=_capture):
            _run_data_source_switch_job_impl(job.id, request_id="req-882", db=test_db)
        assert seen["request_id"] == "req-882"
        # 上下文不泄漏给调用线程的后续代码
        assert get_request_id() != "req-882"


class TestSubmitGuards:
    def test_submit_creates_pending_job(self, test_db, monkeypatch):
        _product(test_db, "F882S")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", return_value=None), \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([["F882S", "CN_OTC"]], "akshare")
        job = test_db.query(SyncJob).get(job_id)
        assert job.job_type == JOB_TYPE and job.status == "pending"
        assert job.total == 1
        assert job.params["products"] == [["F882S", "CN_OTC"]]
        assert job.params["data_source"] == "akshare"
        mock_exec.return_value.submit.assert_called_once()

    def test_submit_conflict_when_same_type_active(self, test_db, monkeypatch):
        _product(test_db, "F882S2")
        test_db.add(SyncJob(job_type=JOB_TYPE, status="running", triggered_by="manual"))
        test_db.commit()
        patch_non_closing_session_local(monkeypatch, test_db)
        with pytest.raises(ConflictError):
            submit_data_source_switch_job([["F882S2", "CN_OTC"]], "akshare")

    def test_other_job_types_do_not_block_switch(self, test_db, monkeypatch):
        _product(test_db, "F882S3")
        test_db.add(SyncJob(job_type="price_history_sync", status="running", triggered_by="manual"))
        test_db.add(SyncJob(job_type="snapshot_recalc", status="running", triggered_by="manual"))
        test_db.commit()
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", return_value=None), \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([["F882S3", "CN_OTC"]], "akshare")
        assert test_db.query(SyncJob).get(job_id).status == "pending"

    def test_delivery_failure_marks_failed_not_pending(self, test_db, monkeypatch):
        _product(test_db, "F882S4")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", return_value=None), \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value.submit.side_effect = RuntimeError("线程池满了")
            with pytest.raises(RuntimeError):
                submit_data_source_switch_job([["F882S4", "CN_OTC"]], "akshare")
        jobs = test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).all()
        assert len(jobs) == 1
        assert jobs[0].status == "failed"  # 不留 pending 孤儿占锁
        assert "任务投递失败" in jobs[0].error_message

    def test_submitted_job_visible_to_independent_session(self, test_db, monkeypatch):
        """交接契约：真实自持会话提交后独立会话可读见（用后自清理）。

        产品须真正落库——test_db 工作在 SAVEPOINT 里，其写入对独立会话不可见，
        故造数也走真实 SessionLocal（与 price sync 的 scope=all 免造数形态不同）。
        """
        from app.database import SessionLocal

        setup = SessionLocal()
        try:
            p = create_product(setup, code="F882S5", market="CN_OTC")
            p.data_source = "tushare"
            setup.commit()
        finally:
            setup.close()
        with patch("app.services.data_source_probe.probe_data_source", return_value=None), \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([["F882S5", "CN_OTC"]], "akshare")
        probe = SessionLocal()
        try:
            assert probe.query(SyncJob).get(job_id) is not None
        finally:
            probe.query(SyncJob).filter(SyncJob.id == job_id).delete()
            probe.query(Product).filter(Product.code == "F882S5", Product.market == "CN_OTC").delete()
            probe.commit()
            probe.close()

    def test_quick_check_source_failure_rejects_whole_batch(self, test_db, monkeypatch):
        _product(test_db, "F882Q1")
        _product(test_db, "F882Q2")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", side_effect=_probe_error("unreachable")):
            with pytest.raises(BusinessError) as exc:
                submit_data_source_switch_job([["F882Q1", "CN_OTC"], ["F882Q2", "CN_OTC"]], "akshare")
        assert exc.value.code == "DATA_SOURCE_PROBE_FAILED"
        assert test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).count() == 0
        assert test_db.query(Product).filter(Product.code == "F882Q1").one().data_source == "tushare"

    def test_quick_check_token_missing_rejects_whole_batch(self, test_db, monkeypatch):
        _product(test_db, "F882Q3", source="akshare")
        patch_non_closing_session_local(monkeypatch, test_db)
        token_err = BusinessError("INVALID_DATA_SOURCE", "Tushare token 未配置", details={"reason": "token_missing"})
        with patch("app.services.data_source_probe.probe_data_source", side_effect=token_err):
            with pytest.raises(BusinessError) as exc:
                submit_data_source_switch_job([["F882Q3", "CN_OTC"]], "tushare")
        assert exc.value.code == "INVALID_DATA_SOURCE"
        assert test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).count() == 0

    def test_quick_check_no_data_proceeds_to_job(self, test_db, monkeypatch):
        """产品级 no_data 不是源故障：不早拒，交逐产品循环 skipped。"""
        _product(test_db, "F882Q4")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", side_effect=_probe_error("no_data")), \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([["F882Q4", "CN_OTC"]], "akshare")
        assert test_db.query(SyncJob).get(job_id).status == "pending"


class TestPrevalidationFailFast:
    """整批拒绝零写入：任一非法 → 不建 job、不改任何产品行。"""

    def _assert_rejected(self, test_db, monkeypatch, products, data_source, code):
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", return_value=None):
            with pytest.raises(BusinessError) as exc:
                submit_data_source_switch_job(products, data_source)
        assert exc.value.code == code
        assert test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).count() == 0

    def test_over_limit_rejected(self, test_db, monkeypatch):
        pairs = [[f"F882L{i}", "CN_OTC"] for i in range(BATCH_MAX_PRODUCTS + 1)]
        self._assert_rejected(test_db, monkeypatch, pairs, "akshare", "INVALID_PARAM")

    def test_duplicate_entry_rejected(self, test_db, monkeypatch):
        _product(test_db, "F882DUP")
        self._assert_rejected(
            test_db, monkeypatch,
            [["F882DUP", "CN_OTC"], ["F882DUP", "CN_OTC"]], "akshare", "INVALID_PARAM",
        )

    def test_empty_list_rejected(self, test_db, monkeypatch):
        self._assert_rejected(test_db, monkeypatch, [], "akshare", "INVALID_PARAM")

    def test_missing_product_rejected(self, test_db, monkeypatch):
        _product(test_db, "F882OK")
        self._assert_rejected(
            test_db, monkeypatch,
            [["F882OK", "CN_OTC"], ["F882NOPE", "CN_OTC"]], "akshare", "INVALID_PARAM",
        )
        # 合法条目也不被部分写入
        assert test_db.query(Product).filter(Product.code == "F882OK").one().data_source == "tushare"

    def test_virtual_product_rejected(self, test_db, monkeypatch):
        # 虚拟产品 market=""（种子口径），清单条目市场为空 → fail-fast
        self._assert_rejected(test_db, monkeypatch, [["CASH", ""]], "akshare", "INVALID_PARAM")

    def test_hk_tushare_incompatible_rejected(self, test_db, monkeypatch):
        _product(test_db, "F882HK", market="HK_MUTUAL", source="akshare")
        self._assert_rejected(test_db, monkeypatch, [["F882HK", "HK_MUTUAL"]], "tushare", "INVALID_DATA_SOURCE")

    def test_illegal_data_source_rejected(self, test_db, monkeypatch):
        _product(test_db, "F882X")
        self._assert_rejected(test_db, monkeypatch, [["F882X", "CN_OTC"]], "wind", "INVALID_DATA_SOURCE")

    def test_null_data_source_rejected(self, test_db, monkeypatch):
        _product(test_db, "F882N")
        self._assert_rejected(test_db, monkeypatch, [["F882N", "CN_OTC"]], None, "INVALID_PARAM")

    def test_malformed_entry_rejected(self, test_db, monkeypatch):
        self._assert_rejected(test_db, monkeypatch, [["F882M"]], "akshare", "INVALID_PARAM")

    def test_null_entry_field_rejected(self, test_db, monkeypatch):
        self._assert_rejected(test_db, monkeypatch, [[None, "CN_OTC"]], "akshare", "INVALID_PARAM")

    def test_whitespace_entry_stripped_and_accepted(self, test_db, monkeypatch):
        _product(test_db, "F882W")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", return_value=None), \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([[" F882W ", " CN_OTC "]], "akshare")
        assert test_db.query(SyncJob).get(job_id).params["products"] == [["F882W", "CN_OTC"]]


class TestOrphanRecovery:
    def test_startup_recovery_marks_switch_orphans_interrupted(self, test_db, monkeypatch):
        pending = SyncJob(job_type=JOB_TYPE, status="pending", triggered_by="manual")
        running = SyncJob(job_type=JOB_TYPE, status="running", triggered_by="manual")
        test_db.add_all([pending, running])
        test_db.commit()
        patch_non_closing_session_local(monkeypatch, test_db)
        count = recover_orphan_jobs()
        assert count >= 2
        test_db.refresh(pending)
        test_db.refresh(running)
        assert pending.status == "interrupted"
        assert running.status == "interrupted"


class TestSubmitSameValueNoOp:
    """B1（L2 评论 6925267091）：受理阶段同值 no-op，与单只 F1 同口径（不校验/不探）。

    既有 test_same_value_is_noop_without_probe_audit_or_reset 直调执行体，绕过受理路径；
    本组在 submit 入口钉住：全同值跳过快检仍受理；存量 HK+tushare 同值不被误拦；
    混合批次只校验/快检实际换源条目。
    """

    def test_all_same_value_accepts_without_quick_check(self, test_db, monkeypatch):
        _product(test_db, "F882N1", source="akshare")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source") as probe, \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([["F882N1", "CN_OTC"]], "akshare")
        probe.assert_not_called()
        assert test_db.query(SyncJob).get(job_id).status == "pending"

    def test_same_value_accepts_even_if_source_unreachable(self, test_db, monkeypatch):
        _product(test_db, "F882N2", source="akshare")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch(
            "app.services.data_source_probe.probe_data_source",
            side_effect=_probe_error("unreachable"),
        ), patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([["F882N2", "CN_OTC"]], "akshare")
        assert test_db.query(SyncJob).get(job_id).status == "pending"

    def test_hk_legacy_tushare_same_value_accepts_without_probe(self, test_db, monkeypatch):
        # 存量港互认+tushare（历史非法组合）同值重提交：F1 口径整体 no-op，不校验不探
        _product(test_db, "F882N3", market="HK_MUTUAL", source="tushare")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source") as probe, \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job([["F882N3", "HK_MUTUAL"]], "tushare")
        probe.assert_not_called()
        assert test_db.query(SyncJob).get(job_id).status == "pending"

    def test_mixed_batch_quick_checks_first_changed_only(self, test_db, monkeypatch):
        # HK 已是目标源（同值 no-op），CN 实际换源：只校验/快检 CN
        _product(test_db, "F882N4HK", market="HK_MUTUAL", source="akshare")
        _product(test_db, "F882N4CN", source="tushare")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source", return_value=None) as probe, \
             patch("app.services.data_source_switch_job._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_data_source_switch_job(
                [["F882N4HK", "HK_MUTUAL"], ["F882N4CN", "CN_OTC"]], "akshare",
            )
        # HK 同值不探不校验；快检只打实际换源的首只（CN）
        probe.assert_called_once()
        assert probe.call_args[0][1] == "F882N4CN"
        assert test_db.query(SyncJob).get(job_id).status == "pending"

    def test_actual_change_still_validated_before_quick_check(self, test_db, monkeypatch):
        _product(test_db, "F882N5", market="HK_MUTUAL", source="akshare")
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.data_source_probe.probe_data_source") as probe:
            with pytest.raises(BusinessError) as exc:
                submit_data_source_switch_job([["F882N5", "HK_MUTUAL"]], "tushare")
        assert exc.value.code == "INVALID_DATA_SOURCE"
        probe.assert_not_called()


class TestFailedTerminalPreservesCheckpoints:
    """B5（L2 评论 6925267091）后端钉住：逐产品 checkpoint 已提交的成功不被外层异常撤销。

    failed ≠ 零成功；前端文案须按 success_count 表达（前端回归见 E2E）。
    """

    def test_second_checkpoint_commit_error_keeps_first_success(self, test_db, monkeypatch):
        p1 = _product(test_db, "F882K1")
        p2 = _product(test_db, "F882K2")
        job = _make_job(test_db, [("F882K1", "CN_OTC"), ("F882K2", "CN_OTC")])
        real_commit = test_db.commit
        calls = {"n": 0}

        def flaky_commit():
            calls["n"] += 1
            # 1=running 落库，2=首只 checkpoint，3=第二只 checkpoint（注入失败）
            if calls["n"] == 3:
                raise RuntimeError("模拟第二只 checkpoint 提交失败")
            return real_commit()

        monkeypatch.setattr(test_db, "commit", flaky_commit)
        with patch("app.services.data_source_probe.probe_data_source", return_value=None):
            _run_data_source_switch_job_impl(job.id, db=test_db)

        test_db.refresh(job)
        test_db.refresh(p1)
        test_db.refresh(p2)
        assert job.status == "failed"
        assert (job.success_count, job.done, job.failed_count) == (1, 1, 0)
        assert len(job.params["results"]) == 1
        assert job.params["results"][0]["status"] == "success"
        assert p1.data_source == "akshare"  # 已提交 checkpoint 保留
        assert p2.data_source == "tushare"  # 失败只回滚当前事务