"""平台市值台账管理命令组（issue #905 PR3）。

台账型组合（kind=ledger）的市值登记（entry）与本金进出（flow）读写 + 收益汇总，
与 REST /api/portfolios/{code}/ledger/* 同口径。创建/激活在 portfolio 命令组
（ir portfolio create --kind ledger / ir portfolio activate）。
写侧 admin、读侧登录用户；draft 不可预录（S3），净入金为负附 warning（S4）。
"""
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import SUMMARY_FIELDS, build_body, project_fields, resolve_body

app = typer.Typer(no_args_is_help=True)


def _list_output(result: dict, resource: str, fields: Optional[str], full: bool) -> None:
    """台账列表为非分页 List（后端一次返回全部），按 --fields/--full/摘要字段裁剪输出。"""
    effective = fields if fields else (None if full else SUMMARY_FIELDS.get(resource))
    success(data=project_fields(result["data"], effective))


# ---- 市值台账 entry ----

@app.command("entry-list")
def entry_list(
    code: str = typer.Argument(..., help="组合代码"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码筛选"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
    full: bool = typer.Option(False, "--full", help="输出全字段（默认摘要字段）"),
):
    """查询台账市值记录（每平台每日一行）"""
    client = APIClient.from_config()
    result = client.get(f"/api/portfolios/{code}/ledger/entries", params=build_body(platform_code=platform_code))
    _list_output(result, "ledger_entry", fields, full)


@app.command("entry-add")
def entry_add(
    code: str = typer.Argument(..., help="组合代码"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码(必填)"),
    value_date: Optional[str] = typer.Option(None, "--value-date", help="市值日期 YYYY-MM-DD(必填，任意自然日)"),
    market_value: Optional[float] = typer.Option(None, "--market-value", help="市值(必填，≥0)"),
    asset_kind: Optional[str] = typer.Option(None, "--asset-kind", help="资产分类 cash/non_cash（默认 non_cash）"),
    note: Optional[str] = typer.Option(None, "--note", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """登记平台市值（仅 active 台账型放行；同平台同日重复由后端唯一键拒绝）"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("platform_code", "value_date", "market_value"),
        platform_code=platform_code,
        value_date=value_date,
        market_value=market_value,
        asset_kind=asset_kind,
        note=note,
    )
    result = client.post(f"/api/portfolios/{code}/ledger/entries", json_data=body)
    success(data=result["data"])


@app.command("entry-update")
def entry_update(
    code: str = typer.Argument(..., help="组合代码"),
    entry_id: int = typer.Argument(..., help="市值记录 ID"),
    market_value: Optional[float] = typer.Option(None, "--market-value", help="市值（≥0）"),
    asset_kind: Optional[str] = typer.Option(None, "--asset-kind", help="资产分类 cash/non_cash"),
    note: Optional[str] = typer.Option(None, "--note", help="备注；清空用 --json '{\"note\": null}'"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """修改某日市值/分类/备注（平台与日期不可改，改日期请删后重建；写侧留痕）"""
    client = APIClient.from_config()
    body = resolve_body(json_body, market_value=market_value, asset_kind=asset_kind, note=note)
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/portfolios/{code}/ledger/entries/{entry_id}", json_data=body)
    success(data=result["data"])


@app.command("entry-delete")
def entry_delete(
    code: str = typer.Argument(..., help="组合代码"),
    entry_id: int = typer.Argument(..., help="市值记录 ID"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认，未提供则拒绝执行"),
):
    """删除某日市值记录（删除后该日缺失、读侧回退前值）"""
    if not yes:
        error("CONFIRM_REQUIRED", f"删除市值记录 {entry_id} 请追加 --yes 确认")
    client = APIClient.from_config()
    result = client.delete(f"/api/portfolios/{code}/ledger/entries/{entry_id}")
    success(data=result["data"])


# ---- 本金进出 flow ----

@app.command("flow-list")
def flow_list(
    code: str = typer.Argument(..., help="组合代码"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码筛选"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
    full: bool = typer.Option(False, "--full", help="输出全字段（默认摘要字段）"),
):
    """查询本金进出记录（同平台同日可多笔）"""
    client = APIClient.from_config()
    result = client.get(f"/api/portfolios/{code}/ledger/flows", params=build_body(platform_code=platform_code))
    _list_output(result, "ledger_flow", fields, full)


@app.command("flow-add")
def flow_add(
    code: str = typer.Argument(..., help="组合代码"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码(必填)"),
    flow_date: Optional[str] = typer.Option(None, "--flow-date", help="进出日期 YYYY-MM-DD(必填，任意自然日)"),
    direction: Optional[str] = typer.Option(None, "--direction", help="方向 inflow/outflow(必填)"),
    amount: Optional[float] = typer.Option(None, "--amount", help="本金金额(必填，>0)"),
    note: Optional[str] = typer.Option(None, "--note", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """登记本金进出（仅 active 台账型放行；净入金为负时响应附 warning，不静默）"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("platform_code", "flow_date", "direction", "amount"),
        platform_code=platform_code,
        flow_date=flow_date,
        direction=direction,
        amount=amount,
        note=note,
    )
    result = client.post(f"/api/portfolios/{code}/ledger/flows", json_data=body)
    data = result["data"]
    warnings = data.get("warnings") if isinstance(data, dict) else None
    success(data=data, hints=warnings or None)


@app.command("flow-update")
def flow_update(
    code: str = typer.Argument(..., help="组合代码"),
    flow_id: int = typer.Argument(..., help="进出记录 ID"),
    flow_date: Optional[str] = typer.Option(None, "--flow-date", help="进出日期 YYYY-MM-DD"),
    direction: Optional[str] = typer.Option(None, "--direction", help="方向 inflow/outflow"),
    amount: Optional[float] = typer.Option(None, "--amount", help="本金金额（>0）"),
    note: Optional[str] = typer.Option(None, "--note", help="备注；清空用 --json '{\"note\": null}'"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """修改某笔进出（平台不可改；写侧留痕）"""
    client = APIClient.from_config()
    body = resolve_body(json_body, flow_date=flow_date, direction=direction, amount=amount, note=note)
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/portfolios/{code}/ledger/flows/{flow_id}", json_data=body)
    data = result["data"]
    warnings = data.get("warnings") if isinstance(data, dict) else None
    success(data=data, hints=warnings or None)


@app.command("flow-delete")
def flow_delete(
    code: str = typer.Argument(..., help="组合代码"),
    flow_id: int = typer.Argument(..., help="进出记录 ID"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认，未提供则拒绝执行"),
):
    """删除某笔本金进出记录"""
    if not yes:
        error("CONFIRM_REQUIRED", f"删除进出记录 {flow_id} 请追加 --yes 确认")
    client = APIClient.from_config()
    result = client.delete(f"/api/portfolios/{code}/ledger/flows/{flow_id}")
    success(data=result["data"])


# ---- 收益汇总 ----

@app.command("totals")
def totals(
    code: str = typer.Argument(..., help="组合代码"),
    as_of_date: Optional[str] = typer.Option(None, "--as-of-date", help="汇总截止日 YYYY-MM-DD（默认业务今天）"),
):
    """查询台账收益汇总（当前市值/净入金/累计收益；收益=市值−净入金，step-forward）"""
    client = APIClient.from_config()
    result = client.get(f"/api/portfolios/{code}/ledger/totals", params=build_body(as_of_date=as_of_date))
    data = result["data"]
    warnings = data.get("warnings") if isinstance(data, dict) else None
    success(data=data, hints=warnings or None)