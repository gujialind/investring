from sqlalchemy.orm import Session
from app.config import get_settings
from app.models.scheduled_task import ScheduledTask

# #746 cron 双源收敛：调度器注册的 cron 只读 settings（scheduler_service），
# DB 行的 cron_expr 没有任何调度消费方、纯展示。展示与生效不得分叉，故这两个
# 任务的 cron_expr 在启动同步时取 settings 实际生效值，种子值仅为缺省快照。
_SETTINGS_CRON_ATTRS = {
    "nav_sync": "scheduler_cron_daily",
    "snapshot_generate": "scheduler_cron_snapshot",
}


def _effective_cron(code: str, seeded: str) -> str:
    attr = _SETTINGS_CRON_ATTRS.get(code)
    if attr is None:
        return seeded
    return getattr(get_settings(), attr)


def scheduled_task_definitions() -> list[dict]:
    return [
        {
            "code": "nav_sync",
            "name": "净值同步",
            "description": (
                "每日 07:00 增量同步产品净值并检测分红（非交易日由交易日闸门跳过、不落执行记录）；"
                "窗口下界回看最近 10 个交易日，"
                "用于回补上游迟披露的净值缺口；禁用将导致净值数据中断，估值与收益统计停留在最后一次同步日"
            ),
            "cron_expr": "0 7 * * *",
        },
        {
            "code": "snapshot_generate",
            "name": "组合快照生成",
            "description": (
                "每日 07:30 为开启自动快照的活跃组合逐日补齐快照（非交易日由交易日闸门跳过）"
                "（依赖当日净值同步先成功，缺净值将 fail-fast 并记入日志，需净值回补后重试）；"
                "仅自动任务受组合开关约束，手动生成/重算不受影响"
            ),
            "cron_expr": "30 7 * * *",
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
        code = task_data["code"]
        existing = db.query(ScheduledTask).filter(
            ScheduledTask.code == code
        ).first()
        cron = _effective_cron(code, task_data["cron_expr"])

        if not existing:
            task = ScheduledTask(**{**task_data, "cron_expr": cron})
            db.add(task)
        else:
            existing.name = task_data["name"]
            existing.description = task_data["description"]
            # 有 settings 映射的任务（nav_sync / snapshot_generate）cron_expr 随启动
            # 同步为实际生效值（#746）；其余任务 cron 列仅登记、无生效源，
            # 不得用种子值覆盖环境自定义
            if code in _SETTINGS_CRON_ATTRS:
                existing.cron_expr = cron

    db.commit()
