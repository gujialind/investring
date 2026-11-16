"""#887 CLI dividend-config 命令组：list 参数/摘要投影、set 请求体与本地预校验。

remove 的 --yes 门槛由 test_destructive_confirm.py 的 DESTRUCTIVE 表覆盖。
"""
import json

import httpx
import pytest
import typer

from ir_cli.client import APIClient
from ir_cli.main import app


@pytest.fixture()
def cli():
    return typer.main.get_command(app)


def _patch(monkeypatch, handler):
    client = APIClient("http://test")
    client._client = httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(handler), trust_env=False,
    )
    monkeypatch.setattr(
        APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
    )


def _invoke(cli, args):
    with pytest.raises(SystemExit) as ei:
        cli.main(args, standalone_mode=False)
    return ei.value.code


class TestList:
    def test_key_params_and_summary_projection(self, monkeypatch, cli, capsys):
        seen = {}
        row = {
            "id": 1, "portfolio_code": "P1", "product_code": "000300.OF", "market": "CN_OTC",
            "platform_code": "MYCF", "dividend_method": "reinvest_dividend",
            "effective_from": "2026-11-16", "source": "default",
            "created_at": "2026-11-16T00:00:00", "updated_at": "2026-11-16T00:00:00",
        }

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"items": [row], "total": 1, "page": 1, "page_size": 20})

        _patch(monkeypatch, handler)
        code = _invoke(cli, [
            "dividend-config", "list", "--portfolio-code", "P1",
            "--product-code", "000300.OF", "--platform-code", "MYCF",
        ])
        assert code == 0
        assert seen["params"]["portfolio_code"] == "P1"
        assert seen["params"]["product_code"] == "000300.OF"
        assert seen["params"]["platform_code"] == "MYCF"
        assert seen["params"]["page"] == "1"
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["data"][0]["source"] == "default"
        assert "created_at" not in out["data"][0]  # 摘要投影裁掉非摘要字段

    def test_market_forwarded_when_given(self, monkeypatch, cli):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"items": [], "total": 0, "page": 1, "page_size": 20})

        _patch(monkeypatch, handler)
        _invoke(cli, [
            "dividend-config", "list", "--portfolio-code", "P1",
            "--product-code", "000300.OF", "--platform-code", "MYCF", "--market", "CN_OTC",
        ])
        assert seen["params"]["market"] == "CN_OTC"


class TestSet:
    def _run(self, monkeypatch, cli, extra):
        seen = {}

        def handler(request):
            if request.method == "POST" and request.url.path == "/api/dividend-configs":
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"id": 1, "source": "explicit"})
            return httpx.Response(404, json={"detail": "not found"})

        _patch(monkeypatch, handler)
        args = [
            "dividend-config", "set", "--portfolio-code", "P1",
            "--product-code", "000300.OF", "--platform-code", "MYCF",
            "--method", "reinvest_dividend", "--effective-from", "2026-11-16",
        ] + extra
        code = _invoke(cli, args)
        return code, seen.get("body")

    def test_body_fields_and_market_omitted(self, monkeypatch, cli):
        code, body = self._run(monkeypatch, cli, [])
        assert code == 0
        assert body["dividend_method"] == "reinvest_dividend"
        assert body["effective_from"] == "2026-11-16"
        assert "market" not in body  # 省略 → 后端按产品唯一市场补全

    def test_market_forwarded(self, monkeypatch, cli):
        code, body = self._run(monkeypatch, cli, ["--market", "CN_OTC"])
        assert code == 0
        assert body["market"] == "CN_OTC"

    def test_invalid_method_rejected_locally(self, monkeypatch, cli, capsys):
        code, body = self._run(monkeypatch, cli, ["--method", "dividend_pending"])
        # --method 出现两次，typer 取最后一个；本地枚举预校验拒绝
        assert code == 1
        assert body is None
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "VALIDATION_ERROR"

    def test_invalid_date_rejected_locally(self, monkeypatch, cli, capsys):
        seen = {}

        def handler(request):
            seen["called"] = True
            return httpx.Response(200, json={})

        _patch(monkeypatch, handler)
        code = _invoke(cli, [
            "dividend-config", "set", "--portfolio-code", "P1",
            "--product-code", "000300.OF", "--platform-code", "MYCF",
            "--method", "cash_dividend", "--effective-from", "2026-13-99",
        ])
        assert code == 1
        assert "called" not in seen
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "VALIDATION_ERROR"

    def test_missing_required_rejected_locally(self, monkeypatch, cli, capsys):
        seen = {}

        def handler(request):
            seen["called"] = True
            return httpx.Response(200, json={})

        _patch(monkeypatch, handler)
        code = _invoke(cli, [
            "dividend-config", "set", "--portfolio-code", "P1",
            "--product-code", "000300.OF", "--platform-code", "MYCF",
            "--method", "cash_dividend",
        ])
        assert code == 1
        assert "called" not in seen
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "VALIDATION_ERROR"