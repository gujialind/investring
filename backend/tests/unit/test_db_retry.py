# ============================================================================
# 单元测试：MySQL 1213 有界重试 (test_db_retry.py)
# ============================================================================
# issue #618：并发生成快照时，两个事务各自那条命中 0 行的 DELETE 在唯一索引上留下
# 互相兼容的 gap lock，随后的 INSERT 要 insert intention lock → 循环等待 → InnoDB
# 报 1213 并把一方整体回滚。app/utils/db_retry.py 的处置是「整体回滚 + 有界重放」。
#
# 本文件钉住三类契约（死锁本身是 MySQL + REPEATABLE READ 专属，SQLite 造不出来，
# 故这里用真实 errno 形状的异常驱动，不依赖方言）：
#   1. 判定只认 errno 1213，且会沿 SQLAlchemy 的 .orig 与 __cause__/__context__
#      往里走；1205（等锁超时）与 1062（重复键）刻意不重试；不做消息文本匹配
#   2. 重试范围覆盖「整个工作单元含 commit」，重放前必须 rollback 复位会话，
#      预算耗尽后原样抛出原异常对象（router 的 report_unexpected 要拿真因）
#   3. 每次重放留一条 WARNING（可恢复事件口径），退避随尝试次数递增
# ============================================================================

import logging

import pymysql.err
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.services.exceptions import BusinessError
from app.utils.db_retry import (
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_ATTEMPTS,
    MYSQL_DEADLOCK,
    is_retryable_deadlock,
    retry_on_deadlock,
)

DEADLOCK_MSG = "Deadlock found when trying to get lock; try restarting transaction"
# 与 #618 现场一致的语句形状：牺牲者是 investor_holding 的 INSERT
DEADLOCK_STMT = (
    "INSERT INTO investor_holding (portfolio_code, investor_code, shares, snapshot_date) "
    "VALUES (%s, %s, %s, %s)"
)


def sa_deadlock(message: str = DEADLOCK_MSG) -> OperationalError:
    """真实的 SQLAlchemy 包装形状：OperationalError.orig -> pymysql.err.OperationalError"""
    return OperationalError(DEADLOCK_STMT, {}, pymysql.err.OperationalError(1213, message))


def sa_error(errno: int, message: str, cls=pymysql.err.OperationalError) -> OperationalError:
    return OperationalError(DEADLOCK_STMT, {}, cls(errno, message))


class FakeSession:
    """只记录 rollback/commit 调用次数：retry_on_deadlock 对 db 的全部要求就是这两件。"""

    def __init__(self):
        self.rollbacks = 0
        self.commits = 0

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


class Recorder:
    """按脚本逐次抛异常/返回值的工作单元，记录每次尝试。"""

    def __init__(self, script, *, commit: FakeSession | None = None):
        self.script = list(script)
        self.calls = 0
        self._commit = commit

    def __call__(self):
        self.calls += 1
        if self._commit is not None:
            self._commit.commit()
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def no_sleep(monkeypatch):
    """退避睡眠全部记录但不真睡：用例只关心「睡了几次、各睡多久」。"""
    slept: list[float] = []
    monkeypatch.setattr("app.utils.db_retry.time.sleep", slept.append)
    return slept


# ============================================================================
# 1. 判定：只认 errno 1213
# ============================================================================

class TestIsRetryableDeadlock:
    def test_raw_dbapi_error_with_errno_1213(self):
        assert is_retryable_deadlock(pymysql.err.OperationalError(1213, DEADLOCK_MSG))

    def test_sqlalchemy_wrapped_via_orig(self):
        """现场形状：SQLAlchemy 把驱动异常挂在 .orig，只看最外层会漏判"""
        assert is_retryable_deadlock(sa_deadlock())

    def test_wrapped_again_via_raise_from(self):
        """业务层再包一层（raise ... from ...）也要认出来"""
        try:
            try:
                raise sa_deadlock()
            except OperationalError as inner:
                raise RuntimeError("快照生成失败") from inner
        except RuntimeError as outer:
            assert is_retryable_deadlock(outer)

    def test_wrapped_again_via_implicit_context(self):
        """except 块里另抛异常（无 from）时走 __context__"""
        try:
            try:
                raise sa_deadlock()
            except OperationalError:
                raise RuntimeError("快照生成失败")
        except RuntimeError as outer:
            assert is_retryable_deadlock(outer)

    def test_errno_constant_is_pinned(self):
        """errno 常量本身钉死：改这个数等于改重试范围，必须是有意识的动作"""
        assert MYSQL_DEADLOCK == 1213

    @pytest.mark.parametrize(
        "errno_,message",
        [
            (1205, "Lock wait timeout exceeded; try restarting transaction"),
            (1062, "Duplicate entry 'X-ADMIN-2026-09-23' for key 'uix_holding_snapshot'"),
            (2013, "Lost connection to MySQL server during query"),
            (1146, "Table 'ir_test.investor_holding' doesn't exist"),
        ],
    )
    def test_other_errnos_are_not_retryable(self, errno_, message):
        """1205 尤其关键：它的消息也写着 try restarting transaction，
        但那是「等锁超时」，重试只会把同样的等待再来一遍并加倍负载。"""
        assert not is_retryable_deadlock(sa_error(errno_, message))

    def test_message_text_is_not_matched(self):
        """消息里出现 Deadlock 字样但 errno 不是 1213 → 不重试。
        钉死「只认 errno、不做文本匹配」这个取舍。"""
        assert not is_retryable_deadlock(sa_error(1062, "Duplicate entry ... not a Deadlock"))

    def test_errno_as_string_is_not_matched(self):
        """errno 必须是整数 1213；字符串 "1213" 不算（避免退化成文本匹配）"""
        assert not is_retryable_deadlock(OperationalError(
            DEADLOCK_STMT, {}, pymysql.err.OperationalError("1213", DEADLOCK_MSG)
        ))

    @pytest.mark.parametrize("exc", [
        ValueError("依赖数据校验失败"),
        BusinessError(code="MISSING_NAV", message="净值缺失"),
        RuntimeError("boom"),
        OperationalError(DEADLOCK_STMT, {}, pymysql.err.OperationalError()),
    ])
    def test_non_db_and_argless_errors_are_not_retryable(self, exc):
        """领域异常与无 args 的异常都不该被当成死锁"""
        assert not is_retryable_deadlock(exc)

    def test_self_referential_cause_chain_terminates(self):
        """raise x from x 这类自指会让 __cause__ 成环；不去重就是把死锁换成死循环"""
        exc = RuntimeError("self")
        exc.__cause__ = exc
        exc.__context__ = exc
        assert not is_retryable_deadlock(exc)

    def test_long_chain_with_cycle_terminates(self):
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__ = b
        b.__cause__ = a
        assert not is_retryable_deadlock(a)


# ============================================================================
# 2. 重试行为
# ============================================================================

class TestRetryOnDeadlock:
    def test_success_first_attempt_does_nothing_extra(self, no_sleep):
        db = FakeSession()
        work = Recorder([{"success": True}])
        assert retry_on_deadlock(db, work, operation="generate_snapshot") == {"success": True}
        assert work.calls == 1
        assert db.rollbacks == 0 and no_sleep == []

    def test_deadlock_then_success_is_absorbed(self, no_sleep):
        """#618 的现场：第一次 1213，重放成功 → 调用方拿到正常结果"""
        db = FakeSession()
        # commit=db 让工作单元在每次尝试里先提交再抛：模拟「死锁在 commit 处才暴露」，
        # 这正是 commit 必须落在重试范围内的理由。
        work = Recorder([sa_deadlock(), {"success": True, "message": "快照生成成功"}], commit=db)
        out = retry_on_deadlock(db, work, operation="generate_snapshot")
        assert out["success"] is True
        assert work.calls == 2
        # 重放前必须复位会话，否则下一次 flush 抛 PendingRollbackError 盖掉真因
        assert db.rollbacks == 1
        # commit 在重试范围内：整个工作单元（含提交）被重放，而不是只补一次 INSERT
        assert db.commits == 2

    def test_retry_budget_is_bounded_and_original_exception_propagates(self, no_sleep):
        db = FakeSession()
        last = sa_deadlock("third")
        work = Recorder([sa_deadlock("first"), sa_deadlock("second"), last])
        with pytest.raises(OperationalError) as ei:
            retry_on_deadlock(db, work, operation="generate_snapshot", max_attempts=3)
        assert work.calls == 3
        # 抛出的是最后那次的原对象：router 的 report_unexpected 要拿它记 error_type
        assert ei.value is last
        # 预算耗尽后刻意不再 rollback（各端点 rollback 时机不同，留给调用点）
        assert db.rollbacks == 2

    def test_default_budget_is_three_attempts(self, no_sleep):
        db = FakeSession()
        work = Recorder([sa_deadlock()])
        assert DEFAULT_MAX_ATTEMPTS == 3
        with pytest.raises(OperationalError):
            retry_on_deadlock(db, work, operation="generate_snapshot")
        assert work.calls == DEFAULT_MAX_ATTEMPTS

    def test_max_attempts_one_disables_retry(self, no_sleep):
        db = FakeSession()
        work = Recorder([sa_deadlock(), {"success": True}])
        with pytest.raises(OperationalError):
            retry_on_deadlock(db, work, operation="generate_snapshot", max_attempts=1)
        assert work.calls == 1 and db.rollbacks == 0

    def test_non_retryable_error_raises_immediately_without_rollback(self, no_sleep):
        """非 1213 一律原样抛出、不 rollback、不重放——响应契约与 #618 之前一致"""
        db = FakeSession()
        for exc in (
            sa_error(1205, "Lock wait timeout exceeded; try restarting transaction"),
            IntegrityError(DEADLOCK_STMT, {}, pymysql.err.IntegrityError(1062, "Duplicate entry")),
            BusinessError(code="SNAPSHOT_NOT_CONTINUOUS", message="不连续"),
            ValueError("依赖数据校验失败"),
        ):
            db.rollbacks = 0
            work = Recorder([exc, {"success": True}])
            with pytest.raises(type(exc)) as ei:
                retry_on_deadlock(db, work, operation="generate_snapshot")
            assert ei.value is exc
            assert work.calls == 1
            assert db.rollbacks == 0
        assert no_sleep == []

    def test_backoff_grows_with_attempt_number(self, no_sleep):
        db = FakeSession()
        work = Recorder([sa_deadlock(), sa_deadlock(), {"success": True}])
        retry_on_deadlock(db, work, operation="generate_snapshot",
                          max_attempts=4, base_delay=0.1)
        assert no_sleep == [pytest.approx(0.1), pytest.approx(0.2)]

    def test_default_base_delay_keeps_request_budget(self, no_sleep):
        """默认退避必须很小：这发生在请求线程内，E2E 对这条 POST 只给 15s"""
        assert DEFAULT_BASE_DELAY == 0.05
        db = FakeSession()
        work = Recorder([sa_deadlock(), sa_deadlock(), {"success": True}])
        retry_on_deadlock(db, work, operation="generate_snapshot")
        assert sum(no_sleep) < 1.0

    def test_zero_base_delay_skips_sleep(self, no_sleep):
        db = FakeSession()
        work = Recorder([sa_deadlock(), {"success": True}])
        retry_on_deadlock(db, work, operation="generate_snapshot", base_delay=0)
        assert no_sleep == []
        assert work.calls == 2

    def test_default_path_really_sleeps(self, monkeypatch):
        """默认退避确实走 time.sleep（no_sleep 夹具把它换掉了，
        这里单独确认默认路径不是空转、退避不是摆设）"""
        called = []
        monkeypatch.setattr("app.utils.db_retry.time.sleep", called.append)
        db = FakeSession()
        work = Recorder([sa_deadlock(), {"success": True}])
        retry_on_deadlock(db, work, operation="generate_snapshot")
        assert called == [DEFAULT_BASE_DELAY * 1]


# ============================================================================
# 3. 留痕
# ============================================================================

class TestRetryLogging:
    def test_each_retry_leaves_one_warning(self, no_sleep, caplog):
        db = FakeSession()
        work = Recorder([sa_deadlock(), sa_deadlock(), {"success": True}])
        with caplog.at_level(logging.WARNING, logger="app.utils.db_retry"):
            retry_on_deadlock(db, work, operation="generate_snapshot")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2, "每次重放各留一条 WARNING"
        assert [r.attempt for r in warnings] == [1, 2]
        assert all(r.operation == "generate_snapshot" for r in warnings)
        assert all(r.max_attempts == DEFAULT_MAX_ATTEMPTS for r in warnings)

    def test_retry_is_warning_not_error(self, no_sleep, caplog):
        """已被吸收的失败属可恢复事件：WARNING 而非 ERROR（ERROR 留给真冒到响应的）"""
        db = FakeSession()
        work = Recorder([sa_deadlock(), {"success": True}])
        with caplog.at_level(logging.DEBUG, logger="app.utils.db_retry"):
            retry_on_deadlock(db, work, operation="generate_snapshot")
        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    def test_success_without_retry_logs_nothing(self, no_sleep, caplog):
        db = FakeSession()
        work = Recorder([{"success": True}])
        with caplog.at_level(logging.DEBUG, logger="app.utils.db_retry"):
            retry_on_deadlock(db, work, operation="generate_snapshot")
        assert caplog.records == []

    def test_exhausted_budget_leaves_warnings_for_each_retry(self, no_sleep, caplog):
        db = FakeSession()
        work = Recorder([sa_deadlock()])
        with caplog.at_level(logging.WARNING, logger="app.utils.db_retry"):
            with pytest.raises(OperationalError):
                retry_on_deadlock(db, work, operation="generate_snapshot", max_attempts=3)
        # 2 次重放各一条；第 3 次失败直接抛出，由调用点记 ERROR
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
