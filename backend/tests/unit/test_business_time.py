"""business_time 单元测试（#746）。

只验 helper 本身的派生与配置驱动路径；消费方（闸门 / target_date / job_id /
day_start 窗口）的换钟验收在 test_scheduler_service.py 与 test_run_nav_sync.py，
它们经同一 `_utcnow` 冻结点。
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.utils import business_time
from app.utils.business_time import (
    business_day_start,
    business_now,
    business_today,
)

SH = ZoneInfo("Asia/Shanghai")


def _freeze(monkeypatch, instant: datetime) -> None:
    monkeypatch.setattr(business_time, "_utcnow", lambda: instant)


class TestBusinessClock:
    def test_0700_cst_rolls_to_business_tomorrow_of_utc(self, monkeypatch):
        """事故瞬间形态：UTC 仍是 10-19 23:00，业务日已是 10-20。

        business_today 必须取业务侧——这正是 nav_sync target_date 落 T-2 的根因点。
        """
        _freeze(monkeypatch, datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc))
        assert business_today() == date(2026, 10, 20)
        assert business_today() != datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc).date()

    def test_business_now_is_shanghai_wall_clock(self, monkeypatch):
        _freeze(monkeypatch, datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc))
        assert business_now().strftime("%Y-%m-%d %H:%M") == "2026-10-20 07:00"

    def test_boundary_just_before_business_midnight(self, monkeypatch):
        """15:59Z（CST 23:59）仍属前一业务日；16:00Z（CST 00:00）翻篇"""
        _freeze(monkeypatch, datetime(2026, 10, 19, 15, 59, tzinfo=timezone.utc))
        assert business_today() == date(2026, 10, 19)
        _freeze(monkeypatch, datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc))
        assert business_today() == date(2026, 10, 20)

    def test_timezone_is_settings_driven(self, monkeypatch):
        """helper 与调度器共用 settings.timezone 单一来源：改配置即换钟"""
        s = get_settings()
        monkeypatch.setattr(s, "timezone", "UTC", raising=False)
        _freeze(monkeypatch, datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc))
        assert business_today() == date(2026, 10, 19)  # UTC 视角回到旧行为
        monkeypatch.setattr(s, "timezone", "Asia/Shanghai", raising=False)
        assert business_today() == date(2026, 10, 20)


class TestBusinessDayStart:
    """day_start 是「业务午夜在进程墙钟上的 naive 表示」——与 started_at 同钟可比。"""

    def test_naive_value_and_instant_identity(self, monkeypatch):
        _freeze(monkeypatch, datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc))
        ds = business_day_start()
        assert ds.tzinfo is None
        # 进程 naive 值挂回进程本地时区后，指向的瞬时 == 业务日午夜瞬时。
        # （replace 假设进程本地时区无夏令时——CI/部署/开发机为 UTC 或 CST，成立。）
        local = datetime.now().astimezone().tzinfo
        assert ds.replace(tzinfo=local) == datetime(2026, 10, 20, 0, 0, tzinfo=SH)

    def test_utc_process_window_starts_at_1600_previous_day(self, monkeypatch):
        """UTC 进程（生产形态）：业务日窗口的进程 naive 起点是前一日 16:00。

        这正是 07:00 CST 落库的记录（naive 形态随进程时区）仍属「当日」的原因——
        若 day_start 误取 CST 午夜的 naive 字面值（10-20 00:00），它与 UTC naive 的
        started_at 比较会把当日记录判到窗外（#746 陷阱②）。
        """
        instant = datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)
        _freeze(monkeypatch, instant)
        ds = business_day_start()
        # started_at 的形态：run_task 用进程 naive 钟写「冻结瞬间」
        record = instant.astimezone().replace(tzinfo=None)
        assert record >= ds
        if datetime.now().astimezone().utcoffset() == timedelta(0):
            assert ds == datetime(2026, 10, 19, 16, 0)
