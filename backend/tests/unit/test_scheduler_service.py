# ============================================================================
# 单元测试：调度触发体（issue #406）
# ============================================================================
# 覆盖两条每日 job 的触发体全流程（真实 `_trigger_*` 函数，不复制其逻辑）：
#   互斥/交易日闸门 → 统一编排 run_task(trigger_type="scheduled") → RELEASE_LOCK
#
# 实现取舍：
# 1. `SessionLocal` 由触发体的**函数体内** `from app.database import SessionLocal`
#    取回，`scheduler_service` 模块上并无该属性 → 替身打在 `app.database.SessionLocal`
#    （打在 `scheduler_service` 上到不了）。替身会话与 test_db 绑同一连接，故其中的
#    落库对 test_db 可见。
# 2. 锁与日历用替身表达：SQLite 没有 GET_LOCK；同一 seed 日历行在用例内改 is_open
#    会与 conftest 的 savepoint 重启监听器抢事务。替身只接 GET_LOCK/RELEASE_LOCK
#    两条 SQL，其余原样委托真实 `Session.execute`，故 run_task 的落库仍是真实现。
#    真实 MySQL 上的 GET_LOCK 语义由 CI 的 backend-test-mysql job 覆盖。
# ============================================================================

from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog
from app.services import scheduler_service
from app.services.task_runner import TRIGGER_SCHEDULED, _TASK_DISPATCH, run_task


def _acquired(value: int) -> MagicMock:
    """伪造 `SELECT GET_LOCK(...)` / `RELEASE_LOCK(...)` 的返回值"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


class _FakeCalendarQuery:
    """替身 query 链：filter().first() 返回预设日历行（None = 查不到该日）"""

    def __init__(self, row):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


def _lock_handler(decisions, counter):
    """GET_LOCK / RELEASE_LOCK 的替身：只接这两条 SQL，其余原样交回真实实现。

    `*args, **kwargs` 必须透传——SQLAlchemy 会把 `execution_options=` 一路传进
    `Session.execute`，签名收窄会让 ORM 内部调用炸在这里。
    """
    def _inner(session, stmt, *args, **kwargs):
        sql = str(stmt)
        if "GET_LOCK" in sql:
            value = decisions[counter["get"]] if counter["get"] < len(decisions) else 1
            counter["get"] += 1
            return _acquired(value)
        if "RELEASE_LOCK" in sql:
            counter["release"] += 1
            return _acquired(1)
        return _real_session_execute(session, stmt, *args, **kwargs)

    return _inner


_real_session_execute = Session.execute


def _run_trigger(test_db, trigger, *, calendar, decisions):
    """在替身环境里跑一条**真实**触发体，返回锁调用计数 `{"get": n, "release": n}`。

    - SessionLocal 指向替身会话（与 test_db 绑同一连接，故落库可见）
    - execute 替身：GET_LOCK 按 decisions 作答、RELEASE_LOCK 恒成功，
      其余 SQL 原样委托真实 `Session.execute`（ORM 落库照常走真实现）
    - query 替身：交易日历按 calendar 作答（None = 查不到当日）

    两处替身都走「子类覆写」，不改 `Session` 本身的属性——`Session.query` 是
    SQLAlchemy 的 `_query_cls` 描述符，patch 掉会破坏描述符协议并影响同进程后续用例。

    `SessionLocal` **必须打在 `app.database` 上**：触发体是函数体内
    `from app.database import SessionLocal`，在 `scheduler_service` 上 patch 到不了。
    """
    counter = {"get": 0, "release": 0}
    handler = _lock_handler(decisions, counter)

    class _FakeSession(type(test_db)):
        def query(self, *args, **kwargs):
            return _FakeCalendarQuery(calendar)

        def execute(self, *args, **kwargs):
            return handler(self, *args, **kwargs)

    # 与 test_db 绑同一连接（== 同一事务），故替身会话里的落库对 test_db 可见
    fake_db = _FakeSession(bind=test_db.get_bind())

    patches = [
        patch("app.database.SessionLocal", lambda: fake_db),
        patch.object(_FakeSession, "close"),
    ]
    for p in patches:
        p.start()
    try:
        trigger()
    finally:
        for p in reversed(patches):
            p.stop()
    return counter


class TestShouldRunTodayGate:
    """闸门四态：锁被占 / 非交易日 / 日历缺当日 / 放行"""

    def test_lock_denied_short_circuits_before_calendar(self, test_db):
        with patch.object(type(test_db), "execute", return_value=_acquired(0)), \
             patch.object(type(test_db), "query", side_effect=AssertionError("不该查日历")):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is False

    def test_non_trading_day_skips(self, test_db):
        cal = MagicMock(is_open=False)
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query", return_value=_FakeCalendarQuery(cal)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is False

    def test_calendar_missing_skips(self, test_db):
        """日历未同步（查不到当日记录）同样按非交易日跳过"""
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query", return_value=_FakeCalendarQuery(None)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is False

    def test_lock_and_trading_day_pass(self, test_db):
        cal = MagicMock(is_open=True)
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query", return_value=_FakeCalendarQuery(cal)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is True


class TestLockNames:
    """锁是进程级互斥资源：两条 job 不得共用同一把锁"""

    def test_two_distinct_locks(self):
        assert scheduler_service.NAV_SYNC_LOCK == "daily_nav_sync_lock"
        assert scheduler_service.SNAPSHOT_GENERATE_LOCK == "snapshot_generate_lock"
        assert scheduler_service.NAV_SYNC_LOCK != scheduler_service.SNAPSHOT_GENERATE_LOCK


class TestTriggerBodySkips:
    """两类跳过场景：不执行任务、不落 TaskExecutionLog、仍释放锁"""

    def test_lock_held_by_other_process_skips(self, test_db, monkeypatch):
        executed = []

        def fake_dispatch(db, log_id):
            executed.append(log_id)
            return {"synced_count": 0, "products_count": 0, "failed_products": []}

        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", fake_dispatch)

        counter = _run_trigger(
            test_db, scheduler_service._trigger_daily_nav_sync,
            decisions=[0], calendar=MagicMock(is_open=True),
        )

        assert counter == {"get": 1, "release": 1}
        assert executed == []
        assert test_db.query(TaskExecutionLog).count() == 0

    def test_non_trading_day_skips(self, test_db, monkeypatch):
        executed = []

        def fake_dispatch(db, log_id):
            executed.append(log_id)
            return {"snapshots_generated": 0, "portfolios_processed": 0,
                    "warnings": [], "auto_confirm_failed": []}

        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", fake_dispatch)

        counter = _run_trigger(
            test_db, scheduler_service._trigger_daily_snapshot_generate,
            decisions=[1], calendar=MagicMock(is_open=False),
        )

        assert counter == {"get": 1, "release": 1}
        assert executed == []
        assert test_db.query(TaskExecutionLog).count() == 0


class TestTriggerBodyRuns:
    """闸门放行：落 trigger_type="scheduled" 记录，且锁一定被释放"""

    def _task(self, test_db, code: str):
        task = test_db.query(ScheduledTask).filter_by(code=code).first()
        if task is None:
            task = ScheduledTask(code=code, name=code, is_enabled=True)
            test_db.add(task)
            test_db.commit()
        return task

    def test_nav_sync_records_scheduled_log(self, test_db, monkeypatch):
        self._task(test_db, "nav_sync")
        canned = {
            "synced_count": 3, "products_count": 3,
            "failed_products": [], "dividends_detected": 0,
            "target_date": date.today().isoformat(),
        }
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: dict(canned))

        counter = _run_trigger(
            test_db, scheduler_service._trigger_daily_nav_sync,
            decisions=[1], calendar=MagicMock(is_open=True),
        )

        assert counter == {"get": 1, "release": 1}
        logs = test_db.query(TaskExecutionLog).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.task_code == "nav_sync"
        assert log.trigger_type == TRIGGER_SCHEDULED == "scheduled"
        assert log.status == "success"
        assert log.records_total == 3
        assert log.duration_ms is not None

    def test_snapshot_generate_records_scheduled_log(self, test_db, monkeypatch):
        self._task(test_db, "snapshot_generate")
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", lambda db, log_id: {
            "snapshots_generated": 2, "portfolios_processed": 2,
            "warnings": [], "auto_confirm_failed": [],
            "target_date": date.today().isoformat(),
        })

        counter = _run_trigger(
            test_db, scheduler_service._trigger_daily_snapshot_generate,
            decisions=[1], calendar=MagicMock(is_open=True),
        )

        assert counter == {"get": 1, "release": 1}
        log = test_db.query(TaskExecutionLog).one()
        assert log.task_code == "snapshot_generate"
        assert log.trigger_type == "scheduled"
        assert log.status == "success"
        assert log.records_total == 2

    def test_failure_still_records_log_and_releases_lock(self, test_db, monkeypatch):
        """任务抛异常：触发体吞掉异常（调度线程不能崩），但执行记录已落 failed"""
        self._task(test_db, "nav_sync")

        def boom(db, log_id):
            raise RuntimeError("远程数据源超时")

        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", boom)

        counter = _run_trigger(
            test_db, scheduler_service._trigger_daily_nav_sync,
            decisions=[1], calendar=MagicMock(is_open=True),
        )

        assert counter == {"get": 1, "release": 1}  # 异常路径同样释放锁
        log = test_db.query(TaskExecutionLog).one()
        assert log.status == "failed"
        assert log.error_message == "远程数据源超时"
        assert "RuntimeError" in log.error_stack

    def test_real_orchestrator_is_used(self, test_db):
        """触发体不该自带编排：落库走 task_runner.run_task（防两条路径再分叉）"""
        task = self._task(test_db, "nav_sync")
        assert task.last_run_at is None

        with patch("app.services.market_data_service.sync_product_prices") as mock_sync:
            mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
            result = run_task(test_db, "nav_sync", trigger_type=TRIGGER_SCHEDULED)

        assert result["products_count"] > 0
        log = test_db.query(TaskExecutionLog).one()
        assert log.trigger_type == "scheduled"
        assert log.records_total == result["products_count"]
