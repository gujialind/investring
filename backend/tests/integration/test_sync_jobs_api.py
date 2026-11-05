"""
sync_jobs API 集成测试（P5.5）

验证：
- POST /api/sync-jobs/price → 200 + job_id
- 已有 running job 时 POST → 409 Conflict
- GET /api/sync-jobs/{id} → 返回状态
- GET /api/sync-jobs/{id}/details → 返回明细
- _run_price_sync_job_impl 的 price_incremental_sync 分支下界走共享 helper（#698；
  此前该分支零覆盖——本文件既有用例全部用 price_history_sync）

投递入口自持会话（#592）：提交类用例统一把 SessionLocal 重定向回 test_db，
否则独立事务的提交逃逸 SAVEPOINT 回滚、且看不到 test_db 里预置的 running job。
"""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

from app.models.price_record import PriceRecord
from app.models.sync_job import SyncJob
from app.models.nav_sync_detail import NavSyncDetail
from app.services.market_data_service import (
    _run_price_sync_job_impl, resolve_incremental_start_date,
)
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

    def test_scope_hyphen_normalized_to_by_product(self, client, admin_headers, test_db, monkeypatch):
        """#808 验收 2：by-product（连字符）经 schema 归一为 by_product 落进 job params"""
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.market_data_service._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            response = client.post(
                "/api/sync-jobs/price",
                json={"scope": "by-product",
                      "products": [["510300.SH", "CN_EXCHANGE"]]},
                headers=admin_headers,
            )
        assert response.status_code == 200
        job = test_db.query(SyncJob).filter(SyncJob.id == response.json()["job_id"]).first()
        assert job.params["scope"] == "by_product"

    def test_unknown_scope_rejected_422(self, client, admin_headers, test_db):
        """#808 验收 2：未知 scope 不再静默落入全量分支 → 422 且不落 job"""
        response = client.post(
            "/api/sync-jobs/price",
            json={"scope": "bogus"},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "scope 可用值" in response.text
        assert "by_product" in response.text
        assert test_db.query(SyncJob).count() == 0

    @pytest.mark.parametrize(
        "products",
        [
            [["510300.SH"]],
            [["510300.SH", "CN_EXCHANGE", "EXTRA"]],
            [["510300.SH", "  "]],
        ],
    )
    def test_malformed_products_rejected_422(
        self, client, admin_headers, test_db, products
    ):
        """#808 S1：条目非两段或含空段 → 投递前 422，不在 worker 静默跑 0 产品任务"""
        response = client.post(
            "/api/sync-jobs/price",
            json={"scope": "by_product", "products": products},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert test_db.query(SyncJob).count() == 0

    def test_products_segments_stripped_before_store(
        self, client, admin_headers, test_db, monkeypatch
    ):
        """#808 S1：段内空格归一，避免 (code, market) 精确匹配静默落空"""
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.market_data_service._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            response = client.post(
                "/api/sync-jobs/price",
                json={"scope": "by_product",
                      "products": [[" 510300.SH ", " CN_EXCHANGE "]]},
                headers=admin_headers,
            )
        assert response.status_code == 200
        job = test_db.query(SyncJob).filter(SyncJob.id == response.json()["job_id"]).first()
        assert job.params["products"] == [["510300.SH", "CN_EXCHANGE"]]

    def test_by_product_without_products_rejected_422(self, client, admin_headers, test_db):
        """#808 Q3：scope=by_product 且 products 空 → 422，不在 worker 静默跑 0 产品任务"""
        response = client.post(
            "/api/sync-jobs/price",
            json={"scope": "by_product", "products": []},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert test_db.query(SyncJob).count() == 0

    def test_scope_all_with_products_rejected_422(self, client, admin_headers, test_db):
        """#821 验收 1：scope=all 带清单 → 422，不落 job（worker 会静默忽略清单）"""
        response = client.post(
            "/api/sync-jobs/price",
            json={"scope": "all", "products": [["510300.SH", "CN_EXCHANGE"]]},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert test_db.query(SyncJob).count() == 0


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


class TestIncrementalJobWindow:
    """`_run_price_sync_job_impl` 的 price_incremental_sync 分支窗口下界（#698）。

    该分支此前零覆盖（本文件既有用例全部用 price_history_sync）：`ir market sync-all`
    不带 --start-date 走的正是这里，与调度路径必须同口径。断言方式与
    `test_run_nav_sync.py::TestIncrementalStart` 一致——各自断言 `== resolve_incremental_start_date(...)`，
    任一处绕过单点即红，强于两调用点互比。
    """

    def _job(self, test_db, **overrides):
        params = {"job_type": "price_incremental_sync", "scope": "by_product",
                  "products": [["510300.SH", "CN_EXCHANGE"]]}
        params.update(overrides.pop("params", {}))
        job = SyncJob(job_type=overrides.pop("job_type", "price_incremental_sync"),
                      status="pending", triggered_by="manual", params=params)
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        return job

    def test_incremental_branch_uses_shared_lower_bound(self, test_db, monkeypatch):
        """有存量记录：下界 = 共享 helper 的回看下界，且与已存日期重叠（不再是 MAX+1）。"""
        anchor = date(2025, 6, 13)
        test_db.add(PriceRecord(product_code="510300.SH", market="CN_EXCHANGE",
                                price_date=anchor, unit_price=3.5, source="tushare"))
        test_db.commit()
        job = self._job(test_db)
        patch_non_closing_session_local(monkeypatch, test_db)

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
            _run_price_sync_job_impl(job.id)

        assert mock_sync.call_count == 1, "每产品每次运行恰 1 次调用（#698 验收第 6 条）"
        kwargs = mock_sync.call_args.kwargs
        assert kwargs["start_date"] == resolve_incremental_start_date(
            test_db, "510300.SH", "CN_EXCHANGE"
        )
        assert kwargs["start_date"] <= anchor, "回看段须重叠库内日期，退回 MAX+1 即 #698 复发"

    def test_incremental_branch_without_history_passes_none(self, test_db, monkeypatch):
        """无价格记录（新产品）：incremental 分支保持 start=None 全量语义。"""
        job = self._job(test_db)
        patch_non_closing_session_local(monkeypatch, test_db)

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
            _run_price_sync_job_impl(job.id)

        assert mock_sync.call_count == 1
        assert mock_sync.call_args.kwargs["start_date"] is None

    def test_history_branch_still_honours_explicit_start_date(self, test_db, monkeypatch):
        """回归保护：显式 start_date（price_history_sync）分支不受回看改动影响。"""
        job = self._job(test_db, job_type="price_history_sync",
                        params={"job_type": "price_history_sync", "start_date": "2025-06-01"})
        patch_non_closing_session_local(monkeypatch, test_db)

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
            _run_price_sync_job_impl(job.id)

        assert mock_sync.call_args.kwargs["start_date"] == date(2025, 6, 1)


class TestByProductNoMatch:
    """#821：by_product 清单在库内全部未命中时，终态须区别于「成功空跑」。"""

    def test_all_products_missing_marks_failed(self, test_db, monkeypatch):
        job = SyncJob(
            job_type="price_incremental_sync",
            status="pending",
            triggered_by="manual",
            params={
                "job_type": "price_incremental_sync",
                "scope": "by_product",
                "products": [["999999.SH", "CN_EXCHANGE"]],
            },
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        patch_non_closing_session_local(monkeypatch, test_db)

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            _run_price_sync_job_impl(job.id)
            assert mock_sync.call_count == 0, "全部未命中时不得执行任何同步"

        test_db.refresh(job)
        assert job.total == 0
        assert job.status == "failed", "0 产品任务不得记为 success（#821 验收 2）"
        assert "未命中" in (job.error_message or "")
        assert job.finished_at is not None
        # #831 边界：全未命中走 #821 early-return，不落 miss 明细——与部分命中
        # （success + 留痕）的形态不对称为刻意保留，防后来者「统一」两条路径
        assert test_db.query(NavSyncDetail).filter(
            NavSyncDetail.job_id == job.id
        ).count() == 0


class TestByProductPartialMiss:
    """#831：by_product 部分命中时，未命中条目须在任务上可见（明细 + skipped_count +
    error_message 汇总），total/终态判定/failed_count 语义不动。"""

    _MISS = ["999999.XX", "CN_OTC"]

    def _run(self, test_db, monkeypatch, products, sync_ok=True, **param_overrides):
        params = {
            "job_type": "price_incremental_sync",
            "scope": "by_product",
            "products": products,
        }
        params.update(param_overrides)
        job = SyncJob(
            job_type="price_incremental_sync", status="pending",
            triggered_by="manual", params=params,
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        patch_non_closing_session_local(monkeypatch, test_db)

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {
                "success": sync_ok, "synced_count": 10, "source": "tushare",
                **({} if sync_ok else {"message": "上游超时"}),
            }
            _run_price_sync_job_impl(job.id)

        test_db.refresh(job)
        details = test_db.query(NavSyncDetail).filter(
            NavSyncDetail.job_id == job.id
        ).all()
        return job, {d.product_code: d for d in details}

    def test_partial_miss_visible_with_traces_on_success(self, test_db, monkeypatch):
        """命中全成功仍记 success，但丢弃在三处可见；total/done/failed_count 不吞 miss。"""
        job, by_code = self._run(
            test_db, monkeypatch,
            products=[["510300.SH", "CN_EXCHANGE"], self._MISS],
        )
        assert job.status == "success", "命中部分全成功仍按既有终态记 success（获准口径）"
        assert job.total == 1, "total 保持命中数口径，不回退 #821 的 total==0 断言"
        assert job.done == 1
        assert job.failed_count == 0, "miss 不是同步失败，不得计入 failed_count"
        assert job.skipped_count == 1
        assert "未命中已跳过" in (job.error_message or "")
        assert self._MISS[0] in job.error_message
        miss = by_code.get(self._MISS[0])
        assert miss is not None, "未命中条目必须落明细行，不再静默丢弃"
        assert miss.status == "miss"
        assert miss.market == self._MISS[1]
        assert miss.error_message == "库内未命中，已跳过"
        assert by_code["510300.SH"].status == "success"

    def test_hit_sync_failure_keeps_miss_traces(self, test_db, monkeypatch):
        """命中部分失败 → failed 终态下 miss 留痕不被吞（终态判定不动）。"""
        job, by_code = self._run(
            test_db, monkeypatch,
            products=[["510300.SH", "CN_EXCHANGE"], self._MISS],
            sync_ok=False,
        )
        assert job.status == "failed"
        assert job.failed_count == 1
        assert job.skipped_count == 1
        assert by_code[self._MISS[0]].status == "miss"

    def test_explicit_end_date_pins_miss_nav_date(self, test_db, monkeypatch):
        """miss 明细 nav_date 取窗口上界：显式 end_date 优先（#746 口径同源）。"""
        job, by_code = self._run(
            test_db, monkeypatch,
            products=[["510300.SH", "CN_EXCHANGE"], self._MISS],
            end_date="2025-06-30",
        )
        assert by_code[self._MISS[0]].nav_date == "2025-06-30"

    def test_duplicate_miss_entries_counted_once(self, test_db, monkeypatch):
        """清单重复同一未命中条目只落一行、计一次，防计数虚增。"""
        job, by_code = self._run(
            test_db, monkeypatch,
            products=[["510300.SH", "CN_EXCHANGE"], self._MISS, list(self._MISS)],
        )
        assert job.skipped_count == 1
        rows = test_db.query(NavSyncDetail).filter(
            NavSyncDetail.job_id == job.id,
            NavSyncDetail.status == "miss",
        ).count()
        assert rows == 1


@pytest.mark.dialect
class TestByProductMissMysqlDialect:
    """PR #832 处置两条 L2 Suggestion 的 MySQL 方言反例——SQLite 下形态不显形
    （`==` 二进制比较、VARCHAR 长度不强制），仅 MySQL 双跑有语义：

    - S1：utf8mb4_general_ci 大小写不敏感，变体条目实际命中并同步 success，
      miss 判定须同口径归一，不得把「已同步」条目误记 miss；
    - S2：>20 字符的未命中条目撞 product_code/market 定长列（写侧 1406），
      不得楔死任务；丢弃经 skipped_count/error_message 照常可见。

    断言只依赖任务终态与明细形态，不依赖具体驱动异常类型。
    """

    def _mysql_only(self, test_db):
        if test_db.get_bind().dialect.name != "mysql":
            pytest.skip("大小写不敏感排序规则与定长列 1406 仅 MySQL 方言成立")

    def _run(self, test_db, monkeypatch, products):
        job = SyncJob(
            job_type="price_incremental_sync", status="pending",
            triggered_by="manual",
            params={
                "job_type": "price_incremental_sync",
                "scope": "by_product",
                "products": products,
            },
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {
                "success": True, "synced_count": 10, "source": "tushare",
            }
            _run_price_sync_job_impl(job.id)
        test_db.refresh(job)
        details = test_db.query(NavSyncDetail).filter(
            NavSyncDetail.job_id == job.id
        ).all()
        return job, {d.product_code: d for d in details}

    def test_case_variant_hit_entry_not_misrecorded_miss(self, test_db, monkeypatch):
        """S1 反例：510300.sh/cn_exchange 在 _ci 下命中既有行，只应落 success 明细。

        修复前 miss 判定按 Python 精确比较，该条目会被误记 status="miss"，与同一
        产品同步 success 自相矛盾；归一后大小写变体不再进 missed_entries。
        """
        self._mysql_only(test_db)
        job, by_code = self._run(
            test_db, monkeypatch,
            products=[["510300.sh", "cn_exchange"], ["999999.XX", "CN_OTC"]],
        )
        assert job.total == 1, "_ci 命中查询只认得既有行，变体条目不得另算一票"
        assert job.skipped_count == 1, "变体条目已命中已同步，不计 skip"
        assert job.status == "success"
        assert "999999.XX" in (job.error_message or "")
        miss_rows = [d for d in by_code.values() if d.status == "miss"]
        assert [d.product_code for d in miss_rows] == ["999999.XX"], (
            "变体条目不得出现 miss 行"
        )
        assert by_code["510300.SH"].status == "success"

    def test_overlong_miss_entry_stays_visible_without_wedging(self, test_db, monkeypatch):
        """S2 反例：25 字符未命中条目不落定长列明细，计数与汇总仍可见、任务正常终态。

        修复前该条目原样写 VARCHAR(20) 撞 1406，经外层 except 把任务楔死在
        running（SQLite 无长度强制，本地全绿抓不到）。
        """
        self._mysql_only(test_db)
        long_code = "U" * 25
        job, by_code = self._run(
            test_db, monkeypatch,
            products=[["510300.SH", "CN_EXCHANGE"], [long_code, "CN_OTC"]],
        )
        assert job.status == "success", "超长条目不得楔死整单或牵连命中部分"
        assert job.skipped_count == 1, "不落明细也要计数，丢弃可见语义不丢"
        assert "U" * 25 in (job.error_message or ""), "汇总须点名超长条目原文"
        assert long_code not in by_code, "超长原文不进定长列"
        assert by_code["510300.SH"].status == "success"
