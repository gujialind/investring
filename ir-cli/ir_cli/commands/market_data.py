"""市场数据命令组"""
import sys
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import EXIT_USAGE, error, success

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
    """同步产品价格数据（全量或按周期，#856 起调 sync-history；无日期=成立以来全量）"""
    client = APIClient.from_config()
    body = {}
    if start_date is not None:
        body["start_date"] = start_date
    if end_date is not None:
        body["end_date"] = end_date
    result = client.post(f"/api/market-data/products/{code}/{market}/sync-history", json_data=body)
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
    scope: Optional[str] = typer.Option(None, "--scope", help="范围(all/by_product)；传 --products 且缺省时自动 by_product"),
    products: Optional[str] = typer.Option(None, "--products", help="产品列表，格式: code|market,code|market"),
    data_source: Optional[str] = typer.Option(None, "--data-source", help="数据源过滤(tushare/akshare)"),
):
    """批量同步价格数据（异步任务）"""
    client = APIClient.from_config()
    parsed = []
    if products is not None:
        for p in products.split(","):
            # #808：必须恰好两段且 code/market 去空格后非空，否则精确匹配静默落空
            parts = [s.strip() for s in p.split("|")]
            if len(parts) != 2 or not all(parts):
                print(
                    f"warning: 跳过格式错误的产品条目 '{p.strip()}'（需 code|market 格式）",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            parsed.append(parts)
        if not parsed:
            # #808：条目全部非法必须报错终止——空清单照常 POST 会静默退化为全量同步
            error(
                "USAGE_ERROR",
                "--products 条目全部格式错误（需 code|market 格式），未下发同步任务",
                details={"skipped": [p.strip() for p in products.split(",")]},
                exit_code=EXIT_USAGE,
            )
    if scope is None:
        # #808：给了 --products 未显式 --scope 时自动 by_product，缺省不再静默全量
        scope = "by_product" if parsed else "all"
    else:
        normalized_scope = scope.replace("-", "_")
        if normalized_scope == "all" and parsed:
            # #808 Q2：--scope all 与 --products 语义冲突，拒绝而非静默忽略清单
            error(
                "USAGE_ERROR",
                "--scope all 与 --products 冲突：限定清单请省略 --scope（自动 by_product）或显式传 by_product",
                exit_code=EXIT_USAGE,
            )
        if normalized_scope == "by_product" and not parsed:
            # #808 Q3：by_product 缺清单此前到后端才 422（exit 1），CLI 侧同类用法错误统一 exit 64
            error(
                "USAGE_ERROR",
                "--scope by_product 需要 --products：未提供有效产品清单，未下发同步任务",
                exit_code=EXIT_USAGE,
            )
    body = {"scope": scope}
    if start_date is not None:
        body["start_date"] = start_date
    if end_date is not None:
        body["end_date"] = end_date
    if products is not None:
        body["products"] = parsed
    if data_source is not None:
        body["data_source"] = data_source
    result = client.post("/api/sync-jobs/price", json_data=body)
    success(data=result["data"])
