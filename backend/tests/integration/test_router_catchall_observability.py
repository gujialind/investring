# ============================================================================
# 集成测试：路由兜底翻 5xx 的可观测性 (test_router_catchall_observability.py)
# — issue #553
# ============================================================================
# 背景：`HTTPException` 由中间件链**内侧**的 handler 就地渲染成响应，冒不到挂着
# `Exception` handler 的最外层，故 router 内「`except Exception` → 抛 5xx」曾让
# #405 建的可观测性整套失效——stdout 无 ERROR、`system_error_log` 零行。
#
# 覆盖验收断言：
# 1. 快照侧 5 处兜底（generate / recalculate / generate-next / delete / bulk-delete）：
#    触发非预期异常 → stdout 出现**一条** ERROR，含 exception 堆栈、request_id、
#    method、path、operation；
# 2. `record_system_error` 收到的是**原始异常类名**与真实路径/方法（不是 HTTPException）；
#    system_error_log 真写库时确实落一行；
# 3. 响应契约不变：`{error, message}` 与四个错误码逐字保持（#553 的硬约束）；
# 4. 反向：领域异常（BusinessError → 422）不新增 ERROR 噪音，仍走 #405 的 WARNING 口径。
# ============================================================================

from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func

from app.models.system_error_log import SystemErrorLog
from app.request_context import REQUEST_ID_HEADER
from app.services.exceptions import BusinessError
from tests.conftest import log_lines, only_log_line
from tests.factories import create_value_snapshot

BOOM_MESSAGE = "快照生成炸了"
SNAP_DATE = date(2026, 1, 5)


def _boom(exc: BaseException) -> MagicMock:
    return MagicMock(side_effect=exc)


@pytest.fixture
def purge_system_error_rows():
    """清掉本用例真落进 system_error_log 的行——跨用例污染防护。

    `record_system_error` 走**独立 session** 提交（见 audit_service），不受 test_db
    事务回滚保护，所以真落库用例结束後行是留着的；而 test_log_cleanup 那类用例按整表
    计数断言（「清理后还剩几行」），多一行就红。谁落谁清，别把脏数据留给下一个用例。
    """
    from app.database import SessionLocal

    probe = SessionLocal()
    watermark = probe.query(func.max(SystemErrorLog.id)).scalar() or 0
    probe.close()
    yield
    probe = SessionLocal()
    try:
        probe.query(SystemErrorLog).filter(SystemErrorLog.id > watermark).delete()
        probe.commit()
    finally:
        probe.close()


def _assert_outlet_logged(json_log_capture, *, operation: str, method: str, path: str, request_id: str):
    """断言 stdout 里那条兜底 ERROR：字段齐、有真堆栈、与访问日志同一 request_id。"""
    line = only_log_line(json_log_capture, level="ERROR", operation=operation)
    assert line["message"] == "路由兜底未预期异常"
    assert line["logger"] == "app.error_reporting"
    assert line["method"] == method
    assert line["path"] == path
    assert line["status_code"] == 500
    # 与访问日志同一条 request_id——排障时用它把「500」和「哪个请求」串起来
    assert line["request_id"] == request_id
    assert only_log_line(json_log_capture, message="HTTP 请求")["request_id"] == request_id
    # exc_info=原始异常：堆栈里必须有原始异常类型与信息，不能只剩 HTTPException 那句话
    assert "RuntimeError" in line["exception"]
    assert BOOM_MESSAGE in line["exception"]
    assert "Traceback (most recent call last)" in line["exception"]


def _assert_error_record(spy, *, path: str, method: str):
    """断言落 system_error_log 的参数：error_type 是原始异常类名（#553 的根因是它被换掉）"""
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["error_type"] == "RuntimeError"
    assert kwargs["error_message"] == BOOM_MESSAGE
    assert "RuntimeError" in kwargs["error_stack"]
    assert kwargs["request_path"] == path
    assert kwargs["request_method"] == method


# (用例名, 被打的 service 函数, HTTP 方法, 路径, 请求体, 期望错误码, operation)
# 注意 delete / bulk-delete 两处的 `_delete_existing_snapshots` 是**函数内 import**，
# 必须打模块属性（`app.services.snapshot_service._delete_existing_snapshots`）才生效
CASES = [
    (
        "generate",
        "app.routers.snapshots.generate_daily_snapshots",
        "POST",
        "/api/snapshots/generate",
        {"portfolio_code": "TEST_PORT", "target_date": SNAP_DATE.isoformat()},
        "SNAPSHOT_GENERATION_FAILED",
        "generate_snapshot",
    ),
    (
        "recalculate",
        "app.routers.snapshots.recalculate_snapshots",
        "POST",
        "/api/snapshots/recalculate",
        {
            "portfolio_code": "TEST_PORT",
            "start_date": SNAP_DATE.isoformat(),
            "end_date": SNAP_DATE.isoformat(),
        },
        "RECALCULATION_FAILED",
        "recalculate",
    ),
    (
        "generate-next",
        "app.routers.snapshots.generate_next_snapshot",
        "POST",
        "/api/snapshots/generate-next",
        {"portfolio_code": "TEST_PORT"},
        "SNAPSHOT_GENERATION_FAILED",
        "generate_next",
    ),
    (
        "delete",
        "app.services.snapshot_service._delete_existing_snapshots",
        "DELETE",
        f"/api/snapshots/TEST_PORT/{SNAP_DATE.isoformat()}",
        None,
        "DELETE_FAILED",
        "delete_snapshot",
    ),
]


class TestSnapshotCatchAllOutlets:
    """快照侧兜底：ERROR 日志 + system_error_log 两路都通"""

    @pytest.mark.parametrize(
        "case,target,method,url,payload,error_code,operation",
        CASES,
        ids=[case[0] for case in CASES],
    )
    def test_outlet_records_unexpected(
        self, client, admin_headers, monkeypatch, json_log_capture,
        case, target, method, url, payload, error_code, operation,
    ):
        spy = MagicMock()
        monkeypatch.setattr("app.error_reporting.record_system_error", spy)
        monkeypatch.setattr(target, _boom(RuntimeError(BOOM_MESSAGE)))

        resp = client.request(method, url, headers=admin_headers, json=payload) if payload else client.request(
            method, url, headers=admin_headers
        )

        # 响应契约不变：仍是 {error, message} 与既有错误码（#553 的硬约束）
        assert resp.status_code == 500, resp.text
        assert resp.json()["detail"]["error"] == error_code
        assert BOOM_MESSAGE in resp.json()["detail"]["message"]

        _assert_outlet_logged(
            json_log_capture,
            operation=operation,
            method=method,
            path=url,
            request_id=resp.headers[REQUEST_ID_HEADER],
        )
        _assert_error_record(spy, path=url, method=method)

    def test_bulk_delete_outlet_carries_snap_date(
        self, client, admin_headers, monkeypatch, json_log_capture, sample_portfolio, test_db,
    ):
        """批量删除的兜底在**逐日循环内**，除常规字段外还带 snap_date 便于定位到哪一天"""
        create_value_snapshot(test_db, sample_portfolio.code, SNAP_DATE, 10000.0, 10000.0, 1.0)
        spy = MagicMock()
        monkeypatch.setattr("app.error_reporting.record_system_error", spy)
        monkeypatch.setattr(
            "app.services.snapshot_service._delete_existing_snapshots",
            _boom(RuntimeError(BOOM_MESSAGE)),
        )

        url = f"/api/snapshots/{sample_portfolio.code}/bulk/{SNAP_DATE.isoformat()}"
        resp = client.delete(f"{url}?confirm=true", headers=admin_headers)

        assert resp.status_code == 500, resp.text
        assert resp.json()["detail"]["error"] == "BULK_DELETE_FAILED"
        line = only_log_line(json_log_capture, level="ERROR", operation="delete_snapshots_bulk")
        assert line["snap_date"] == SNAP_DATE.isoformat()
        _assert_error_record(spy, path=url, method="DELETE")

    def test_real_system_error_log_row(self, client, admin_headers, monkeypatch, purge_system_error_rows):
        """不替换落库函数：system_error_log 真新增一行（验收断言「至少快照侧」的那条）

        断言用**独立会话**读：`record_system_error` 走的也是独立 session（见
        audit_service），而 `test_db` 这条连接上的事务快照读不到另一条连接新提交的
        行（SQLite 测试库实测）——拿 test_db 去数永远数不到，不是落库失败。
        """
        from app.database import SessionLocal

        probe = SessionLocal()
        try:
            before = probe.query(SystemErrorLog).count()
            monkeypatch.setattr(
                "app.routers.snapshots.generate_daily_snapshots",
                _boom(RuntimeError(BOOM_MESSAGE)),
            )
            resp = client.post(
                "/api/snapshots/generate",
                headers=admin_headers,
                json={"portfolio_code": "TEST_PORT", "target_date": SNAP_DATE.isoformat()},
            )
            assert resp.status_code == 500

            rows = probe.query(SystemErrorLog).order_by(SystemErrorLog.id).all()
            assert len(rows) == before + 1, f"system_error_log 未新增行：{before} → {len(rows)}"
            row = rows[-1]
            assert row.error_type == "RuntimeError", "error_type 必须是原始异常类名"
            assert row.request_path == "/api/snapshots/generate"
            assert row.request_method == "POST"
        finally:
            probe.close()


# 其余三个 router 的同形态兜底（#553 那 10 处里的另 5 处）：逐个同样要求留痕。
# 注意 market_data 三个端点的 detail 是**纯字符串**（不是 {error, message}），
# 故这里只断言原文含异常信息，不改契约。
OTHER_CASES = [
    (
        "market_data_get",
        "app.routers.market_data.get_price_records",
        "GET",
        "/api/market-data/products/TEST_CODE/CN_OEF/price-data",
        None,
        "get_price_data",
    ),
    (
        "market_data_sync",
        "app.routers.market_data.sync_price_data",
        "POST",
        "/api/market-data/products/TEST_CODE/CN_OEF/sync-price-data",
        {"start_date": SNAP_DATE.isoformat(), "end_date": SNAP_DATE.isoformat()},
        "sync_price_data_endpoint",
    ),
    (
        "market_data_sync_history",
        "app.routers.market_data.sync_price_data",
        "POST",
        "/api/market-data/products/TEST_CODE/CN_OEF/sync-history",
        None,
        "sync_history",
    ),
    (
        "tasks_run",
        "app.services.task_runner.run_task",
        "POST",
        "/api/system/tasks/nav_sync/run",
        None,
        "run_task",
    ),
    (
        "trading_calendar_sync",
        "app.routers.trading_calendar.sync_service",
        "POST",
        "/api/trading-calendar/sync",
        {"year": 2026},
        "sync_trading_calendar",
    ),
]


class TestOtherRouterCatchAllOutlets:
    @pytest.mark.parametrize(
        "case,target,method,url,payload,operation",
        OTHER_CASES,
        ids=[case[0] for case in OTHER_CASES],
    )
    def test_outlet_records_unexpected(
        self, client, admin_headers, monkeypatch, json_log_capture, test_db,
        case, target, method, url, payload, operation,
    ):
        if case == "tasks_run":
            # run_task 端点先查任务记录、且要求 enabled，否则走不到 try 里的兜底
            from app.models.scheduled_task import ScheduledTask

            task = test_db.query(ScheduledTask).filter(ScheduledTask.code == "nav_sync").first()
            if task is None:
                test_db.add(ScheduledTask(code="nav_sync", name="净值同步", is_enabled=True, cron_expr="0 2 * * *"))
            else:
                task.is_enabled = True
            test_db.commit()

        spy = MagicMock()
        monkeypatch.setattr("app.error_reporting.record_system_error", spy)
        monkeypatch.setattr(target, _boom(RuntimeError(BOOM_MESSAGE)))

        resp = client.request(method, url, headers=admin_headers, **( {"json": payload} if payload is not None else {} ))

        assert resp.status_code == 500, resp.text
        assert BOOM_MESSAGE in str(resp.json()["detail"])

        _assert_outlet_logged(
            json_log_capture,
            operation=operation,
            method=method,
            path=url,
            request_id=resp.headers[REQUEST_ID_HEADER],
        )
        _assert_error_record(spy, path=url, method=method)

    def test_tushare_api_error_branch_also_leaves_trace(
        self, client, admin_headers, monkeypatch, json_log_capture,
    ):
        """`except TushareAPIError → 500` 是窄捕获（不计入那 10 处），同形态一并处理"""
        from app.services.trading_calendar_service import TushareAPIError

        spy = MagicMock()
        monkeypatch.setattr("app.error_reporting.record_system_error", spy)
        monkeypatch.setattr(
            "app.routers.trading_calendar.sync_service",
            MagicMock(side_effect=TushareAPIError("上游数据源炸了")),
        )

        resp = client.post("/api/trading-calendar/sync", headers=admin_headers, json={"year": 2026})

        assert resp.status_code == 500, resp.text
        assert resp.json()["detail"]["error"] == "SYNC_FAILED"
        line = only_log_line(json_log_capture, level="ERROR", operation="sync_trading_calendar")
        assert "TushareAPIError" in line["exception"]
        spy.assert_called_once()
        # 落库同样记原始异常类名：换成 HTTPException 就回到 #553 的原点
        assert spy.call_args.kwargs["error_type"] == "TushareAPIError"


class TestNoErrorNoiseOnExpectedBranches:
    """反向：业务拒绝仍走 WARNING 口径，不因 #553 刷出 ERROR 噪音"""

    def test_business_error_keeps_contract_and_stays_quiet(
        self, client, admin_headers, monkeypatch, json_log_capture,
    ):
        spy = MagicMock()
        monkeypatch.setattr("app.error_reporting.record_system_error", spy)
        monkeypatch.setattr(
            "app.routers.snapshots.generate_daily_snapshots",
            MagicMock(side_effect=BusinessError("SNAPSHOT_NOT_CONTINUOUS", "快照日不连续")),
        )

        resp = client.post(
            "/api/snapshots/generate",
            headers=admin_headers,
            json={"portfolio_code": "TEST_PORT", "target_date": SNAP_DATE.isoformat()},
        )

        # BusinessError 由全局 handler 映射，仍是 422 + 稳定错误码
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "SNAPSHOT_NOT_CONTINUOUS"
        spy.assert_not_called()
        # 唯一的非访问日志应是 handler 那行 WARNING，不是 ERROR
        errors = [line for line in log_lines(json_log_capture) if line.get("level") == "ERROR"]
        assert not errors, f"业务拒绝不该产生 ERROR 级日志：{errors}"
        assert only_log_line(json_log_capture, message="业务拒绝")["level"] == "WARNING"
