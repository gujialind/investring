# ============================================================================
# 单元测试：集中日志配置 (test_logging_config.py) — issue #404
# ============================================================================
# 覆盖 stdout JSON 行形态、级别推导、setup_logging() 幂等，以及两条约定：
# 不钉 sqlalchemy.engine 级别（SQL 详略归 database.py 的 echo）、压掉 uvicorn.access。
# ============================================================================

import io
import json
import logging
from contextlib import redirect_stdout

import pytest

from app.config import get_settings
from app.context import RequestContext, request_context_var
from app.logging_config import JsonFormatter, resolve_log_level, setup_logging
from tests.conftest import log_lines


@pytest.fixture
def json_logger():
    """挂了 JsonFormatter 的独立 logger（不冒泡到 root，避免与别的测试互相干扰）。"""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.logging_config")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        yield logger, buffer
    finally:
        logger.handlers = []
        logger.propagate = True


def _payload(buffer: io.StringIO) -> dict:
    lines = log_lines(buffer)
    assert len(lines) == 1, f"期望恰好 1 行，实际 {len(lines)}：{lines}"
    return lines[0]


class TestJsonFormatter:
    """stdout 每行是单个可解析 JSON 对象，含 timestamp/level/logger/message"""

    def test_required_fields(self, json_logger):
        logger, buffer = json_logger
        logger.info("组合快照已生成")

        payload = _payload(buffer)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logging_config"
        assert payload["message"] == "组合快照已生成"
        assert payload["timestamp"].endswith("+00:00")

    def test_chinese_not_escaped(self, json_logger):
        logger, buffer = json_logger
        logger.info("组合快照已生成")

        assert "组合快照已生成" in buffer.getvalue()

    def test_exception_folded_into_own_field(self, json_logger):
        """堆栈进 exception 字段，不把 message 撑成多行（否则 JSON 行会被换行撕开）"""
        logger, buffer = json_logger
        try:
            raise ValueError("净值缺失")
        except ValueError:
            logger.error("快照生成失败", exc_info=True)

        payload = _payload(buffer)
        assert payload["message"] == "快照生成失败"
        assert "ValueError: 净值缺失" in payload["exception"]
        assert len(buffer.getvalue().splitlines()) == 1

    def test_extra_fields_pass_through(self, json_logger):
        logger, buffer = json_logger
        logger.warning(
            "业务拒绝", extra={"code": "MISSING_NAV", "path": "/api/snapshots/generate"}
        )

        payload = _payload(buffer)
        assert payload["code"] == "MISSING_NAV"
        assert payload["path"] == "/api/snapshots/generate"

    def test_request_context_attached(self, json_logger):
        logger, buffer = json_logger
        token = request_context_var.set(
            RequestContext(request_id="rid-1", actor="ADMIN", client_ip="10.0.0.1")
        )
        try:
            logger.info("请求内日志")
        finally:
            request_context_var.reset(token)

        payload = _payload(buffer)
        assert payload["request_id"] == "rid-1"
        assert payload["actor"] == "ADMIN"

    def test_extra_request_id_wins_over_context(self, json_logger):
        """未预期异常 handler 跑在上下文解绑之后，只能靠 extra= 显式带回 request_id"""
        logger, buffer = json_logger
        token = request_context_var.set(RequestContext(request_id="from-context"))
        try:
            logger.error("未预期异常", extra={"request_id": "from-scope"})
        finally:
            request_context_var.reset(token)

        assert _payload(buffer)["request_id"] == "from-scope"

    def test_no_context_no_fields(self, json_logger):
        """后台线程没有请求可归属，不该凭空出现空的 request_id/actor 字段"""
        logger, buffer = json_logger
        logger.info("后台任务日志")

        payload = _payload(buffer)
        assert "request_id" not in payload
        assert "actor" not in payload


class TestResolveLogLevel:
    """LOG_LEVEL 优先，否则由 debug 推导"""

    def test_derived_from_debug_flag(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "log_level", "")
        monkeypatch.setattr(settings, "debug", True)
        assert resolve_log_level() == "DEBUG"

        monkeypatch.setattr(settings, "debug", False)
        assert resolve_log_level() == "INFO"

    def test_explicit_log_level_wins(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "debug", True)
        monkeypatch.setattr(settings, "log_level", "warning")
        assert resolve_log_level() == "WARNING"

    def test_unknown_log_level_ignored(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "debug", False)
        monkeypatch.setattr(settings, "log_level", "verbose")
        assert resolve_log_level() == "INFO"


class TestSetupLogging:
    def test_repeated_calls_do_not_duplicate_lines(self, json_log_capture):
        """uvicorn / pytest / run_e2e_backend.py 是多入口，重复配置不得让一行变两行"""
        root = logging.getLogger()
        root.handlers.clear()  # 模拟进程内第一个入口

        with redirect_stdout(json_log_capture):
            setup_logging()
            setup_logging()

        configured = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
        assert len(configured) == 1

        logging.getLogger("app.test").info("一行")
        assert len(log_lines(json_log_capture)) == 1

    def test_does_not_pin_sqlalchemy_engine(self, json_log_capture):
        """SQL 详略由 database.py 的 echo=settings.debug 控制，这里再钉级别会与之争用"""
        assert logging.getLogger().handlers, "前置：dictConfig 应已配置 root"

        engine_logger = logging.getLogger("sqlalchemy.engine")
        assert engine_logger.level == logging.NOTSET
        assert engine_logger.handlers == []

    def test_uvicorn_access_suppressed(self, json_log_capture):
        """访问日志由 RequestContextMiddleware 单点产出，uvicorn 那份必须关掉"""
        access_logger = logging.getLogger("uvicorn.access")
        assert access_logger.propagate is False

        access_logger.info('127.0.0.1:1234 - "GET /api/portfolios HTTP/1.1" 200')
        assert log_lines(json_log_capture) == []

    def test_uvicorn_own_logs_still_json(self, json_log_capture):
        """uvicorn 启动/ASGI 异常日志改走 root，与应用日志同形态"""
        error_logger = logging.getLogger("uvicorn.error")
        assert error_logger.propagate is True

        error_logger.info(
            "Started server process [16027]",
            extra={"color_message": "Started server process [\x1b[36m%d\x1b[0m]"},
        )
        payload = _payload(json_log_capture)
        assert payload["logger"] == "uvicorn.error"
        assert payload["message"] == "Started server process [16027]"
        # ANSI 转义 + 未渲染的 %d 占位对消费方是噪声，渲染后的文本已在 message
        assert "color_message" not in payload
