"""认证管理命令组"""
import os
import sys
from typing import Optional

import typer
from ir_cli import config
from ir_cli.client import APIClient
from ir_cli.output import EXIT_CONNECTION, EXIT_USAGE, success, error

app = typer.Typer(no_args_is_help=True)


def _password(env: str, prompt: str, stdin: bool = False) -> str:
    if stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    elif os.environ.get(env):
        value = os.environ[env]
    elif sys.stdin.isatty():
        value = typer.prompt(prompt, hide_input=True, err=True)
    else:
        error("PASSWORD_REQUIRED", f"请使用隐藏交互输入、标准输入选项或安全注入 {env}", exit_code=EXIT_USAGE)
    if not value:
        error("PASSWORD_REQUIRED", "密码不能为空", exit_code=EXIT_USAGE)
    return value


@app.command("login")
def login(
    code: str = typer.Option(..., "--code", help="用户代码"),
    password_stdin: bool = typer.Option(False, "--password-stdin", help="从标准输入读取一行密码"),
):
    """登录；密码隐藏输入或安全注入 IR_PASSWORD，不接受明文密码参数"""
    password = _password("IR_PASSWORD", "密码", password_stdin)
    client = APIClient(config.get_base_url())
    try:
        data = client.post("/api/auth/login", json_data={"code": code, "password": password})["data"]
    finally:
        client.close()
    try:
        valid = (
            isinstance(data, dict)
            and isinstance(data.get("token"), str) and bool(data["token"])
            and isinstance(data.get("expires_at"), str)
            and (config.token_remaining(data) or 0) > 0
            and isinstance(data.get("user"), dict)
            and data["user"].get("code") == code
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        error("INVALID_RESPONSE", "登录响应无效，原凭证未替换", exit_code=EXIT_CONNECTION)
    try:
        config.save_token(data)
    except OSError:
        error("CREDENTIAL_STORE_ERROR", "无法安全保存登录凭证", exit_code=EXIT_CONNECTION)
    success(data={"user": data["user"], "expires_at": data["expires_at"]})


@app.command("logout")
def logout():
    """撤销当前密码会话；不撤销代理 Key，不改写 IR_TOKEN"""
    client = APIClient.from_config(require_auth=True)
    try:
        client.post("/api/auth/logout")
    finally:
        client.close()
    if not os.environ.get("IR_TOKEN"):
        try:
            config.clear_token(expected_token=client.token)
        except OSError:
            error("CREDENTIAL_STORE_ERROR", "服务端已登出，但本地凭证无法清除；无需重试登出", exit_code=EXIT_CONNECTION)
    success(data={"message": "登出成功"})


@app.command("change-password")
def change_password(
    password_stdin: bool = typer.Option(False, "--password-stdin", help="从标准输入依次读取旧密码、新密码两行"),
):
    """修改密码；支持 IR_OLD_PASSWORD / IR_NEW_PASSWORD 安全注入"""
    client = APIClient.from_config()
    old_password = _password("IR_OLD_PASSWORD", "旧密码", password_stdin)
    new_password = _password("IR_NEW_PASSWORD", "新密码", password_stdin)
    try:
        result = client.put("/api/auth/password", json_data={
            "old_password": old_password, "new_password": new_password,
        })
    finally:
        client.close()
    if not os.environ.get("IR_TOKEN"):
        try:
            config.clear_token(expected_token=client.token)
        except OSError:
            error("CREDENTIAL_STORE_ERROR", "密码已修改，但本地凭证无法清除；请使用新密码登录，勿重复修改", exit_code=EXIT_CONNECTION)
    success(data=result["data"])


@app.command("status")
def status():
    """向服务端验证当前凭证（IR_TOKEN 优先），不输出秘密"""
    client = APIClient.from_config()
    try:
        result = client.get("/api/auth/status")
    finally:
        client.close()
    success(data={**result["data"], "base_url": client.base_url})


@app.command("key-create")
def key_create(
    label: str = typer.Option(..., "--label", help="代理凭证标签"),
    scope: list[str] = typer.Option(["ledger:read"], "--scope", help="权限范围：ledger:read 或 ledger:write，可重复"),
    investor: Optional[str] = typer.Option(None, "--investor", help="所属用户代码，默认当前管理员"),
    expires_at: Optional[str] = typer.Option(None, "--expires-at", help="含时区的 ISO 到期时间；默认永不过期"),
):
    """管理员创建独立代理 Key；stdout 仅本次返回秘密，请保存到密钥管理器"""
    client = APIClient.from_config()
    body = {"label": label, "scopes": scope}
    if investor is not None:
        body["investor_code"] = investor
    if expires_at is not None:
        body["expires_at"] = expires_at
    try:
        result = client.post("/api/auth/keys", json_data=body)
    finally:
        client.close()
    success(data=result["data"])


@app.command("key-list")
def key_list():
    """管理员列出 Key 元数据，不返回秘密"""
    client = APIClient.from_config()
    try:
        result = client.get("/api/auth/keys")
    finally:
        client.close()
    success(data=result["data"])


@app.command("key-revoke")
def key_revoke(key_id: str = typer.Argument(..., help="待撤销的凭证 ID")):
    """管理员持久化撤销指定 Key"""
    client = APIClient.from_config()
    try:
        result = client.post(f"/api/auth/keys/{key_id}/revoke")
    finally:
        client.close()
    success(data=result["data"])
