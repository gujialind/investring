# ============================================================================
# 集成测试：任务执行记录（issue #406）
# ============================================================================
# 覆盖 `task_runner.run_task` 编排层的落库行为——手动触发与调度触发共用它，
# 故这里用 trigger_type 参数区分两条来源，而非起真实调度线程
# （调度触发体的互斥/交易日闸门见 tests/unit/test_scheduler_service.py）。
#
# 断言要点：
# - 一次运行恰好落一条 TaskExecutionLog，status / duration_ms / records_* 真实且自洽
# - records_failed 为 None 表示「未度量」，不被写成 0（log_cleanup 场景）
# - 异常路径 status=failed + error_message 摘要 + error_stack 含 traceback
# - 自动运行产生的 NavSyncDetail.task_log_id 非空且指向当次执行记录
# - ruff 无关：本文件沿用仓库既有测试风格
# ============================================================================

from datetime import datetime

import pytest

from app.models.nav_sync_detail import NavSyncDetail
from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog
from app.services.task_runner import TRIGGER_MANUAL, TRIGGER_SCHEDULED, run_task


@pytest.fixture
def nav_sync_task(test_db) -> ScheduledTask:
    """nav_sync 的 ScheduledTask 记录（run_task 会回写它的 last_run_at）"""
    task = test_db.query(ScheduledTask).filter_by(code="nav_sync").first()
    if task is None:
        task = ScheduledTask(code="nav_sync", name="净值同步", is_enabled=True)
        test_db.add(task)
        test_db.commit()
    return task


def _only_log(db) -> TaskExecutionLog:
    logs = db.query(TaskExecutionLog).order_by(TaskExecutionLog.id).all()
    assert len(logs) == 1, f"期望恰好一条执行记录，实际 {len(logs)} 条"
    return logs[0]


class TestManualTrigger:
    """手动触发（routers/tasks.py 走的就是这个入口）"""

    def test_success_records_duration_and_counts(self, test_db, nav_sync_task):
        from unittest.mock import patch

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 1, "source": "tushare"}
            result = run_task(test_db, "nav_sync", trigger_type=TRIGGER_MANUAL)

        log = _only_log(test_db)
        assert log.task_code == "nav_sync"
        assert log.trigger_type == "manual"
        assert log.status == "success"
        assert log.started_at is not None and log.finished_at is not None
        assert log.finished_at >= log.started_at

        # duration_ms = finished_at - started_at（毫秒），且 > 0
        expected_ms = int((log.finished_at - log.started_at).total_seconds() * 1000)
        assert log.duration_ms == expected_ms
        assert log.duration_ms > 0

        # records_* 自洽：total == success + failed
        assert result["products_count"] == log.records_total
        assert log.records_failed == len(result["failed_products"]) == 0
        assert log.records_success == log.records_total
        assert log.records_total == log.records_success + log.records_failed
        assert log.error_message is None
        assert log.error_stack is None

    def test_partial_failure_maps_to_partial_success(self, test_db, nav_sync_task):
        from unittest.mock import patch

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {
                "success": False, "synced_count": 0,
                "message": "数据源不可用", "source": "tushare",
            }
            result = run_task(test_db, "nav_sync", trigger_type=TRIGGER_MANUAL)

        log = _only_log(test_db)
        assert log.status == "partial_success"
        assert log.records_failed == len(result["failed_products"]) > 0
        assert log.records_total == log.records_success + log.records_failed
        # 失败逐产品明细仍落库，且父记录可关联
        details = test_db.query(NavSyncDetail).all()
        assert details and all(d.task_log_id == log.id for d in details)

    def test_task_last_run_at_written(self, test_db, nav_sync_task):
        from unittest.mock import patch

        assert nav_sync_task.last_run_at is None
        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
            run_task(test_db, "nav_sync")

        test_db.refresh(nav_sync_task)
        assert nav_sync_task.last_run_at is not None


class TestScheduledTrigger:
    """调度触发：与手动同入口，仅 trigger_type 不同"""

    def test_scheduled_creates_log_with_nav_sync_details(self, test_db, nav_sync_task):
        from unittest.mock import patch

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 2, "source": "tushare"}
            result = run_task(test_db, "nav_sync", trigger_type=TRIGGER_SCHEDULED)

        log = _only_log(test_db)
        assert log.trigger_type == "scheduled"
        # 与手动触发同口径（同一实现，不该有分叉）
        assert log.status == "success"
        assert log.records_total == result["products_count"] > 0
        assert log.duration_ms > 0

        # 自动同步产生的逐产品明细必须有父记录，且指向当次运行
        details = test_db.query(NavSyncDetail).all()
        assert len(details) == result["products_count"]
        assert all(d.task_log_id == log.id for d in details)

    def test_trigger_type_is_parameterised(self, test_db, nav_sync_task):
        """两个取值落库后可按 trigger_type 过滤区分来源"""
        from unittest.mock import patch

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
            run_task(test_db, "nav_sync", trigger_type=TRIGGER_SCHEDULED)
            run_task(test_db, "nav_sync", trigger_type=TRIGGER_MANUAL)

        scheduled = test_db.query(TaskExecutionLog).filter_by(
            trigger_type="scheduled"
        ).all()
        manual = test_db.query(TaskExecutionLog).filter_by(trigger_type="manual").all()
        assert len(scheduled) == 1 and len(manual) == 1
        assert scheduled[0].id != manual[0].id


class TestFailurePath:
    """任务抛异常：终态记录 + 原样上抛"""

    def test_exception_records_failed_with_stack(self, test_db, nav_sync_task):
        from unittest.mock import patch

        def boom(*args, **kwargs):
            raise RuntimeError("同步炸了")

        with patch("app.services.task_runner._TASK_DISPATCH", {"nav_sync": boom}):
            with pytest.raises(RuntimeError, match="同步炸了"):
                run_task(test_db, "nav_sync", trigger_type=TRIGGER_SCHEDULED)

        log = _only_log(test_db)
        assert log.status == "failed"
        assert log.error_message == "同步炸了"  # 保留摘要语义
        assert "RuntimeError: 同步炸了" in log.error_stack  # 含 traceback
        assert "Traceback (most recent call last)" in log.error_stack
        assert log.duration_ms is not None and log.duration_ms >= 0
        assert log.finished_at is not None
        assert log.records_total is None  # 未跑到计数环节

    def test_error_message_truncated_to_1000(self, test_db, nav_sync_task):
        from unittest.mock import patch

        def boom(*args, **kwargs):
            raise RuntimeError("x" * 5000)

        with patch("app.services.task_runner._TASK_DISPATCH", {"nav_sync": boom}):
            with pytest.raises(RuntimeError):
                run_task(test_db, "nav_sync")

        log = _only_log(test_db)
        assert len(log.error_message) == 1000

    def test_business_error_still_recorded_then_reraised(self, test_db, nav_sync_task):
        """BusinessError 与普通异常同口径落库，由 router 决定 HTTP 映射"""
        from unittest.mock import patch

        from app.services.exceptions import BusinessError

        def boom(*args, **kwargs):
            raise BusinessError(code="MISSING_NAV", message="缺净值")

        with patch("app.services.task_runner._TASK_DISPATCH", {"nav_sync": boom}):
            with pytest.raises(BusinessError):
                run_task(test_db, "nav_sync")

        log = _only_log(test_db)
        assert log.status == "failed"
        assert log.error_message == "缺净值"


class TestLogCleanupRecords:
    """log_cleanup：可派生的填、不可派生的留 NULL"""

    def test_failed_count_stays_null(self, test_db):
        task = test_db.query(ScheduledTask).filter_by(code="log_cleanup").first()
        if task is None:
            test_db.add(ScheduledTask(code="log_cleanup", name="日志清理", is_enabled=True))
            test_db.commit()

        run_task(test_db, "log_cleanup", trigger_type=TRIGGER_MANUAL)

        log = _only_log(test_db)
        assert log.status == "success"
        assert log.records_total == log.records_success  # 删除总行数
        assert log.records_failed is None  # 未度量，不是 0


class TestDurationClockSafety:
    """duration_ms 不因时钟回拨变负（>= 0 的硬约束，避免读侧出现负耗时）"""

    def test_duration_non_negative(self, test_db, nav_sync_task):
        from unittest.mock import patch

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
            run_task(test_db, "nav_sync")

        log = _only_log(test_db)
        assert log.duration_ms >= 0
        assert isinstance(log.started_at, datetime)
