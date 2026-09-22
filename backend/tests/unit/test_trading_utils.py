# ============================================================================
# 单元测试：交易日 helper 的日历耗尽语义（issue #591）
# ============================================================================
# 钉死 get_next/prev_trading_day（抛错版）与 try_get_*（探测版）的三条契约：
# 1. days=0 是场内 T+0 的合法零查询路径：不查日历、返回入参、绝不抛错。
# 2. 日历不足（全耗尽 / 部分耗尽）→ 抛错版抛 CALENDAR_NOT_SYNCED 并带如实 details、
#    探测版返回 None；两者都**绝不**回退返回入参或伪造自然日。
# 3. 返回类型收紧：抛错版 date（非 Optional）、探测版 Optional[date]。
#
# 惯用法：在 test_db 的 SAVEPOINT 事务内清空并重建 TradingCalendar 行造边界，不用
# ensure_trading_day（它会 commit()、逃逸夹具隔离）。整表清空后两个方向都只认受控行，
# 测试结束由 SAVEPOINT 回滚，不影响 session 级种子。
# ============================================================================
import datetime as _dt
import typing
from datetime import date

import pytest

from app.models.trading_calendar import TradingCalendar
from app.services.exceptions import BusinessError
from app.services import trading_utils

FAR = date(2099, 1, 10)  # 受控孤立窗口的锚点


def _cal(db, day, is_open=True):
    db.add(TradingCalendar(calendar_date=day, is_open=is_open, exchange="SSE"))


def _only(db, *days):
    """清空全表，只留 days 为开市日（其余一律不存在），两个方向都变成受控小窗口。"""
    db.query(TradingCalendar).delete(synchronize_session=False)
    for d in days:
        _cal(db, d)
    db.flush()


class TestDaysZeroIsTPlusZero:
    def test_returns_from_date_without_query(self, test_db):
        assert trading_utils.get_next_trading_day(test_db, FAR, days=0) == FAR
        assert trading_utils.get_prev_trading_day(test_db, FAR, days=0) == FAR

    def test_never_raises_even_when_calendar_empty(self, test_db):
        """days=0 完全不查日历：孤立区间无任何行也不抛（场内 T+0 全靠它）。"""
        _only(test_db)  # 2098 之后清空
        assert trading_utils.get_next_trading_day(test_db, FAR, days=0) == FAR
        assert trading_utils.get_prev_trading_day(test_db, FAR, days=0) == FAR

    def test_negative_days_clamped_to_zero(self, test_db):
        _only(test_db)
        assert trading_utils.get_next_trading_day(test_db, FAR, days=-3) == FAR


class TestExhaustionRaises:
    def test_full_exhaustion_next(self, test_db):
        """issue 复现形态：只有 2099-01-05 开市，从其后一天要下一个交易日 → 走不动。"""
        _only(test_db, date(2099, 1, 5))
        with pytest.raises(BusinessError) as ei:
            trading_utils.get_next_trading_day(test_db, date(2099, 1, 6), days=1)
        err = ei.value
        assert err.code == "CALENDAR_NOT_SYNCED"
        assert err.http_status == 422
        assert err.details["resolved_days"] == 0
        assert err.details["requested_days"] == 1
        assert err.details["direction"] == "next"

    def test_partial_exhaustion_next(self, test_db):
        """days=3 但只有 1 个后续交易日 → 抛错、resolved_days=1（旧代码返回那个中间日）。"""
        _only(test_db, date(2099, 1, 11), date(2099, 1, 12))
        with pytest.raises(BusinessError) as ei:
            trading_utils.get_next_trading_day(test_db, FAR, days=3)
        assert ei.value.details == {
            "from_date": FAR.isoformat(),
            "direction": "next",
            "requested_days": 3,
            "resolved_days": 2,
        }

    def test_full_exhaustion_prev(self, test_db):
        _only(test_db, date(2099, 1, 9))  # FAR 之前只有这一天
        with pytest.raises(BusinessError) as ei:
            trading_utils.get_prev_trading_day(test_db, date(2099, 1, 5), days=1)
        assert ei.value.code == "CALENDAR_NOT_SYNCED"
        assert ei.value.details["resolved_days"] == 0

    def test_partial_exhaustion_prev(self, test_db):
        _only(test_db, date(2099, 1, 8), date(2099, 1, 9))
        with pytest.raises(BusinessError) as ei:
            trading_utils.get_prev_trading_day(test_db, FAR, days=3)
        assert ei.value.details["requested_days"] == 3
        assert ei.value.details["resolved_days"] == 2

    def test_never_returns_from_date_on_exhaustion(self, test_db):
        """绝不回退返回入参——那是 #591 编造日期的根因。"""
        _only(test_db)
        with pytest.raises(BusinessError):
            trading_utils.get_next_trading_day(test_db, FAR, days=1)


class TestTryVariants:
    @pytest.mark.parametrize("direction", ["next", "prev"])
    def test_returns_none_instead_of_raising(self, test_db, direction):
        _only(test_db)
        fn = (
            trading_utils.try_get_next_trading_day
            if direction == "next"
            else trading_utils.try_get_prev_trading_day
        )
        assert fn(test_db, FAR, days=1) is None

    def test_days_zero_returns_from_date(self, test_db):
        _only(test_db)
        assert trading_utils.try_get_next_trading_day(test_db, FAR, days=0) == FAR


class TestCorrectDates:
    def test_long_holiday_span_returns_calendar_date(self, test_db):
        """连续 9 日闭市后按日历返回准确的下一开市日（不是自然日推算）。"""
        days = [date(2099, 2, 1) + _dt.timedelta(days=i) for i in range(12)]
        _only(test_db, days[0], days[11])  # 起点与终点开市，中间全闭市
        assert trading_utils.get_next_trading_day(test_db, days[0], days=1) == days[11]

    def test_days_n_walks_n_open_days(self, test_db):
        d1, d2, d3 = date(2099, 3, 1), date(2099, 3, 2), date(2099, 3, 3)
        _only(test_db, d1, d2, d3)
        assert trading_utils.get_next_trading_day(test_db, d1, days=2) == d3

    def test_closed_days_are_skipped(self, test_db):
        """is_open=False 的行不算交易日。"""
        _only(test_db, date(2099, 4, 1), date(2099, 4, 5))
        _cal(test_db, date(2099, 4, 2), is_open=False)
        _cal(test_db, date(2099, 4, 3), is_open=False)
        test_db.flush()
        assert trading_utils.get_next_trading_day(test_db, date(2099, 4, 1), days=1) == date(2099, 4, 5)


class TestReturnAnnotations:
    """抛错版收敛为 date（不再 Optional），探测版保持 Optional[date]（#591 验收 4）。"""

    def test_raising_versions_are_not_optional(self):
        hints = typing.get_type_hints(trading_utils.get_next_trading_day)
        assert hints["return"] is date
        assert typing.get_type_hints(trading_utils.get_prev_trading_day)["return"] is date

    def test_try_versions_are_optional(self):
        assert typing.get_type_hints(
            trading_utils.try_get_next_trading_day
        )["return"] == typing.Optional[date]
