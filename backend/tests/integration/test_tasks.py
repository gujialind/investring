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
    """POST /api/system/tasks/{code}/run（#714：提交后台执行，立即返回执行记录 id）

    旧形态在请求内同步跑完任务并摊平结果 dict，长任务被 nginx 60s 上限切断成 504、
    任务却在服务端照常成功。现在请求只负责投递，终态经执行记录查询。
    """

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

    @pytest.fixture
    def enabled_cleanup_task(self, test_db):
        task = test_db.query(ScheduledTask).filter_by(code="log_cleanup").first()
        if task is None:
            task = ScheduledTask(code="log_cleanup", name="日志清理", is_enabled=True)
            test_db.add(task)
        else:
            task.is_enabled = True
        test_db.commit()
        return task

    def test_submit_returns_execution_id_and_does_not_run_in_request(
        self, client, admin_headers, test_db, enabled_task, monkeypatch
    ):
        """响应体是受理回执四键；请求线程内不执行任务（投递到线程池）。

        自持会话经 patch_non_closing_session_local 重定向回 test_db，
        否则独立事务既看不到 test_db 的前置数据、其 commit 也会逃逸用例回滚。
        """
        from unittest.mock import MagicMock, patch

        from tests.session_helpers import patch_non_closing_session_local

        patch_non_closing_session_local(monkeypatch, test_db)
        captured = []
        executor = MagicMock()
        executor.submit.side_effect = lambda fn, *a: captured.append((fn, a))

        with patch("app.services.market_data_service._get_executor", return_value=executor):
            resp = client.post("/api/system/tasks/nav_sync/run", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"execution_id", "task_code", "status", "message"}
        assert body["task_code"] == "nav_sync"
        assert body["status"] == "pending"
        # message 必须自带「怎么查终态」，否则操作人拿到的还是一个不可判定的回执
        assert f"#{body['execution_id']}" in body["message"]

        assert len(captured) == 1, "任务应在请求线程内投递、而非同步执行"
        log = test_db.query(TaskExecutionLog).filter_by(id=body["execution_id"]).one()
        assert log.status == "pending"
        assert log.trigger_type == "manual"
        # 提交 ≠ 开始执行：started_at 由执行体接管时才写（duration 口径随之从执行起算）
        assert log.started_at is None
        assert log.records_total is None

    def test_background_body_adopts_pending_row_instead_of_creating_one(
        self, client, admin_headers, test_db, enabled_cleanup_task, monkeypatch
    ):
        """执行体接管预建行：pending → running → 终态，全程只有一条执行记录。

        用 log_cleanup（纯本地 DELETE，无外部数据源），并直接同步调用捕获到的执行体，
        不依赖线程池时序。
        """
        from unittest.mock import MagicMock, patch

        from tests.session_helpers import patch_non_closing_session_local

        patch_non_closing_session_local(monkeypatch, test_db)
        captured = []
        executor = MagicMock()
        executor.submit.side_effect = lambda fn, *a: captured.append((fn, a))

        with patch("app.services.market_data_service._get_executor", return_value=executor):
            body = client.post(
                "/api/system/tasks/log_cleanup/run", headers=admin_headers
            ).json()

        execution_id = body["execution_id"]
        fn, args = captured[0]
        fn(*args)  # 真执行体（自持会话已被重定向到 test_db）

        log = test_db.query(TaskExecutionLog).filter_by(id=execution_id).one()
        assert test_db.query(TaskExecutionLog).count() == 1, "接管形态不得再建一条记录"
        assert log.status == "success"
        assert log.started_at is not None
        assert log.duration_ms is not None
        # log_cleanup 的 records 口径（#406）：total=success=删除行数、failed 为 None
        assert log.records_failed is None
        # 投递失败不留 pending 孤儿的对偶：真正执行过的行必须落到终态
        assert log.finished_at is not None

    def test_duplicate_submit_returns_409(
        self, client, admin_headers, test_db, enabled_task, monkeypatch
    ):
        """同 code 未终态时重复触发被拒（#714 前手动路径完全没有并发保护）。"""
        from datetime import datetime

        from tests.session_helpers import patch_non_closing_session_local

        # 投递入口的自持会话必须重定向回 test_db：否则它看不见下面这条尚在外层事务里的
        # running 行，409 分支根本打不到（用例结束仍会整体回滚，不污染同库后续用例）。
        patch_non_closing_session_local(monkeypatch, test_db)

        running = TaskExecutionLog(
            task_code="nav_sync",
            trigger_type="manual",
            status="running",
            started_at=datetime.now(),
        )
        test_db.add(running)
        test_db.commit()

        resp = client.post("/api/system/tasks/nav_sync/run", headers=admin_headers)

        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "TASK_ALREADY_RUNNING"
        # 拒绝不产生新记录，也不得改动正在执行的那条
        assert test_db.query(TaskExecutionLog).count() == 1
        assert running.status == "running"

    def test_disabled_task_rejected_before_log(self, client, admin_headers, test_db, enabled_task):
        """未启用返回 400，且不产生执行记录（前置校验在投递之前）"""
        enabled_task.is_enabled = False
        test_db.commit()

        resp = client.post("/api/system/tasks/nav_sync/run", headers=admin_headers)

        assert resp.status_code == 400
        assert resp.json() == {"detail": "任务未启用，请先启用后再执行"}
        assert test_db.query(TaskExecutionLog).count() == 0

    def test_unknown_code_returns_404_and_no_log(self, client, admin_headers, test_db):
        """未知任务码：404，且不产生执行记录"""
        resp = client.post("/api/system/tasks/NO_SUCH_TASK/run", headers=admin_headers)

        assert resp.status_code == 404
        assert test_db.query(TaskExecutionLog).count() == 0

    def test_viewer_cannot_run(self, client, viewer_headers, enabled_task):
        resp = client.post("/api/system/tasks/nav_sync/run", headers=viewer_headers)
        assert resp.status_code == 403


class TestTaskExecutionLookup:
    """GET /api/system/tasks/executions/{id}（#714：手动触发后的轮询入口）"""

    def test_returns_single_execution_by_id(self, client, admin_headers, test_db, sample_task):
        log = _add_log(test_db, sample_task.code, "running")

        resp = client.get(
            f"/api/system/tasks/executions/{log.id}", headers=admin_headers
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == log.id
        assert body["task_code"] == sample_task.code
        assert body["status"] == "running"

    def test_terminal_state_visible_after_completion(
        self, client, admin_headers, test_db, sample_task
    ):
        """终态记录可被轮询读到（success + records 是操作人的判定依据）"""
        log = _add_log(test_db, sample_task.code, "success")
        log.records_total = 134
        log.records_success = 134
        log.duration_ms = 162050
        test_db.commit()

        body = client.get(
            f"/api/system/tasks/executions/{log.id}", headers=admin_headers
        ).json()

        assert body["status"] == "success"
        assert (body["records_total"], body["records_success"]) == (134, 134)
        assert body["duration_ms"] == 162050

    def test_missing_execution_returns_404(self, client, admin_headers):
        resp = client.get("/api/system/tasks/executions/999999", headers=admin_headers)

        assert resp.status_code == 404
        assert resp.json() == {"detail": "任务执行记录 999999 不存在"}

    def test_viewer_cannot_read_execution(self, test_db, client, viewer_headers, sample_task):
        log = _add_log(test_db, sample_task.code, "success")

        resp = client.get(
            f"/api/system/tasks/executions/{log.id}", headers=viewer_headers
        )

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
