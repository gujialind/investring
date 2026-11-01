"""stdout 恒单 JSON 契约（issue #800）

`ir market sync-all --products` 遇到不含 `|` 的非法条目时，warning 直接
`print` 到 stdout，与随后的成功 JSON 混流：`json.loads(stdout)` / `jq`
解析失败，依赖机读面的脚本与 agent 会误判命令失败。诊断信息一律走
stderr（`_PROTOCOL_HELP`：脚本/Agent 解析请只读取 stdout）。

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests -q
"""
import json

import pytest
from typer.testing import CliRunner

from ir_cli.client import APIClient
from ir_cli.main import app


def _runner() -> CliRunner:
    try:
        # click <8.2 默认混流，需显式分离 stderr；click >=8.2 无此参数且默认分离
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class _FakeClient:
    """记录调用并按 APIClient 契约回 {"data": ...} 信封"""

    def __init__(self):
        self.calls = []

    def post(self, path, json_data=None, params=None, raise_errors=False):
        self.calls.append((path, json_data))
        return {"data": {"job_id": 7, "status": "queued"}}


def _run_sync_all(args, fake):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            APIClient,
            "from_config",
            classmethod(lambda cls, require_auth=True: fake),
        )
        return _runner().invoke(app, ["market", "sync-all", *args])


class TestSyncAllStdoutContract:
    def test_malformed_product_warning_goes_to_stderr_only(self):
        fake = _FakeClient()
        result = _run_sync_all(
            ["--products", "022959.OF,000001.SZ|CN_EXCHANGE"], fake
        )
        assert result.exit_code == 0
        # stdout 恰好是一份 JSON 文档，warning 不在里面
        doc = json.loads(result.stdout)
        assert doc["ok"] is True
        assert "warning" not in result.stdout
        # 诊断信息在 stderr，且点名条条目
        assert "warning" in result.stderr
        assert "022959.OF" in result.stderr
        # 非法条目被丢弃，合法条目照常提交
        assert fake.calls[0][1]["products"] == [["000001.SZ", "CN_EXCHANGE"]]

    def test_valid_products_keep_stdout_clean_and_stderr_empty(self):
        fake = _FakeClient()
        result = _run_sync_all(["--products", "000001.SZ|CN_EXCHANGE"], fake)
        assert result.exit_code == 0
        assert json.loads(result.stdout)["ok"] is True
        assert result.stderr.strip() == ""
