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

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog
from app.models.trading_calendar import TradingCalendar
from app.services import scheduler_service
from app.services.task_runner import (
    TRIGGER_CASCADE,
    TRIGGER_MANUAL,
    TRIGGER_RETRY,
    TRIGGER_SCHEDULED,
    _TASK_DISPATCH,
    run_task,
)
from app.utils.business_time import business_day_start, business_now, business_today
from tests.unit.business_clock_helpers import (
    MON_0700_CST,
    SAT_0700_CST,
    SUN_0700_CST,
    TUE_0700_CST,
    freeze_business_clock as _freeze_business_clock,
)


def _acquired(value: int) -> MagicMock:
    """伪造 `SELECT GET_LOCK(...)` / `RELEASE_LOCK(...)` 的返回值"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


class _FakeCalendarQuery:
    """替身 query 链：filter().first() 返回预设日历行（None = 查不到该日）。

    #746：记录 filter 实际查询的 calendar_date 值。旧替身对查询条件完全失明——
    闸门换任何钟都恒返预设行、永远全绿；有了 date_log，用例才能断言
    「查的是业务今天」而不只是「查到了一行」。
    """

    def __init__(self, row, date_log=None):
        self._row = row
        self._date_log = date_log

    def filter(self, *args, **kwargs):
        if self._date_log is not None:
            for criterion in args:
                value = getattr(getattr(criterion, "right", None), "value", None)
                if isinstance(value, date):
                    self._date_log.append(value)
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


def _run_trigger(test_db, trigger, *, calendar, decisions, date_log=None):
    """在替身环境里跑一条**真实**触发体，返回锁调用计数 `{"get": n, "release": n}`。

    - SessionLocal 指向替身会话（与 test_db 绑同一连接，故落库可见）
    - execute 替身：GET_LOCK 按 decisions 作答、RELEASE_LOCK 恒成功，
      其余 SQL 原样委托真实 `Session.execute`（ORM 落库照常走真实现）
    - query 替身：**只**拦截交易日历查询并按 calendar 作答（None = 查不到当日）；
      其余模型查询（当日成功记录等）委托真实 Session.query——若对所有 query 都返回
      日历替身，#747 的「当日快照已成功」判据会被静默短路成 AttributeError/假绿。
      传入 date_log 列表时记录闸门实际查询的 calendar_date（#746）

    两处替身都走「子类覆写」，不改 `Session` 本身的属性——`Session.query` 是
    SQLAlchemy 的 `_query_cls` 描述符，patch 掉会破坏描述符协议并影响同进程后续用例。

    `SessionLocal` **必须打在 `app.database` 上**：触发体是函数体内
    `from app.database import SessionLocal`，在 `scheduler_service` 上 patch 到不了。
    """
    counter = {"get": 0, "release": 0}
    handler = _lock_handler(decisions, counter)

    class _FakeSession(type(test_db)):
        def query(self, *args, **kwargs):
            if args and args[0] is TradingCalendar:
                return _FakeCalendarQuery(calendar, date_log=date_log)
            return Session.query(self, *args, **kwargs)

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
    """闸门四态：锁被占 / 非交易日 / 日历缺当日 / 放行（#746 起附查询日期口径断言）"""

    def test_lock_denied_short_circuits_before_calendar(self, test_db):
        with patch.object(type(test_db), "execute", return_value=_acquired(0)), \
             patch.object(type(test_db), "query", side_effect=AssertionError("不该查日历")):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is False

    def test_non_trading_day_skips(self, test_db):
        cal = MagicMock(is_open=False)
        queried = []
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query",
                          return_value=_FakeCalendarQuery(cal, date_log=queried)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is False
        # 闸门查的是**业务今天**，不是进程本地日（#746）
        assert queried == [business_today()]

    def test_calendar_missing_skips(self, test_db):
        """日历未同步（查不到当日记录）同样按非交易日跳过"""
        queried = []
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query",
                          return_value=_FakeCalendarQuery(None, date_log=queried)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is False
        assert queried == [business_today()]

    def test_lock_and_trading_day_pass(self, test_db):
        cal = MagicMock(is_open=True)
        queried = []
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query",
                          return_value=_FakeCalendarQuery(cal, date_log=queried)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is True
        assert queried == [business_today()]


class TestBusinessClockGate:
    """#746 验收：冻结 07:00 CST（UTC 仍是前一日 23:00）时闸门按业务日判定。

    事故形态：周一 07:00 CST 被当周日（UTC）跳过、周六 07:00 CST 被当周五（UTC）
    误跑。两个瞬间的 UTC 日期都指向日判定的另一端，进程钟一漏换即翻红。
    """

    def test_monday_0700_cst_queries_business_monday(self, test_db, monkeypatch):
        _freeze_business_clock(monkeypatch, MON_0700_CST)
        cal = MagicMock(is_open=True)
        queried = []
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query",
                          return_value=_FakeCalendarQuery(cal, date_log=queried)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is True
        assert queried == [date(2026, 10, 19)]  # 业务周一；UTC 进程钟上还是 10-18 周日

    def test_saturday_0700_cst_queries_business_saturday(self, test_db, monkeypatch):
        _freeze_business_clock(monkeypatch, SAT_0700_CST)
        cal = MagicMock(is_open=False)  # 业务周六闭市 → 跳过（旧 UTC 钟会查到周五行误跑）
        queried = []
        with patch.object(type(test_db), "execute", return_value=_acquired(1)), \
             patch.object(type(test_db), "query",
                          return_value=_FakeCalendarQuery(cal, date_log=queried)):
            assert scheduler_service._should_run_today(
                test_db, scheduler_service.NAV_SYNC_LOCK
            ) is False
        assert queried == [date(2026, 10, 17)]

    def test_trigger_body_gate_uses_business_day(self, test_db, monkeypatch):
        """经真实触发体走一遍：冻结周二 07:00 CST，#747 起 nav 闸门锚定业务昨天（周一）"""
        _freeze_business_clock(monkeypatch, TUE_0700_CST)
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 0, "products_count": 0, "failed_products": [],
        })
        queried = []
        _run_trigger(test_db, scheduler_service._trigger_daily_nav_sync,
                     decisions=[1], calendar=MagicMock(is_open=True),
                     date_log=queried)
        assert queried == [date(2026, 10, 19)]  # 业务昨天；进程 UTC 钟上是 10-18 周日


class TestNavGateAnchorsYesterday:
    """#747：nav_sync 的闸门锚定**业务昨天**（昨天收盘后是否有待同步的新净值）。

    判据不是「今天是否交易日」：周六（昨天周五）要跑、周日/周一（昨天非交易日）不跑。
    用例同时钉住被查询的日期——只断言「是否放行」的话，闸门退回 business_today()
    在周日/周一同样是闭市、同样跳过，测试会假绿。
    """

    def _run_nav(self, test_db, monkeypatch, *, is_open: bool):
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 0, "new_nav_count": 0, "products_count": 0,
            "failed_products": [],
        })
        queried = []
        _run_trigger(test_db, scheduler_service._trigger_daily_nav_sync,
                     decisions=[1], calendar=MagicMock(is_open=is_open),
                     date_log=queried)
        return queried

    def test_saturday_runs_with_friday_gate(self, test_db, monkeypatch):
        """周六 07:00：昨天（周五）是交易日 → 放行"""
        _freeze_business_clock(monkeypatch, SAT_0700_CST)
        queried = self._run_nav(test_db, monkeypatch, is_open=True)
        assert queried == [date(2026, 10, 16)]  # 业务周五，不是业务周六 10-17
        assert test_db.query(TaskExecutionLog).one().trigger_type == TRIGGER_SCHEDULED

    def test_sunday_blocks_with_saturday_gate(self, test_db, monkeypatch):
        """周日 07:00：昨天（周六）闭市 → 跳过且不建记录"""
        _freeze_business_clock(monkeypatch, SUN_0700_CST)
        queried = self._run_nav(test_db, monkeypatch, is_open=False)
        assert queried == [date(2026, 10, 17)]  # 业务周六，不是业务周日 10-18
        assert test_db.query(TaskExecutionLog).count() == 0

    def test_monday_blocks_with_sunday_gate(self, test_db, monkeypatch):
        """周一 07:00：昨天（周日）闭市 → 跳过（旧「按今天判」会放行并白跑）"""
        _freeze_business_clock(monkeypatch, MON_0700_CST)
        queried = self._run_nav(test_db, monkeypatch, is_open=False)
        assert queried == [date(2026, 10, 18)]  # 业务周日，不是业务周一 10-19
        assert test_db.query(TaskExecutionLog).count() == 0

    def test_weekday_runs_with_previous_trading_day_gate(self, test_db, monkeypatch):
        """工作日行为不变：周二 07:00 查周一（交易日）→ 放行"""
        _freeze_business_clock(monkeypatch, TUE_0700_CST)
        queried = self._run_nav(test_db, monkeypatch, is_open=True)
        assert queried == [date(2026, 10, 19)]
        assert test_db.query(TaskExecutionLog).count() == 1


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


# ============================================================================
# nav_sync 有界自动重试 + 快照联动补跑（issue #715）
# ============================================================================


class _FakeScheduler:
    """捕获 add_job 调用的调度器替身：timezone 必须是真 tzinfo（datetime.now 要用）。"""

    def __init__(self):
        self.timezone = ZoneInfo("Asia/Shanghai")
        self.jobs = []  # [(func, kwargs), ...]

    def add_job(self, func, **kwargs):
        self.jobs.append((func, kwargs))


def _session_with_locks(test_db, decisions):
    """只拦截 GET_LOCK/RELEASE_LOCK 的会话替身，query/add/commit 全走真实实现。

    用于 `_maybe_cascade_snapshot`：它要对 TaskExecutionLog 做真实计数查询、经
    run_task 真实落库，同时又要 SQLite 上并不存在的 GET_LOCK 互斥。execute 替身只
    接这两条锁 SQL，其余原样委托真实 `Session.execute`（与 `_run_trigger` 同源）。
    """
    counter = {"get": 0, "release": 0}
    handler = _lock_handler(decisions, counter)

    class _LockSession(type(test_db)):
        def execute(self, *args, **kwargs):
            return handler(self, *args, **kwargs)

    db = _LockSession(bind=test_db.get_bind())
    patches = [patch.object(_LockSession, "close")]
    for p in patches:
        p.start()
    return db, counter, patches


class TestMaybeScheduleNavRetry:
    """`_maybe_schedule_nav_retry`：终态判定 + 有界序号 + 一次性 date job 参数形态"""

    def test_success_does_not_schedule(self, monkeypatch):
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        assert scheduler_service._maybe_schedule_nav_retry("success", attempt=1) is False
        assert fake.jobs == []

    def test_partial_success_registers_bounded_date_job(self, monkeypatch):
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        before = business_now()
        assert scheduler_service._maybe_schedule_nav_retry("partial_success", attempt=1) is True

        func, kwargs = fake.jobs[0]
        assert func is scheduler_service._trigger_nav_sync_retry
        assert kwargs["trigger"] == "date"
        assert kwargs["jobstore"] == "default"          # 持久化 → 重启存活
        assert kwargs["replace_existing"] is True        # 确定性 id 去重
        assert kwargs["misfire_grace_time"] is None      # 停机跨过触发时刻仍补跑
        assert kwargs["args"] == [1]
        assert kwargs["id"] == f"nav_sync_retry_{business_today().isoformat()}_1"
        # run_date ≈ now + 2h（放宽下界，避免时钟抖动误判）
        assert kwargs["run_date"] >= before + scheduler_service.NAV_RETRY_INTERVAL - timedelta(seconds=5)

    def test_job_id_uses_business_day_across_utc_boundary(self, monkeypatch):
        """#746 验收：冻结 07:00 CST（UTC 仍是前一日），job id 与 run_date 取业务日。

        UTC 日期（10-19）与业务日期（10-20）在此瞬间分叉——job id 若退回
        `date.today()` 会拿到 10-19 而翻红（确定性、与时钟时刻无关）。
        """
        _freeze_business_clock(monkeypatch, TUE_0700_CST)
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        assert scheduler_service._maybe_schedule_nav_retry("failed", attempt=1) is True
        _func, kwargs = fake.jobs[0]
        assert kwargs["id"] == "nav_sync_retry_2026-10-20_1"
        # 冻结瞬间 UTC 日期是 10-19、业务日期是 10-20：id 取的是业务侧
        assert TUE_0700_CST.date() == date(2026, 10, 19)
        assert kwargs["run_date"].astimezone(
            ZoneInfo("Asia/Shanghai")
        ).strftime("%Y-%m-%d %H:%M") == "2026-10-20 09:00"

    def test_failed_status_also_schedules(self, monkeypatch):
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        assert scheduler_service._maybe_schedule_nav_retry("failed", attempt=3) is True
        assert fake.jobs[0][1]["args"] == [3]

    def test_attempts_bounded_at_max(self, monkeypatch):
        """序号超过上限即停手，不再注册（有界，不无限打上游）"""
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        over = scheduler_service.NAV_RETRY_MAX_ATTEMPTS + 1
        assert scheduler_service._maybe_schedule_nav_retry("partial_success", attempt=over) is False
        assert fake.jobs == []

    def test_no_scheduler_is_noop(self, monkeypatch):
        """调度器未初始化（如测试期 SCHEDULER_ENABLED=false）时安静跳过，不崩"""
        monkeypatch.setattr(scheduler_service, "_scheduler", None)
        assert scheduler_service._maybe_schedule_nav_retry("partial_success", attempt=1) is False


class TestFirstRunSchedulesRetry:
    """首跑定时触发体：非全量成功 → 注册第 1 次重试；全量成功 → 不注册"""

    def _task(self, test_db, code):
        task = test_db.query(ScheduledTask).filter_by(code=code).first()
        if task is None:
            task = ScheduledTask(code=code, name=code, is_enabled=True)
            test_db.add(task)
            test_db.commit()
        return task

    def test_partial_first_run_schedules_attempt_1(self, test_db, monkeypatch):
        self._task(test_db, "nav_sync")
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 1, "products_count": 2, "failed_products": ["A.OF"],
            "target_date": date.today().isoformat(),
        })

        _run_trigger(test_db, scheduler_service._trigger_daily_nav_sync,
                     decisions=[1], calendar=MagicMock(is_open=True))

        log = test_db.query(TaskExecutionLog).one()
        assert log.trigger_type == TRIGGER_SCHEDULED
        assert log.status == "partial_success"
        assert len(fake.jobs) == 1
        assert fake.jobs[0][1]["args"] == [1]

    def test_success_first_run_schedules_nothing(self, test_db, monkeypatch):
        self._task(test_db, "nav_sync")
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 2, "products_count": 2, "failed_products": [],
            "target_date": date.today().isoformat(),
        })

        _run_trigger(test_db, scheduler_service._trigger_daily_nav_sync,
                     decisions=[1], calendar=MagicMock(is_open=True))

        assert test_db.query(TaskExecutionLog).one().status == "success"
        assert fake.jobs == []

    def test_success_with_new_nav_cascades(self, test_db, monkeypatch):
        """#747：终态 success 且有新增价格日 → 首跑也联动快照（不再只挂在重试成功上）"""
        self._task(test_db, "nav_sync")
        cascade = MagicMock()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", cascade)
        monkeypatch.setattr(scheduler_service, "_scheduler", _FakeScheduler())
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 2, "new_nav_count": 2, "products_count": 2,
            "failed_products": [], "target_date": date.today().isoformat(),
        })

        _run_trigger(test_db, scheduler_service._trigger_daily_nav_sync,
                     decisions=[1], calendar=MagicMock(is_open=True))

        cascade.assert_called_once()

    def test_success_without_new_nav_does_not_cascade(self, test_db, monkeypatch):
        """增量窗只重放回看段（new_nav_count=0）→ 不重算快照"""
        self._task(test_db, "nav_sync")
        cascade = MagicMock()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", cascade)
        monkeypatch.setattr(scheduler_service, "_scheduler", _FakeScheduler())
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 2, "new_nav_count": 0, "products_count": 2,
            "failed_products": [], "target_date": date.today().isoformat(),
        })

        _run_trigger(test_db, scheduler_service._trigger_daily_nav_sync,
                     decisions=[1], calendar=MagicMock(is_open=True))

        cascade.assert_not_called()


class TestRetryTriggerBody:
    """重试触发体：success/partial_success 有新净值 → 联动快照；非 success → 注册下一次"""

    def _task(self, test_db, code):
        task = test_db.query(ScheduledTask).filter_by(code=code).first()
        if task is None:
            task = ScheduledTask(code=code, name=code, is_enabled=True)
            test_db.add(task)
            test_db.commit()
        return task

    def test_retry_success_writes_retry_log_and_cascades(self, test_db, monkeypatch):
        self._task(test_db, "nav_sync")
        cascade = MagicMock()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", cascade)
        monkeypatch.setattr(scheduler_service, "_scheduler", _FakeScheduler())
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 2, "products_count": 2, "failed_products": [],
            "new_nav_count": 2,
            "target_date": date.today().isoformat(),
        })

        counter = _run_trigger(test_db, lambda: scheduler_service._trigger_nav_sync_retry(1),
                               decisions=[1], calendar=MagicMock(is_open=True))

        assert counter == {"get": 1, "release": 1}
        log = test_db.query(TaskExecutionLog).one()
        assert log.trigger_type == TRIGGER_RETRY == "retry"
        assert log.status == "success"
        cascade.assert_called_once()  # 全量成功且有新增净值 → 检查快照联动

    def test_retry_success_without_new_nav_does_not_cascade(self, test_db, monkeypatch):
        """#747：success 但无新增价格日 → 不联动（且 success 不注册下一次重试）"""
        self._task(test_db, "nav_sync")
        fake = _FakeScheduler()
        cascade = MagicMock()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", cascade)
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 1, "new_nav_count": 0, "products_count": 1,
            "failed_products": [], "target_date": date.today().isoformat(),
        })

        _run_trigger(test_db, lambda: scheduler_service._trigger_nav_sync_retry(1),
                     decisions=[1], calendar=MagicMock(is_open=True))

        cascade.assert_not_called()
        assert fake.jobs == []

    def test_retry_partial_success_with_new_nav_cascades_and_reschedules(self, test_db, monkeypatch):
        """#747：partial_success 有新增净值时既联动、也继续注册下一次重试"""
        self._task(test_db, "nav_sync")
        fake = _FakeScheduler()
        cascade = MagicMock()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", cascade)
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 1, "new_nav_count": 1, "products_count": 2,
            "failed_products": ["A.OF"], "target_date": date.today().isoformat(),
        })

        _run_trigger(test_db, lambda: scheduler_service._trigger_nav_sync_retry(1),
                     decisions=[1], calendar=MagicMock(is_open=True))

        cascade.assert_called_once()
        assert len(fake.jobs) == 1
        assert fake.jobs[0][1]["args"] == [2]

    def test_retry_saturday_uses_same_yesterday_gate(self, test_db, monkeypatch):
        """#747：周六重试与首跑同判据（业务昨天=周五）→ 放行并落 retry 记录"""
        self._task(test_db, "nav_sync")
        _freeze_business_clock(monkeypatch, SAT_0700_CST)
        monkeypatch.setattr(scheduler_service, "_scheduler", _FakeScheduler())
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", MagicMock())
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 1, "new_nav_count": 0, "products_count": 1,
            "failed_products": [], "target_date": "2026-10-16",
        })

        queried = []
        _run_trigger(test_db, lambda: scheduler_service._trigger_nav_sync_retry(2),
                     decisions=[1], calendar=MagicMock(is_open=True), date_log=queried)

        assert queried == [date(2026, 10, 16)]
        assert test_db.query(TaskExecutionLog).one().trigger_type == TRIGGER_RETRY

    def test_retry_partial_schedules_next_attempt(self, test_db, monkeypatch):
        self._task(test_db, "nav_sync")
        fake = _FakeScheduler()
        cascade = MagicMock()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", cascade)
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 1, "products_count": 2, "failed_products": ["A.OF"],
            "target_date": date.today().isoformat(),
        })

        _run_trigger(test_db, lambda: scheduler_service._trigger_nav_sync_retry(1),
                     decisions=[1], calendar=MagicMock(is_open=True))

        assert test_db.query(TaskExecutionLog).one().trigger_type == TRIGGER_RETRY
        cascade.assert_not_called()          # 未全量成功不联动
        assert len(fake.jobs) == 1
        assert fake.jobs[0][1]["args"] == [2]  # 序号递增

    def test_retry_gate_denied_warns_and_terminates_chain(self, test_db, monkeypatch, caplog):
        """#715 复核 Suggestion：重试时刻闸门拒绝（锁被占/非交易日）→ 重试链静默终止是
        本 PR 要消灭的「失败不可见」。断言：不落执行记录、不注册下一次、留一条 WARNING。"""
        self._task(test_db, "nav_sync")
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        ran = []
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync",
                            lambda db, log_id: ran.append(1) or {
                                "synced_count": 0, "products_count": 0, "failed_products": []})

        import logging
        with caplog.at_level(logging.WARNING, logger="app.services.scheduler_service"):
            _run_trigger(test_db, lambda: scheduler_service._trigger_nav_sync_retry(2),
                         decisions=[0], calendar=MagicMock(is_open=True))  # 锁被占

        assert ran == []                              # 未执行 nav_sync
        assert fake.jobs == []                        # 未注册下一次重试
        assert test_db.query(TaskExecutionLog).count() == 0
        assert any("重试链终止" in r.message and "第 2 次" in r.message
                   for r in caplog.records)

    def test_retry_exhausted_stops(self, test_db, monkeypatch):
        self._task(test_db, "nav_sync")
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", MagicMock())
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 1, "products_count": 2, "failed_products": ["A.OF"],
            "target_date": date.today().isoformat(),
        })

        last = scheduler_service.NAV_RETRY_MAX_ATTEMPTS
        _run_trigger(test_db, lambda: scheduler_service._trigger_nav_sync_retry(last),
                     decisions=[1], calendar=MagicMock(is_open=True))

        assert fake.jobs == []  # 第 last 次仍失败 → 尝试 last+1 超上限 → 停手


class TestSnapshotCascade:
    """联动补跑：当日快照已成功→跳过；未成功→补跑一次(cascade)；补跑失败→不再追"""

    def _seed_snapshot_log(self, test_db, status, trigger=TRIGGER_SCHEDULED):
        log = TaskExecutionLog(
            task_code="snapshot_generate", trigger_type=trigger, status=status,
            started_at=datetime.now(),
        )
        test_db.add(log)
        test_db.commit()
        return log

    def test_already_succeeded_today_skips(self, test_db, monkeypatch):
        self._seed_snapshot_log(test_db, "success")
        called = []
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate",
                            lambda db, log_id: called.append(1) or {
                                "portfolios_processed": 0, "warnings": [], "auto_confirm_failed": []})

        db, counter, patches = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db)
        finally:
            for p in reversed(patches):
                p.stop()

        assert called == []                       # 未补跑
        assert counter["get"] == 0                # 连快照锁都没取
        assert test_db.query(TaskExecutionLog).count() == 1  # 只有种子行

    def test_day_start_window_is_business_day_not_process_midnight(self, test_db, monkeypatch):
        """#746 陷阱②：「当日成功记录」窗锚在**业务午夜换算到进程墙钟**的时点。

        冻结周二 07:00 CST（UTC 仍是周一 23:00）。种子记录 started_at 写成
        2026-10-19 02:00（进程 naive，业务上属**前一**业务日）。若窗口起点误取
        进程日界（无论 UTC 午夜还是 CST 进程 10-19 00:00），它都落在窗内、被误判
        「当日已成功」而跳过联动；正确实现（业务午夜换算进程墙钟）下它落在窗外，
        联动照常补跑。
        """
        _freeze_business_clock(monkeypatch, TUE_0700_CST)
        log = TaskExecutionLog(
            task_code="snapshot_generate", trigger_type=TRIGGER_SCHEDULED,
            status="success", started_at=datetime(2026, 10, 19, 2, 0),
        )
        test_db.add(log)
        test_db.commit()
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", lambda db, log_id: {
            "snapshots_generated": 1, "portfolios_processed": 1,
            "warnings": [], "auto_confirm_failed": [],
        })

        db, _counter, patches = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db)
        finally:
            for p in reversed(patches):
                p.stop()

        cascade_logs = [l for l in test_db.query(TaskExecutionLog).all()
                        if l.trigger_type == TRIGGER_CASCADE]
        assert len(cascade_logs) == 1  # 前一业务日的成功不算当日 → 补跑

    def test_business_morning_record_counts_as_today(self, test_db, monkeypatch):
        """边界另一侧：07:00 CST 落库的记录（UTC 进程钟上是前一日 23:00 naive）
        属业务当日 → 联动跳过。与上一条合起来把窗口两侧都钉死。"""
        _freeze_business_clock(monkeypatch, TUE_0700_CST)
        # started_at 由进程 naive 钟写入，取「冻结瞬间换算到进程墙钟」的 naive 形态，
        # 与生产 run_task 写 started_at 的时钟完全同源（datetime.now() 不带 tz）
        process_naive_morning = TUE_0700_CST.astimezone().replace(tzinfo=None)
        log = TaskExecutionLog(
            task_code="snapshot_generate", trigger_type=TRIGGER_SCHEDULED,
            status="success", started_at=process_naive_morning,
        )
        test_db.add(log)
        test_db.commit()
        called = []
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate",
                            lambda db, log_id: called.append(1) or {
                                "portfolios_processed": 0, "warnings": [], "auto_confirm_failed": []})

        db, counter, patches = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db)
        finally:
            for p in reversed(patches):
                p.stop()

        assert called == []
        assert counter["get"] == 0
        assert process_naive_morning >= business_day_start()

    def test_not_succeeded_today_cascades_once(self, test_db, monkeypatch):
        self._seed_snapshot_log(test_db, "partial_success")  # 当日定时快照非成功
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", lambda db, log_id: {
            "snapshots_generated": 1, "portfolios_processed": 1,
            "warnings": [], "auto_confirm_failed": [],
        })

        db, counter, patches = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db)
        finally:
            for p in reversed(patches):
                p.stop()

        assert counter == {"get": 1, "release": 1}  # 取/放快照锁各一次
        cascade_logs = [
            log for log in test_db.query(TaskExecutionLog).all()
            if log.trigger_type == TRIGGER_CASCADE
        ]
        assert len(cascade_logs) == 1
        assert cascade_logs[0].task_code == "snapshot_generate"
        assert cascade_logs[0].status == "success"

    def test_no_snapshot_log_today_cascades(self, test_db, monkeypatch):
        """当日从未跑过快照（无记录）也视为「未成功」→ 补跑"""
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", lambda db, log_id: {
            "snapshots_generated": 0, "portfolios_processed": 0,
            "warnings": [], "auto_confirm_failed": [],
        })

        db, _counter, patches = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db)
        finally:
            for p in reversed(patches):
                p.stop()

        cascade_logs = [
            log for log in test_db.query(TaskExecutionLog).all()
            if log.trigger_type == TRIGGER_CASCADE
        ]
        assert len(cascade_logs) == 1

    def test_cascade_failure_does_not_recurse(self, test_db, monkeypatch):
        """补跑自身失败：run_task 落 failed 并 re-raise，联动吞掉不追（只此一次）"""
        def boom(db, log_id):
            raise RuntimeError("快照依赖缺失")

        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", boom)

        db, counter, patches = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db)  # 不抛
        finally:
            for p in reversed(patches):
                p.stop()

        assert counter == {"get": 1, "release": 1}  # 失败仍释放锁
        logs = test_db.query(TaskExecutionLog).all()
        assert len(logs) == 1                        # 只有一次 cascade 记录，无二次
        assert logs[0].trigger_type == TRIGGER_CASCADE
        assert logs[0].status == "failed"

    def test_snapshot_lock_held_defers_to_recheck(self, test_db, monkeypatch):
        """快照锁被占（当日快照首跑仍在进行）→ 本次不并发补跑，但登记有界延迟复查，
        不当作联动终态永久丢弃（#715 复核 Blocker）。"""
        ran = []
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate",
                            lambda db, log_id: ran.append(1) or {
                                "portfolios_processed": 0, "warnings": [], "auto_confirm_failed": []})
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)

        db, counter, patches = _session_with_locks(test_db, [0])  # GET_LOCK 返回 0
        try:
            scheduler_service._maybe_cascade_snapshot(db)
        finally:
            for p in reversed(patches):
                p.stop()

        assert ran == []                        # 本次未补跑（不并发）
        assert counter["get"] == 1
        assert counter["release"] == 0          # 未取到锁不释放
        assert test_db.query(TaskExecutionLog).count() == 0
        # 登记了第 1 次延迟复查而非静默丢弃
        assert len(fake.jobs) == 1
        func, kwargs = fake.jobs[0]
        assert func is scheduler_service._trigger_snapshot_cascade_recheck
        assert kwargs["args"] == [1]
        assert kwargs["id"] == f"snapshot_cascade_recheck_{business_today().isoformat()}_1"

    def test_lock_held_recheck_runs_once_after_holder_fails(self, test_db, monkeypatch):
        """时序①：锁持有者最终失败/部分成功 → 延迟复查（锁已释放）后恰好补跑一次 cascade。"""
        self._seed_snapshot_log(test_db, "partial_success")  # 当日定时快照非成功
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", lambda db, log_id: {
            "snapshots_generated": 1, "portfolios_processed": 1,
            "warnings": [], "auto_confirm_failed": [],
        })
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)

        # 首次联动：锁被占（decisions=[0]）→ 只登记复查，不补跑
        db0, c0, p0 = _session_with_locks(test_db, [0])
        try:
            scheduler_service._maybe_cascade_snapshot(db0)
        finally:
            for p in reversed(p0):
                p.stop()
        assert len(fake.jobs) == 1
        assert not [l for l in test_db.query(TaskExecutionLog).all()
                    if l.trigger_type == TRIGGER_CASCADE]

        # 复查触发（锁已释放，decisions=[1]）→ 重查当日仍无成功记录 → 补跑一次
        db1, c1, p1 = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db1, recheck_attempt=1)
        finally:
            for p in reversed(p1):
                p.stop()

        cascade_logs = [l for l in test_db.query(TaskExecutionLog).all()
                        if l.trigger_type == TRIGGER_CASCADE]
        assert len(cascade_logs) == 1           # 恰好补跑一次
        assert cascade_logs[0].status == "success"
        assert c1 == {"get": 1, "release": 1}

    def test_lock_held_recheck_skips_after_holder_succeeds(self, test_db, monkeypatch):
        """时序②：锁持有者最终成功 → 复查时当日已有 success 记录 → 不补跑。"""
        ran = []
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate",
                            lambda db, log_id: ran.append(1) or {
                                "portfolios_processed": 0, "warnings": [], "auto_confirm_failed": []})
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)

        # 首次联动：锁被占 → 登记复查
        db0, _c0, p0 = _session_with_locks(test_db, [0])
        try:
            scheduler_service._maybe_cascade_snapshot(db0)
        finally:
            for p in reversed(p0):
                p.stop()
        assert len(fake.jobs) == 1

        # 持锁的定时快照最终成功落库
        self._seed_snapshot_log(test_db, "success")

        # 复查触发：重查当日成功记录 → 跳过，连快照锁都不取，不补跑
        db1, c1, p1 = _session_with_locks(test_db, [1])
        try:
            scheduler_service._maybe_cascade_snapshot(db1, recheck_attempt=1)
        finally:
            for p in reversed(p1):
                p.stop()

        assert ran == []
        assert c1["get"] == 0
        assert not [l for l in test_db.query(TaskExecutionLog).all()
                    if l.trigger_type == TRIGGER_CASCADE]


class TestMaybeScheduleSnapshotCascadeRecheck:
    """`_maybe_schedule_snapshot_cascade_recheck`：有界序号 + 确定性 id 去重 + 一次性 date job 形态"""

    def test_registers_bounded_date_job(self, monkeypatch):
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        before = business_now()
        assert scheduler_service._maybe_schedule_snapshot_cascade_recheck(1) is True

        func, kwargs = fake.jobs[0]
        assert func is scheduler_service._trigger_snapshot_cascade_recheck
        assert kwargs["trigger"] == "date"
        assert kwargs["jobstore"] == "default"          # 持久化 → 重启存活
        assert kwargs["replace_existing"] is True        # 确定性 id 去重
        assert kwargs["misfire_grace_time"] is None
        assert kwargs["args"] == [1]
        assert kwargs["id"] == f"snapshot_cascade_recheck_{business_today().isoformat()}_1"
        assert kwargs["run_date"] >= before + scheduler_service.SNAPSHOT_CASCADE_RECHECK_INTERVAL - timedelta(seconds=5)

    def test_recheck_job_id_uses_business_day_across_utc_boundary(self, monkeypatch):
        """#746 验收：冻结 07:00 CST（UTC 仍为前一日）复查 job id 取业务日"""
        _freeze_business_clock(monkeypatch, TUE_0700_CST)
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        assert scheduler_service._maybe_schedule_snapshot_cascade_recheck(1) is True
        assert fake.jobs[0][1]["id"] == "snapshot_cascade_recheck_2026-10-20_1"

    def test_attempts_bounded_at_max(self, monkeypatch):
        """序号超上限即停手，交次日自愈（有界，不无限复查）"""
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        over = scheduler_service.SNAPSHOT_CASCADE_RECHECK_MAX_ATTEMPTS + 1
        assert scheduler_service._maybe_schedule_snapshot_cascade_recheck(over) is False
        assert fake.jobs == []

    def test_no_scheduler_is_noop(self, monkeypatch):
        monkeypatch.setattr(scheduler_service, "_scheduler", None)
        assert scheduler_service._maybe_schedule_snapshot_cascade_recheck(1) is False

    def test_deterministic_id_dedups_same_day(self, monkeypatch):
        """同日同序号重复登记 → 同一确定性 job id（多 worker 下只排一个，同日最多补跑一次）"""
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        scheduler_service._maybe_schedule_snapshot_cascade_recheck(1)
        scheduler_service._maybe_schedule_snapshot_cascade_recheck(1)
        ids = [kwargs["id"] for _func, kwargs in fake.jobs]
        assert ids[0] == ids[1] == f"snapshot_cascade_recheck_{business_today().isoformat()}_1"
        assert all(kwargs["replace_existing"] for _f, kwargs in fake.jobs)


class TestManualRunDoesNotRetry:
    """手动触发不经调度重试：run_task 自身不注册重试（重试只在调度触发体上挂）"""

    def test_manual_partial_success_schedules_nothing(self, test_db, monkeypatch):
        fake = _FakeScheduler()
        monkeypatch.setattr(scheduler_service, "_scheduler", fake)
        task = test_db.query(ScheduledTask).filter_by(code="nav_sync").first()
        if task is None:
            test_db.add(ScheduledTask(code="nav_sync", name="nav_sync", is_enabled=True))
            test_db.commit()
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 1, "products_count": 2, "failed_products": ["A.OF"],
            "target_date": date.today().isoformat(),
        })

        run_task(test_db, "nav_sync", trigger_type=TRIGGER_MANUAL)

        assert test_db.query(TaskExecutionLog).one().status == "partial_success"
        assert fake.jobs == []  # run_task 不挂重试逻辑


class TestSnapshotGenerateFallbackSkip:
    """#747：07:30 快照兜底——当日已成功（含 nav 联动）则跳过、不建第二条记录"""

    def test_success_today_skips_scheduled_generate(self, test_db, monkeypatch):
        seed = TaskExecutionLog(
            task_code="snapshot_generate", trigger_type=TRIGGER_CASCADE,
            status="success", started_at=datetime.now(),
        )
        test_db.add(seed)
        test_db.commit()
        ran = []
        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate",
                            lambda db, log_id: ran.append(1) or {})

        counter = _run_trigger(
            test_db, scheduler_service._trigger_daily_snapshot_generate,
            decisions=[1], calendar=MagicMock(is_open=True),
        )

        assert counter == {"get": 1, "release": 1}
        assert ran == []
        assert test_db.query(TaskExecutionLog).count() == 1  # 只有种子行，未建第二条

    def test_previous_business_day_success_does_not_skip(self, test_db, monkeypatch):
        """窗口按业务日：前一业务日成功不算当日成功，定时快照照常执行"""
        _freeze_business_clock(monkeypatch, TUE_0700_CST)
        seed = TaskExecutionLog(
            task_code="snapshot_generate", trigger_type=TRIGGER_SCHEDULED,
            status="success", started_at=datetime(2026, 10, 19, 2, 0),
        )
        test_db.add(seed)
        test_db.commit()
        ran = []

        def fake_dispatch(db, log_id):
            ran.append(log_id)
            return {
                "snapshots_generated": 1, "portfolios_processed": 1,
                "warnings": [], "auto_confirm_failed": [],
            }

        monkeypatch.setitem(_TASK_DISPATCH, "snapshot_generate", fake_dispatch)

        _run_trigger(
            test_db, scheduler_service._trigger_daily_snapshot_generate,
            decisions=[1], calendar=MagicMock(is_open=True),
        )

        assert len(ran) == 1
        assert test_db.query(TaskExecutionLog).count() == 2


class TestManualNavNoCascade:
    """#747：手动 nav_sync 不经调度触发体，因而不联动快照（无需新分支）"""

    def test_manual_run_does_not_cascade_even_with_new_nav(self, test_db, monkeypatch):
        task = test_db.query(ScheduledTask).filter_by(code="nav_sync").first()
        if task is None:
            test_db.add(ScheduledTask(code="nav_sync", name="nav_sync", is_enabled=True))
            test_db.commit()
        cascade = MagicMock()
        monkeypatch.setattr(scheduler_service, "_maybe_cascade_snapshot", cascade)
        monkeypatch.setattr(scheduler_service, "_scheduler", _FakeScheduler())
        monkeypatch.setitem(_TASK_DISPATCH, "nav_sync", lambda db, log_id: {
            "synced_count": 3, "new_nav_count": 3, "products_count": 3,
            "failed_products": [], "target_date": date.today().isoformat(),
        })

        run_task(test_db, "nav_sync", trigger_type=TRIGGER_MANUAL)

        cascade.assert_not_called()


class TestRetryJobPersistence:
    """重启存活：重试 job 落持久化 JobStore，新调度器实例能读回"""

    def test_retry_job_survives_scheduler_restart(self, tmp_path, monkeypatch):
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

        url = f"sqlite:///{tmp_path / 'jobs.db'}"
        job_id = f"nav_sync_retry_{business_today().isoformat()}_1"

        def make_scheduler():
            return BackgroundScheduler(
                jobstores={"default": SQLAlchemyJobStore(url=url)},
                timezone="Asia/Shanghai",
            )

        s1 = make_scheduler()
        s1.start()
        try:
            monkeypatch.setattr(scheduler_service, "_scheduler", s1)
            assert scheduler_service._maybe_schedule_nav_retry("partial_success", 1) is True
            assert s1.get_job(job_id) is not None
        finally:
            s1.shutdown(wait=False)

        # 模拟进程重启：全新调度器实例连同一 JobStore，应能读回未触发的重试 job
        s2 = make_scheduler()
        s2.start()
        try:
            assert s2.get_job(job_id) is not None
        finally:
            s2.shutdown(wait=False)

