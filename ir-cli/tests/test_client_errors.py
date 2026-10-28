"""
网络异常结构化输出与链式 --confirm 错误恢复测试（issue #72/#73/#77）

覆盖：
- 任何 httpx 网络异常（含 RemoteProtocolError 等）stdout 均输出结构化 JSON、退出码 3
- 仅幂等 GET 按 IR_RETRY 重试，非幂等请求不重试
- raise_errors=True 时 HTTP >=400 抛 ApiError；默认 False 行为回归（直接退出）
- 链式 create --confirm 确认失败时错误 JSON 携带已创建记录 id

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/ -q
"""
import json

import httpx
import pytest
import typer

from ir_cli.client import APIClient, ApiError
from ir_cli.main import app


def _read_stdout_json(capsys):
    """读取 stdout 并断言为单个合法 JSON 文档，返回 (doc, captured)"""
    captured = capsys.readouterr()
    assert captured.out.strip(), "stdout 应有结构化 JSON 输出"
    return json.loads(captured.out), captured


def _client_raising(monkeypatch, exc: Exception) -> APIClient:
    """构造底层 httpx 请求恒抛指定异常的客户端"""
    client = APIClient("http://test")

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(client._client, "request", boom)
    return client


def _client_with_transport(handler) -> APIClient:
    """构造 MockTransport 客户端（handler 返回 httpx.Response 或抛异常）"""
    client = APIClient("http://test")
    client._client = httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    return client


class TestNetworkErrors:
    """网络异常分类：任何失败路径 stdout 都有结构化 JSON，退出码 3"""

    def test_remote_protocol_error_outputs_network_error_exit_3(self, monkeypatch, capsys):
        client = _client_raising(
            monkeypatch, httpx.RemoteProtocolError("Server disconnected without response")
        )
        with pytest.raises(SystemExit) as ei:
            client.post("/api/snapshots/recalculate", json_data={})
        assert ei.value.code == 3
        doc, _ = _read_stdout_json(capsys)
        assert doc["ok"] is False
        assert doc["error"]["code"] == "NETWORK_ERROR"
        assert "RemoteProtocolError" in doc["error"]["message"]

    def test_read_error_outputs_network_error_exit_3(self, monkeypatch, capsys):
        client = _client_raising(monkeypatch, httpx.ReadError("connection reset"))
        with pytest.raises(SystemExit) as ei:
            client.post("/api/trades")
        assert ei.value.code == 3
        doc, _ = _read_stdout_json(capsys)
        assert doc["error"]["code"] == "NETWORK_ERROR"

    def test_timeout_outputs_timeout_error_exit_3(self, monkeypatch, capsys):
        client = _client_raising(monkeypatch, httpx.ReadTimeout("timed out"))
        with pytest.raises(SystemExit) as ei:
            client.post("/api/snapshots/recalculate")
        assert ei.value.code == 3
        doc, _ = _read_stdout_json(capsys)
        assert doc["error"]["code"] == "TIMEOUT_ERROR"
        assert "IR_HTTP_TIMEOUT" in doc["error"]["message"]

    def test_connect_error_keeps_connection_error_exit_3(self, monkeypatch, capsys):
        client = _client_raising(monkeypatch, httpx.ConnectError("refused"))
        with pytest.raises(SystemExit) as ei:
            client.get("/api/trades")
        assert ei.value.code == 3
        doc, _ = _read_stdout_json(capsys)
        assert doc["error"]["code"] == "CONNECTION_ERROR"
        # S3（#799 评审）：文案须提示「不读代理环境变量」，避免依赖代理的环境误查 IR_BASE_URL
        assert "代理" in doc["error"]["message"]


class TestRetrySemantics:
    """重试语义保留：仅幂等 GET 重试，非幂等请求任何网络异常不重试"""

    def test_get_retries_new_exception_types_then_succeeds(self, monkeypatch, capsys):
        monkeypatch.setenv("IR_RETRY", "2")
        monkeypatch.setattr("ir_cli.client.time.sleep", lambda s: None)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.RemoteProtocolError("flaky")
            return httpx.Response(200, json={"id": 1})

        client = _client_with_transport(handler)
        result = client.get("/api/trades/1")
        assert result["data"] == {"id": 1}
        assert calls["n"] == 3

    def test_post_does_not_retry_on_network_error(self, monkeypatch, capsys):
        monkeypatch.setenv("IR_RETRY", "5")
        calls = {"n": 0}

        def boom(*args, **kwargs):
            calls["n"] += 1
            raise httpx.RemoteProtocolError("boom")

        client = APIClient("http://test")
        monkeypatch.setattr(client._client, "request", boom)
        with pytest.raises(SystemExit) as ei:
            client.post("/api/trades")
        assert ei.value.code == 3
        assert calls["n"] == 1


class TestRaiseErrors:
    """raise_errors 参数：默认 False 直接退出（回归），True 抛 ApiError"""

    @staticmethod
    def _client_422():
        def handler(request):
            return httpx.Response(
                422, json={"detail": {"error": "MISSING_NAV", "message": "净值缺失"}}
            )

        return _client_with_transport(handler)

    def test_default_false_exits_directly(self, capsys):
        client = self._client_422()
        with pytest.raises(SystemExit) as ei:
            client.post("/api/trades/1/confirm")
        assert ei.value.code == 1
        doc, _ = _read_stdout_json(capsys)
        assert doc["ok"] is False
        assert doc["error"]["code"] == "MISSING_NAV"

    def test_raise_errors_true_raises_api_error_without_output(self, capsys):
        client = self._client_422()
        with pytest.raises(ApiError) as ei:
            client.post("/api/trades/1/confirm", raise_errors=True)
        assert ei.value.code == "MISSING_NAV"
        assert ei.value.message == "净值缺失"
        assert ei.value.details == {"http_status": 422}
        # 未向 stdout 输出任何错误 JSON（由调用方决定输出）
        assert capsys.readouterr().out == ""


class TestChainedConfirm:
    """链式 create --confirm：确认失败时错误 JSON 携带已创建记录 id（issue #72）"""

    @pytest.fixture()
    def cli(self):
        return typer.main.get_command(app)

    @staticmethod
    def _patch_from_config(monkeypatch, handler):
        client = _client_with_transport(handler)
        monkeypatch.setattr(
            APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
        )

    TRADE_CREATE_ARGS = [
        "trade", "create",
        "--portfolio-code", "P1", "--product-code", "F001",
        "--type", "buy", "--trade-date", "2026-07-28",
        "--actual-amount", "1000", "--platform-code", "PF1",
    ]

    def test_trade_confirm_failure_carries_created_trade_id(self, monkeypatch, capsys, cli):
        def handler(request):
            if request.method == "POST" and request.url.path == "/api/trades":
                return httpx.Response(200, json={"id": 42, "status": "pending"})
            if request.url.path == "/api/trades/42/confirm":
                return httpx.Response(
                    422, json={"detail": {"error": "MISSING_NAV", "message": "净值缺失"}}
                )
            return httpx.Response(404, json={"detail": "not found"})

        self._patch_from_config(monkeypatch, handler)
        with pytest.raises(SystemExit) as ei:
            cli.main(self.TRADE_CREATE_ARGS + ["--confirm"], standalone_mode=False)
        assert ei.value.code == 1
        doc, captured = _read_stdout_json(capsys)  # stdout 为单个合法 JSON 文档
        assert doc["ok"] is False
        assert doc["error"]["code"] == "MISSING_NAV"
        assert doc["error"]["details"]["created_trade_id"] == 42
        assert doc["error"]["details"]["http_status"] == 422
        assert any("ir trade confirm 42" in h for h in doc["error"]["hints"])
        # 进度信息只出现在 stderr
        assert "已创建" in captured.err

    def test_sub_confirm_failure_carries_created_subscription_id(self, monkeypatch, capsys, cli):
        def handler(request):
            if request.method == "POST" and request.url.path == "/api/subscriptions":
                return httpx.Response(200, json={"id": 7, "status": "pending"})
            if request.url.path == "/api/subscriptions/7/confirm":
                return httpx.Response(
                    422,
                    json={"detail": {"error": "NAV_NOT_AVAILABLE", "message": "净值快照缺失"}},
                )
            return httpx.Response(404, json={"detail": "not found"})

        self._patch_from_config(monkeypatch, handler)
        args = [
            "sub", "create",
            "--portfolio-code", "P1", "--investor-code", "I1",
            "--type", "subscribe", "--apply-date", "2026-07-28",
            "--amount", "1000", "--platform-code", "PF1", "--confirm",
        ]
        with pytest.raises(SystemExit) as ei:
            cli.main(args, standalone_mode=False)
        assert ei.value.code == 1
        doc, _ = _read_stdout_json(capsys)
        assert doc["error"]["code"] == "NAV_NOT_AVAILABLE"
        assert doc["error"]["details"]["created_subscription_id"] == 7
        assert any("ir sub confirm 7" in h for h in doc["error"]["hints"])


class TestAllowDuplicate:
    """--allow-duplicate 选项：显式传入时请求体携带 allow_duplicate=true"""

    @pytest.fixture()
    def cli(self):
        return typer.main.get_command(app)

    def _run_create(self, monkeypatch, cli, extra_args):
        seen = {}

        def handler(request):
            if request.method == "POST" and request.url.path == "/api/trades":
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"id": 1, "status": "pending"})
            return httpx.Response(404, json={"detail": "not found"})

        TestChainedConfirm._patch_from_config(monkeypatch, handler)
        with pytest.raises(SystemExit) as ei:
            cli.main(TestChainedConfirm.TRADE_CREATE_ARGS + extra_args, standalone_mode=False)
        assert ei.value.code == 0
        return seen["body"]

    def test_flag_adds_allow_duplicate_to_body(self, monkeypatch, capsys, cli):
        body = self._run_create(monkeypatch, cli, ["--allow-duplicate"])
        assert body["allow_duplicate"] is True

    def test_default_omits_allow_duplicate(self, monkeypatch, capsys, cli):
        body = self._run_create(monkeypatch, cli, [])
        assert "allow_duplicate" not in body
