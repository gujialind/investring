"""APScheduler 定时调度服务。

多 worker 互斥：用 MySQL GET_LOCK 确保每日定时任务只在一个进程中执行。
两条独立每日 job（issue #156 剥离）：daily_nav_sync（净值同步+分红检测）与
daily_snapshot_generate（组合快照生成，仅处理开启 auto_snapshot_enabled 的
活跃组合），均直接调 task_runner 执行体，不走 sync_job 路径。

任务执行记录（#406）：两条 job 都经 `task_runner.run_task(..., trigger_type="scheduled")`
落 TaskExecutionLog，与手动触发共用同一编排实现、口径不漂移。**两类跳过场景不落库**
（锁被另一个进程持有、当天非交易日）——跳过不是执行，执行历史只记真正执行过的运行。
"""
import logging
from datetime import date

from app.config import get_settings

logger = logging.getLogger(__name__)

_scheduler = None

# GET_LOCK 锁名（与任务码解耦：锁是进程级互斥资源，任务码是业务标识）
NAV_SYNC_LOCK = "daily_nav_sync_lock"
SNAPSHOT_GENERATE_LOCK = "snapshot_generate_lock"


def init_scheduler():
    """应用启动时调用：初始化 scheduler + 注册每日 job + 孤儿恢复。"""
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPool

    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("调度器已禁用 (scheduler_enabled=False)")
        return

    _scheduler = BackgroundScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(
                url=settings.database_url,
                tablename=settings.scheduler_jobstore_table,
            )
        },
        executors={
            "default": APSThreadPool(max_workers=2),
        },
        timezone="Asia/Shanghai",
    )
    _scheduler.start()

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
        logger.info(f"恢复 {recovered} 个孤儿 running job -> interrupted")


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
    """APScheduler 触发体：互斥+交易日闸门 → run_task(trigger_type=scheduled)。"""
    from app.database import SessionLocal
    from app.services.task_runner import TRIGGER_SCHEDULED, run_task

    db = SessionLocal()
    try:
        if not _should_run_today(db, NAV_SYNC_LOCK):
            return
        result = run_task(db, "nav_sync", trigger_type=TRIGGER_SCHEDULED)
        logger.info(f"每日净值同步完成: {result.get('synced_count', 0)} 条")
    except Exception as e:
        logger.error(f"每日净值同步失败: {e}", exc_info=True)
    finally:
        _release_lock(db, NAV_SYNC_LOCK)
        db.close()


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
