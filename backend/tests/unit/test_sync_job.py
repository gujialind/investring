"""
SyncJob 状态机 + 孤儿恢复 + 单 running 锁测试（P5.3）

验证：
- 孤儿 running / pending job 恢复（→ interrupted）
- 单 running 锁（已有 running 时 ConflictError）
- SyncJob 状态流转
"""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from app.models.sync_job import SyncJob
from app.services.market_data_service import recover_orphan_jobs, submit_price_sync_job, ConflictError
from tests.session_helpers import patch_non_closing_session_local


class TestOrphanRecovery:
    """孤儿 running / pending job 恢复"""

    def test_orphan_running_marked_interrupted(self, test_db, monkeypatch):
        """status='running' 的孤儿 job → interrupted"""
        job = SyncJob(job_type="price_history_sync", status="running", triggered_by="manual")
        test_db.add(job)
        test_db.commit()

        # recover_orphan_jobs 自持 SessionLocal（#592）：重定向回 test_db 即可走真实
        # 代码路径，且保持本夹具的 SAVEPOINT 隔离。
        patch_non_closing_session_local(monkeypatch, test_db)
        assert recover_orphan_jobs() == 1

        test_db.refresh(job)
        assert job.status == "interrupted"
        assert job.finished_at is not None

    def test_orphan_pending_marked_interrupted(self, test_db, monkeypatch):
        """#592：提交后进程崩溃遗留的 pending 行 → 同样被启动恢复标记 interrupted"""
        job = SyncJob(job_type="price_history_sync", status="pending", triggered_by="manual")
        test_db.add(job)
        test_db.commit()

        patch_non_closing_session_local(monkeypatch, test_db)
        assert recover_orphan_jobs() == 1

        test_db.refresh(job)
        assert job.status == "interrupted"
        assert "提交后未被执行体接管" in job.error_message


class TestSingleRunningLock:
    """单 running 锁"""

    def test_conflict_when_running_job_exists(self, test_db, monkeypatch):
        """已有 running job 时提交新 job → ConflictError"""
        job = SyncJob(job_type="price_history_sync", status="running", triggered_by="manual")
        test_db.add(job)
        test_db.commit()

        patch_non_closing_session_local(monkeypatch, test_db)
        with pytest.raises(ConflictError, match="已有价格同步任务在运行中"):
            submit_price_sync_job({"scope": "all"}, triggered_by="manual")

    def test_submit_succeeds_when_no_running(self, test_db, monkeypatch):
        """无 running job 时可正常提交"""
        # mock _get_executor 避免真正提交线程
        with patch("app.services.market_data_service._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            patch_non_closing_session_local(monkeypatch, test_db)
            job_id = submit_price_sync_job({"scope": "all"}, triggered_by="manual")
            assert job_id is not None
            assert isinstance(job_id, int)

            job = test_db.query(SyncJob).filter(SyncJob.id == job_id).first()
            assert job.status == "pending"
            assert job.triggered_by == "manual"


class TestStateMachine:
    """SyncJob 状态流转"""

    def test_pending_initial(self, test_db):
        """新建 job 初始状态为 pending"""
        job = SyncJob(job_type="price_history_sync", triggered_by="manual")
        test_db.add(job)
        test_db.commit()
        assert job.status == "pending"

    def test_interrupted_status(self, test_db):
        """interrupted 状态可被设置"""
        job = SyncJob(job_type="price_history_sync", status="running", triggered_by="manual")
        test_db.add(job)
        test_db.commit()

        job.status = "interrupted"
        job.finished_at = datetime.utcnow()
        test_db.commit()

        test_db.refresh(job)
        assert job.status == "interrupted"
