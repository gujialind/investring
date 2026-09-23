# ============================================================================
# 集成测试：快照生成端点对 MySQL 1213 的有界重试 (test_snapshots_deadlock_retry.py)
# — issue #618
# ============================================================================
# 背景：CI 的 E2E capture 侧（--workers=2 --retries=0）偶发 `POST /api/snapshots/generate`
# 返回 500，根因是两个并发事务在唯一索引上互等 gap lock / insert intention lock，
# InnoDB 报 1213 并把一方整体回滚。实测与取舍见 app/utils/db_retry.py 的模块 docstring。
#
# test_db_retry.py 已经把重试逻辑本身钉死了；本文件钉的是**接线**——helper 有单测
# 但没被端点用上，等于没修。故覆盖：
#   1. service 的 flush 阶段抛 1213 → 端点重放后 200，快照真落库
#   2. commit 阶段才抛 1213 → 同样被吸收（commit 必须在重试范围内）
#   3. 预算耗尽 → 仍是既有的 500 + SNAPSHOT_GENERATION_FAILED，且
#      report_unexpected 拿到的是**原始** OperationalError（error_type 不是 HTTPException），
#      整体回滚不留半截快照
#   4. 1205 等锁超时**不**重试——它的消息同样写着 "try restarting transaction"，
#      只认 errno 才不会被这句话骗到
#   5. happy path 零额外开销：不重放、不留 WARNING
#
# 死锁是 MySQL + REPEATABLE READ 专属（SQLite 造不出 1213），故这里用真实 errno
# 形状的异常驱动端点，不打 dialect marker：被测对象是重试接线，与方言无关。
# ============================================================================

from datetime import date

import pymysql.err
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from app.models import InvestorHolding, PortfolioPosition, PortfolioValueSnapshot
from app.request_context import REQUEST_ID_HEADER
from app.utils.db_retry import DEFAULT_MAX_ATTEMPTS
from tests.conftest import log_lines
from tests.integration.test_snapshot_forced_adjustment import (
    D0,
    EX_DAY,
    _setup,
)

DEADLOCK_MSG = "Deadlock found when trying to get lock; try restarting transaction"
LOCK_TIMEOUT_MSG = "Lock wait timeout exceeded; try restarting transaction"
# 与 #618 现场一致的语句形状：牺牲者是 investor_holding 的 INSERT
DEADLOCK_STMT = (
    "INSERT INTO investor_holding (portfolio_code, investor_code, shares, snapshot_date) "
    "VALUES (%s, %s, %s, %s)"
)
GENERATE_URL = "/api/snapshots/generate"
LOGGER_NAME = "app.utils.db_retry"


def _db_error(errno: int, message: str) -> OperationalError:
    """SQLAlchemy 的现场包装形状：OperationalError.orig -> pymysql.err.OperationalError"""
    return OperationalError(
        DEADLOCK_STMT, {}, pymysql.err.OperationalError(errno, message)
    )


def _deadlock() -> OperationalError:
    return _db_error(1213, DEADLOCK_MSG)


def _generate(client, admin_headers, code: str, target: date = EX_DAY):
    return client.post(
        GENERATE_URL,
        json={"portfolio_code": code, "target_date": target.isoformat()},
        headers=admin_headers,
    )


def _patch_generate(monkeypatch, fail_times: int, errno: int = 1213, message: str = DEADLOCK_MSG):
    """把端点用的 generate_daily_snapshots 换成「前 N 次抛错、之后走真实现」。

    必须打 `app.routers.snapshots.generate_daily_snapshots`：router 是 from-import
    进来的，改 `app.services.snapshot_service` 上的属性对 router 的引用无效。
    """
    import app.routers.snapshots as snapshots_router

    real = snapshots_router.generate_daily_snapshots
    calls: list[dict] = []

    def flaky(**kwargs):
        calls.append(kwargs)
        if len(calls) <= fail_times:
            raise _db_error(errno, message)
        return real(**kwargs)

    monkeypatch.setattr(snapshots_router, "generate_daily_snapshots", flaky)
    return calls


def _warning_lines(json_log_capture) -> list[dict]:
    return [
        line for line in log_lines(json_log_capture)
        if line.get("logger") == LOGGER_NAME and line.get("level") == "WARNING"
    ]


def _snapshot_rows(test_db, code: str, target: date = EX_DAY) -> dict:
    return {
        "portfolio_position": test_db.query(func.count(PortfolioPosition.id)).filter(
            PortfolioPosition.portfolio_code == code,
            PortfolioPosition.snapshot_date == target,
        ).scalar(),
        "portfolio_value_snapshot": test_db.query(func.count(PortfolioValueSnapshot.id)).filter(
            PortfolioValueSnapshot.portfolio_code == code,
            PortfolioValueSnapshot.snapshot_date == target,
        ).scalar(),
        "investor_holding": test_db.query(func.count(InvestorHolding.id)).filter(
            InvestorHolding.portfolio_code == code,
            InvestorHolding.snapshot_date == target,
        ).scalar(),
    }


class TestGenerateAbsorbsDeadlock:
    """验收 1：1213 被有界重试吸收，响应 200 且快照真落库"""

    def test_flush_stage_deadlock_is_absorbed(
        self, client, admin_headers, test_db, monkeypatch, json_log_capture
    ):
        code = "DL618A"
        _setup(test_db, code)
        calls = _patch_generate(monkeypatch, fail_times=1)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["portfolio_code"] == code
        assert body["snapshot_date"] == EX_DAY.isoformat()
        assert len(calls) == 2, "首次 1213 后应重放一次"
        assert [c["portfolio_code"] for c in calls] == [code, code]
        assert [c["target_date"] for c in calls] == [EX_DAY, EX_DAY]

        rows = _snapshot_rows(test_db, code)
        assert rows["portfolio_value_snapshot"] == 1
        assert rows["investor_holding"] == 1
        assert rows["portfolio_position"] >= 1, f"基金行与现金行都应落库：{rows}"

        # 重放留痕：一条 WARNING，带 operation 与尝试次数（便于按端点聚合）
        warnings = _warning_lines(json_log_capture)
        assert len(warnings) == 1
        assert warnings[0]["operation"] == "generate_snapshot"
        assert warnings[0]["attempt"] == 1
        assert warnings[0]["max_attempts"] == DEFAULT_MAX_ATTEMPTS
        # 与访问日志同一 request_id：排障时能把「重放过」和「哪个请求」串起来
        assert warnings[0]["request_id"] == resp.headers[REQUEST_ID_HEADER]

    def test_commit_stage_deadlock_is_also_absorbed(
        self, client, admin_headers, test_db, monkeypatch, json_log_capture
    ):
        """死锁也可能在 commit 处才暴露，故 commit 必须落在重试范围内。

        做法是把会话的 commit 换成「首次抛 1213、之后走真实现」；service 本身不打桩，
        所以这条同时证明重放能真跑完整个生成流程。
        """
        code = "DL618B"
        _setup(test_db, code)
        real_commit = test_db.commit
        commits = {"n": 0}

        def flaky_commit():
            commits["n"] += 1
            if commits["n"] == 1:
                raise _deadlock()
            return real_commit()

        monkeypatch.setattr(test_db, "commit", flaky_commit)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        assert commits["n"] == 2
        assert len(_warning_lines(json_log_capture)) == 1
        assert _snapshot_rows(test_db, code)["portfolio_value_snapshot"] == 1

    def test_second_retry_also_absorbed(
        self, client, admin_headers, test_db, monkeypatch, json_log_capture
    ):
        """连续两次 1213 仍在预算内（默认 3 次尝试）"""
        code = "DL618C"
        _setup(test_db, code)
        calls = _patch_generate(monkeypatch, fail_times=2)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 200, resp.text
        assert len(calls) == 3
        warnings = _warning_lines(json_log_capture)
        assert [w["attempt"] for w in warnings] == [1, 2]
        assert _snapshot_rows(test_db, code)["portfolio_value_snapshot"] == 1


class TestBudgetExhaustedKeepsContract:
    """验收 3：预算耗尽后响应契约与留痕口径与 #618 之前逐字一致"""

    def test_exhausted_returns_500_with_original_cause(
        self, client, admin_headers, test_db, monkeypatch, json_log_capture
    ):
        from unittest.mock import MagicMock

        code = "DL618D"
        _setup(test_db, code)
        calls = _patch_generate(monkeypatch, fail_times=99)
        spy = MagicMock()
        monkeypatch.setattr("app.error_reporting.record_system_error", spy)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 500, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "SNAPSHOT_GENERATION_FAILED"
        assert DEADLOCK_MSG in detail["message"], "响应仍带原始消息，便于前端/排障定位"
        assert len(calls) == DEFAULT_MAX_ATTEMPTS, "尝试次数必须有界"

        # report_unexpected 拿到的是**原始** OperationalError，不是 HTTPException
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["error_type"] == "OperationalError"
        assert DEADLOCK_MSG in kwargs["error_message"]
        assert "OperationalError" in kwargs["error_stack"]
        assert kwargs["request_path"] == GENERATE_URL
        assert kwargs["request_method"] == "POST"

        # stdout：2 条重放 WARNING + 1 条兜底 ERROR（同一 request_id）
        request_id = resp.headers[REQUEST_ID_HEADER]
        warnings = _warning_lines(json_log_capture)
        assert [w["attempt"] for w in warnings] == [1, 2]
        errors = [
            line for line in log_lines(json_log_capture)
            if line.get("level") == "ERROR" and line.get("operation") == "generate_snapshot"
        ]
        assert len(errors) == 1
        assert errors[0]["request_id"] == request_id
        assert errors[0]["logger"] == "app.error_reporting"

        # 整体回滚：不留半截快照（基线 D0 的行仍在）
        assert _snapshot_rows(test_db, code, EX_DAY) == {
            "portfolio_position": 0,
            "portfolio_value_snapshot": 0,
            "investor_holding": 0,
        }
        assert _snapshot_rows(test_db, code, D0)["portfolio_value_snapshot"] == 1


class TestNonDeadlockErrorsAreNotRetried:
    """验收 4/5：不该重试的一次都不重试，happy path 零额外开销"""

    def test_lock_wait_timeout_is_not_retried_despite_its_message(
        self, client, admin_headers, test_db, monkeypatch, json_log_capture
    ):
        """1205 的消息同样写着 "try restarting transaction"，但那是等锁超时：
        重试只会把同样的等待再来一遍并加倍负载。只认 errno 才不会被这句话骗到。"""
        code = "DL618E"
        _setup(test_db, code)
        calls = _patch_generate(monkeypatch, fail_times=99, errno=1205, message=LOCK_TIMEOUT_MSG)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 500, resp.text
        assert resp.json()["detail"]["error"] == "SNAPSHOT_GENERATION_FAILED"
        assert LOCK_TIMEOUT_MSG in resp.json()["detail"]["message"]
        assert len(calls) == 1, "1205 不该重试"
        assert _warning_lines(json_log_capture) == []

    def test_duplicate_key_is_not_retried(
        self, client, admin_headers, test_db, monkeypatch
    ):
        code = "DL618F"
        _setup(test_db, code)
        calls = _patch_generate(
            monkeypatch, fail_times=99, errno=1062,
            message="Duplicate entry 'DL618F-VIEWER-2025-06-09' for key 'uix_holding_snapshot'",
        )

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 500, resp.text
        assert len(calls) == 1, "重复键是数据问题，重试只会再撞一次"

    def test_happy_path_has_no_retry_overhead(
        self, client, admin_headers, test_db, monkeypatch, json_log_capture
    ):
        code = "DL618G"
        _setup(test_db, code)
        calls = _patch_generate(monkeypatch, fail_times=0)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 200, resp.text
        assert len(calls) == 1, "成功路径不得重放"
        assert _warning_lines(json_log_capture) == [], "成功路径不该产生重试 WARNING"
        assert _snapshot_rows(test_db, code)["portfolio_value_snapshot"] == 1

    def test_domain_error_still_maps_to_422(
        self, client, admin_headers, test_db, monkeypatch
    ):
        """回归：领域异常/校验失败的分派不被重试层改变"""
        from app.services.exceptions import BusinessError

        code = "DL618H"
        _setup(test_db, code)
        import app.routers.snapshots as snapshots_router

        def boom(**kwargs):
            raise BusinessError(code="SNAPSHOT_NOT_CONTINUOUS", message="快照不连续")

        monkeypatch.setattr(snapshots_router, "generate_daily_snapshots", boom)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "SNAPSHOT_NOT_CONTINUOUS"

    def test_value_error_still_maps_to_422_validation_failed(
        self, client, admin_headers, test_db, monkeypatch
    ):
        code = "DL618I"
        _setup(test_db, code)
        import app.routers.snapshots as snapshots_router

        def boom(**kwargs):
            raise ValueError("依赖数据校验失败: 存在 pending 交易")

        monkeypatch.setattr(snapshots_router, "generate_daily_snapshots", boom)

        resp = _generate(client, admin_headers, code)

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "VALIDATION_FAILED"
