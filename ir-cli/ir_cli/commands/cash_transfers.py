"""现金转移命令组"""
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import success
from ir_cli.utils import resolve_body, run_list

app = typer.Typer(no_args_is_help=True)


@app.command("create")
def create(
    portfolio_code: str = typer.Option(..., "--portfolio-code", help="组合代码"),
    from_platform: Optional[str] = typer.Option(None, "--from", help="转出平台代码(必填)"),
    to_platform: Optional[str] = typer.Option(None, "--to", help="转入平台代码(必填)"),
    amount: Optional[float] = typer.Option(None, "--amount", help="转移金额(必填)"),
    transfer_date: Optional[str] = typer.Option(None, "--date", help="转出日期(YYYY-MM-DD)(必填)"),
    cross_day: bool = typer.Option(False, "--cross-day", help="是否跨天到账"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """创建平台间现金转移

    \b
    示例:
      ir cash-transfer create --portfolio-code PORT001 --from ALIPAY --to CITIC --amount 5000 --date 2026-06-05
    """
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("from_platform", "to_platform", "amount", "transfer_date"),
        from_platform=from_platform,
        to_platform=to_platform,
        amount=amount,
        transfer_date=transfer_date,
        cross_day=cross_day,
        notes=notes,
    )
    result = client.post(f"/api/portfolios/{portfolio_code}/cash-transfer", json_data=body)
    success(data=result["data"])


@app.command("list")
def list_transfers(
    portfolio_code: str = typer.Option(..., "--portfolio-code", help="组合代码"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
):
    """获取现金转移列表"""
    client = APIClient.from_config()
    run_list(client, f"/api/portfolios/{portfolio_code}/cash-transfers", page=page, page_size=page_size, all_pages=all_pages, fields=fields)


@app.command("confirm")
def confirm(
    transfer_group: str = typer.Argument(..., help="转移组标识"),
    portfolio_code: str = typer.Option(..., "--portfolio-code", help="组合代码"),
):
    """确认跨天现金转移（两腿同时确认）"""
    client = APIClient.from_config()
    result = client.post(f"/api/portfolios/{portfolio_code}/cash-transfer/{transfer_group}/confirm")
    success(data=result["data"])


@app.command("cancel")
def cancel(
    transfer_group: str = typer.Argument(..., help="转移组标识"),
    portfolio_code: str = typer.Option(..., "--portfolio-code", help="组合代码"),
):
    """撤销平台间现金转移（#788：两腿逻辑置 cancelled，保留记录 + 审计）

    转错平台/金额填错/重复转移的正式撤销通道；已确认转移受组级快照保护与
    转入平台现金守卫（可用现金不足则拒绝）。

    \b
    示例:
      ir cash-transfer cancel a1b2c3d4e5f6 --portfolio-code PORT001
    """
    client = APIClient.from_config()
    result = client.post(f"/api/portfolios/{portfolio_code}/cash-transfer/{transfer_group}/cancel")
    success(data=result["data"])
