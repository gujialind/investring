"""构造期异常与全局 JSON 错误出口测试（issue #798，路线 A+C）

覆盖：
- A：trust_env=False —— no_proxy 含带方括号 IPv6（[::1]，curl 语义合法）等任意取值时
  APIClient 构造不抛、请求正常；不读取 HTTP_PROXY/HTTPS_PROXY 环境代理
- issue 验收第 5 条原文直译：monkeypatch urllib.request.getproxies 返回
  {"no": "localhost,[::1],::1"}，构造 APIClient("https://example.com") 不抛异常
- C1：构造期异常 → stdout 单个 JSON（CLIENT_INIT_ERROR + hints）+ exit 3，无 Traceback
- C2：命令体未预期异常 → INTERNAL_ERROR JSON + exit 1，无 Traceback
  （UsageError → exit 64 / Abort 回归见 test_usage_contract.py 与既有套件）

运行方式（仓库根）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/test_client_init.py -q
"""
import json
import urllib.request

import httpx
import pytest
import typer

from ir_cli.client import APIClient
from ir_cli.main import app


def _read_stdout_json(capsys):
    """读取 stdout 并断言为单个合法 JSON 文档且无 Traceback，返回 (doc, captured)"""
    captured = capsys.readouterr()
    assert captured.out.strip(), "stdout 应有结构化 JSON 输出"
    assert "Traceback" not in captured.out, "stdout 不得漏出 Traceback"
    assert "Traceback" not in captured.err, "stderr 不得漏出 Traceback"
    return json.loads(captured.out), captured


class TestTrustEnvDisabled:
    """A：不读环境代理，任意 no_proxy/proxy 取值不影响构造与请求"""

    def test_acceptance5_monkeypatched_getproxies_no_raise(self, monkeypatch):
        """issue 验收第 5 条原文直译用例"""
        monkeypatch.setattr(
            urllib.request, "getproxies", lambda: {"no": "localhost,[::1],::1"}
        )
        client = APIClient("https://example.com")  # 不抛即通过
        assert client._client is not None

    @pytest.mark.parametrize(
        "no_proxy_value",
        ["::1", "[::1]", "127.0.0.0/8", ".localhost", "::1,[::1],127.0.0.0/8,.localhost"],
    )
    def test_acceptance3_any_no_proxy_value_no_raise(self, monkeypatch, no_proxy_value):
        """验收第 3 条（A 语义收窄版）：任意 no_proxy 取值不影响构造与请求"""
        monkeypatch.setenv("NO_PROXY", no_proxy_value)
        monkeypatch.setenv("no_proxy", no_proxy_value)
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"no": no_proxy_value})
        client = APIClient("http://test")
        # 请求成功（MockTransport 替换底层 client，与既有 test_client_errors.py 同法）
        client._client = httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"id": 1})),
            trust_env=False,
        )
        assert client.get("/api/trades/1")["data"] == {"id": 1}

    def test_bracketed_ipv6_env_regression_798(self, monkeypatch):
        """#798 崩溃形态回归：NO_PROXY=[::1] 时构造不再抛 InvalidURL"""
        monkeypatch.setenv("NO_PROXY", "[::1]")
        monkeypatch.setenv("no_proxy", "[::1]")
        APIClient("http://test")  # 修复前此处抛 httpx.InvalidURL

    def test_trust_env_flag_is_false(self):
        """验收第 4 条（A 口径）：trust_env 显式关闭"""
        client = APIClient("http://test")
        assert client._client.trust_env is False

    def test_env_proxy_not_mounted(self, monkeypatch):
        """验收第 4 条（A 口径）：显式设置 HTTP(S)_PROXY 也不生效（不产生代理 mounts）"""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
        monkeypatch.setenv("https_proxy", "http://proxy.example:8080")
        monkeypatch.setattr(
            urllib.request, "getproxies", lambda: {"https": "http://proxy.example:8080"}
        )
        client = APIClient("https://example.com")
        assert client._client._mounts == {}


class TestClientInitError:
    """C1：构造期异常 → CLIENT_INIT_ERROR JSON + exit 3"""

    def test_construct_failure_outputs_json_exit_3(self, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise httpx.InvalidURL("Invalid port: ':1]'")

        monkeypatch.setattr(httpx, "Client", boom)
        with pytest.raises(SystemExit) as ei:
            APIClient("http://test")
        assert ei.value.code == 3
        doc, _ = _read_stdout_json(capsys)
        assert doc["ok"] is False
        assert doc["error"]["code"] == "CLIENT_INIT_ERROR"
        assert "InvalidURL" in doc["error"]["message"]
        assert doc["error"]["details"]["base_url"] == "http://test"
        assert doc["error"]["hints"], "应附带补救指引 hints"


class TestInternalError:
    """C2：命令体未预期异常 → INTERNAL_ERROR JSON + exit 1，不漏 Traceback"""

    def test_unexpected_exception_outputs_json_exit_1(self, monkeypatch, capsys):
        cli = typer.main.get_command(app)

        def boom(cls, require_auth=True):
            raise RuntimeError("unexpected failure")

        monkeypatch.setattr(APIClient, "from_config", classmethod(boom))
        with pytest.raises(SystemExit) as ei:
            cli.main(["portfolio", "get", "P1"], standalone_mode=False)
        assert ei.value.code == 1
        doc, _ = _read_stdout_json(capsys)
        assert doc["ok"] is False
        assert doc["error"]["code"] == "INTERNAL_ERROR"
        assert "RuntimeError" in doc["error"]["message"]
        assert doc["error"]["hints"]
