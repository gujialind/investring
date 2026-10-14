"""任务管理命令组"""
import time
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import success, error
from ir_cli.utils import run_list

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_tasks(
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
):
    """获取任务列表"""
    client = APIClient.from_config()
    run_list(client, "/api/system/tasks", page=page, page_size=page_size, all_pages=all_pages, fields=fields)


@app.command("describe")
def describe(code: str = typer.Argument(..., help="任务代码")):
    """查看任务详情（含作用说明与最近一次执行记录）"""
    client = APIClient.from_config()
    result = client.get(f"/api/system/tasks/{code}")
    success(data=result["data"])


@app.command("run")
def run(
    code: str = typer.Argument(..., help="任务代码"),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="提交后轮询至终态再返回（默认开；#714 后任务不再在请求内跑完，--no-wait 只返回受理回执）",
    ),
    poll_interval: int = typer.Option(5, "--poll-interval", help="--wait 轮询间隔秒数"),
):
    """手动执行任务（提交即返，--wait 轮询终态）"""
    client = APIClient.from_config()
    accepted = client.post(f"/api/system/tasks/{code}/run")["data"]
    execution_id = accepted.get("execution_id")
    if not wait:
        success(
            data=accepted,
            hints=[f"轮询终态: ir task execution {execution_id}（pending/running → success/failed/partial_success/interrupted）"],
        )
        return

    # 每次轮询都是短请求，不受 nginx 60s 上限影响（#714 的 504 根因）
    while True:
        state = client.get(f"/api/system/tasks/executions/{execution_id}")["data"]
        if state.get("status") not in ("pending", "running"):
            break
        time.sleep(poll_interval)

    if state.get("status") == "success":
        success(data=state)
        return
    error(
        "TASK_EXECUTION_NOT_SUCCESS",
        f"任务 {code} 执行终态为 {state.get('status')}",
        details=state,
    )


@app.command("execution")
def execution(execution_id: int = typer.Argument(..., help="执行记录 ID")):
    """查看单条任务执行记录（手动触发回执里的 execution_id）"""
    client = APIClient.from_config()
    result = client.get(f"/api/system/tasks/executions/{execution_id}")
    success(data=result["data"])


@app.command("enable")
def enable(code: str = typer.Argument(..., help="任务代码")):
    """启用任务"""
    client = APIClient.from_config()
    result = client.post(f"/api/system/tasks/{code}/enable")
    success(data=result["data"])


@app.command("disable")
def disable(code: str = typer.Argument(..., help="任务代码")):
    """禁用任务"""
    client = APIClient.from_config()
    result = client.post(f"/api/system/tasks/{code}/disable")
    success(data=result["data"])


@app.command("logs")
def logs(
    code: str = typer.Argument(..., help="任务代码"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
):
    """查看任务执行日志"""
    client = APIClient.from_config()
    run_list(client, f"/api/system/tasks/{code}/logs", page=page, page_size=page_size, all_pages=all_pages, fields=fields)
