"""组合管理命令组"""
import json
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import SUMMARY_FIELDS, build_body, project_fields, resolve_body, run_list

app = typer.Typer(no_args_is_help=True)


def _parse_display_config(value: Optional[str]) -> Optional[dict]:
    """解析 --display-config JSON 字符串（issue #144）；非法即本地报错退出。

    清空配置请走 --json '{"display_config": null}'（resolve_body 过滤 None，
    逐项参数无法表达显式 null）。
    """
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        error("VALIDATION_ERROR", f"--display-config 不是合法 JSON：{e}")
    if not isinstance(parsed, dict):
        error(
            "VALIDATION_ERROR",
            '--display-config 必须是 JSON 对象，如 \'{"ASSET_STOCK": "style"}\'',
        )
    return parsed


@app.command("list")
def list_portfolios(
    status: Optional[str] = typer.Option(None, "--status", help="状态筛选(draft/active/closed)"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
):
    """获取组合列表"""
    client = APIClient.from_config()
    params = build_body(status=status)
    run_list(client, "/api/portfolios", params, page=page, page_size=page_size, all_pages=all_pages, fields=fields)


@app.command("create")
def create(
    code: Optional[str] = typer.Option(None, "--code", help="组合代码(必填)"),
    name: Optional[str] = typer.Option(None, "--name", help="组合名称(必填)"),
    description: Optional[str] = typer.Option(None, "--description", help="描述"),
    display_config: Optional[str] = typer.Option(
        None, "--display-config",
        help='持仓明细二级分组维度 JSON，如 \'{"ASSET_STOCK": "style"}\'（issue #144）',
    ),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """创建组合"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("code", "name"),
        code=code,
        name=name,
        description=description,
        # --json 直传优先：逐项参数仅在其缺省时解析，避免对被忽略的参数报错
        display_config=(
            _parse_display_config(display_config) if json_body is None else None
        ),
    )
    result = client.post("/api/portfolios", json_data=body)
    success(data=result["data"])


@app.command("get")
def get(code: str = typer.Argument(..., help="组合代码")):
    """获取组合详情"""
    client = APIClient.from_config()
    result = client.get(f"/api/portfolios/{code}")
    success(data=result["data"])


@app.command("context")
def context(code: str = typer.Argument(..., help="组合代码")):
    """一次性获取组合操作上下文（供 AI agent 操作前侦察，替代 4-5 次分步查询）。

    聚合：组合详情、快照状态（含最新快照日）、实时可用现金、pending 申赎/交易。
    """
    client = APIClient.from_config()
    portfolio = client.get(f"/api/portfolios/{code}")["data"]
    snapshot_status = client.get(f"/api/snapshots/portfolios/{code}/status")["data"]
    available_cash = client.get(f"/api/positions/portfolio/{code}/available-cash")["data"]
    subs = client.get_all(
        "/api/subscriptions", params={"portfolio_code": code, "status": "pending"}
    )["data"]
    trades = client.get_all(
        "/api/trades", params={"portfolio_code": code, "status": "pending"}
    )["data"]
    pending_subs = project_fields(subs, SUMMARY_FIELDS["subscription"])
    pending_trades = project_fields(trades, SUMMARY_FIELDS["trade"])
    hints = None
    if pending_subs or pending_trades:
        hints = [
            f"存在 {len(pending_subs)} 笔 pending 申赎、{len(pending_trades)} 笔 pending 交易，"
            "生成快照前需先 confirm 或 cancel 影响目标日的记录"
        ]
    success(
        data={
            "portfolio": portfolio,
            "snapshot_status": snapshot_status,
            "available_cash": available_cash,
            "pending_subscriptions": pending_subs,
            "pending_trades": pending_trades,
        },
        hints=hints,
    )


@app.command("update")
def update(
    code: str = typer.Argument(..., help="组合代码"),
    name: Optional[str] = typer.Option(None, "--name", help="组合名称"),
    description: Optional[str] = typer.Option(None, "--description", help="描述"),
    display_config: Optional[str] = typer.Option(
        None, "--display-config",
        help='持仓明细二级分组维度 JSON，如 \'{"ASSET_STOCK": "style"}\'；'
        '清空配置请用 --json \'{"display_config": null}\'（issue #144）',
    ),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """更新组合信息"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        name=name,
        description=description,
        # --json 直传优先：逐项参数仅在其缺省时解析，避免对被忽略的参数报错
        display_config=(
            _parse_display_config(display_config) if json_body is None else None
        ),
    )
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/portfolios/{code}", json_data=body)
    success(data=result["data"])


@app.command("close")
def close(
    code: str = typer.Argument(..., help="组合代码"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认，未提供则拒绝执行"),
):
    """关闭组合"""
    if not yes:
        error("CONFIRM_REQUIRED",
              f"关闭组合 {code} 会阻断其申赎与调仓：请追加 --yes 确认，先用 ir portfolio context {code} 核实，误关可用 ir portfolio reactivate {code} 回退")
    client = APIClient.from_config()
    result = client.post(f"/api/portfolios/{code}/close")
    success(data=result["data"])


@app.command("reactivate")
def reactivate(code: str = typer.Argument(..., help="组合代码")):
    """重新激活组合"""
    client = APIClient.from_config()
    result = client.post(f"/api/portfolios/{code}/reactivate")
    success(data=result["data"])


@app.command("nav-history")
def nav_history(
    code: str = typer.Argument(..., help="组合代码"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="开始日期(YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="结束日期(YYYY-MM-DD)"),
):
    """获取净值历史"""
    client = APIClient.from_config()
    params = build_body(start_date=start_date, end_date=end_date)
    result = client.get(f"/api/portfolios/{code}/nav-history", params=params)
    success(data=result["data"])


@app.command("returns")
def returns(code: str = typer.Argument(..., help="组合代码")):
    """获取收益率（轻量口径：累计 + 年化）"""
    client = APIClient.from_config()
    result = client.get(f"/api/portfolios/{code}/returns")
    success(data=result["data"])


@app.command("performance")
def performance(code: str = typer.Argument(..., help="组合代码")):
    """获取全量绩效指标（TWR / MWR / 区间收益 / 回撤 / 波动率）"""
    client = APIClient.from_config()
    result = client.get(f"/api/portfolios/{code}/performance")
    success(data=result["data"])


@app.command("cash-flow")
def cash_flow(code: str = typer.Argument(..., help="组合代码")):
    """获取资金流"""
    client = APIClient.from_config()
    result = client.get(f"/api/portfolios/{code}/cash-flow")
    success(data=result["data"])
