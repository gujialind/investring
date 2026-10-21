"""业务时钟单一来源（#746）。

APScheduler 按 `settings.timezone`（默认 Asia/Shanghai）触发 cron，而生产容器进程
为 UTC：用进程本地 `datetime.now()`/`date.today()` 算「业务昨天」时，07:00 CST 恰是
前一日 23:00 UTC，目标日落到 T-2、交易日闸门方向反转（周一被当周日跳过、周六被当
周五误跑）。业务日期计算一律经本模块取数，与调度触发共用同一时区设置。

`_utcnow()` 是唯一测试冻结点：business_today / business_now / business_day_start
全部由它派生，patch 一处即全链路同钟（只 patch business_today 会让 job_id、
run_date 走真钟，验收形同虚设）。

进程瞬时戳（TaskExecutionLog.started_at、jwt exp 等）不属业务日语义，仍用原钟。
"""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings


def _utcnow() -> datetime:
    """唯一取钟点。测试经 monkeypatch 本函数冻结业务时钟。"""
    return datetime.now(timezone.utc)


def business_timezone() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def business_now() -> datetime:
    """业务时区的 aware 当前时刻。"""
    return _utcnow().astimezone(business_timezone())


def business_today() -> date:
    """业务时区的「今天」。业务目标日惯例为 business_today() - 1。"""
    return business_now().date()


def business_day_start() -> datetime:
    """业务日零点在**进程墙钟**上的 naive 表示。

    与 TaskExecutionLog.started_at 比较必须同钟：started_at 由进程 naive 钟
    （datetime.now()）写入，把业务午夜换算回进程时区再脱 tzinfo，07:00 CST
    （= 前一日 23:00 UTC）落库的当日记录才在「业务当日」窗口内。
    """
    midnight = datetime.combine(
        business_today(), time.min, tzinfo=business_timezone()
    )
    return midnight.astimezone().replace(tzinfo=None)
