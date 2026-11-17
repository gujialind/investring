"""POST /api/products/batch-data-source 集成测试（#882，SPEC v1.1 §2.2）。

端点契约：admin 门控、显式 null INVALID_PARAM、整批预校验 fail-fast 零写入、
单 active 锁 409、受理回执 job_id；终态与逐产品台账经 GET /sync-jobs/{id} 与 /details。
探测全 mock（不触网）。
"""
from unittest.mock import MagicMock, patch

import pytest

from app.models.product import Product
from app.models.sync_job import SyncJob
from app.services.data_source_switch_job import (
    BATCH_MAX_PRODUCTS,
    JOB_TYPE,
    _run_data_source_switch_job_impl,
)
from app.services.exceptions import BusinessError
from tests.factories import create_product
from tests.session_helpers import patch_non_closing_session_local

URL = "/api/products/batch-data-source"


def _product(db, code, market="CN_OTC", source="tushare"):
    p = create_product(db, code=code, market=market)
    p.data_source = source
    db.commit()
    return p


def _post(client, headers, products, data_source="akshare"):
    return client.post(
        URL, json={"products": products, "data_source": data_source}, headers=headers
    )


@pytest.fixture
def mocked_submit(test_db, monkeypatch):
    """投递链路替身：自持会话重定向 + 探测通过 + 线程池不真跑。"""
    patch_non_closing_session_local(monkeypatch, test_db)
    with patch("app.services.data_source_probe.probe_data_source", return_value=None), \
         patch("app.services.data_source_switch_job._get_executor") as mock_exec:
        mock_exec.return_value = MagicMock()
        yield mock_exec


class TestBatchEndpointContract:
    def test_admin_submit_returns_job_id(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882A")
        resp = _post(client, admin_headers, [{"code": "B882A", "market": "CN_OTC"}])
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["job_id"], int)
        assert data["status"] == "pending"
        assert data["message"] == "任务已提交"
        job = test_db.query(SyncJob).get(data["job_id"])
        assert job.job_type == JOB_TYPE
        assert job.params["products"] == [["B882A", "CN_OTC"]]

    def test_non_admin_forbidden(self, client, viewer_headers, test_db):
        _product(test_db, "B882B")
        resp = _post(client, viewer_headers, [{"code": "B882B", "market": "CN_OTC"}])
        assert resp.status_code == 403

    def test_unauthenticated(self, client, test_db):
        resp = _post(client, {}, [{"code": "B882C", "market": "CN_OTC"}])
        assert resp.status_code == 401

    def test_explicit_null_data_source_invalid_param(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882D")
        resp = client.post(
            URL,
            json={"products": [{"code": "B882D", "market": "CN_OTC"}], "data_source": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        assert test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).count() == 0

    def test_explicit_null_products_invalid_param(self, client, admin_headers, test_db, mocked_submit):
        resp = client.post(URL, json={"products": None, "data_source": "akshare"}, headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_null_entry_field_invalid_param(self, client, admin_headers, test_db, mocked_submit):
        resp = _post(client, admin_headers, [{"code": None, "market": "CN_OTC"}])
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_unknown_field_rejected(self, client, admin_headers, test_db, mocked_submit):
        resp = client.post(
            URL,
            json={"products": [{"code": "B882E", "market": "CN_OTC"}], "data_source": "akshare", "force": True},
            headers=admin_headers,
        )
        assert resp.status_code == 422  # extra="forbid"：v1 无 force 参数

    def test_over_limit_zero_write(self, client, admin_headers, test_db, mocked_submit):
        entries = [{"code": f"B882L{i}", "market": "CN_OTC"} for i in range(BATCH_MAX_PRODUCTS + 1)]
        resp = _post(client, admin_headers, entries)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        assert test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).count() == 0

    def test_duplicate_entry_zero_write(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882F")
        resp = _post(client, admin_headers, [
            {"code": "B882F", "market": "CN_OTC"}, {"code": "B882F", "market": "CN_OTC"},
        ])
        assert resp.status_code == 422
        assert test_db.query(Product).filter(Product.code == "B882F").one().data_source == "tushare"

    def test_missing_product_zero_write(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882G")
        resp = _post(client, admin_headers, [
            {"code": "B882G", "market": "CN_OTC"}, {"code": "B882NOPE", "market": "CN_OTC"},
        ])
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        assert test_db.query(Product).filter(Product.code == "B882G").one().data_source == "tushare"
        assert test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).count() == 0

    def test_hk_tushare_incompatible_zero_write(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882HK", market="HK_MUTUAL", source="akshare")
        resp = _post(client, admin_headers, [{"code": "B882HK", "market": "HK_MUTUAL"}], data_source="tushare")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATA_SOURCE"
        assert test_db.query(Product).filter(Product.code == "B882HK").one().data_source == "akshare"

    def test_illegal_source_rejected(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882H")
        resp = _post(client, admin_headers, [{"code": "B882H", "market": "CN_OTC"}], data_source="wind")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATA_SOURCE"

    def test_conflict_when_switch_job_active(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882I")
        test_db.add(SyncJob(job_type=JOB_TYPE, status="running", triggered_by="manual"))
        test_db.commit()
        resp = _post(client, admin_headers, [{"code": "B882I", "market": "CN_OTC"}])
        assert resp.status_code == 409

    def test_source_level_probe_failure_rejects_batch(self, client, admin_headers, test_db, monkeypatch):
        _product(test_db, "B882J")
        patch_non_closing_session_local(monkeypatch, test_db)
        err = BusinessError("DATA_SOURCE_PROBE_FAILED", "数据源暂时不可达，请稍后重试", details={"reason": "unreachable"})
        with patch("app.services.data_source_probe.probe_data_source", side_effect=err):
            resp = _post(client, admin_headers, [{"code": "B882J", "market": "CN_OTC"}])
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "DATA_SOURCE_PROBE_FAILED"
        assert test_db.query(SyncJob).filter(SyncJob.job_type == JOB_TYPE).count() == 0
        assert test_db.query(Product).filter(Product.code == "B882J").one().data_source == "tushare"


class TestBatchJobVisibility:
    def test_status_and_details_expose_ledger(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882V1")
        _product(test_db, "B882V2")
        resp = _post(client, admin_headers, [
            {"code": "B882V1", "market": "CN_OTC"}, {"code": "B882V2", "market": "CN_OTC"},
        ])
        job_id = resp.json()["job_id"]

        # 执行体：首只探测失败 skipped、次只成功 → partial
        err = BusinessError("DATA_SOURCE_PROBE_FAILED", "目标数据源未返回该产品的净值，请核对代码与市场", details={"reason": "no_data"})
        with patch("app.services.data_source_probe.probe_data_source", side_effect=[err, None]):
            _run_data_source_switch_job_impl(job_id, db=test_db)

        status = client.get(f"/api/sync-jobs/{job_id}", headers=admin_headers)
        assert status.status_code == 200
        body = status.json()
        assert body["job_type"] == JOB_TYPE
        assert body["status"] == "partial"
        assert (body["total"], body["done"], body["success_count"], body["skipped_count"]) == (2, 2, 1, 1)

        details = client.get(f"/api/sync-jobs/{job_id}/details", headers=admin_headers)
        assert details.status_code == 200
        d = details.json()
        assert d["job"]["id"] == job_id
        by_code = {r["code"]: r for r in d["details"]}
        assert by_code["B882V1"]["status"] == "skipped"
        assert by_code["B882V1"]["old_source"] is None
        assert "no_data" in by_code["B882V1"]["reason"]
        assert by_code["B882V2"]["status"] == "success"
        assert by_code["B882V2"]["old_source"] == "tushare"
        assert by_code["B882V2"]["new_source"] == "akshare"
        # 台账 old_source 供反向批量切回（误操作恢复，SPEC §6）
        assert test_db.query(Product).filter(Product.code == "B882V1").one().data_source == "tushare"
        assert test_db.query(Product).filter(Product.code == "B882V2").one().data_source == "akshare"

    def test_details_empty_before_run(self, client, admin_headers, test_db, mocked_submit):
        _product(test_db, "B882W")
        resp = _post(client, admin_headers, [{"code": "B882W", "market": "CN_OTC"}])
        job_id = resp.json()["job_id"]
        details = client.get(f"/api/sync-jobs/{job_id}/details", headers=admin_headers)
        assert details.status_code == 200
        assert details.json()["details"] == []

    def test_price_sync_details_still_nav_sync_detail(self, client, admin_headers, test_db):
        """回归：价格同步类型 /details 仍走 NavSyncDetail 表，不受新分支影响。"""
        job = SyncJob(job_type="price_history_sync", status="success", triggered_by="manual")
        test_db.add(job)
        test_db.commit()
        details = client.get(f"/api/sync-jobs/{job.id}/details", headers=admin_headers)
        assert details.status_code == 200
        assert details.json()["details"] == []