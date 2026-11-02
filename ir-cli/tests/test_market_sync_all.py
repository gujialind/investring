"""`ir market sync-all` 范围限定语义回归（issue #808；stdout 单 JSON 契约为 #800 存量）

覆盖：
- T1 全非法条目：USAGE_ERROR（exit 64），stdout 单 JSON，不发 POST（#808 修订：#800 时期
  钉的「exit 0 照常 POST」正是 #808 要修的退化形态）
- T2 混合条目：合法条目照常下发，scope 自动 by_product，stdout 仍单 JSON，stderr 含 warning
- T3 全合法：stderr 不含该 warning（正常路径不多打诊断）
- T4 --products 不带 --scope：body scope 自动 by_product（#808 验收 1）
- T5 显式 --scope by-product：原样下发，归一在后端 schema（backend test_sync_jobs_api 钉）
- T6 显式 --scope all 与 --products 冲突：USAGE_ERROR（exit 64），不发 POST（#808 Q2）

钉契约面（stdout 单 JSON + warning 在 stderr + scope/products 下发值），不钉 warning 完整措辞。

运行方式（仓库根）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/test_market_sync_all.py -q
"""
import json

from typer.testing import CliRunner

from ir_cli.client import APIClient
from ir_cli.main import app

WARNING_MARK = "跳过格式错误的产品条目"


def _runner() -> CliRunner:
    try:
        # click <8.2 默认混流，需显式分离 stderr；click >=8.2 无此参数且默认分离
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class _RecordingClient:
    """记录请求的假客户端，按 APIClient 契约返回 {"data": ...} 信封"""

    def __init__(self):
        self.calls: list = []

    def post(self, path, json_data=None, params=None, raise_errors=False):
        self.calls.append({"method": "POST", "path": path, "json": json_data})
        return {"data": {"message": f"POST {path}"}}


def _patch_client(monkeypatch) -> _RecordingClient:
    client = _RecordingClient()
    monkeypatch.setattr(
        APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
    )
    return client


def _invoke(args):
    result = _runner().invoke(app, args)
    assert not (
        result.exception and not isinstance(result.exception, SystemExit)
    ), f"命令抛出未处理异常: {result.exception!r}"
    return result


def _price_post(client) -> dict:
    return next(call for call in client.calls if call["path"] == "/api/sync-jobs/price")


def test_all_invalid_entries_usage_error_no_post(monkeypatch):
    """T1：#808 验收 3——全非法条目报错终止（JSON 错误出口 + 非 0），不发 POST；stdout 仍单 JSON"""
    client = _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--products", "BAD_ENTRY"])
    assert result.exit_code == 64
    doc = json.loads(result.stdout)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "USAGE_ERROR"
    assert WARNING_MARK in result.stderr
    assert not client.calls


def test_mixed_entries_valid_still_submitted(monkeypatch):
    """T2：混合输入——合法条目照常下发（自动 by_product），跳过语义与输出路由不变"""
    client = _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--products", "BAD,022959.OF|CN_OTC"])
    assert result.exit_code == 0
    json.loads(result.stdout)
    assert WARNING_MARK in result.stderr
    post = _price_post(client)
    assert post["json"]["products"] == [["022959.OF", "CN_OTC"]]
    assert post["json"]["scope"] == "by_product"  # #808：--products 缺省 scope 自动 by_product


def test_all_valid_entries_no_warning(monkeypatch):
    """T3：全合法路径无多余诊断（回归：正常调用不引入 stderr 噪音）"""
    _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--products", "022959.OF|CN_OTC"])
    assert result.exit_code == 0
    json.loads(result.stdout)
    assert WARNING_MARK not in result.stderr


def test_products_without_scope_auto_by_product(monkeypatch):
    """T4：#808 验收 1——--products 不带 --scope 时 body scope 自动 by_product"""
    client = _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--products", "022959.OF|CN_OTC"])
    assert result.exit_code == 0
    json.loads(result.stdout)
    post = _price_post(client)
    assert post["json"]["scope"] == "by_product"
    assert post["json"]["products"] == [["022959.OF", "CN_OTC"]]


def test_explicit_hyphen_scope_passes_through(monkeypatch):
    """T5：#808 验收 2——显式 --scope by-product 原样下发，归一在后端 schema 层"""
    client = _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--scope", "by-product",
                      "--products", "022959.OF|CN_OTC"])
    assert result.exit_code == 0
    json.loads(result.stdout)
    post = _price_post(client)
    assert post["json"]["scope"] == "by-product"
    assert post["json"]["products"] == [["022959.OF", "CN_OTC"]]


def test_scope_all_with_products_conflict(monkeypatch):
    """T6：#808 Q2——显式 --scope all 与 --products 冲突 → USAGE_ERROR，不发 POST"""
    client = _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--scope", "all",
                      "--products", "022959.OF|CN_OTC"])
    assert result.exit_code == 64
    doc = json.loads(result.stdout)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "USAGE_ERROR"
    assert not client.calls
