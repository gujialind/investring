"""调仓交易管理命令组"""
import sys
import typer
from typing import Optional
from ir_cli.client import APIClient, ApiError
from ir_cli.output import error, success
from ir_cli.utils import SUMMARY_FIELDS, build_body, project_fields, resolve_body, run_list

app = typer.Typer(no_args_is_help=True)

# --quiet 时写操作仅输出的关键字段（create/confirm：后端回完整记录）
QUIET_FIELDS = "id,status,confirm_date"
# cancel/unconfirm 的后端响应只有 {message}（routers/trades.py），按 QUIET_FIELDS 投影
# 会得到三个 null——比报错更像真值，容易被下游当数据消费（#520）。这类命令单独投影
# message，不替后端臆造它没返回的状态。
QUIET_MESSAGE_FIELDS = "message"
# 确认后提醒：快照未生成前不计入持仓；confirmed ≠ 现金当天可用（#493）
SNAPSHOT_HINT = "确认后需生成确认日快照才计入持仓: ir snapshot generate --portfolio-code <code> --target-date <confirm_date>；confirmed 只表示已记账，不等于现金当天可用"
# 买入创建即扣款、基金份额待确认（#493）：避免把 pending 基金腿误当成「钱还没划走」
BUY_CREATED_HINT = "买入创建即记扣款（配对 CASH sell 腿已 confirmed，现金日 = 下单日），基金份额仍待确认；这份扣款不可重复下单"
# 卖出创建期不接受任何到账信息（#493）：到账平台/到账日在 confirm 时录入
SELL_ARRIVAL_HINT = (
    "卖出创建期不接受到账信息；确认时用 ir trade confirm <id> "
    "--cash-platform-code <到账平台> --cash-confirm-date <到账日> 录入（缺省 A=C、同基金腿平台），"
    "到账日之前的快照该笔记 IN_TRANSIT_SELL"
)
# 调仓不再自动确认（#493 决策 6 / #471）：到期未确认会阻断快照推进，需人工确认
MANUAL_CONFIRM_HINT = "调仓不参与快照自动确认，到期未确认会阻断快照推进，需人工 confirm"


@app.command("list")
def list_trades(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码"),
    status: Optional[str] = typer.Option(None, "--status", help="状态(pending/confirmed/cancelled)"),
    trade_type: Optional[str] = typer.Option(None, "--type", help="类型(buy/sell)"),
    product_code: Optional[str] = typer.Option(None, "--product-code", help="产品代码（单独使用时跨市场全匹配）"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型（与 --product-code 组合为精确过滤）"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="交易平台代码"),
    trade_date_start: Optional[str] = typer.Option(None, "--trade-date-start", help="交易日期起(YYYY-MM-DD, 闭区间)"),
    trade_date_end: Optional[str] = typer.Option(None, "--trade-date-end", help="交易日期止(YYYY-MM-DD, 闭区间)"),
    confirm_date_start: Optional[str] = typer.Option(None, "--confirm-date-start", help="确认日期起(YYYY-MM-DD, 闭区间)"),
    confirm_date_end: Optional[str] = typer.Option(None, "--confirm-date-end", help="确认日期止(YYYY-MM-DD, 闭区间)"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
    full: bool = typer.Option(False, "--full", help="输出全字段（默认仅摘要字段）"),
):
    """获取交易列表（默认输出摘要字段，--full 全字段）

    确认日期筛选对 pending 记录命中预计确认日（创建时按产品 confirm_days 设定）。
    基金腿响应带只读派生 cash_platform_code/cash_confirm_date（买=扣款平台/下单日，
    卖=CASH 到账腿的平台/到账日）；无配对现金腿（如待确认卖出）时为 null，
    CASH 腿自身不回填。
    """
    client = APIClient.from_config()
    params = build_body(
        portfolio_code=portfolio_code, status=status, trade_type=trade_type,
        product_code=product_code, market=market, platform_code=platform_code,
        trade_date_start=trade_date_start, trade_date_end=trade_date_end,
        confirm_date_start=confirm_date_start, confirm_date_end=confirm_date_end,
    )
    run_list(
        client, "/api/trades", params,
        page=page, page_size=page_size, all_pages=all_pages,
        fields=fields, default_fields=SUMMARY_FIELDS["trade"], full=full,
    )


@app.command("create")
def create(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码(必填)"),
    product_code: Optional[str] = typer.Option(None, "--product-code", help="产品代码(必填)"),
    trade_type: Optional[str] = typer.Option(None, "--type", help="类型(buy/sell)(必填)"),
    trade_date: Optional[str] = typer.Option(None, "--trade-date", help="交易日期(YYYY-MM-DD)(必填)"),
    actual_amount: Optional[float] = typer.Option(None, "--actual-amount", help="实际金额"),
    fee: float = typer.Option(0, "--fee", help="手续费"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码(必填)"),
    cash_platform_code: Optional[str] = typer.Option(None, "--cash-platform-code", help="扣款平台（仅买入）：缺省同基金腿平台；卖出到账平台在 confirm 传"),
    market: Optional[str] = typer.Option(None, "--market", help="市场类型（省略时自动解析；LOF 多市场须显式指定）"),
    price: Optional[float] = typer.Option(None, "--price", help="价格"),
    shares: Optional[float] = typer.Option(None, "--shares", help="份额"),
    amount: Optional[float] = typer.Option(None, "--amount", help="金额"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
    allow_duplicate: bool = typer.Option(False, "--allow-duplicate", help="跳过重复交易检测（后端报 DUPLICATE_TRADE 且确需重复录入时使用）"),
    auto_confirm: bool = typer.Option(False, "--confirm", help="创建成功后立即确认（快捷组合；卖出到账缺省 A=C、同基金腿平台）"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 id/status/confirm_date"),
):
    """创建交易（--confirm 可链式创建+确认）

    \b
    买入：创建即扣款（CASH sell 腿直接 confirmed、现金日 = 下单日 T），基金份额待确认；
          --cash-platform-code 指定扣款平台。
    卖出：创建只建基金腿、不接受到账信息，确认时用 confirm --cash-platform-code/--cash-confirm-date
          录入到账平台与到账日（缺省 A=C、同基金腿平台）；需要自定义到账信息时走「先 create 再 confirm」两步。

    \b
    示例:
      ir trade create --portfolio-code PORT001 --product-code 022959.OF --type buy --amount 10000 --trade-date 2026-06-05 --platform-code ALIPAY
    """
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("portfolio_code", "product_code", "trade_type", "trade_date", "platform_code"),
        portfolio_code=portfolio_code,
        product_code=product_code,
        trade_type=trade_type,
        trade_date=trade_date,
        actual_amount=actual_amount,
        fee=fee,
        platform_code=platform_code,
        cash_platform_code=cash_platform_code,
        market=market,
        price=price,
        shares=shares,
        amount=amount,
        notes=notes,
    )
    # 卖出创建期不接受到账**平台**（#493）：与后端 CASH_PLATFORM_NOT_ALLOWED 同码同语义，
    # 前置拦截省一次往返，并把「改在 confirm 传」的入口直接写进 hints。
    # 只拦平台：到账**日期**没有对应的 CLI 选项，仅可能经 --json 漏入，由后端
    # CASH_CONFIRM_DATE_NOT_ALLOWED 兜底（同为创建期拒绝，无正确性差异）。
    if body.get("trade_type") == "sell" and body.get("cash_platform_code"):
        platform = body["cash_platform_code"]
        error(
            "CASH_PLATFORM_NOT_ALLOWED",
            "卖出交易在创建时不能指定到账平台（--cash-platform-code 创建时仅供买入扣款）",
            hints=[
                f"去掉 --cash-platform-code 重新执行 create 拿到 id 后，在确认时传入: "
                f"ir trade confirm <id> --cash-platform-code {platform}"
            ],
        )
    if allow_duplicate:
        body["allow_duplicate"] = True
    result = client.post("/api/trades", json_data=body)
    created = result["data"]
    if auto_confirm and isinstance(created, dict) and created.get("id"):
        # 确认失败时 stdout 仍为单个错误 JSON，但携带已创建的 id，避免重复创建（issue #72）。
        # #493 起买入创建即扣款，误重建 = 重复扣款，该 id 是必须的逃生口。
        print(f"[info] 交易已创建 id={created['id']}，正在确认...", file=sys.stderr)
        try:
            result = client.post(f"/api/trades/{created['id']}/confirm", raise_errors=True)
        except ApiError as e:
            error(
                e.code,
                e.message,
                details={**(e.details or {}), "created_trade_id": created["id"]},
                hints=[f"交易已创建未确认，勿重复创建；修复问题后执行: ir trade confirm {created['id']}"],
            )
    data = result["data"]
    hints = None
    if isinstance(data, dict):
        if data.get("status") == "pending":
            # 买入：pending 的是基金腿，扣款腿在创建时已 confirmed
            hints = [
                f"ir trade confirm {data.get('id')}",
                BUY_CREATED_HINT if data.get("trade_type") == "buy" else SELL_ARRIVAL_HINT,
                MANUAL_CONFIRM_HINT,
            ]
        elif data.get("status") == "confirmed":
            hints = [SNAPSHOT_HINT, MANUAL_CONFIRM_HINT]
    success(data=project_fields(data, QUIET_FIELDS) if quiet else data, hints=hints)


@app.command("get")
def get(id: int = typer.Argument(..., help="交易ID")):
    """获取交易详情"""
    client = APIClient.from_config()
    result = client.get(f"/api/trades/{id}")
    success(data=result["data"])


@app.command("preview")
def preview(
    id: int = typer.Argument(..., help="交易ID"),
    confirm_date: Optional[str] = typer.Option(None, "--confirm-date", help="确认日期(YYYY-MM-DD)"),
    price: Optional[float] = typer.Option(None, "--price", help="确认价格"),
    cash_platform_code: Optional[str] = typer.Option(None, "--cash-platform-code", help="到账平台（仅卖出；预览本次有效到账平台）"),
    cash_confirm_date: Optional[str] = typer.Option(None, "--cash-confirm-date", help="到账日（仅卖出；缺省 A=C）"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 preview/paired_cash_amount"),
):
    """确认前预览：返回真实确认将写入的净值/份额/金额与有效现金平台/日期，不落库。

    需要后端支持 GET /api/trades/{id}/preview（issue #65 后的版本）。
    仅 pending 状态可预览；场外基金 T 日净值缺失时返回 MISSING_NAV。
    卖出可用 --cash-platform-code/--cash-confirm-date 预览本次到账平台与到账日，
    确认时将按同一组输入写入（preview 零写入）。
    """
    client = APIClient.from_config()
    params = {}
    if confirm_date is not None:
        params["confirm_date"] = confirm_date
    if price is not None:
        params["price"] = price
    if cash_platform_code is not None:
        params["cash_platform_code"] = cash_platform_code
    if cash_confirm_date is not None:
        params["cash_confirm_date"] = cash_confirm_date
    result = client.get(f"/api/trades/{id}/preview", params=params)
    data = result["data"]
    hints = [
        "预览为时点快照，实际以确认时为准",
        f"核对无误后执行: ir trade confirm {id}",
    ]
    # data 为嵌套结构 {trade, preview, paired_cash_amount}，--quiet 时裁掉冗长的 trade 全字段
    success(
        data=project_fields(data, "preview,paired_cash_amount") if quiet else data,
        hints=hints,
    )


@app.command("confirm")
def confirm(
    id: int = typer.Argument(..., help="交易ID"),
    confirm_date: Optional[str] = typer.Option(None, "--confirm-date", help="确认日期(YYYY-MM-DD)"),
    price: Optional[float] = typer.Option(None, "--price", help="确认价格"),
    sync_nav: bool = typer.Option(False, "--sync-nav", help="MISSING_NAV 时自动回填净值并重试"),
    cash_platform_code: Optional[str] = typer.Option(None, "--cash-platform-code", help="到账平台（仅卖出；缺省同基金腿平台）"),
    cash_confirm_date: Optional[str] = typer.Option(None, "--cash-confirm-date", help="到账日（仅卖出；缺省 A=C，即基金确认日当天到账）"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 id/status/confirm_date"),
):
    """确认交易（卖出在此录入到账平台/到账日，买入扣款在创建时已固定）。

    卖出：新建配对 CASH buy 到账腿（confirmed，trade_date = 基金确认日 C、
    confirm_date = 到账日 A）；A 缺省 = C，到账平台缺省 = 基金腿平台。
    若 A > C，C 起到账日前的快照该笔记 IN_TRANSIT_SELL，到账日起转为 CASH。
    买入：--cash-platform-code/--cash-confirm-date 不接受改写（扣款日固定下单日 T）。
    """
    client = APIClient.from_config()
    params = {}
    if confirm_date is not None:
        params["confirm_date"] = confirm_date
    if price is not None:
        params["price"] = price
    if sync_nav:
        params["sync_nav"] = True
    if cash_platform_code is not None:
        params["cash_platform_code"] = cash_platform_code
    if cash_confirm_date is not None:
        params["cash_confirm_date"] = cash_confirm_date
    result = client.post(f"/api/trades/{id}/confirm", params=params)
    data = result["data"]
    success(data=project_fields(data, QUIET_FIELDS) if quiet else data, hints=[SNAPSHOT_HINT])


@app.command("cancel")
def cancel(
    id: int = typer.Argument(..., help="交易ID"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 message（该端点只回 message）"),
):
    """取消交易。

    约束：仅场外（CN_OTC）pending 状态基金腿可取消；场内交易当天确认，不可取消。
    整组回退（含配对 CASH 腿）；组内任一腿确认日及之后已有快照则拒绝，
    返回 SNAPSHOT_DEPENDENCY，需先删除对应快照。
    """
    client = APIClient.from_config()
    result = client.post(f"/api/trades/{id}/cancel")
    data = result["data"]
    success(data=project_fields(data, QUIET_MESSAGE_FIELDS) if quiet else data)


@app.command("unconfirm")
def unconfirm(
    id: int = typer.Argument(..., help="交易ID"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 message（该端点只回 message）"),
):
    """取消确认交易。

    约束：仅 confirmed 状态可取消确认；组内任一腿确认日及之后已有快照，
    返回 SNAPSHOT_DEPENDENCY，需先删除对应快照。
    买入：基金腿回 pending，配对 CASH 扣款腿**保持 confirmed**（扣款既成事实，日期金额不动）。
    卖出：基金腿回 pending，配对 CASH 到账腿**被删除**（回到「未创建」态，
    再次确认时按新的到账平台/到账日重建）。
    """
    client = APIClient.from_config()
    result = client.post(f"/api/trades/{id}/unconfirm")
    data = result["data"]
    success(data=project_fields(data, QUIET_MESSAGE_FIELDS) if quiet else data)


@app.command("update")
def update(
    id: int = typer.Argument(..., help="交易ID"),
    shares: Optional[float] = typer.Option(None, "--shares", help="份额"),
    amount: Optional[float] = typer.Option(None, "--amount", help="金额"),
    price: Optional[float] = typer.Option(None, "--price", help="价格"),
    fee: Optional[float] = typer.Option(None, "--fee", help="手续费"),
    actual_amount: Optional[float] = typer.Option(None, "--actual-amount", help="实际金额"),
    trade_date: Optional[str] = typer.Option(None, "--trade-date", help="交易日期(YYYY-MM-DD)"),
    cash_confirm_date: Optional[str] = typer.Option(None, "--cash-confirm-date", help="到账日修正（仅已确认卖出，可配合 --notes）"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):

    """更新交易（仅 pending 状态可改，confirmed 需先 unconfirm；cancelled 不可改）。

    pending：改动 trade_date/金额/份额会同步配对扣款腿；confirm_date 不开放直改，
    补录覆盖请用 confirm --confirm-date；pending 不接受 --cash-confirm-date
    （买入扣款日固定下单日 T、卖出到账日在确认时录入，后端报 INVALID_PARAM）。
    已确认卖出：唯一窄例外是 --cash-confirm-date 修正到账日（可同时改 --notes），
    混入其他字段整体拒绝；组内任一腿确认日及之后已有快照则拒绝（先删快照）。
    """
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        shares=shares,
        amount=amount,
        price=price,
        fee=fee,
        actual_amount=actual_amount,
        trade_date=trade_date,
        cash_confirm_date=cash_confirm_date,
        notes=notes,
    )
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/trades/{id}", json_data=body)
    success(data=result["data"])


@app.command("delete")
def delete(id: int = typer.Argument(..., help="交易ID")):
    """删除交易（仅 pending/cancelled 基金腿可删，confirmed 需先 unconfirm）。

    整组级联删除同一 transfer_group 的配对 CASH 腿；组内任一腿确认日及之后
    已有快照则拒绝（先删快照）。调仓 CASH 腿不可直接删除，只能由基金腿驱动。
    """
    client = APIClient.from_config()
    result = client.delete(f"/api/trades/{id}")
    success(data=result["data"])
