"""业务时钟冻结 helper（#746）。

`app.utils.business_time._utcnow` 是业务时钟的唯一取数点；测试 patch 它即让
business_today / business_now / business_day_start 及全部消费方（闸门、job_id、
day_start、target_date）同源换钟——只 patch business_today 会让 job_id、run_date
走真钟，验收形同虚设。刻意不引 freezegun：requirements 无此依赖，单点 patch
已覆盖全部业务日期形态。
"""
from datetime import datetime, timezone

# 07:00 CST 的 UTC 瞬间 = 前一日 23:00 UTC：业务日与进程（UTC）日在此分叉，
# 是时区 bug 的显形窗口。三个常量分别对应周一/周六/周二早晨。
MON_0700_CST = datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc)  # 业务 10-19 周一
SAT_0700_CST = datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc)  # 业务 10-17 周六
SUN_0700_CST = datetime(2026, 10, 17, 23, 0, tzinfo=timezone.utc)  # 业务 10-18 周日
TUE_0700_CST = datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)  # 业务 10-20 周二


def freeze_business_clock(monkeypatch, utc_instant: datetime) -> None:
    """把业务时钟钉在给定 UTC 瞬间（aware datetime，须带 tzinfo）。"""
    monkeypatch.setattr("app.utils.business_time._utcnow", lambda: utc_instant)
