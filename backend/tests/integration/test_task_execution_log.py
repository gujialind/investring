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
    """取当次执行记录，**并 refresh**——断言一律针对落库值而非内存态。

    必须 refresh 的原因（#406，MySQL job 实测）：`task_execution_log` 的
    `started_at` / `finished_at` 是 `DateTime`（无小数位），MySQL 存 DATETIME(0) 时按**秒
    取整**（#458 复核口径：MySQL 8 默认是 round，`TIME_TRUNCATE_FRACTIONAL` 打开才是截断，
    单列误差都不超过 0.5s）。不 refresh 时读到的是内存里的原始微秒值，
    `finished_at - started_at` 有小数差；refresh 后两者同为整秒，而 `duration_ms` 是落库前
    按**未取整**值算的，于是「duration_ms == finished_at - started_at」这条断言在 MySQL 上
    必红、在 SQLite 上（保留微秒）通过。生产读侧看到的也是取整后的值，故断言口径应以落库值
    表达；两者的允许差距见 `_duration_matches_wall_clock`。
    """
    logs = db.query(TaskExecutionLog).order_by(TaskExecutionLog.id).all()
    assert len(logs) == 1, f"期望恰好一条执行记录，实际 {len(logs)} 条"
    db.refresh(logs[0])
    return logs[0]


# 「两列各自取整到秒」相对真实耗时的固有漂移上界（#458）：duration_ms 由 task_runner
# 用**未取整**的内存值算（`int((finished_at - started_at).total_seconds() * 1000)`），
# 而 started_at / finished_at 落库时**各自独立**取整到整秒（单列误差 ≤ 0.5s）。
# 先各自取整再相减 ≠ 先相减再取整 ⇒ 漂移 = (e(finished) - e(started)) * 1000 ∈ (-1000, 1000) ms，
# **方向可正可负**，两端同向取整时甚至可以是 0。取闭区间：round 模式下 ±1000ms 可达
# （截断模式下退化为 (-1000, 0)，同样落在区间内），故换 sql_mode 也无需改判据。
DURATION_TOLERANCE_MS = 1000


def _duration_matches_wall_clock(duration_ms: int, wall_ms: int) -> bool:
    """duration_ms 与「落库两列之差」是否在秒级取整漂移内自洽（#458）。

    ⚠️ 别当强判据用：SQLite 保留微秒时 `wall_ms == duration_ms`，区间必然成立；它的判别力
    在 MySQL（wall_ms 是整秒倍数）与「量级/单位错误」上——如 duration_ms 误按秒计
    （245000 vs wall_ms=245）必红。守住「耗时被真实度量」的是调用点的 `duration_ms > 0`。
    """
    return wall_ms - DURATION_TOLERANCE_MS <= duration_ms <= wall_ms + DURATION_TOLERANCE_MS


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

        # duration_ms 有真实值且 > 0。**刻意不断言 equality**：两列的 DateTime 无小数位，
        # 落库时各自被取整到整秒，而 duration_ms 按未取整值计算，短任务在 MySQL 上就是
        # 「duration_ms=16、两列同秒」；跨秒边界时反过来会「duration_ms=245、两列差 1000」
        # （#458 实测）。故判据拆成两条互补断言：> 0 守住「耗时被真实度量」（#406 改用
        # >= 0 时丢掉了它，恒 0 的缺陷从此无人拦），区间判据守住「与落库两列量级自洽」。
        assert log.duration_ms is not None
        # > 0 而非 >= 0：即便 sync_product_prices 被 mock，run_nav_sync 仍对每个产品做真实
        # DB I/O（SELECT max(price_date) + INSERT NavSyncDetail），派发不会亚毫秒——
        # 与 TestScheduledTrigger 同口径的那条断言一致。
        assert log.duration_ms > 0
        wall_ms = int((log.finished_at - log.started_at).total_seconds() * 1000)
        assert _duration_matches_wall_clock(log.duration_ms, wall_ms)

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


class TestDurationWallClockTolerance:
    """±1000ms 判据的边界守门（#458）：只吸收秒级取整漂移，不吸收真实缺陷。

    纯算术、不依赖 DB 方言，两个 job 都跑——issue #458 的验收断言「构造跨秒边界场景必须
    通过」与「构造真实缺陷场景必须报红」在此固化为常驻用例，避免演示一次就蒸发
    （code-review.md §0 第 5 类「覆盖无声蒸发」）。
    """

    def test_straddling_second_boundary_passes(self):
        """#458 实测形态：真实耗时 245ms，两端反向取整后落库成跨 1 秒"""
        assert _duration_matches_wall_clock(duration_ms=245, wall_ms=1000)

    def test_both_columns_rounded_same_way_passes(self):
        """漂移的另一侧：两端同向取整（短任务常见），两列同秒而 duration_ms 仍是真值"""
        assert _duration_matches_wall_clock(duration_ms=245, wall_ms=0)

    def test_real_defects_fail(self):
        assert not _duration_matches_wall_clock(duration_ms=0, wall_ms=5000)  # 耗时未度量
        assert not _duration_matches_wall_clock(duration_ms=245_000, wall_ms=245)  # 单位错：秒当毫秒

    def test_tolerance_edges_are_inclusive(self):
        """round 模式下 ±1000ms 可达，故必须闭区间（写成 < 1000 会在边界上翻车）"""
        assert _duration_matches_wall_clock(duration_ms=0, wall_ms=1000)
        assert _duration_matches_wall_clock(duration_ms=2000, wall_ms=1000)
        assert not _duration_matches_wall_clock(duration_ms=2001, wall_ms=1000)
        assert not _duration_matches_wall_clock(duration_ms=-1, wall_ms=1000)
