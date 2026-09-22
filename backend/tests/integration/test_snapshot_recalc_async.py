# ============================================================================
# 集成测试：issue #89 快照重算异步 job 化 (test_snapshot_recalc_async.py)
# ============================================================================
# - submit_snapshot_recalc_job：落 job / 同类型单 active 锁 / 与价格同步锁互不阻塞
# - _run_snapshot_recalc_job_impl：成功 commit、errors 整体回滚、异常兜底的 job 终态
# - REST POST /api/snapshots/recalculate-async：提交 / 409 冲突 / 404 组合不存在
# ============================================================================

import pytest
from unittest.mock import patch, MagicMock

from app.models.sync_job import SyncJob
from app.services.market_data_service import ConflictError, submit_price_sync_job
from app.services.snapshot_recalc_job import (
    submit_snapshot_recalc_job,
    _run_snapshot_recalc_job_impl,
)
from tests.factories import create_portfolio, create_sync_job
from tests.session_helpers import patch_non_closing_session_local


PARAMS = {"portfolio_code": None, "start_date": "2025-01-06", "end_date": "2025-01-07"}


def _mock_executor():
    return patch("app.services.snapshot_recalc_job._get_executor", return_value=MagicMock())


def _mock_price_executor():
    return patch("app.services.market_data_service._get_executor", return_value=MagicMock())


class TestSubmit:
    """提交与锁语义（#592：投递入口自持会话，重定向回 test_db 保持 SAVEPOINT 隔离）"""

    def test_submit_creates_pending_job(self, test_db, monkeypatch):
        patch_non_closing_session_local(monkeypatch, test_db)
        with _mock_executor():
            job_id = submit_snapshot_recalc_job(PARAMS)
        job = test_db.query(SyncJob).filter(SyncJob.id == job_id).first()
        assert job.job_type == "snapshot_recalc"
        assert job.status == "pending"
        assert job.params["start_date"] == "2025-01-06"

    def test_conflict_when_recalc_job_active(self, test_db, monkeypatch):
        create_sync_job(test_db, job_type="snapshot_recalc", status="running")
        patch_non_closing_session_local(monkeypatch, test_db)
        with pytest.raises(ConflictError, match="已有快照重算任务在运行中"):
            submit_snapshot_recalc_job(PARAMS)

    def test_price_job_does_not_block_recalc(self, test_db, monkeypatch):
        """价格同步任务运行中不阻塞重算提交（锁按 job_type 分离）"""
        create_sync_job(test_db, job_type="price_history_sync", status="running")
        patch_non_closing_session_local(monkeypatch, test_db)
        with _mock_executor():
            job_id = submit_snapshot_recalc_job(PARAMS)
        assert job_id is not None

    def test_recalc_job_does_not_block_price(self, test_db, monkeypatch):
        """重算任务运行中不阻塞价格同步提交"""
        create_sync_job(test_db, job_type="snapshot_recalc", status="running")
        patch_non_closing_session_local(monkeypatch, test_db)
        with _mock_price_executor():
            job_id = submit_price_sync_job({"scope": "all"})
        assert job_id is not None

    def test_submission_failure_marks_job_failed_not_pending(self, test_db, monkeypatch):
        """投递抛错 → job 终态 failed，不留永久占锁的 pending 孤儿（#592，同价格同步）"""
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.snapshot_recalc_job._get_executor") as mock_exec:
            mock_exec.return_value.submit.side_effect = RuntimeError("线程池已关闭")
            with pytest.raises(RuntimeError, match="线程池已关闭"):
                submit_snapshot_recalc_job(PARAMS)

        job = test_db.query(SyncJob).filter(
            SyncJob.job_type == "snapshot_recalc"
        ).order_by(SyncJob.id.desc()).first()
        assert job.status == "failed"
        assert job.error_message and "任务投递失败" in job.error_message
        assert job.finished_at is not None

        # failed 终态不再阻塞后续提交（pending 孤儿会永久 409）
        with _mock_executor():
            second_id = submit_snapshot_recalc_job(PARAMS)
        assert second_id > job.id


class TestRunImpl:
    """后台执行体终态（注入 test_db，mock recalculate_snapshots）"""

    def _make_job(self, db, params=PARAMS):
        return create_sync_job(db, job_type="snapshot_recalc", status="pending", params=params)

    def test_success_sets_job_success(self, test_db):
        job = self._make_job(test_db)
        result = {"success": True, "message": "ok", "results": [
            {"portfolio_code": "P1", "errors": [], "total_processed": 3},
        ]}
        with patch(
            "app.services.snapshot_recalc_job.recalculate_snapshots",
            return_value=result,
        ):
            _run_snapshot_recalc_job_impl(job.id, db=test_db)
        test_db.refresh(job)
        assert job.status == "success"
        assert job.success_count == 3
        assert job.failed_count == 0
        assert job.finished_at is not None

    def test_errors_set_job_failed_with_message(self, test_db):
        """任一日 errors → job failed，error_message 含失败日期"""
        job = self._make_job(test_db)
        result = {"success": True, "message": "ok", "results": [
            {"portfolio_code": "P1", "errors": [
                {"date": "2025-01-07", "error": "校验失败: NAV 缺失"},
            ], "total_processed": 1},
        ]}
        with patch(
            "app.services.snapshot_recalc_job.recalculate_snapshots",
            return_value=result,
        ):
            _run_snapshot_recalc_job_impl(job.id, db=test_db)
        test_db.refresh(job)
        assert job.status == "failed"
        assert "2025-01-07" in job.error_message
        assert job.failed_count == 1

    def test_precheck_exception_sets_job_failed(self, test_db):
        """预校验 ValueError → job failed（未删任何快照，整体无变化）"""
        job = self._make_job(test_db)
        with patch(
            "app.services.snapshot_recalc_job.recalculate_snapshots",
            side_effect=ValueError("预校验失败，未删除任何快照"),
        ):
            _run_snapshot_recalc_job_impl(job.id, db=test_db)
        test_db.refresh(job)
        assert job.status == "failed"
        assert "预校验失败" in job.error_message


class TestRestAsyncEndpoint:
    """REST POST /api/snapshots/recalculate-async"""

    def test_submit_returns_job_id(self, client, admin_headers, test_db, monkeypatch):
        create_portfolio(test_db, code="RCA_P1", status="active")
        patch_non_closing_session_local(monkeypatch, test_db)
        with _mock_executor():
            resp = client.post(
                "/api/snapshots/recalculate-async",
                json={"portfolio_code": "RCA_P1",
                      "start_date": "2025-01-06", "end_date": "2025-01-07"},
                headers=admin_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        job = test_db.query(SyncJob).filter(SyncJob.id == data["job_id"]).first()
        assert job.job_type == "snapshot_recalc"

    def test_conflict_returns_409(self, client, admin_headers, test_db, monkeypatch):
        create_portfolio(test_db, code="RCA_P2", status="active")
        create_sync_job(test_db, job_type="snapshot_recalc", status="running")
        patch_non_closing_session_local(monkeypatch, test_db)
        resp = client.post(
            "/api/snapshots/recalculate-async",
            json={"portfolio_code": "RCA_P2",
                  "start_date": "2025-01-06", "end_date": "2025-01-07"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "RECALC_JOB_CONFLICT"

    def test_portfolio_not_found_returns_404(self, client, admin_headers, test_db):
        resp = client.post(
            "/api/snapshots/recalculate-async",
            json={"portfolio_code": "NO_SUCH_PORT",
                  "start_date": "2025-01-06", "end_date": "2025-01-07"},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "PORTFOLIO_NOT_FOUND"
