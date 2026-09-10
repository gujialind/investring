# ============================================================================
# 集成测试：任务管理 (test_tasks.py)
# ============================================================================
# 覆盖 issue #85：
# - GET /api/system/tasks/{code}/logs 返回分页结构（回归此前 response_model 不匹配导致的 500）
# - GET /api/system/tasks/{code} 任务详情（description + last_execution）与 404
# ============================================================================

from datetime import datetime

import pytest

from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog


@pytest.fixture
def sample_task(test_db) -> ScheduledTask:
    """创建一个测试用定时任务"""
    code = "TEST_TASK"
    existing = test_db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if existing:
        return existing
    task = ScheduledTask(
        code=code,
        name="测试任务",
        description="测试用任务：说明任务作用与数据影响",
        cron_expr="0 7 * * 1-5",
    )
    test_db.add(task)
    test_db.commit()
    test_db.refresh(task)
    return task


def _add_log(test_db, code: str, status: str) -> TaskExecutionLog:
    log = TaskExecutionLog(
        task_code=code,
        trigger_type="manual",
        status=status,
        started_at=datetime.now(),
        finished_at=datetime.now(),
    )
    test_db.add(log)
    test_db.commit()
    test_db.refresh(log)
    return log


class TestTaskLogs:
    """任务执行日志查询"""

    def test_task_logs_returns_pagination(self, client, admin_headers, test_db, sample_task):
        """日志接口返回 200 且为分页结构（回归 500）"""
        _add_log(test_db, sample_task.code, "success")

        resp = client.get(
            f"/api/system/tasks/{sample_task.code}/logs", headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(["items", "total", "page", "page_size"]).issubset(data.keys())
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["items"][0]["task_code"] == sample_task.code

    def test_task_logs_empty_task(self, client, admin_headers, sample_task):
        """无执行记录时返回空 items 而非 500"""
        resp = client.get(
            "/api/system/tasks/NO_SUCH_TASK/logs", headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_viewer_cannot_read_task_logs(self, client, viewer_headers, sample_task):
        """viewer 无权查看任务日志"""
        resp = client.get(
            f"/api/system/tasks/{sample_task.code}/logs", headers=viewer_headers
        )
        assert resp.status_code == 403


class TestTaskDescribe:
    """任务详情接口"""

    def test_describe_task(self, client, admin_headers, test_db, sample_task):
        """返回任务全字段 + 最近一次执行记录"""
        _add_log(test_db, sample_task.code, "failed")
        latest = _add_log(test_db, sample_task.code, "success")

        resp = client.get(f"/api/system/tasks/{sample_task.code}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == sample_task.code
        assert data["name"] == "测试任务"
        assert data["description"] == sample_task.description
        assert data["cron_expr"] == "0 7 * * 1-5"
        assert data["is_enabled"] is True
        assert data["last_execution"] is not None
        assert data["last_execution"]["id"] == latest.id
        assert data["last_execution"]["status"] == "success"

    def test_describe_task_without_execution(self, client, admin_headers, sample_task):
        """从未执行过的任务 last_execution 为 null"""
        resp = client.get(f"/api/system/tasks/{sample_task.code}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["last_execution"] is None

    def test_describe_task_not_found(self, client, admin_headers):
        """任务不存在返回 404"""
        resp = client.get("/api/system/tasks/NOT_EXIST", headers=admin_headers)
        assert resp.status_code == 404

    def test_describe_does_not_shadow_logs_route(self, client, admin_headers, sample_task):
        """/{code} 不会抢占 /{code}/logs 路由"""
        resp = client.get(
            f"/api/system/tasks/{sample_task.code}/logs", headers=admin_headers
        )
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_viewer_cannot_describe_task(self, client, viewer_headers, sample_task):
        """viewer 无权查看任务详情"""
        resp = client.get(f"/api/system/tasks/{sample_task.code}", headers=viewer_headers)
        assert resp.status_code == 403


class TestRunTaskEndpoint:
    """POST /api/system/tasks/{code}/run（#406：编排下沉 service 后响应契约不变）"""

    @pytest.fixture
    def enabled_task(self, test_db):
        task = test_db.query(ScheduledTask).filter_by(code="nav_sync").first()
        if task is None:
            task = ScheduledTask(code="nav_sync", name="净值同步", is_enabled=True)
            test_db.add(task)
        else:
            task.is_enabled = True
        test_db.commit()
        return task

    def test_success_response_shape_unchanged(self, client, admin_headers, test_db, enabled_task):
        """响应体与文案逐字保持：{"message": …, **result}"""
        from unittest.mock import patch

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 1, "source": "tushare"}
            resp = client.post("/api/system/tasks/nav_sync/run", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "任务 nav_sync 执行完成"
        assert set(body) >= {"message", "synced_count", "products_count",
                             "failed_products", "target_date"}

        # 落库走统一编排：一条 manual 记录、status/records 有真值
        logs = test_db.query(TaskExecutionLog).all()
        assert len(logs) == 1
        assert logs[0].trigger_type == "manual"
        assert logs[0].status == "success"
        assert logs[0].records_total == body["products_count"]
        assert logs[0].duration_ms is not None

    def test_log_cleanup_keeps_deleted_logs_wrapper(self, client, admin_headers, test_db):
        """log_cleanup 的 deleted_logs 包装键保留（CLI/前端既有契约）"""
        task = test_db.query(ScheduledTask).filter_by(code="log_cleanup").first()
        if task is None:
            task = ScheduledTask(code="log_cleanup", name="日志清理", is_enabled=True)
            test_db.add(task)
        else:
            task.is_enabled = True
        test_db.commit()

        resp = client.post("/api/system/tasks/log_cleanup/run", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "任务 log_cleanup 执行成功"
        assert set(body["deleted_logs"]) == {
            "login_logs", "audit_logs", "nav_sync_details", "task_logs", "error_logs",
        }

    def test_disabled_task_rejected_before_log(self, client, admin_headers, test_db, enabled_task):
        """未启用返回 400，且不产生执行记录（前置校验在编排之前）"""
        enabled_task.is_enabled = False
        test_db.commit()

        resp = client.post("/api/system/tasks/nav_sync/run", headers=admin_headers)

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Task is disabled"
        assert test_db.query(TaskExecutionLog).count() == 0

    def test_unknown_code_returns_404_and_no_log(self, client, admin_headers, test_db):
        """未知任务码：404，且不产生执行记录"""
        resp = client.post("/api/system/tasks/NO_SUCH_TASK/run", headers=admin_headers)

        assert resp.status_code == 404
        assert test_db.query(TaskExecutionLog).count() == 0

    def test_viewer_cannot_run(self, client, viewer_headers, enabled_task):
        resp = client.post("/api/system/tasks/nav_sync/run", headers=viewer_headers)
        assert resp.status_code == 403


class TestInitTaskDescriptions:
    """初始化任务文案"""

    def test_init_updates_existing_description(self, test_db):
        """已存在记录会被同步为最新 description 文案"""
        from app.init_tasks import init_scheduled_tasks

        stale = test_db.query(ScheduledTask).filter(ScheduledTask.code == "nav_sync").first()
        if not stale:
            stale = ScheduledTask(
                code="nav_sync", name="旧名称", description="旧文案", cron_expr="0 7 * * 1-5"
            )
            test_db.add(stale)
            test_db.commit()
        else:
            stale.description = "旧文案"
            test_db.commit()

        init_scheduled_tasks(test_db)

        refreshed = test_db.query(ScheduledTask).filter(
            ScheduledTask.code == "nav_sync"
        ).first()
        assert refreshed.description != "旧文案"
        # #156：快照生成已剥离为独立任务，nav_sync 文案不再含「快照」
        assert "快照" not in refreshed.description
        assert refreshed.cron_expr == "0 7 * * 1-5"

    def test_init_seeds_snapshot_generate_task(self, test_db):
        """#156：snapshot_generate 任务记录被种子，文案说明开关语义"""
        from app.init_tasks import init_scheduled_tasks

        init_scheduled_tasks(test_db)

        task = test_db.query(ScheduledTask).filter(
            ScheduledTask.code == "snapshot_generate"
        ).first()
        assert task is not None
        assert task.name == "组合快照生成"
        assert "开启自动快照" in task.description
        assert task.cron_expr == "30 7 * * 1-5"

    def test_init_preserves_custom_cron_expr(self, test_db):
        """已有记录的自定义 cron_expr 在 init 后保持不变"""
        from app.init_tasks import init_scheduled_tasks

        existing = test_db.query(ScheduledTask).filter(ScheduledTask.code == "nav_sync").first()
        if not existing:
            existing = ScheduledTask(
                code="nav_sync", name="净值同步", description="旧文案", cron_expr="30 6 * * 1-5"
            )
            test_db.add(existing)
        else:
            existing.cron_expr = "30 6 * * 1-5"
        test_db.commit()

        init_scheduled_tasks(test_db)

        refreshed = test_db.query(ScheduledTask).filter(
            ScheduledTask.code == "nav_sync"
        ).first()
        assert refreshed.cron_expr == "30 6 * * 1-5"
