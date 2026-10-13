"""产品管理命令组"""
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import build_body, resolve_body, run_list

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_products(
    product_type: Optional[str] = typer.Option(None, "--product-type", help="产品类型(ETF/OEF/LOF/CASH)"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型(CN_EXCHANGE/CN_OTC/...)"),
    data_source: Optional[str] = typer.Option(None, "--data-source", help="数据源(tushare/akshare)"),
    data_source_status: Optional[str] = typer.Option(None, "--data-source-status", help="同步状态(pending/success/failed/error/skipped)"),
    include_virtual: bool = typer.Option(False, "--include-virtual", help="包含虚拟产品(CASH/IN_TRANSIT，#327 起默认排除)"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
):
    """获取产品列表"""
    client = APIClient.from_config()
    params = build_body(
        product_type=product_type,
        market=market,
        data_source=data_source,
        data_source_status=data_source_status,
    )
    if include_virtual:
        params["include_virtual"] = True
    run_list(client, "/api/products", params, page=page, page_size=page_size, all_pages=all_pages, fields=fields)


@app.command("create")
def create(
    code: Optional[str] = typer.Option(None, "--code", help="产品代码(必填)"),
    name: Optional[str] = typer.Option(None, "--name", help="产品名称(必填)"),
    product_type: Optional[str] = typer.Option(None, "--product-type", help="产品类型(ETF/OEF/LOF/CASH)(必填)"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型"),
    asset_class_code: Optional[str] = typer.Option(None, "--asset-class-code", help="资产分类代码"),
    confirm_days: Optional[int] = typer.Option(None, "--confirm-days", help="确认天数(不传按市场+QDII推导：场内0/场外QDII 2/其余1)"),
    nav_lag_days: int = typer.Option(0, "--nav-lag-days", help="快照估值取价滞后交易日数(0=当日，场外QDII/互认基金常为1)"),
    is_qdii: bool = typer.Option(False, "--is-qdii/--no-qdii", help="是否QDII(纯展示标签)"),
    sync: bool = typer.Option(False, "--sync", help="创建后立即回填历史净值"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """创建产品"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("code", "name", "product_type"),
        code=code,
        name=name,
        product_type=product_type,
        market=market,
        asset_class_code=asset_class_code,
        confirm_days=confirm_days,
        nav_lag_days=nav_lag_days,
        is_qdii=is_qdii,
        sync_history=sync,
    )
    result = client.post("/api/products", json_data=body)
    success(data=result["data"])


@app.command("get")
def get(
    code: str = typer.Argument(..., help="产品代码"),
    market: Optional[str] = typer.Argument(None, help="市场类型（省略时自动解析；LOF 多市场须显式指定）"),
):
    """获取产品详情"""
    client = APIClient.from_config()
    path = f"/api/products/{code}/{market}" if market else f"/api/products/{code}"
    result = client.get(path)
    success(data=result["data"])


@app.command("update")
def update(
    code: str = typer.Argument(..., help="产品代码"),
    market: str = typer.Argument(..., help="市场类型（现值）"),
    name: Optional[str] = typer.Option(None, "--name", help="产品名称"),
    product_type: Optional[str] = typer.Option(None, "--product-type", help="产品类型(ETF/OEF/LOF/CASH/IN_TRANSIT)"),
    asset_class_code: Optional[str] = typer.Option(None, "--asset-class-code", help="资产分类代码"),
    confirm_days: Optional[int] = typer.Option(None, "--confirm-days", help="确认天数(纯显式更新，不传不改)"),
    nav_lag_days: Optional[int] = typer.Option(None, "--nav-lag-days", help="快照估值取价滞后交易日数(0=当日，互认基金设1)"),
    is_qdii: Optional[bool] = typer.Option(None, "--is-qdii/--no-qdii", help="是否QDII(纯展示标签，不影响取价与确认天数)"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """更新产品（#232：product_type 可改；market 变更须 --json '{"market": ...}'，仅限无交易/事件/持仓引用的产品）"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        name=name,
        product_type=product_type,
        asset_class_code=asset_class_code,
        confirm_days=confirm_days,
        nav_lag_days=nav_lag_days,
        is_qdii=is_qdii,
    )
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/products/{code}/{market}", json_data=body)
    success(data=result["data"])


@app.command("delete")
def delete(
    code: str = typer.Argument(..., help="产品代码"),
    market: str = typer.Argument(..., help="市场类型"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认，未提供则拒绝执行"),
):
    """删除产品"""
    if not yes:
        error("CONFIRM_REQUIRED",
              f"删除产品 {code}({market}) 不可逆：请追加 --yes 确认，先用 ir product get {code} {market} 核实对象与被引用情况")
    client = APIClient.from_config()
    result = client.delete(f"/api/products/{code}/{market}")
    success(data=result["data"])
