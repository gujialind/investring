# ============================================================================
# 集成测试：请求上下文中间件与异常 handler (test_request_context.py) — issue #404
# ============================================================================
# 覆盖 X-Request-ID 贯穿（入站沿用 / 非法丢弃 / 探活跳过）、contextvar 跨
# threadpool 传到 service 层、业务拒绝与未预期异常两条日志路径。
# ============================================================================

import logging
from datetime import date

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.request_context import REQUEST_ID_HEADER
from app.services import portfolio_service
from tests.conftest import log_lines, only_log_line
from tests.factories import create_investor, create_portfolio, ensure_trading_day

NON_TRADING_DAY = date(2031, 3, 8)  # 周六


class TestRequestIdPropagation:
    def test_access_log_matches_response_header(self, client, admin_headers, json_log_capture):
        resp = client.get("/api/portfolios", headers=admin_headers)
        assert resp.status_code == 200

        access = only_log_line(json_log_capture, message="HTTP 请求")
        assert access["request_id"] == resp.headers[REQUEST_ID_HEADER]
        assert access["method"] == "GET"
        assert access["path"] == "/api/portfolios"
        assert access["status_code"] == 200
        assert access["duration_ms"] >= 0
        # 认证依赖解析出的投资人落到访问日志上（B #405 的审计 actor 走同一条通路）
        assert access["actor"] == "ADMIN"

    def test_request_id_and_actor_reach_service_layer(self, client, admin_headers, json_log_capture, monkeypatch):
        """同步依赖与同步 endpoint 各跑在自己的 threadpool context 拷贝里：
        service 层仍读得到 request_id / actor，靠的是中间件派发前绑定的可变上下文对象。
        """
        real_list = portfolio_service.list_portfolios

        def logged_list(*args, **kwargs):
            logging.getLogger(portfolio_service.__name__).info("service 层日志")
            return real_list(*args, **kwargs)

        monkeypatch.setattr(portfolio_service, "list_portfolios", logged_list)

        resp = client.get("/api/portfolios", headers=admin_headers)
        assert resp.status_code == 200

        request_id = resp.headers[REQUEST_ID_HEADER]
        service_line = only_log_line(json_log_capture, message="service 层日志")
        assert service_line["logger"] == "app.services.portfolio_service"
        assert service_line["request_id"] == request_id
        assert service_line["actor"] == "ADMIN"
        assert only_log_line(json_log_capture, message="HTTP 请求")["request_id"] == request_id

    def test_inbound_request_id_reused(self, client, admin_headers, json_log_capture):
        resp = client.get(
            "/api/portfolios",
            headers={**admin_headers, REQUEST_ID_HEADER: "upstream-trace-42"},
        )

        assert resp.headers[REQUEST_ID_HEADER] == "upstream-trace-42"
        assert only_log_line(json_log_capture, message="HTTP 请求")["request_id"] == "upstream-trace-42"

    def test_unsafe_inbound_request_id_replaced(self, client, admin_headers, json_log_capture):
        """入站值会原样回写响应头并进日志，非法字符必须丢弃（防响应头注入）"""
        inbound = "bad id with spaces"
        resp = client.get(
            "/api/portfolios", headers={**admin_headers, REQUEST_ID_HEADER: inbound}
        )

        generated = resp.headers[REQUEST_ID_HEADER]
        assert generated != inbound
        assert len(generated) == 32
        assert only_log_line(json_log_capture, message="HTTP 请求")["request_id"] == generated

    def test_health_probe_not_access_logged(self, client, json_log_capture):
        """探活每 N 秒一次，记进访问日志会淹没真实流量"""
        resp = client.get("/health")

        assert resp.status_code == 200
        assert REQUEST_ID_HEADER not in resp.headers
        # 不断言缓冲全空：TestClient 底层 httpx 自己会打一行 INFO，与中间件无关
        middleware_lines = [
            line for line in log_lines(json_log_capture) if line["logger"] == "app.request_context"
        ]
        assert middleware_lines == []


class TestExceptionHandlers:
    def test_business_rejection_logged_and_contract_unchanged(
        self, client, admin_headers, test_db, json_log_capture
    ):
        ensure_trading_day(test_db, NON_TRADING_DAY, is_open=False)
        create_portfolio(test_db, code="LOG404_PORT", status="active")
        create_investor(test_db, code="LOG404_INV")

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "LOG404_PORT",
                "investor_code": "LOG404_INV",
                "sub_type": "subscribe",
                "amount": 10000.0,
                "apply_date": NON_TRADING_DAY.isoformat(),
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )

        assert resp.status_code == 422
        assert resp.json()["detail"] == {
            "error": "NON_TRADING_DAY",
            "message": "非交易日，请等待交易日再提交",
        }

        warning = only_log_line(json_log_capture, message="业务拒绝")
        assert warning["level"] == "WARNING"
        assert warning["code"] == "NON_TRADING_DAY"
        assert warning["status_code"] == 422
        assert warning["actor"] == "ADMIN"
        assert warning["request_id"] == resp.headers[REQUEST_ID_HEADER]
        assert only_log_line(json_log_capture, message="HTTP 请求")["status_code"] == 422

    def test_unhandled_exception_logged_and_returns_500(self, monkeypatch, json_log_capture):
        def boom():
            raise RuntimeError("kaboom")

        monkeypatch.setitem(app.dependency_overrides, get_db, boom)
        # ServerErrorMiddleware 调完 handler 仍会重抛，关掉透传才拿得到响应体
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/portfolios")

        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error"}

        request_id = resp.headers[REQUEST_ID_HEADER]
        error = only_log_line(json_log_capture, message="未预期异常")
        assert error["level"] == "ERROR"
        assert "RuntimeError: kaboom" in error["exception"]
        assert error["request_id"] == request_id
        assert error["status_code"] == 500

        # 异常穿透到最外层的 ServerErrorMiddleware，本中间件没见过 response.start，按 500 记
        access = only_log_line(json_log_capture, message="HTTP 请求")
        assert access["request_id"] == request_id
        assert access["status_code"] == 500
