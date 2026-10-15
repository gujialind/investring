# ============================================================================
# 集成测试：日志清理（test_log_cleanup.py）— issue #426
# ============================================================================
# cleanup_old_logs 删 task_execution_log 曾撞 nav_sync_detail.task_log_id 外键
# （无 ondelete）抛 IntegrityError：log_cleanup 任务每周必失败，且 DELETE 顺序
# （login→audit→task→error）使其后的 system_error_log 清理永远执行不到。
# 修复：删父表前先删 nav_sync_detail 子行——按自身 created_at 老化（docstring
# 既有承诺的 90 天），并用子查询兜住「自身未老化但仍引用老化父行」的边界行。
# SQLite 即可覆盖：conftest 已开 PRAGMA foreign_keys=ON。
# ============================================================================

from datetime import datetime, timedelta

from app.models.audit_log import AuditLog
from app.models.login_log import LoginLog
from app.models.nav_sync_detail import NavSyncDetail
from app.models.scheduled_task import ScheduledTask
from app.models.system_error_log import SystemErrorLog
from app.models.task_execution_log import TaskExecutionLog
from app.services.task_runner import TRIGGER_SCHEDULED, cleanup_old_logs

AGED = datetime.now() - timedelta(days=120)
FRESH = datetime.now()


def _task_log(db, *, created_at, task_code="nav_sync"):
    log = TaskExecutionLog(
        task_code=task_code,
        # 取常量而非字面量（#406）：早先这里是私取值 "cron"，与实际写入点
        # （manual / scheduled）分叉，会让「按 trigger_type 过滤」的读侧断言失真
        trigger_type=TRIGGER_SCHEDULED,
        status="success",
        started_at=created_at,
        finished_at=created_at,
        created_at=created_at,
    )
    db.add(log)
    db.flush()
    return log


def _detail(db, task_log_id, *, created_at):
    detail = NavSyncDetail(
        task_log_id=task_log_id,
        product_code="161017",
        market="CN_OTC",
        nav_date="2026-01-05",
        status="success",
        created_at=created_at,
    )
    db.add(detail)
    db.flush()
    return detail


class TestCleanupOldLogsForeignKey:
    """#426 核心回归：老化父行被子行引用时清理不抛、四表 + 明细全部按保留期删除"""

    def test_aged_rows_cleaned_despite_fk_reference(self, test_db):
        aged_parent = _task_log(test_db, created_at=AGED)
        _detail(test_db, aged_parent.id, created_at=AGED)
        # 边界行：子行比父行晚创建、自身未到 cutoff，但引用了老化父行——
        # 单靠自身 created_at 老化会漏掉它，父行即删不掉（子查询分支）
        _detail(test_db, aged_parent.id, created_at=FRESH)
        test_db.add(LoginLog(
            investor_code="ADMIN", action="login", status="success", created_at=AGED,
        ))
        test_db.add(AuditLog(
            investor_code="ADMIN", action="confirm", resource_type="subscription",
            created_at=AGED,
        ))
        test_db.add(SystemErrorLog(
            error_type="ProbeError", error_message="老化行", created_at=AGED,
        ))

        # 未老化行不得误删
        fresh_parent = _task_log(test_db, created_at=FRESH)
        _detail(test_db, fresh_parent.id, created_at=FRESH)
        test_db.add(LoginLog(
            investor_code="ADMIN", action="login", status="success", created_at=FRESH,
        ))
        test_db.add(AuditLog(
            investor_code="ADMIN", action="confirm", resource_type="subscription",
            created_at=FRESH,
        ))
        test_db.add(SystemErrorLog(
            error_type="ProbeError", error_message="新行", created_at=FRESH,
        ))

        result = cleanup_old_logs(test_db)

        assert result == {
            "login_logs": 1,
            "audit_logs": 1,
            "nav_sync_details": 2,
            "task_logs": 1,
            "error_logs": 1,
        }
        assert test_db.query(LoginLog).count() == 1
        assert test_db.query(AuditLog).count() == 1
        assert test_db.query(SystemErrorLog).count() == 1
        assert test_db.query(TaskExecutionLog).count() == 1
        assert test_db.query(NavSyncDetail).count() == 1

    def test_boundary_child_alone_does_not_block_parent_delete(self, test_db):
        """只有边界子行（自身未老化、引用老化父行）时父行也必须删得掉"""
        aged_parent = _task_log(test_db, created_at=AGED)
        _detail(test_db, aged_parent.id, created_at=FRESH)

        result = cleanup_old_logs(test_db)

        assert result["nav_sync_details"] == 1
        assert result["task_logs"] == 1
        assert test_db.query(NavSyncDetail).count() == 0
        assert test_db.query(TaskExecutionLog).count() == 0


class TestLogCleanupRouter:
    """任务经 router 全程走通：不抛 500、任务日志记 success"""

    def test_run_log_cleanup_marks_success(self, client, admin_headers, test_db, monkeypatch):
        """router 全程走通：老化行真被删、本次运行记录落 success。

        #714 后请求只负责投递，清理发生在执行体里，故捕获投递后同步调用执行体。
        `deleted_logs` 响应键随同步契约一起退役——清理行数由执行记录的
        records_total/records_success 承载（口径见 task_runner._derive_log_fields）。
        """
        from unittest.mock import MagicMock, patch

        from tests.session_helpers import patch_non_closing_session_local

        # 执行体与投递入口的自持会话都要重定向回 test_db：老化行尚在外层事务里，
        # 独立会话看不见；重定向后 close() 归零、commit 原样，用例结束整体回滚。
        patch_non_closing_session_local(monkeypatch, test_db)

        task = test_db.query(ScheduledTask).filter_by(code="log_cleanup").first()
        if task is None:
            task = ScheduledTask(code="log_cleanup", name="日志清理", is_enabled=True)
            test_db.add(task)
        else:
            task.is_enabled = True
        aged_parent = _task_log(test_db, created_at=AGED)
        _detail(test_db, aged_parent.id, created_at=AGED)
        test_db.add(SystemErrorLog(
            error_type="ProbeError", error_message="老化行", created_at=AGED,
        ))
        test_db.commit()

        captured = []
        executor = MagicMock()
        executor.submit.side_effect = lambda fn, *a: captured.append((fn, a))
        with patch("app.services.market_data_service._get_executor", return_value=executor):
            resp = client.post("/api/system/tasks/log_cleanup/run", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert len(captured) == 1, "清理应在执行体内发生，不在请求线程内"

        fn, args = captured[0]
        fn(*args)

        assert test_db.query(TaskExecutionLog).filter_by(id=aged_parent.id).first() is None
        # 本次运行自己的记录是新行：保留且记 success
        run_log = (
            test_db.query(TaskExecutionLog)
            .filter_by(id=body["execution_id"])
            .one()
        )
        assert run_log.status == "success"
        # 3 = 1 条任务日志 + 1 条净值同步明细 + 1 条系统错误日志
        assert run_log.records_total == 3
        assert run_log.records_success == 3
