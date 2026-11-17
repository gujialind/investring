"""管理员重置他人密码命令（#893）：请求体形态、安全输入通道、错误出口与本地凭证不动。"""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from typer.testing import CliRunner

from ir_cli import config
from ir_cli.client import APIClient
from ir_cli.main import app


def credential(token="admin-token", hours=168):
    return {"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
            "user": {"code": "ADMIN", "name": "Admin", "role": "admin"}}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    for name in ("IR_TOKEN", "IR_PASSWORD", "IR_OLD_PASSWORD", "IR_NEW_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IR_BASE_URL", "http://test")
    monkeypatch.setenv("IR_RETRY", "0")


@pytest.mark.parametrize("use_stdin", [False, True])
def test_reset_password_body_and_credentials_untouched(use_stdin, monkeypatch):
    original = credential()
    config.save_token(original)
    secret = "reset-secret"
    seen = []

    def put(self, path, json_data=None):
        seen.append((path, json_data))
        return {"data": {"message": "密码修改成功，请重新登录"}}

    monkeypatch.setattr(APIClient, "put", put)
    if not use_stdin:
        monkeypatch.setenv("IR_NEW_PASSWORD", secret)
    args = ["auth", "reset-password", "--code", "VIEWER"] + (["--password-stdin"] if use_stdin else [])
    result = CliRunner().invoke(app, args, input=secret + "\n" if use_stdin else None)
    assert result.exit_code == 0, result.output
    assert seen == [("/api/auth/password", {"target_code": "VIEWER", "new_password": secret})]
    assert "old_password" not in seen[0][1]
    assert secret not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["data"]["message"] == "密码修改成功，请重新登录"
    assert config.load_token() == original


def test_reset_password_forbidden_exit(monkeypatch):
    config.save_token(credential())
    monkeypatch.setenv("IR_NEW_PASSWORD", "reset-secret")

    def request(self, method, path, **kwargs):
        return self._handle_response(httpx.Response(
            403, json={"detail": {"error": "FORBIDDEN", "message": "需要管理员权限"}},
        ))

    monkeypatch.setattr(APIClient, "_request", request)
    result = CliRunner().invoke(app, ["auth", "reset-password", "--code", "VIEWER"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "FORBIDDEN"
    assert config.load_token() is not None


def test_reset_password_self_rejected_credentials_untouched(monkeypatch):
    """--code 指向自己时后端走「改自己」分支 400 OLD_PASSWORD_REQUIRED；
    拒绝路径与成功路径一样不触碰本地凭证（#893 L2 Blocker 验证项 3）。"""
    original = credential()
    config.save_token(original)
    monkeypatch.setenv("IR_NEW_PASSWORD", "reset-secret")

    def request(self, method, path, **kwargs):
        return self._handle_response(httpx.Response(
            400, json={"detail": {"error": "OLD_PASSWORD_REQUIRED", "message": "修改密码需要提供旧密码"}},
        ))

    monkeypatch.setattr(APIClient, "_request", request)
    result = CliRunner().invoke(app, ["auth", "reset-password", "--code", "ADMIN"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "OLD_PASSWORD_REQUIRED"
    assert config.load_token() == original


def test_reset_password_requires_password_without_interaction(monkeypatch):
    config.save_token(credential())
    calls = []
    monkeypatch.setattr(APIClient, "put", lambda self, path, json_data=None: calls.append(path) or {})
    result = CliRunner().invoke(app, ["auth", "reset-password", "--code", "VIEWER"], input=None)
    assert result.exit_code == 64
    assert json.loads(result.stdout)["error"]["code"] == "PASSWORD_REQUIRED"
    assert calls == []