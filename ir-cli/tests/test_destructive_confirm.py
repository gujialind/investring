"""破坏性命令的 --yes 确认门槛（issue #629）

覆盖：
- 六命令不带 `--yes` → exit 1 + `CONFIRM_REQUIRED` + **零后端调用**（判空在 `from_config()` 之前）
- 带 `--yes` → 命中期望 method/path（`portfolio close` 是 POST 生命周期写，其余为 DELETE）
- `--help` 选项面出现 `--yes`（手册 ↔ 实现的双向比对由 test_doc_parity 守门）
- `delete-bulk` 回归：不带 `--yes` 时 hints 仍点名 `--dry-run`——#629 起静态表只给通用文案，
  该指引由命令调用点显式持有

运行方式（仓库根）：
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


class _RecordingClient:
    """记录请求的假客户端，按 APIClient 契约返回 {"data": ...} 信封"""

    def __init__(self):
        self.calls: list = []

    def _record(self, method, path, params=None, json_data=None):
        self.calls.append(
            {"method": method, "path": path, "params": params or {}, "json": json_data}
        )
        return {"data": {"message": f"{method} {path}"}}

    def get(self, path, params=None):
        return self._record("GET", path, params=params)

    def post(self, path, json_data=None, params=None, raise_errors=False):
        return self._record("POST", path, params=params, json_data=json_data)

    def put(self, path, json_data=None):
        return self._record("PUT", path, json_data=json_data)

    def delete(self, path, params=None):
        return self._record("DELETE", path, params=params)


def _patch_client(monkeypatch) -> _RecordingClient:
    client = _RecordingClient()
    monkeypatch.setattr(
        APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
    )
    return client


def _run(cli, args):
    result = _runner().invoke(cli, args)
    assert not (
        result.exception and not isinstance(result.exception, SystemExit)
    ), f"命令抛出未处理异常: {result.exception!r}"
    return result.exit_code, json.loads(result.stdout), result.stdout


# (命令 argv, 带 --yes 后期望命中的 method, path)
DESTRUCTIVE = [
    (["investor", "delete", "alice"], "DELETE", "/api/investors/alice"),
    (["portfolio", "close", "P1"], "POST", "/api/portfolios/P1/close"),
    (["share-event", "delete", "7"], "DELETE", "/api/share-change-events/7"),
    (["product", "delete", "022959.OF", "CN_OTC"], "DELETE", "/api/products/022959.OF/CN_OTC"),
    (["platform", "delete", "alipay"], "DELETE", "/api/platforms/alipay"),
    (["snapshot", "delete", "P1", "2026-01-05"], "DELETE", "/api/snapshots/P1/2026-01-05"),
]


class TestConfirmGateRejects:
    """不带 --yes：拒执行且一个请求都不发"""

    @pytest.mark.parametrize("args,method,path", DESTRUCTIVE)
    def test_without_yes_is_refused_before_any_request(self, monkeypatch, cli, args, method, path):
        client = _patch_client(monkeypatch)
        code, doc, _ = _run(cli, args)
        assert code == 1
        assert doc["ok"] is False
        assert doc["error"]["code"] == "CONFIRM_REQUIRED"
        assert "--yes" in doc["error"]["message"]
        assert client.calls == []

    @pytest.mark.parametrize("args,method,path", DESTRUCTIVE)
    def test_hint_is_generic_and_names_no_unsupported_flag(self, monkeypatch, cli, args, method, path):
        """#629：这六命令没有 --dry-run，通用 hint 不得承诺它（否则重演 #616 的虚假指引）"""
        _patch_client(monkeypatch)
        _, doc, _ = _run(cli, args)
        hint = doc["error"]["hints"][0]
        assert "--yes" in hint
        assert "--dry-run" not in hint


class TestConfirmGateAllows:
    """带 --yes：按原契约发出请求"""

    @pytest.mark.parametrize("args,method,path", DESTRUCTIVE)
    def test_with_yes_hits_expected_endpoint(self, monkeypatch, cli, args, method, path):
        client = _patch_client(monkeypatch)
        code, _, raw = _run(cli, args + ["--yes"])
        assert code == 0, raw
        assert client.calls == [{"method": method, "path": path, "params": {}, "json": None}]


class TestOptionFace:
    @pytest.mark.parametrize("args,method,path", DESTRUCTIVE)
    def test_help_lists_yes(self, cli, args, method, path):
        result = _runner().invoke(cli, args + ["--help"])
        assert result.exit_code == 0
        assert "--yes" in result.stdout.split("Options:", 1)[1]


class TestDeleteBulkRegression:
    """同口径先例不能被本次改动弄坏：delete-bulk 仍要能指到 --dry-run"""

    def test_delete_bulk_without_yes_keeps_dry_run_guidance(self, monkeypatch, cli):
        client = _patch_client(monkeypatch)
        code, doc, _ = _run(cli, ["snapshot", "delete-bulk", "P1", "2026-01-05"])
        assert code == 1
        assert doc["error"]["code"] == "CONFIRM_REQUIRED"
        assert any("--dry-run" in h for h in doc["error"]["hints"])
        assert client.calls == []


@pytest.fixture()
def cli():
    return app
