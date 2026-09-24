from sqlalchemy.orm import Session
from app.models.scheduled_task import ScheduledTask


def scheduled_task_definitions() -> list[dict]:
    return [
        {
            "code": "nav_sync",
            "name": "净值同步",
            "description": (
                "每交易日 07:00 增量同步产品净值并检测分红；"
                "禁用将导致净值数据中断，估值与收益统计停留在最后一次同步日"
            ),
            "cron_expr": "0 7 * * 1-5",
        },
        {
            "code": "snapshot_generate",
            "name": "组合快照生成",
            "description": (
                "每交易日 07:30 为开启自动快照的活跃组合逐日补齐快照"
                "（依赖当日净值同步先成功，缺净值将 fail-fast 并记入日志、次日自愈）；"
                "仅自动任务受组合开关约束，手动生成/重算不受影响"
            ),
            "cron_expr": "30 7 * * 1-5",
        },
        {
            "code": "trading_calendar_sync",
            "name": "交易日历同步",
            "description": (
                "每年 1 月 1 日 02:00 同步新年度交易日历；"
                "禁用将导致新年度缺少交易日数据，交易确认日与快照日期推算失败"
            ),
            "cron_expr": "0 2 1 1 *",
        },
        {
            "code": "log_cleanup",
            "name": "日志清理",
            "description": (
                "每周日 04:00 清理超过保留期的系统日志与任务执行日志；"
                "禁用不影响业务数据，但日志表会持续膨胀"
            ),
            "cron_expr": "0 4 * * 0",
        },
    ]


def init_scheduled_tasks(db: Session) -> None:
    for task_data in scheduled_task_definitions():
        existing = db.query(ScheduledTask).filter(
            ScheduledTask.code == task_data["code"]
        ).first()

        if not existing:
            task = ScheduledTask(**task_data)
            db.add(task)
        else:
            existing.name = task_data["name"]
            existing.description = task_data["description"]
            # cron_expr 尊重环境自定义，不随启动重置

    db.commit()
