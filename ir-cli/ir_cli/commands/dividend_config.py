"""分红方式配置管理命令组（#887）。confirm 命令不在此组、不受影响。"""
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import build_body, resolve_body, run_list, SUMMARY_FIELDS

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_configs(
    portfolio_code: str = typer.Option(..., "--portfolio-code", help="组合代码(必填)"),
    product_code: str = typer.Option(..., "--product-code", help="产品代码(必填)"),
    platform_code: str = typer.Option(..., "--platform-code", help="平台代码(必填)"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型，省略按产品唯一市场补全"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
    full: bool = typer.Option(False, "--full", help="输出全字段(默认摘要字段)"),
):
    """获取分红方式配置历史列表（按生效日降序）"""
    client = APIClient.from_config()
    params = build_body(
        portfolio_code=portfolio_code, product_code=product_code,
        platform_code=platform_code, market=market,
    )
    run_list(
        client, "/api/dividend-configs", params, page=page, page_size=page_size,
        all_pages=all_pages, fields=fields, full=full,
        default_fields=SUMMARY_FIELDS["dividend_config"],
    )


@app.command("set")
def set_config(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码(必填)"),
    product_code: Optional[str] = typer.Option(None, "--product-code", help="产品代码(必填)"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码(必填)"),
    method: Optional[str] = typer.Option(None, "--method", help="分红方式(必填): cash_dividend 或 reinvest_dividend"),
    effective_from: Optional[str] = typer.Option(None, "--effective-from", help="生效起始日(YYYY-MM-DD)(必填)，含当日；允许补录过去日期"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型，省略按产品唯一市场补全；写侧仅 CN_OTC"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """写入分红方式配置（追加/提升/替换，source=explicit）

    \b
    示例:
      ir dividend-config set --portfolio-code PORT001 --product-code 000300.OF --platform-code MYCF --method reinvest_dividend --effective-from 2026-11-16
    """
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("portfolio_code", "product_code", "platform_code", "dividend_method", "effective_from"),
        portfolio_code=portfolio_code,
        product_code=product_code,
        platform_code=platform_code,
        market=market,
        dividend_method=method,
        effective_from=effective_from,
    )
    result = client.post("/api/dividend-configs", json_data=body)
    success(data=result["data"])


@app.command("remove")
def remove_config(
    config_id: int = typer.Argument(..., help="配置行 ID"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认，未提供则拒绝执行"),
):
    """删除分红方式配置行（仅今日/未来生效行可删，历史行不可删）"""
    if not yes:
        error("CONFIRM_REQUIRED",
              f"删除配置 {config_id} 不可逆：请追加 --yes 确认，先用 ir dividend-config list 核实对象")
    client = APIClient.from_config()
    result = client.delete(f"/api/dividend-configs/{config_id}")
    success(data=result["data"])