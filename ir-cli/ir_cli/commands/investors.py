"""投资人管理命令组"""
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import resolve_body, run_list

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_investors(
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
):
    """获取投资人列表"""
    client = APIClient.from_config()
    run_list(client, "/api/investors", page=page, page_size=page_size, all_pages=all_pages, fields=fields)


@app.command("create")
def create(
    code: Optional[str] = typer.Option(None, "--code", help="投资人代码(必填)"),
    name: Optional[str] = typer.Option(None, "--name", help="姓名(必填)"),
    password: Optional[str] = typer.Option(None, "--password", help="密码(必填)"),
    phone: Optional[str] = typer.Option(None, "--phone", help="手机号"),
    email: Optional[str] = typer.Option(None, "--email", help="邮箱"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """创建投资人"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("code", "name", "password"),
        code=code,
        name=name,
        password=password,
        phone=phone,
        email=email,
    )
    result = client.post("/api/investors", json_data=body)
    success(data=result["data"])


@app.command("get")
def get(code: str = typer.Argument(..., help="投资人代码")):
    """获取投资人详情"""
    client = APIClient.from_config()
    result = client.get(f"/api/investors/{code}")
    success(data=result["data"])


@app.command("update")
def update(
    code: str = typer.Argument(..., help="投资人代码"),
    name: Optional[str] = typer.Option(None, "--name", help="姓名"),
    phone: Optional[str] = typer.Option(None, "--phone", help="手机号"),
    email: Optional[str] = typer.Option(None, "--email", help="邮箱"),
    role: Optional[str] = typer.Option(None, "--role", help="角色(admin/viewer)"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """更新投资人信息"""
    client = APIClient.from_config()
    body = resolve_body(json_body, name=name, phone=phone, email=email, role=role)
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/investors/{code}", json_data=body)
    success(data=result["data"])


@app.command("delete")
def delete(
    code: str = typer.Argument(..., help="投资人代码"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认，未提供则拒绝执行"),
):
    """删除投资人"""
    if not yes:
        error("CONFIRM_REQUIRED",
              f"删除投资人 {code} 不可逆：请追加 --yes 确认，先用 ir investor get {code} 核实对象")
    client = APIClient.from_config()
    result = client.delete(f"/api/investors/{code}")
    success(data=result["data"])
