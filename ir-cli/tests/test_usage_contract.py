"""
CLI 退出码与 --quiet 产物契约（issue #520）

覆盖：
- 未知选项 / 未知子命令 / 缺必填参数 → stdout 一份 `USAGE_ERROR` JSON + exit 64。
  Click 缺省是「stderr 一行人读文本 + exit 2」，而协议里 2 表示认证错误，
  脚本与 agent 会去跑 `ir auth login`，拼错的参数永远修不好。
- 64 不与 0/1/2/3 既有语义重叠，且取 sysexits.h 的 EX_USAGE
- `--help` 与「无参数打帮助」两条路径仍归 Click 处理，不被误判成用法错误
- trade/sub 的 cancel/unconfirm 的 `--quiet` 输出 `{message}`（后端本就只回这个），
  不再投影成三个 null；create/confirm 的三字段承诺保持不变

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests -q
"""
import json
import os

import pytest
from typer.testing import CliRunner

from ir_cli.client import APIClient
from ir_cli.main import app
from ir_cli.output import EXIT_USAGE


def _runner() -> CliRunner:
    try:
        # click <8.2 默认混流，需显式分离 stderr；click >=8.2 无此参数且默认分离
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _run(args, handler=None):
    """执行命令，返回 (退出码, stdout 文本, stderr 文本)"""
    if handler is None:
        result = _runner().invoke(app, args)
    else:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                APIClient,
                "from_config",
                classmethod(lambda cls, require_auth=True: _FakeClient(handler)),
            )
            result = _runner().invoke(app, args)
    assert not (
        result.exception and not isinstance(result.exception, SystemExit)
    ), f"命令抛出未处理异常: {result.exception!r}"
    return result.exit_code, result.stdout, result.stderr


class _FakeClient:
    """按 APIClient 契约返回 {"data": ...} 信封，供 --quiet 产物断言用"""

    def __init__(self, handler):
        self._handler = handler

    def post(self, path, json_data=None, params=None, raise_errors=False):
        return {"data": self._handler(path)}

    def get(self, path, params=None):
        return {"data": self._handler(path)}


class TestUsageErrorContract:
    def test_unknown_option_is_machine_readable_json(self):
        # #493 把 --cash-confirm-date 从 create 迁到 confirm 后最常踩的一条
        code, out, _ = _run([
            "trade", "create", "--portfolio-code", "P1",
            "--cash-confirm-date", "2026-08-03",
        ])
        assert code == 64
        doc = json.loads(out)
        assert doc["ok"] is False
        assert doc["error"]["code"] == "USAGE_ERROR"
        assert "--cash-confirm-date" in doc["error"]["message"]
        # hints 指向查参数名的正确入口，而不是重跑 auth login
        assert "ir schema --index" in doc["error"]["hints"][0]
        assert "auth login" not in doc["error"]["hints"][0]

    def test_unknown_subcommand_and_missing_required_option(self):
        for args in (["trade", "nope"], ["snapshot", "generate"]):
            code, out, _ = _run(args)
            assert code == 64, args
            doc = json.loads(out)
            assert doc["error"]["code"] == "USAGE_ERROR", args
            assert doc["error"]["message"], args

    def test_usage_errors_do_not_emit_json_on_stderr(self):
        # 协议：stdout 才是机读面。旧行为是把 Error: 文本写 stderr、stdout 全空
        _, out, err = _run(["investor", "--bogus"])
        assert json.loads(out)["error"]["code"] == "USAGE_ERROR"
        assert "No such option" not in err

    def test_exit_code_is_sysexits_usage_and_not_reused(self):
        assert EXIT_USAGE == 64
        assert EXIT_USAGE == os.EX_USAGE  # sysexits.h EX_USAGE，不与既有档位重叠
        assert set(range(0, 4)).isdisjoint({EXIT_USAGE})


class TestHelpPathsUnaffected:
    def test_top_level_help_still_plain_text_on_stdout(self):
        code, out, _ = _run(["--help"])
        assert code == 0
        assert out.startswith("Usage:")
        assert "退出码" in out  # 协议速览随 --help 一起给出
        assert "64" in out

    def test_subcommand_help_lists_options(self):
        code, out, _ = _run(["trade", "cancel", "--help"])
        assert code == 0
        assert "--quiet" in out

    def test_no_args_prints_help_not_usage_error(self):
        # 无参数走 Click 缺省（帮助写 stderr、exit 2），刻意不当用法错误：
        # 它是「没给命令」而不是「参数名错了」，且帮助正文不是 JSON 字段
        code, out, err = _run([])
        assert code == 2
        assert out == ""
        assert "Usage: " in err


class TestQuietArtifactContract:
    @pytest.mark.parametrize(
        "group,sub",
        [
            ("trade", "cancel"),
            ("trade", "unconfirm"),
            ("sub", "cancel"),
            ("sub", "unconfirm"),
        ],
    )
    def test_cancel_unconfirm_quiet_emits_message(self, group, sub):
        _, out, _ = _run(
            [group, sub, "7", "--quiet"],
            handler=lambda path: {"message": "Subscription cancelled successfully"},
        )
        doc = json.loads(out)
        # 逐字等于文档承诺：只有 message，没有 null 占位的 id/status/confirm_date
        assert set(doc["data"].keys()) == {"message"}
        assert doc["data"]["message"]

    def test_create_quiet_still_projects_three_fields(self):
        _, out, _ = _run(
            ["trade", "create", "--portfolio-code", "P1", "--product-code", "F001",
             "--market", "CN_OTC", "--type", "buy", "--trade-date", "2026-07-28",
             "--actual-amount", "1000", "--platform-code", "PF1", "--quiet"],
            handler=lambda path: {
                "id": 9, "status": "pending", "confirm_date": "2026-07-29",
                "notes": "不该出现在 --quiet 输出里",
            },
        )
        doc = json.loads(out)
        assert doc["data"] == {"id": 9, "status": "pending", "confirm_date": "2026-07-29"}

    def test_cancel_without_quiet_returns_backend_body_unchanged(self):
        _, out, _ = _run(
            ["trade", "cancel", "7"],
            handler=lambda path: {"message": "Trade cancelled successfully"},
        )
        assert json.loads(out)["data"] == {"message": "Trade cancelled successfully"}
