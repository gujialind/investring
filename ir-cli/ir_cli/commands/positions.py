"""持仓管理命令组"""
import sys
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import SUMMARY_FIELDS, build_body, run_list

app = typer.Typer(no_args_is_help=True)


def _resolve_required(option_value: Optional[str], arg_value: Optional[str], flag: str) -> str:
    """option 优先；位置参数已弃用（issue #81），仅向 stderr 告警避免污染 stdout JSON"""
    if option_value is not None:
        return option_value
    if arg_value is not None:
        print(f"[warning] 位置参数已弃用，请改用 {flag} 选项", file=sys.stderr)
        return arg_value
    error("VALIDATION_ERROR", f"缺少 {flag}")


@app.command("list")
def list_positions(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码"),
    snapshot_date: Optional[str] = typer.Option(None, "--snapshot-date", help="快照日期(YYYY-MM-DD)"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
    full: bool = typer.Option(False, "--full", help="输出全字段（默认仅摘要字段）"),
):
    """获取持仓列表（默认输出摘要字段，--full 全字段）"""
    client = APIClient.from_config()
    params = build_body(portfolio_code=portfolio_code, snapshot_date=snapshot_date)
    run_list(
        client, "/api/positions", params,
        page=page, page_size=page_size, all_pages=all_pages,
        fields=fields, default_fields=SUMMARY_FIELDS["position"], full=full,
    )


@app.command("get")
def get(id: int = typer.Argument(..., help="持仓ID")):
    """获取单条持仓详情"""
    client = APIClient.from_config()
    result = client.get(f"/api/positions/{id}")
    success(data=result["data"])


@app.command("available-cash")
def available_cash(
    portfolio_code_arg: Optional[str] = typer.Argument(None, metavar="[PORTFOLIO_CODE]", help="[deprecated] 请改用 --portfolio-code"),
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="仅统计该现金平台（缺省为组合合计）；口径为今日实时"),
):
    """获取组合可用现金（实时）"""
    code = _resolve_required(portfolio_code, portfolio_code_arg, "--portfolio-code")
    client = APIClient.from_config()
    params = {}
    if platform_code is not None:
        params["platform_code"] = platform_code
    result = client.get(
        f"/api/positions/portfolio/{code}/available-cash", params=params
    )
    success(data=result["data"])


@app.command("available-shares")
def available_shares(
    portfolio_code_arg: Optional[str] = typer.Argument(None, metavar="[PORTFOLIO_CODE]", help="[deprecated] 请改用 --portfolio-code"),
    product_code_arg: Optional[str] = typer.Argument(None, metavar="[PRODUCT_CODE]", help="[deprecated] 请改用 --product-code"),
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码"),
    product_code: Optional[str] = typer.Option(None, "--product-code", help="产品代码"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型"),
):
    """获取基金可用份额（实时）"""
    pf_code = _resolve_required(portfolio_code, portfolio_code_arg, "--portfolio-code")
    prod_code = _resolve_required(product_code, product_code_arg, "--product-code")
    client = APIClient.from_config()
    params = {}
    if market is not None:
        params["market"] = market
    result = client.get(
        f"/api/positions/portfolio/{pf_code}/product/{prod_code}/available-shares",
        params=params,
    )
    success(data=result["data"])


@app.command("update-cash")
def update_cash(
    portfolio_code: str = typer.Argument(..., help="组合代码"),
    platform_code: str = typer.Option(..., "--platform-code", help="平台代码"),
    cash_amount: float = typer.Option(..., "--cash-amount", help="现金金额"),
    update_date: Optional[str] = typer.Option(None, "--update-date", help="更新日期(YYYY-MM-DD)"),
):
    """更新组合现金持仓"""
    client = APIClient.from_config()
    body = {"platform_code": platform_code, "cash_amount": cash_amount}
    if update_date is not None:
        body["update_date"] = update_date
    result = client.post(f"/api/positions/portfolio/{portfolio_code}/cash-position", json_data=body)
    success(data=result["data"])


@app.command("list-cash-overrides")
def list_cash_overrides(
    portfolio_code: str = typer.Option(..., "--portfolio-code", help="组合代码"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="起始日期(YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="截止日期(YYYY-MM-DD)"),
):
    """查询现金手动覆盖记录（manual_market_value）"""
    client = APIClient.from_config()
    params = build_body(
        platform_code=platform_code, start_date=start_date, end_date=end_date
    )
    result = client.get(
        f"/api/positions/portfolio/{portfolio_code}/cash-position", params=params
    )
    success(data=result["data"])


@app.command("delete-cash")
def delete_cash(
    portfolio_code: str = typer.Option(..., "--portfolio-code", help="组合代码"),
    platform_code: str = typer.Option(..., "--platform-code", help="平台代码"),
    update_date: str = typer.Option(..., "--update-date", help="覆盖记录日期(YYYY-MM-DD)"),
):
    """删除现金手动覆盖记录，删除后该日回退自然计算值（需重算快照生效）"""
    client = APIClient.from_config()
    result = client.delete(
        f"/api/positions/portfolio/{portfolio_code}/cash-position",
        params={"platform_code": platform_code, "update_date": update_date},
    )
    success(data=result["data"])
