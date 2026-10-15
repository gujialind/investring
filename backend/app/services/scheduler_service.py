"""APScheduler 定时调度服务。

多 worker 互斥：用 MySQL GET_LOCK 确保每日定时任务只在一个进程中执行。
两条独立每日 job（issue #156 剥离）：daily_nav_sync（净值同步+分红检测）与
daily_snapshot_generate（组合快照生成，仅处理开启 auto_snapshot_enabled 的
活跃组合），均直接调 task_runner 执行体，不走 sync_job 路径。

任务执行记录（#406）：两条 job 都经 `task_runner.run_task(..., trigger_type="scheduled")`
落 TaskExecutionLog，与手动触发共用同一编排实现、口径不漂移。**两类跳过场景不落库**
（锁被另一个进程持有、当天非交易日）——跳过不是执行，执行历史只记真正执行过的运行。

nav_sync 有界自动重试（#715）：**定时首跑**终态非全量成功（partial_success /
failed）时向持久化 JobStore 注册一次性 date-trigger 重试 job（间隔 2 小时、
上限 3 次，确定性 job id + replace_existing 去重），重试运行以 trigger_type=retry
落执行记录；重试全量成功后若当日快照定时运行未成功，联动补跑一次快照
（trigger_type=cascade），补跑自身失败不再追。手动触发（submit_task 路径）
不挂重试检查。重试 job 与首跑共用 `_should_run_today` 闸门，多 worker 下仍由
GET_LOCK 收敛为单执行；重启后 job 自 JobStore 重载（misfire_grace_time=None，
长时间停机后的过期重试仍会补跑——nav_sync 是增量同步且有交易日闸门，补跑无害）。
"""
import logging
from datetime import date, datetime, time, timedelta

from apscheduler.jobstores.base import BaseJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import inspect

from app.config import get_settings

logger = logging.getLogger(__name__)

_scheduler = None

# GET_LOCK 锁名（与任务码解耦：锁是进程级互斥资源，任务码是业务标识）
NAV_SYNC_LOCK = "daily_nav_sync_lock"
SNAPSHOT_GENERATE_LOCK = "snapshot_generate_lock"

# nav_sync 定时运行非全量成功后的有界重试（#715）。刻意不做配置旋钮：
# 间隔/上限是链路自愈语义的一部分，改动应经代码评审而非环境变量漂移。
NAV_RETRY_INTERVAL = timedelta(hours=2)
NAV_RETRY_MAX_ATTEMPTS = 3


class PreparedSQLAlchemyJobStore(SQLAlchemyJobStore):
    def start(self, scheduler, alias):
        # SQLAlchemyJobStore.start() issues CREATE TABLE even when the schema is prepared.
        BaseJobStore.start(self, scheduler, alias)
        if not inspect(self.engine).has_table(self.jobs_t.name, schema=self.jobs_t.schema):
            raise RuntimeError("Scheduler table is missing; run the explicit bootstrap prepare command")


def init_scheduler():
    """应用启动时调用：初始化 scheduler + 注册每日 job + 孤儿恢复。"""
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPool

    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("调度器已禁用 (scheduler_enabled=False)")
        return

    scheduler = BackgroundScheduler(
        jobstores={
            "default": PreparedSQLAlchemyJobStore(
                url=settings.database_url,
                tablename=settings.scheduler_jobstore_table,
            )
        },
        executors={
            "default": APSThreadPool(max_workers=2),
        },
        timezone="Asia/Shanghai",
    )
    scheduler.start()
    _scheduler = scheduler

    _scheduler.add_job(
        _trigger_daily_nav_sync,
        trigger="cron",
        **_parse_cron(settings.scheduler_cron_daily),
        id="daily_nav_sync",
        replace_existing=True,
        jobstore="default",
    )

    _scheduler.add_job(
        _trigger_daily_snapshot_generate,
        trigger="cron",
        **_parse_cron(settings.scheduler_cron_snapshot),
        id="daily_snapshot_generate",
        replace_existing=True,
        jobstore="default",
    )

    from app.services.market_data_service import recover_orphan_jobs
    recovered = recover_orphan_jobs()
    if recovered:
        logger.info(f"恢复 {recovered} 个孤儿 running/pending job -> interrupted")


def _should_run_today(db, lock_name: str) -> bool:
    """执行前两道闸门：GET_LOCK 互斥 → 交易日判断。

    True 表示本进程持有锁且今天是交易日，可以执行；False 表示应跳过
    （另一进程持有锁、或当日非交易日），调用方直接 return、**不建任务执行记录**。

    锁的获取与释放刻意分处两个函数（本函数取、触发体 finally 释放）：MySQL
    GET_LOCK 是连接级会话锁，同一连接重复释放无副作用，但提前释放会让互斥失效，
    故不在判 False 的分支里释放。
    """
    from sqlalchemy import text
    from app.models.trading_calendar import TradingCalendar

    acquired = db.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name}).scalar()
    if not acquired or acquired == 0:
        logger.info(f"另一个进程已持有 {lock_name}，跳过")
        return False

    today = date.today()
    cal = db.query(TradingCalendar).filter(TradingCalendar.calendar_date == today).first()
    if not cal or not cal.is_open:
        logger.info(f"{today} 非交易日，跳过 {lock_name}")
        return False

    return True


def _release_lock(db, lock_name: str) -> None:
    """释放 GET_LOCK 会话锁。释放本身失败不应掩盖任务异常（finally 内扰动）。"""
    from sqlalchemy import text

    try:
        db.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
    except Exception as e:  # noqa: BLE001 —— 连接已断时释放必失败，只记不抛
        logger.warning(f"释放 {lock_name} 失败（连接可能已断）: {e}")


def _trigger_daily_nav_sync():
    """APScheduler 触发体：互斥+交易日闸门 → run_task(trigger_type=scheduled)。

    终态非全量成功时注册一次性重试 job（#715，`_maybe_schedule_nav_retry`）。
    """
    from app.database import SessionLocal
    from app.services.task_runner import TRIGGER_SCHEDULED

    db = SessionLocal()
    try:
        if not _should_run_today(db, NAV_SYNC_LOCK):
            return
        status = _run_nav_sync_and_derive(db, TRIGGER_SCHEDULED)
        _maybe_schedule_nav_retry(status, attempt=1)
    except Exception as e:
        logger.error(f"每日净值同步触发体异常: {e}", exc_info=True)
    finally:
        _release_lock(db, NAV_SYNC_LOCK)
        db.close()


def _trigger_nav_sync_retry(attempt: int):
    """一次性重试触发体（#715）：与首跑共用闸门（GET_LOCK 互斥 + 交易日判断）。

    - 终态 success → 检查当日快照联动补跑（`_maybe_cascade_snapshot`），链条终止；
    - 非 success 且序号未耗尽 → 注册下一次尝试（序号递增，天然有界）；
    - 序号耗尽 → 停手，执行记录已落库，交人工处置（CLI / 产品页批量同步）。

    run_task 抛异常同样按非成功处理并计入一次尝试——异常路径的 failed 终态已由
    run_task 落库，这里只负责调度语义，不会产生无限自注册。
    """
    from app.database import SessionLocal
    from app.services.task_runner import TRIGGER_RETRY

    db = SessionLocal()
    try:
        if not _should_run_today(db, NAV_SYNC_LOCK):
            return
        status = _run_nav_sync_and_derive(db, TRIGGER_RETRY)
        if status == "success":
            _maybe_cascade_snapshot(db)
        else:
            _maybe_schedule_nav_retry(status, attempt + 1)
    except Exception as e:
        logger.error(f"nav_sync 重试（第 {attempt} 次）触发体异常: {e}", exc_info=True)
    finally:
        _release_lock(db, NAV_SYNC_LOCK)
        db.close()


def _run_nav_sync_and_derive(db, trigger_type: str) -> str:
    """执行 nav_sync 并把终态归一为 status 字符串（#715 重试判定的单一口径）。

    终态经 `_derive_log_fields` 派生（与 TaskExecutionLog 落库同一实现，不分叉）；
    run_task 抛异常时返回 "failed"（failed 终态已由 run_task 落库并 re-raise，
    此处吞掉异常保住调度线程，语义同既有触发体）。
    """
    from app.services.task_runner import _derive_log_fields, run_task

    try:
        result = run_task(db, "nav_sync", trigger_type=trigger_type)
    except Exception as e:
        logger.error(f"净值同步失败 (trigger_type={trigger_type}): {e}", exc_info=True)
        return "failed"
    status = _derive_log_fields("nav_sync", result)[0]
    logger.info(
        f"净值同步完成 (trigger_type={trigger_type}): "
        f"{result.get('synced_count', 0)} 条, status={status}"
    )
    return status


def _maybe_schedule_nav_retry(status: str, attempt: int) -> bool:
    """终态非全量成功时注册第 attempt 次重试（1 起计），返回是否注册成功。

    - 确定性 job id（当日日期 + 序号）+ replace_existing：同日同序号重复注册天然去重；
    - jobstore="default"（持久化）：进程重启后重试仍发生；
    - misfire_grace_time=None：停机跨过触发时刻后仍补跑（见模块 docstring 的取舍）。
    """
    if status == "success":
        return False
    if _scheduler is None:
        logger.warning(f"调度器未初始化，跳过 nav_sync 重试注册（status={status}）")
        return False
    if attempt > NAV_RETRY_MAX_ATTEMPTS:
        logger.warning(
            f"nav_sync 重试已达上限（{NAV_RETRY_MAX_ATTEMPTS} 次），停手交人工处置；"
            f"最后终态={status}"
        )
        return False
    run_date = datetime.now(_scheduler.timezone) + NAV_RETRY_INTERVAL
    job_id = f"nav_sync_retry_{date.today().isoformat()}_{attempt}"
    _scheduler.add_job(
        _trigger_nav_sync_retry,
        trigger="date",
        run_date=run_date,
        args=[attempt],
        id=job_id,
        replace_existing=True,
        jobstore="default",
        misfire_grace_time=None,
    )
    logger.info(
        f"nav_sync 终态={status}，已注册第 {attempt}/{NAV_RETRY_MAX_ATTEMPTS} 次重试: "
        f"{job_id} @ {run_date.isoformat()}"
    )
    return True


def _maybe_cascade_snapshot(db) -> None:
    """nav 重试全量成功后的快照联动补跑（#715）。

    判据：当日 snapshot_generate 已存在 status=success 的执行记录（任何触发方式，
    避免把人工已补好的快照再算一遍）→ 跳过；否则以 trigger_type=cascade 补跑一次。
    补跑自身不成功不再追——快照任务维持既有「次日自愈」语义。

    补跑前取 SNAPSHOT_GENERATE_LOCK：与定时快照 job 同一把进程间互斥锁，防止与
    仍在运行的当日快照首跑并发（锁被占即跳过，说明有快照运行正在进行）。
    """
    from sqlalchemy import text

    from app.models.task_execution_log import TaskExecutionLog
    from app.services.task_runner import TRIGGER_CASCADE, run_task

    day_start = datetime.combine(date.today(), time.min)
    succeeded = (
        db.query(TaskExecutionLog)
        .filter(
            TaskExecutionLog.task_code == "snapshot_generate",
            TaskExecutionLog.status == "success",
            TaskExecutionLog.started_at >= day_start,
        )
        .count()
    )
    if succeeded:
        logger.info("当日快照已成功，跳过联动补跑")
        return

    acquired = db.execute(
        text("SELECT GET_LOCK(:name, 0)"), {"name": SNAPSHOT_GENERATE_LOCK}
    ).scalar()
    if not acquired or acquired == 0:
        logger.info(f"另一个进程已持有 {SNAPSHOT_GENERATE_LOCK}，跳过快照联动补跑")
        return
    try:
        result = run_task(db, "snapshot_generate", trigger_type=TRIGGER_CASCADE)
        logger.info(f"联动快照补跑完成: {result.get('snapshots_generated', 0)} 个")
    except Exception as e:
        logger.error(f"联动快照补跑失败（不再追，交次日自愈）: {e}", exc_info=True)
    finally:
        _release_lock(db, SNAPSHOT_GENERATE_LOCK)


def _trigger_daily_snapshot_generate():
    """APScheduler 触发体：互斥+交易日闸门 → run_task(trigger_type=scheduled)。"""
    from app.database import SessionLocal
    from app.services.task_runner import TRIGGER_SCHEDULED, run_task

    db = SessionLocal()
    try:
        if not _should_run_today(db, SNAPSHOT_GENERATE_LOCK):
            return
        result = run_task(db, "snapshot_generate", trigger_type=TRIGGER_SCHEDULED)
        logger.info(f"每日快照生成完成: {result.get('snapshots_generated', 0)} 个")
    except Exception as e:
        logger.error(f"每日快照生成失败: {e}", exc_info=True)
    finally:
        _release_lock(db, SNAPSHOT_GENERATE_LOCK)
        db.close()


def shutdown_scheduler():
    """应用关闭时调用。"""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _parse_cron(cron_str: str) -> dict:
    """'0 7 * * *' -> {minute:0, hour:7, day:'*', month:'*', day_of_week:'*'}"""
    parts = cron_str.split()
    keys = ["minute", "hour", "day", "month", "day_of_week"]
    return {k: v for k, v in zip(keys, parts)}
