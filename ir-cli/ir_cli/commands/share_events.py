"""份额变动事件管理命令组"""
import typer
from typing import Optional
from ir_cli.client import APIClient
from ir_cli.output import error, success
from ir_cli.utils import build_body, resolve_body, run_list

app = typer.Typer(no_args_is_help=True)


def _dividend_choice(
    event_type: Optional[str],
    cash_pay_date: Optional[str],
    share_confirm_date: Optional[str],
) -> Optional[dict]:
    """预览和确认共用完整候选；省略日期由后端默认到除息日，不读取旧值补齐。"""
    if event_type is None:
        if cash_pay_date is not None or share_confirm_date is not None:
            error(
                "VALIDATION_ERROR",
                "不能单独提供日期：--cash-pay-date / --share-confirm-date 必须与 "
                "--event-type 一起提供（cash_dividend 或 reinvest_dividend）",
            )
        return None
    if event_type not in ("cash_dividend", "reinvest_dividend"):
        error("VALIDATION_ERROR", "--event-type 仅允许 cash_dividend 或 reinvest_dividend")
    return resolve_body(
        None,
        event_type=event_type,
        cash_pay_date=cash_pay_date,
        share_confirm_date=share_confirm_date,
    )


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
    share_confirm_date: Optional[str] = typer.Option(None, "--share-confirm-date", help="再投资份额确认日(YYYY-MM-DD)，仅 reinvest_dividend；须为交易日且晚于除息日；设置后允许权益登记日=除息日，未设置份额按除息日应用"),
    product_code: Optional[str] = typer.Option(None, "--product-code", help="产品代码"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码(平台级事件必填: cash_dividend/reinvest_dividend/forced_adjustment)"),
    entitlement_shares: Optional[float] = typer.Option(None, "--entitlement-shares", help="权益份额"),
    div_cash: Optional[float] = typer.Option(None, "--div-cash", help="每份分红金额(>0)，仅 cash_dividend/reinvest_dividend；0/负数报 INVALID_PARAM；省略放行，但折算按 0 处理会产出「0 现金/0 份额」空事件"),
    reinvest_nav: Optional[float] = typer.Option(None, "--reinvest-nav", help="再投资净值(>0)，仅 reinvest_dividend；省略则确认时取除息日数据源净值，填写则仅作一致性校验"),
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
        share_confirm_date=share_confirm_date,
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
    share_confirm_date: Optional[str] = typer.Option(None, "--share-confirm-date", help="再投资份额确认日(YYYY-MM-DD)，仅 reinvest_dividend；须为交易日且晚于除息日；设置后允许权益登记日=除息日；省略不改，--json 显式 null 清空后份额改按除息日应用"),
    div_cash: Optional[float] = typer.Option(None, "--div-cash", help="每份分红金额(>0)，仅 cash_dividend/reinvest_dividend；按合并后值校验，0/负数报 INVALID_PARAM；省略不改，--json 显式 null 清空（清空后确认折出「0 现金/0 份额」空事件）"),
    reinvest_nav: Optional[float] = typer.Option(None, "--reinvest-nav", help="再投资净值(>0)；--json 显式 null 清空后改由确认时取除息日数据源净值"),
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
        share_confirm_date=share_confirm_date,
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
def delete(
    id: int = typer.Argument(..., help="事件ID"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认，未提供则拒绝执行"),
):
    """删除事件"""
    if not yes:
        error("CONFIRM_REQUIRED",
              f"删除份额事件 {id} 会物理删掉该事件及其子记录，不可逆：请追加 --yes 确认，先用 ir share-event get {id} 核实；已确认事件还须重算其生效日及之后的快照")
    client = APIClient.from_config()
    result = client.delete(f"/api/share-change-events/{id}")
    success(data=result["data"])


@app.command("preview")
def preview(
    id: int = typer.Argument(..., help="事件ID"),
    event_type: Optional[str] = typer.Option(None, "--event-type", help="分红方式：cash_dividend / reinvest_dividend；提供即完整候选，不带则沿用记录；dividend_pending 必须选择"),
    cash_pay_date: Optional[str] = typer.Option(None, "--cash-pay-date", help="现金分红到账日(YYYY-MM-DD)，仅 cash_dividend；须与 --event-type 同传，候选中省略默认除息日，不保留旧日期"),
    share_confirm_date: Optional[str] = typer.Option(None, "--share-confirm-date", help="再投资份额确认日(YYYY-MM-DD)，仅 reinvest_dividend；须与 --event-type 同传，候选中省略默认除息日，不保留旧日期"),
):
    """预览确认结果（只读）；候选参数与 confirm 相同，不保存选择。"""
    choice = _dividend_choice(event_type, cash_pay_date, share_confirm_date)
    client = APIClient.from_config()
    result = client.get(f"/api/share-change-events/{id}/preview", params=choice)
    success(data=result["data"])


@app.command("confirm")
def confirm(
    id: int = typer.Argument(..., help="事件ID"),
    sync_nav: bool = typer.Option(False, "--sync-nav", help="红利再投资除息日净值缺失(MISSING_NAV)时自动同步净值并重试一次（会访问外部数据源）"),
    event_type: Optional[str] = typer.Option(None, "--event-type", help="分红方式：cash_dividend / reinvest_dividend；提供即完整候选，不带则沿用记录；dividend_pending 必须选择"),
    cash_pay_date: Optional[str] = typer.Option(None, "--cash-pay-date", help="现金分红到账日(YYYY-MM-DD)，仅 cash_dividend；须与 --event-type 同传，候选中省略默认除息日，不保留旧日期"),
    share_confirm_date: Optional[str] = typer.Option(None, "--share-confirm-date", help="再投资份额确认日(YYYY-MM-DD)，仅 reinvest_dividend；须与 --event-type 同传，候选中省略默认除息日，不保留旧日期"),
):
    """确认事件；可直接提交完整分红候选，无需先 preview。"""
    choice = _dividend_choice(event_type, cash_pay_date, share_confirm_date)
    client = APIClient.from_config()
    params = {"sync_nav": True} if sync_nav else None
    if choice is None:
        result = client.post(f"/api/share-change-events/{id}/confirm", params=params)
    else:
        result = client.post(f"/api/share-change-events/{id}/confirm", json_data=choice, params=params)
    success(
        data=result["data"],
        hints=["确认后需生成 ex_date 日快照: ir snapshot generate --portfolio-code <code> --target-date <ex_date>；现金分红按 cash_pay_date（未设则 ex_date）到账，此前计分红在途、不计可用现金；非交易日到账由下一交易日快照消费；红利再投资设了 share_confirm_date 时份额按该确认日应用，ex_date 起至确认日前一交易日的快照计再投资应收在途、不计可用现金"],
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
