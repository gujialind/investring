"""#882 CLI 等价用例：batch-set-data-source 的 --yes 门控、#808 清单解析与请求体形状。

与后端 REST 预校验用例（test_products_batch_data_source.py）互为镜像：CLI 侧拦格式/确认，
上限/重复/存在性/市场兼容由后端整批拒绝（422）。轮询复用既有 ir sync-job status/details。
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


def _args(products, extra=None):
    return [
        "product", "batch-set-data-source",
        "--products", products, "--data-source", "akshare",
    ] + (extra or [])


class TestBatchSetDataSource:
    def test_submit_body_shape_and_hints(self, monkeypatch, cli, capsys):
        seen = {}

        def handler(request):
            if request.method == "POST" and request.url.path == "/api/products/batch-data-source":
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"job_id": 7, "status": "pending", "message": "任务已提交"})
            return httpx.Response(404, json={"detail": "not found"})

        _patch(monkeypatch, handler)
        with pytest.raises(SystemExit) as ei:
            cli.main(_args("000001.OF|CN_OTC, 510300.SH|CN_EXCHANGE", ["--yes"]), standalone_mode=False)
        assert ei.value.code == 0
        assert seen["body"] == {
            "products": [
                {"code": "000001.OF", "market": "CN_OTC"},
                {"code": "510300.SH", "market": "CN_EXCHANGE"},
            ],
            "data_source": "akshare",
        }
        out = json.loads(capsys.readouterr().out)
        assert out["data"]["job_id"] == 7
        assert any("sync-job" in h for h in out["hints"])

    def test_yes_required_without_backend_call(self, monkeypatch, cli, capsys):
        called = []

        def handler(request):
            called.append(request.url.path)
            return httpx.Response(200, json={})

        _patch(monkeypatch, handler)
        with pytest.raises(SystemExit) as ei:
            cli.main(_args("000001.OF|CN_OTC"), standalone_mode=False)
        assert ei.value.code == 1
        assert called == []  # 未确认不调用后端
        out = json.loads(capsys.readouterr().out)
        assert out["error"]["code"] == "CONFIRM_REQUIRED"

    def test_malformed_entry_usage_error(self, monkeypatch, cli, capsys):
        called = []

        def handler(request):
            called.append(request.url.path)
            return httpx.Response(200, json={})

        _patch(monkeypatch, handler)
        with pytest.raises(SystemExit) as ei:
            cli.main(_args("000001.OF|CN_OTC,badentry", ["--yes"]), standalone_mode=False)
        assert ei.value.code == 64
        assert called == []
        out = json.loads(capsys.readouterr().out)
        assert out["error"]["code"] == "USAGE_ERROR"

    def test_empty_segment_usage_error(self, monkeypatch, cli, capsys):
        _patch(monkeypatch, lambda request: httpx.Response(200, json={}))
        with pytest.raises(SystemExit) as ei:
            cli.main(_args("000001.OF|", ["--yes"]), standalone_mode=False)
        assert ei.value.code == 64
        out = json.loads(capsys.readouterr().out)
        assert out["error"]["code"] == "USAGE_ERROR"