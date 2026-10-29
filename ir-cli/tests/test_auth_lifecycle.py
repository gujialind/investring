"""认证续期、一次业务写入与安全凭证存储（#797）。"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from typer.testing import CliRunner

from ir_cli import config
from ir_cli.client import APIClient
from ir_cli.main import app


def credential(token="old", hours=1):
    return {"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
            "user": {"code": "ADMIN", "name": "Admin", "role": "admin"}}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    for name in ("IR_TOKEN", "IR_PASSWORD", "IR_OLD_PASSWORD", "IR_NEW_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IR_BASE_URL", "http://test")
    monkeypatch.setenv("IR_RETRY", "0")


def mock_client(handler):
    client = APIClient.from_config()
    client._client.close()
    client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler),
                                headers={"Authorization": f"Bearer {client.token}"})
    return client


def test_renewal_before_single_financial_write_and_json_stdout(capsys):
    config.save_token(credential())
    seen = []
    def handler(request):
        seen.append((request.url.path, request.headers["Authorization"]))
        return httpx.Response(200, json=credential("fresh", 168) if request.url.path.endswith("renew") else {"id": 42})
    client = mock_client(handler)
    assert client.post("/api/trades")["data"] == {"id": 42}
    assert seen == [("/api/auth/renew", "Bearer old"), ("/api/trades", "Bearer fresh")]
    assert config.load_token()["token"] == "fresh"
    output = capsys.readouterr()
    assert output.out == "" and "自动续期" in output.err
    client.close()


@pytest.mark.parametrize("failure", [503, "network"])
def test_transient_renewal_failure_does_not_retry_write(failure, capsys):
    config.save_token(credential())
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("renew"):
            if failure == "network":
                raise httpx.ConnectError("offline")
            return httpx.Response(failure)
        raise httpx.ReadError("write response lost")
    client = mock_client(handler)
    with pytest.raises(SystemExit) as exc:
        client.post("/api/trades")
    assert exc.value.code == 3
    assert calls == ["/api/auth/renew", "/api/trades"]
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert config.load_token()["token"] == "old"
    client.close()


def test_revoked_renewal_stops_business_request(capsys):
    config.save_token(credential())
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401, json={"detail": {"error": "AUTH_REQUIRED", "message": "已撤销"}})
    client = mock_client(handler)
    with pytest.raises(SystemExit) as exc:
        client.post("/api/trades")
    assert exc.value.code == 2
    assert calls == ["/api/auth/renew"]
    assert config.load_token() is None
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "AUTH_REQUIRED"
    client.close()


@pytest.mark.parametrize("external", [False, True])
def test_keys_and_env_tokens_never_renew_or_overwrite_file(external, monkeypatch):
    original = credential("local" if external else "ir_key_test")
    config.save_token(original)
    if external:
        monkeypatch.setenv("IR_TOKEN", "externally-managed-jwt")
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=[])
    client = mock_client(handler)
    client.get("/api/trades")
    assert calls == ["/api/trades"]
    assert config.load_token() == original
    client.close()


@pytest.mark.parametrize("replacement", [None, "other-login"])
def test_inflight_renewal_cannot_resurrect_or_replace_credentials(replacement, capsys):
    config.save_token(credential())
    def handler(request):
        assert request.url.path == "/api/auth/renew"
        if replacement:
            config.save_token(credential(replacement))
        else:
            config.clear_token()
        return httpx.Response(200, json=credential("late-renewal", 168))
    client = mock_client(handler)
    with pytest.raises(SystemExit) as exc:
        client.post("/api/trades")
    assert exc.value.code == 2
    current = config.load_token()
    assert (current["token"] if current else None) == replacement
    client.close()


def test_safe_atomic_persistence_and_compare_swap(tmp_path, monkeypatch):
    config.save_token(credential())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda n: config.save_token(credential(str(n)), expected_token="old"), range(8)))
    assert sum(results) == 1
    saved = config.load_token()
    config.clear_token(expected_token="old")
    assert config.load_token() == saved
    if os.name != "nt":
        assert (config.get_ir_dir() / "token.json").stat().st_mode & 0o777 == 0o600
    def fail_replace(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(config.os, "replace", fail_replace)
    with pytest.raises(OSError):
        config.save_token(credential("lost"))
    assert config.load_token() == saved
    assert not list(config.get_ir_dir().glob(".token-*"))


@pytest.mark.parametrize("payload", [[], {}, {"token": 1}, {"token": "t", "expires_at": "bad"}])
def test_invalid_local_file_is_rejected(payload):
    (config.get_ir_dir() / "token.json").write_text(json.dumps(payload))
    assert config.load_token() is None


def test_expired_file_rejected_without_network(capsys):
    config.save_token(credential(hours=-1))
    assert config.load_token() is None
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit) as exc:
        APIClient.from_config()
    assert exc.value.code == 2


@pytest.mark.parametrize("use_env", [False, True])
def test_password_input_never_appears_in_stdout_or_argv(use_env, monkeypatch):
    secret = "test-password-secret"
    def post(self, path, json_data):
        assert path == "/api/auth/login" and json_data["password"] == secret
        return {"data": credential("signed", 168)}
    monkeypatch.setattr(APIClient, "post", post)
    if use_env:
        monkeypatch.setenv("IR_PASSWORD", secret)
    args = ["auth", "login", "--code", "ADMIN"] + ([] if use_env else ["--password-stdin"])
    result = CliRunner().invoke(app, args, input=None if use_env else secret + "\n")
    assert result.exit_code == 0, result.output
    assert secret not in result.stdout and "signed" not in result.stdout
    assert json.loads(result.stdout)["ok"] is True
    assert config.load_token()["token"] == "signed"


def test_argv_password_is_rejected():
    result = CliRunner().invoke(app, ["auth", "login", "--code", "ADMIN", "--password", "secret"])
    assert result.exit_code != 0


def test_status_uses_remote_env_key_without_leaking_secret(monkeypatch):
    config.save_token(credential("local"))
    monkeypatch.setenv("IR_TOKEN", "ir_key_secret")
    def get(self, path):
        assert path == "/api/auth/status" and self.token == "ir_key_secret"
        return {"data": {"credential_type": "api_key", "label": "Analyst", "expires_at": None}}
    monkeypatch.setattr(APIClient, "get", get)
    result = CliRunner().invoke(app, ["auth", "status"])
    assert result.exit_code == 0 and "ir_key_secret" not in result.stdout
    assert json.loads(result.stdout)["data"]["credential_type"] == "api_key"
    assert config.load_token()["token"] == "local"


@pytest.mark.parametrize("payload", [None, [], {}, {"token": "bad"}, credential("bad", -1),
                                     {**credential("bad"), "expires_at": "invalid"},
                                     {**credential("bad"), "user": {"code": "OTHER"}}])
def test_malformed_renewal_preserves_credentials_and_does_not_write(payload, capsys):
    original = credential()
    config.save_token(original)
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload)
    client = mock_client(handler)
    with pytest.raises(SystemExit) as exc:
        client.post("/api/trades")
    assert exc.value.code == 3
    assert calls == ["/api/auth/renew"]
    assert config.load_token() == original
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_RESPONSE"
    client.close()


def test_chain_renewal_error_remains_available_to_caller(capsys):
    from ir_cli.client import ApiError
    config.save_token(credential())
    client = mock_client(lambda request: httpx.Response(401, json={"detail": "已撤销"}))
    with pytest.raises(ApiError) as exc:
        client.post("/api/trades/42/confirm", raise_errors=True)
    assert exc.value.code == "AUTH_REQUIRED"
    assert capsys.readouterr().out == ""
    client.close()


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("command", ["logout", "change-password"])
def test_logout_password_commands_preserve_external_ownership(command, external, monkeypatch):
    config.save_token(credential("local", 168))
    if external:
        monkeypatch.setenv("IR_TOKEN", "external")
    def request(self, path, **kwargs):
        assert self.token == ("external" if external else "local")
        if command == "change-password":
            assert kwargs["json_data"] == {"old_password": "old-secret", "new_password": "new-secret"}
        return {"data": {"message": "成功"}}
    monkeypatch.setattr(APIClient, "post" if command == "logout" else "put", request)
    args = ["auth", command]
    if command == "change-password":
        args += ["--password-stdin"]
    result = CliRunner().invoke(app, args, input="old-secret\nnew-secret\n")
    assert result.exit_code == 0, result.output
    assert "old-secret" not in result.stdout and "new-secret" not in result.stdout
    assert (config.load_token() is not None) == external


def test_key_commands_use_metadata_and_one_time_secret(monkeypatch):
    config.save_token(credential("local", 168))
    calls = []
    def post(self, path, json_data=None):
        calls.append((path, json_data))
        return {"data": {"key": {"id": "kid"}, "token": "one-time"} if path.endswith("keys") else {"id": "kid", "revoked_at": "now"}}
    monkeypatch.setattr(APIClient, "post", post)
    monkeypatch.setattr(APIClient, "get", lambda self, path: {"data": [{"id": "kid"}]})
    runner = CliRunner()
    created = runner.invoke(app, ["auth", "key-create", "--label", "Analyst", "--scope", "ledger:write"])
    assert created.exit_code == 0
    assert json.loads(created.stdout)["data"]["token"] == "one-time"
    assert calls[0] == ("/api/auth/keys", {"label": "Analyst", "scopes": ["ledger:write"]})
    listing = runner.invoke(app, ["auth", "key-list"])
    revoked = runner.invoke(app, ["auth", "key-revoke", "kid"])
    assert listing.exit_code == revoked.exit_code == 0
    assert "one-time" not in listing.stdout + revoked.stdout
    assert calls[-1][0] == "/api/auth/keys/kid/revoke"


def test_cross_process_credential_compare_swap(tmp_path):
    import subprocess
    import sys
    config.save_token(credential())
    script = '''
import json, sys
from pathlib import Path
from ir_cli import config
data = json.load(sys.stdin)
config.Path.home = lambda: Path(data["home"])
print(json.dumps(config.save_token(data["credential"], expected_token="old")))
'''
    def attempt(index):
        result = subprocess.run([sys.executable, "-c", script],
                                input=json.dumps({"home": str(tmp_path), "credential": credential(str(index))}),
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(results) == 1
    assert config.load_token()["token"] != "old"


@pytest.mark.parametrize("payload", [None, {}, credential("bad", -1),
                                     {**credential("bad"), "user": {"code": "OTHER"}}])
def test_invalid_login_response_preserves_existing_credential(payload, monkeypatch):
    original = credential()
    config.save_token(original)
    monkeypatch.setattr(APIClient, "post", lambda *args, **kwargs: {"data": payload})
    result = CliRunner().invoke(app, ["auth", "login", "--code", "ADMIN", "--password-stdin"], input="secret\n")
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_RESPONSE"
    assert config.load_token() == original


@pytest.mark.parametrize("user", [None, [], "ADMIN", {}])
def test_malformed_local_owner_stops_renewal_without_traceback(user, capsys):
    original = {**credential(), "user": user}
    config.save_token(original)
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=credential("fresh", 168))
    client = mock_client(handler)
    with pytest.raises(SystemExit) as exc:
        client.post("/api/trades")
    assert exc.value.code == 3
    assert calls == ["/api/auth/renew"]
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_RESPONSE"
    assert config.load_token() == original
    client.close()


def test_revocation_cleanup_failure_keeps_auth_error_and_chain_context(monkeypatch, capsys):
    from ir_cli.client import ApiError
    config.save_token(credential())
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401, json={"detail": "已撤销"})
    def fail_clear(**kwargs):
        raise OSError("read-only")
    monkeypatch.setattr(config, "clear_token", fail_clear)
    client = mock_client(handler)
    with pytest.raises(ApiError) as exc:
        client.post("/api/trades/42/confirm", raise_errors=True)
    assert exc.value.code == "AUTH_REQUIRED"
    assert calls == ["/api/auth/renew"]
    output = capsys.readouterr()
    assert output.out == "" and "无法从本地清除" in output.err
    client.close()


@pytest.mark.parametrize("command", ["logout", "change-password"])
def test_cleanup_failure_reports_completed_remote_operation(command, monkeypatch):
    config.save_token(credential("local", 168))
    calls = []
    def request(self, path, **kwargs):
        calls.append(path)
        return {"data": {"message": "成功"}}
    def fail_clear(**kwargs):
        raise OSError("read-only")
    monkeypatch.setattr(APIClient, "post" if command == "logout" else "put", request)
    monkeypatch.setattr(config, "clear_token", fail_clear)
    args = ["auth", command] + (["--password-stdin"] if command == "change-password" else [])
    result = CliRunner().invoke(app, args, input="old-secret\nnew-secret\n")
    assert result.exit_code == 3
    failure = json.loads(result.stdout)["error"]
    assert failure["code"] == "CREDENTIAL_STORE_ERROR"
    assert "密码已修改" in failure["message"] if command == "change-password" else "服务端已登出" in failure["message"]
    assert len(calls) == 1
