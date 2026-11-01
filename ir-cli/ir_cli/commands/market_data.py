"""市场数据命令组"""
import sys
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import success

app = typer.Typer(no_args_is_help=True)


@app.command("price")
def price(
    code: str = typer.Argument(..., help="产品代码"),
    market: str = typer.Argument(..., help="市场类型(CN_EXCHANGE/CN_OTC)"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="开始日期(YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="结束日期(YYYY-MM-DD)"),
    limit: int = typer.Option(30, "--limit", help="返回条数（默认30，最大1000，按日期降序）"),
):
    """查询产品价格数据"""
    client = APIClient.from_config()
    params = {"limit": limit}
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    result = client.get(f"/api/market-data/products/{code}/{market}/price-data", params=params)
    data = result["data"]
    hints = None
    if isinstance(data, list) and len(data) == limit:
        hints = [f"返回条数已达 limit={limit}，可能被截断，可加大 --limit（最大1000）或收窄日期区间"]
    success(data=data, hints=hints)


@app.command("nav-coverage")
def nav_coverage(
    code: str = typer.Argument(..., help="产品代码"),
    market: str = typer.Argument(..., help="市场类型(CN_EXCHANGE/CN_OTC)"),
    start_date: str = typer.Option(..., "--start-date", help="开始日期(YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="结束日期(YYYY-MM-DD)，默认今天"),
):
    """校验区间内净值同步覆盖情况"""
    client = APIClient.from_config()
    params = {"start_date": start_date}
    if end_date is not None:
        params["end_date"] = end_date
    result = client.get(f"/api/market-data/products/{code}/{market}/nav-coverage", params=params)
    success(data=result["data"])


@app.command("sync")
def sync(
    code: str = typer.Argument(..., help="产品代码"),
    market: str = typer.Argument(..., help="市场类型"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="开始日期(YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="结束日期(YYYY-MM-DD)"),
):
    """同步产品价格数据"""
    client = APIClient.from_config()
    body = {}
    if start_date is not None:
        body["start_date"] = start_date
    if end_date is not None:
        body["end_date"] = end_date
    result = client.post(f"/api/market-data/products/{code}/{market}/sync-price-data", json_data=body)
    success(data=result["data"])


@app.command("sync-history")
def sync_history(
    code: str = typer.Argument(..., help="产品代码"),
    market: str = typer.Argument(..., help="市场类型"),
):
    """同步产品完整历史数据"""
    client = APIClient.from_config()
    result = client.post(f"/api/market-data/products/{code}/{market}/sync-history")
    success(data=result["data"])


@app.command("sync-all")
def sync_all(
    start_date: Optional[str] = typer.Option(None, "--start-date", help="开始日期(YYYY-MM-DD)，不传则增量同步"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="结束日期(YYYY-MM-DD)"),
    scope: str = typer.Option("all", "--scope", help="范围(all/by-product)"),
    products: Optional[str] = typer.Option(None, "--products", help="产品列表，格式: code|market,code|market"),
    data_source: Optional[str] = typer.Option(None, "--data-source", help="数据源过滤(tushare/akshare)"),
):
    """批量同步价格数据（异步任务）"""
    client = APIClient.from_config()
    body = {"scope": scope}
    if start_date is not None:
        body["start_date"] = start_date
    if end_date is not None:
        body["end_date"] = end_date
    if products is not None:
        parsed = []
        for p in products.split(","):
            if "|" not in p:
                print(
                    f"warning: 跳过格式错误的产品条目 '{p.strip()}'（需 code|market 格式）",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            parsed.append(p.split("|"))
        body["products"] = parsed
    if data_source is not None:
        body["data_source"] = data_source
    result = client.post("/api/sync-jobs/price", json_data=body)
    success(data=result["data"])
