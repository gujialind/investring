"""`ir market sync-all` 非法条目 warning 的 stdout 单 JSON 契约回归（issue #800）

覆盖：
- T1 全非法条目：exit 0，stdout 为单个合法 JSON（issue 验收第 1 条），warning 在 stderr
- T2 混合条目：合法条目照常下发（POST /api/sync-jobs/price），stdout 仍单 JSON，stderr 含 warning
- T3 全合法：stderr 不含该 warning（正常路径不多打诊断）

钉契约面（stdout 单 JSON + warning 在 stderr），不钉 warning 完整措辞。

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


def test_all_invalid_entries_stdout_is_single_json(monkeypatch):
    """T1：issue 验收第 1 条——BAD_ENTRY 时 stdout 恒单 JSON，warning 走 stderr（#800）"""
    _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--products", "BAD_ENTRY"])
    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert doc["ok"] is True
    assert WARNING_MARK in result.stderr


def test_mixed_entries_valid_still_submitted(monkeypatch):
    """T2：混合输入——合法条目照常下发，跳过语义与请求体不受输出路由影响"""
    client = _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--products", "BAD,022959.OF|CN_OTC"])
    assert result.exit_code == 0
    json.loads(result.stdout)
    assert WARNING_MARK in result.stderr
    post = next(call for call in client.calls if call["path"] == "/api/sync-jobs/price")
    assert post["json"]["products"] == [["022959.OF", "CN_OTC"]]


def test_all_valid_entries_no_warning(monkeypatch):
    """T3：全合法路径无多余诊断（回归：正常调用不引入 stderr 噪音）"""
    _patch_client(monkeypatch)
    result = _invoke(["market", "sync-all", "--products", "022959.OF|CN_OTC"])
    assert result.exit_code == 0
    json.loads(result.stdout)
    assert WARNING_MARK not in result.stderr
