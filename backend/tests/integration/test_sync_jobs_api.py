"""
sync_jobs API 集成测试（P5.5）

验证：
- POST /api/sync-jobs/price → 200 + job_id
- 已有 running job 时 POST → 409 Conflict
- GET /api/sync-jobs/{id} → 返回状态
- GET /api/sync-jobs/{id}/details → 返回明细

投递入口自持会话（#592）：提交类用例统一把 SessionLocal 重定向回 test_db，
否则独立事务的提交逃逸 SAVEPOINT 回滚、且看不到 test_db 里预置的 running job。
"""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from app.models.sync_job import SyncJob
from app.models.nav_sync_detail import NavSyncDetail
from tests.session_helpers import patch_non_closing_session_local


class TestSubmitPriceSync:
    """POST /api/sync-jobs/price"""

    def test_submit_returns_job_id(self, client, admin_headers, test_db, monkeypatch):
        """提交任务返回 job_id"""
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.market_data_service._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            response = client.post(
                "/api/sync-jobs/price",
                json={"scope": "all"},
                headers=admin_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert isinstance(data["job_id"], int)
        assert data["status"] == "pending"

    def test_conflict_when_running(self, client, admin_headers, test_db, monkeypatch):
        """已有 running job → 409"""
        job = SyncJob(job_type="price_history_sync", status="running", triggered_by="manual")
        test_db.add(job)
        test_db.commit()

        patch_non_closing_session_local(monkeypatch, test_db)
        response = client.post(
            "/api/sync-jobs/price",
            json={"scope": "all"},
            headers=admin_headers,
        )
        assert response.status_code == 409

    def test_submit_without_auth(self, client, test_db):
        """未认证 → 401"""
        response = client.post(
            "/api/sync-jobs/price",
            json={"scope": "all"},
        )
        assert response.status_code == 401


class TestGetJobStatus:
    """GET /api/sync-jobs/{job_id}"""

    def test_get_existing_job(self, client, admin_headers, test_db):
        """查询存在的 job"""
        job = SyncJob(job_type="price_history_sync", status="success", triggered_by="manual",
                      total=123, done=123, success_count=120, failed_count=3)
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = client.get(
            f"/api/sync-jobs/{job.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job.id
        assert data["status"] == "success"
        assert data["total"] == 123
        assert data["done"] == 123

    def test_get_nonexistent_job(self, client, admin_headers, test_db):
        """查询不存在的 job → 404"""
        response = client.get(
            "/api/sync-jobs/99999",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestGetJobDetails:
    """GET /api/sync-jobs/{job_id}/details"""

    def test_get_details_with_nav_sync_detail(self, client, admin_headers, test_db):
        """查询 job 明细"""
        job = SyncJob(job_type="price_history_sync", status="partial", triggered_by="manual",
                      total=2, done=2, success_count=1, failed_count=1)
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        detail1 = NavSyncDetail(
            job_id=job.id, product_code="510300.SH", market="CN_EXCHANGE",
            nav_date="2025-07-12", status="success", synced_count=100, source="tushare",
        )
        detail2 = NavSyncDetail(
            job_id=job.id, product_code="000300.OF", market="CN_OTC",
            nav_date="2025-07-12", status="failed", error_message="API 超时",
        )
        test_db.add_all([detail1, detail2])
        test_db.commit()

        response = client.get(
            f"/api/sync-jobs/{job.id}/details",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job"]["id"] == job.id
        assert len(data["details"]) == 2

    def test_get_details_empty(self, client, admin_headers, test_db):
        """job 无明细记录"""
        job = SyncJob(job_type="price_history_sync", status="pending", triggered_by="manual")
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = client.get(
            f"/api/sync-jobs/{job.id}/details",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["details"] == []


class TestInterruptedStatus:
    """interrupted 状态查询"""

    def test_get_interrupted_job(self, client, admin_headers, test_db):
        """interrupted 状态可被查询"""
        job = SyncJob(
            job_type="price_history_sync", status="interrupted", triggered_by="manual",
            error_message="启动时标记为 interrupted：可能上次崩溃遗留",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        response = client.get(
            f"/api/sync-jobs/{job.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "interrupted"
