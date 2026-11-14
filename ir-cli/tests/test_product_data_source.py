"""#880 CLI 等价用例：省略 --data-source 时请求体不带 data_source 键（后端按市场推导）。

与后端 REST 省略用例（test_products_validation.TestCreateDataSourceDerivation）互为镜像：
CLI resolve_body 丢弃 None，故省略选项即不带键，推导发生在后端 service 单点。
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
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(
        APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
    )


class TestProductCreateDataSource:
    def _run(self, monkeypatch, cli, extra):
        seen = {}

        def handler(request):
            if request.method == "POST" and request.url.path == "/api/products":
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"code": "968050", "data_source": "akshare"})
            return httpx.Response(404, json={"detail": "not found"})

        _patch(monkeypatch, handler)
        args = [
            "product", "create", "--code", "968050", "--market", "HK_MUTUAL",
            "--name", "互认", "--product-type", "OEF",
        ] + extra
        with pytest.raises(SystemExit) as ei:
            cli.main(args, standalone_mode=False)
        assert ei.value.code == 0
        return seen["body"]

    def test_omitted_data_source_not_in_body(self, monkeypatch, cli):
        body = self._run(monkeypatch, cli, [])
        assert "data_source" not in body  # 省略 → 后端推导（HK→akshare）

    def test_explicit_data_source_in_body(self, monkeypatch, cli):
        body = self._run(monkeypatch, cli, ["--data-source", "akshare"])
        assert body["data_source"] == "akshare"