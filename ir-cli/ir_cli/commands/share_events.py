"""份额变动事件管理命令组"""
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import build_body, resolve_body, run_list

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_events(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
):
    """获取份额变动事件列表"""
    client = APIClient.from_config()
    params = build_body(portfolio_code=portfolio_code)
    run_list(client, "/api/share-change-events", params, page=page, page_size=page_size, all_pages=all_pages, fields=fields)


@app.command("create")
def create(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码(必填)"),
    event_type: Optional[str] = typer.Option(None, "--event-type", help="事件类型(必填)"),
    ex_date: Optional[str] = typer.Option(None, "--ex-date", help="除息日(YYYY-MM-DD)(必填)"),
    entitlement_date: Optional[str] = typer.Option(None, "--entitlement-date", help="权益登记日(YYYY-MM-DD)(必填)"),
    cash_pay_date: Optional[str] = typer.Option(None, "--cash-pay-date", help="现金分红到账日(YYYY-MM-DD)，仅 cash_dividend；默认除息日，不早于除息日，允许非交易日"),
    product_code: Optional[str] = typer.Option(None, "--product-code", help="产品代码"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码(平台级事件必填: cash_dividend/reinvest_dividend/forced_adjustment)"),
    entitlement_shares: Optional[float] = typer.Option(None, "--entitlement-shares", help="权益份额"),
    div_cash: Optional[float] = typer.Option(None, "--div-cash", help="每股分红金额"),
    reinvest_nav: Optional[float] = typer.Option(None, "--reinvest-nav", help="再投资净值"),
    ratio: Optional[float] = typer.Option(None, "--ratio", help="拆分/合并比例"),
    shares_change: Optional[float] = typer.Option(None, "--shares-change", help="份额变化量"),
    cash_change: Optional[float] = typer.Option(None, "--cash-change", help="现金变化量"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    force_cover: bool = typer.Option(False, "--force-cover", help="平台覆盖不全时降为 warning（默认阻断）"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """创建份额变动事件

    \b
    示例:
      ir share-event create --portfolio-code PORT001 --event-type cash_dividend --ex-date 2026-06-05 --entitlement-date 2026-06-04 --product-code 022959.OF --platform-code ALIPAY --entitlement-shares 10000 --div-cash 0.05
    """
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("portfolio_code", "event_type", "ex_date", "entitlement_date"),
        portfolio_code=portfolio_code,
        event_type=event_type,
        ex_date=ex_date,
        entitlement_date=entitlement_date,
        cash_pay_date=cash_pay_date,
        product_code=product_code,
        market=market,
        platform_code=platform_code,
        entitlement_shares=entitlement_shares,
        div_cash=div_cash,
        reinvest_nav=reinvest_nav,
        ratio=ratio,
        shares_change=shares_change,
        cash_change=cash_change,
        notes=notes,
    )
    result = client.post(
        "/api/share-change-events",
        json_data=body,
        params={"force_cover": force_cover},
    )
    success(data=result["data"])


@app.command("get")
def get(id: int = typer.Argument(..., help="事件ID")):
    """获取事件详情"""
    client = APIClient.from_config()
    result = client.get(f"/api/share-change-events/{id}")
    success(data=result["data"])


@app.command("update")
def update(
    id: int = typer.Argument(..., help="事件ID"),
    ex_date: Optional[str] = typer.Option(None, "--ex-date", help="除息日"),
    entitlement_date: Optional[str] = typer.Option(None, "--entitlement-date", help="权益登记日"),
    cash_pay_date: Optional[str] = typer.Option(None, "--cash-pay-date", help="现金分红到账日(YYYY-MM-DD)，仅 cash_dividend；不早于除息日，允许非交易日；省略不改，--json 显式 null 清空后按除息日到账"),
    div_cash: Optional[float] = typer.Option(None, "--div-cash", help="每股分红金额"),
    reinvest_nav: Optional[float] = typer.Option(None, "--reinvest-nav", help="再投资净值"),
    ratio: Optional[float] = typer.Option(None, "--ratio", help="比例"),
    shares_change: Optional[float] = typer.Option(None, "--shares-change", help="份额变化量"),
    cash_change: Optional[float] = typer.Option(None, "--cash-change", help="现金变化量"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """更新事件"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        ex_date=ex_date,
        entitlement_date=entitlement_date,
        cash_pay_date=cash_pay_date,
        div_cash=div_cash,
        reinvest_nav=reinvest_nav,
        ratio=ratio,
        shares_change=shares_change,
        cash_change=cash_change,
        notes=notes,
    )
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/share-change-events/{id}", json_data=body)
    success(data=result["data"])


@app.command("delete")
def delete(id: int = typer.Argument(..., help="事件ID")):
    """删除事件"""
    client = APIClient.from_config()
    result = client.delete(f"/api/share-change-events/{id}")
    success(data=result["data"])


@app.command("confirm")
def confirm(id: int = typer.Argument(..., help="事件ID")):
    """确认事件"""
    client = APIClient.from_config()
    result = client.post(f"/api/share-change-events/{id}/confirm")
    success(
        data=result["data"],
        hints=["确认后需生成 ex_date 日快照: ir snapshot generate --portfolio-code <code> --target-date <ex_date>；现金分红按 cash_pay_date（未设则 ex_date）到账，此前计分红在途、不计可用现金；非交易日到账由下一交易日快照消费"],
    )


@app.command("cancel")
def cancel(id: int = typer.Argument(..., help="事件ID")):
    """取消事件"""
    client = APIClient.from_config()
    result = client.post(f"/api/share-change-events/{id}/cancel")
    success(data=result["data"])


@app.command("unconfirm")
def unconfirm(id: int = typer.Argument(..., help="事件ID")):
    """取消确认事件。

    约束：仅 confirmed 状态可取消确认；ex_date 及之后已有快照则返回
    SNAPSHOT_DEPENDENCY。基金级父记录会级联删除子记录后置 pending；
    子记录单独 unconfirm 会被拒绝（CANNOT_UNCONFIRM_CHILD）。
    """
    client = APIClient.from_config()
    result = client.post(f"/api/share-change-events/{id}/unconfirm")
    success(data=result["data"])
