"""`ir task run` 异步语义（issue #714）

后端 `POST /{code}/run` 改为提交即返，CLI 侧对应三条承诺：
- 默认 `--wait`：先拿受理回执，再轮询 `GET /executions/{id}` 到终态才退出
- `--no-wait`：只返回回执，不发轮询请求（回执里的 execution_id 是唯一线索）
- 非 success 终态：退出码 1 + `TASK_EXECUTION_NOT_SUCCESS`，details 为该条执行记录

旧形态在请求内同步跑完任务，长任务被 nginx 60s 切成 504 而任务实际成功；本文件
钉住的就是「命令退出码必须反映任务真实结果」这条不被超时破坏。

运行方式（仓库根）：
    PYTHONPATH=ir-cli python3 -m pytest ir-cli/tests -q
"""
import json

import pytest
from typer.testing import CliRunner

from ir_cli.client import APIClient
from ir_cli.main import app

RUN_PATH = "/api/system/tasks/nav_sync/run"
EXEC_PATH = "/api/system/tasks/executions/77"
ACCEPTED = {"execution_id": 77, "task_code": "nav_sync", "status": "pending", "message": "任务 nav_sync 已提交"}
TERMINAL_OK = {"id": 77, "task_code": "nav_sync", "status": "success", "records_total": 12}
TERMINAL_FAILED = {"id": 77, "task_code": "nav_sync", "status": "failed", "error_message": "净值缺失"}
TERMINAL_INTERRUPTED = {"id": 77, "task_code": "nav_sync", "status": "interrupted"}


def _runner() -> CliRunner:
    try:
        # click <8.2 默认混流，需显式分离 stderr；click >=8.2 无此参数且默认分离
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class _ScriptedClient:
    """按脚本回放响应的假客户端：running 状态会被 repeat 若干次再进入终态"""

    def __init__(self, running_times: int, terminal: dict):
        self.calls: list = []
        self._running_times = running_times
        self._terminal = terminal

    def post(self, path, json_data=None, params=None, raise_errors=False):
        self.calls.append(("POST", path))
        return {"data": dict(ACCEPTED)}

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if path != EXEC_PATH:
            raise AssertionError(f"unexpected GET path: {path}")
        if self._running_times > 0:
            self._running_times -= 1
            return {"data": {"id": 77, "task_code": "nav_sync", "status": "running"}}
        return {"data": dict(self._terminal)}

    def put(self, path, json_data=None):  # pragma: no cover - 未被 task 命令使用
        raise AssertionError("task run must not PUT")

    def delete(self, path, params=None):  # pragma: no cover
        raise AssertionError("task run must not DELETE")


def _patch_client(monkeypatch, running_times: int, terminal: dict) -> _ScriptedClient:
    client = _ScriptedClient(running_times, terminal)
    monkeypatch.setattr(
        APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
    )
    # 轮询间隔不影响断言，抹掉 sleep 让用例瞬时完成
    monkeypatch.setattr("ir_cli.commands.tasks.time.sleep", lambda _s: None)
    return client


def _invoke(args):
    return _runner().invoke(app, args)


def _doc(result):
    return json.loads(result.stdout)


@pytest.fixture()
def cli():
    return app


class TestWaitIsDefault:
    def test_polls_until_terminal_and_exits_zero(self, monkeypatch, cli):
        client = _patch_client(monkeypatch, running_times=2, terminal=TERMINAL_OK)
        result = _invoke(["task", "run", "nav_sync"])
        assert result.exit_code == 0, result.stdout
        # 提交 1 次 + 轮询 3 次（2 次 running + 1 次拿到 success）
        assert client.calls == [
            ("POST", RUN_PATH),
            ("GET", EXEC_PATH),
            ("GET", EXEC_PATH),
            ("GET", EXEC_PATH),
        ]
        assert _doc(result)["data"] == TERMINAL_OK

    def test_no_polling_when_wait_disabled(self, monkeypatch, cli):
        client = _patch_client(monkeypatch, running_times=0, terminal=TERMINAL_OK)
        result = _invoke(["task", "run", "nav_sync", "--no-wait"])
        assert result.exit_code == 0, result.stdout
        assert client.calls == [("POST", RUN_PATH)]
        doc = _doc(result)
        assert doc["data"] == ACCEPTED
        # 回执不等于成功：必须把「去哪查终态」说清楚
        assert any("ir task execution 77" in h for h in doc["hints"])


class TestTerminalStateIsFailure:
    @pytest.mark.parametrize(
        "terminal",
        [TERMINAL_FAILED, TERMINAL_INTERRUPTED],
        ids=["failed", "interrupted"],
    )
    def test_non_success_exits_business_error_with_record(self, monkeypatch, cli, terminal):
        _patch_client(monkeypatch, running_times=0, terminal=terminal)
        result = _invoke(["task", "run", "nav_sync"])
        assert result.exit_code == 1
        doc = _doc(result)
        assert doc["ok"] is False
        assert doc["error"]["code"] == "TASK_EXECUTION_NOT_SUCCESS"
        assert terminal["status"] in doc["error"]["message"]
        assert doc["error"]["details"] == terminal


class TestExecutionLookup:
    def test_returns_single_record(self, monkeypatch, cli):
        _patch_client(monkeypatch, running_times=0, terminal=TERMINAL_OK)
        result = _invoke(["task", "execution", "77"])
        assert result.exit_code == 0, result.stdout
        assert _doc(result)["data"] == TERMINAL_OK
