"""申购赎回管理命令组"""
import sys
import typer
from typing import Optional
from ir_cli.client import APIClient, ApiError
from ir_cli.output import error, success
from ir_cli.utils import SUMMARY_FIELDS, build_body, project_fields, resolve_body, run_list

app = typer.Typer(no_args_is_help=True)

# --quiet 时写操作仅输出的关键字段（create/confirm：后端回完整记录）
QUIET_FIELDS = "id,status,confirm_date"
# cancel/unconfirm 的后端响应只有 {message}（routers/subscriptions.py），按 QUIET_FIELDS
# 投影会得到三个 null——比报错更像真值，容易被下游当数据消费（#520）
QUIET_MESSAGE_FIELDS = "message"
# 确认后提醒：快照未生成前不计入投资人份额
SNAPSHOT_HINT = "确认后需生成确认日快照才计入投资人份额: ir snapshot generate --portfolio-code <code> --target-date <confirm_date>"


@app.command("list")
def list_subs(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码"),
    investor_code: Optional[str] = typer.Option(None, "--investor-code", help="投资人代码"),
    status: Optional[str] = typer.Option(None, "--status", help="状态(pending/confirmed/cancelled)"),
    sub_type: Optional[str] = typer.Option(None, "--type", help="类型(subscribe/redeem)"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="交易平台代码"),
    apply_date_start: Optional[str] = typer.Option(None, "--apply-date-start", help="申请日期起(YYYY-MM-DD, 闭区间)"),
    apply_date_end: Optional[str] = typer.Option(None, "--apply-date-end", help="申请日期止(YYYY-MM-DD, 闭区间)"),
    confirm_date_start: Optional[str] = typer.Option(None, "--confirm-date-start", help="确认日期起(YYYY-MM-DD, 闭区间)"),
    confirm_date_end: Optional[str] = typer.Option(None, "--confirm-date-end", help="确认日期止(YYYY-MM-DD, 闭区间)"),
    page: int = typer.Option(1, "--page", help="页码"),
    page_size: int = typer.Option(20, "--page-size", help="每页大小"),
    all_pages: bool = typer.Option(False, "--all", help="自动翻页获取全部记录"),
    fields: Optional[str] = typer.Option(None, "--fields", help="仅输出指定字段(逗号分隔)"),
    full: bool = typer.Option(False, "--full", help="输出全字段（默认仅摘要字段）"),
):
    """获取申赎列表（默认输出摘要字段，--full 全字段）

    确认日期筛选对 pending 记录命中预计确认日（创建时按 T+1 设定）。
    """
    client = APIClient.from_config()
    params = build_body(
        portfolio_code=portfolio_code, investor_code=investor_code,
        status=status, sub_type=sub_type, platform_code=platform_code,
        apply_date_start=apply_date_start, apply_date_end=apply_date_end,
        confirm_date_start=confirm_date_start, confirm_date_end=confirm_date_end,
    )
    run_list(
        client, "/api/subscriptions", params,
        page=page, page_size=page_size, all_pages=all_pages,
        fields=fields, default_fields=SUMMARY_FIELDS["subscription"], full=full,
    )


@app.command("create")
def create(
    portfolio_code: Optional[str] = typer.Option(None, "--portfolio-code", help="组合代码(必填)"),
    investor_code: Optional[str] = typer.Option(None, "--investor-code", help="投资人代码(必填)"),
    sub_type: Optional[str] = typer.Option(None, "--type", help="类型(subscribe/redeem)(必填)"),
    apply_date: Optional[str] = typer.Option(None, "--apply-date", help="申请日期(YYYY-MM-DD)(必填)"),
    amount: Optional[float] = typer.Option(None, "--amount", help="金额(申购用)"),
    shares: Optional[float] = typer.Option(None, "--shares", help="份额(赎回用)"),
    unit_price: Optional[float] = typer.Option(None, "--unit-price", help="净值"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="交易平台代码(必填)"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
    auto_confirm: bool = typer.Option(False, "--confirm", help="创建成功后立即确认（快捷组合）"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 id/status/confirm_date"),
):
    """创建申赎申请（--confirm 可链式创建+确认）

    \b
    示例:
      ir sub create --portfolio-code PORT001 --investor-code INV001 --type subscribe --amount 10000 --apply-date 2026-06-05 --platform-code ALIPAY
    """
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        required=("portfolio_code", "investor_code", "sub_type", "apply_date", "platform_code"),
        portfolio_code=portfolio_code,
        investor_code=investor_code,
        sub_type=sub_type,
        apply_date=apply_date,
        amount=amount,
        shares=shares,
        unit_price=unit_price,
        platform_code=platform_code,
        notes=notes,
    )
    result = client.post("/api/subscriptions", json_data=body)
    created = result["data"]
    if auto_confirm and isinstance(created, dict) and created.get("id"):
        # 确认失败时 stdout 仍为单个错误 JSON，但携带已创建的 id，避免重复创建（issue #72）
        print(f"[info] 申赎已创建 id={created['id']}，正在确认...", file=sys.stderr)
        try:
            result = client.post(f"/api/subscriptions/{created['id']}/confirm", raise_errors=True)
        except ApiError as e:
            error(
                e.code,
                e.message,
                details={**(e.details or {}), "created_subscription_id": created["id"]},
                hints=[f"申赎已创建未确认，勿重复创建；修复问题后执行: ir sub confirm {created['id']}"],
            )
    data = result["data"]
    hints = None
    if isinstance(data, dict):
        if data.get("status") == "pending":
            hints = [f"ir sub confirm {data.get('id')}"]
        elif data.get("status") == "confirmed":
            hints = [SNAPSHOT_HINT]
    success(data=project_fields(data, QUIET_FIELDS) if quiet else data, hints=hints)


@app.command("get")
def get(id: int = typer.Argument(..., help="申赎ID")):
    """获取申赎详情"""
    client = APIClient.from_config()
    result = client.get(f"/api/subscriptions/{id}")
    success(data=result["data"])


@app.command("confirm")
def confirm(
    id: int = typer.Argument(..., help="申赎ID"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 id/status/confirm_date"),
):
    """确认申赎"""
    client = APIClient.from_config()
    result = client.post(f"/api/subscriptions/{id}/confirm")
    data = result["data"]
    success(data=project_fields(data, QUIET_FIELDS) if quiet else data, hints=[SNAPSHOT_HINT])


@app.command("cancel")
def cancel(
    id: int = typer.Argument(..., help="申赎ID"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 message（该端点只回 message）"),
):
    """取消申赎"""
    client = APIClient.from_config()
    result = client.post(f"/api/subscriptions/{id}/cancel")
    data = result["data"]
    success(data=project_fields(data, QUIET_MESSAGE_FIELDS) if quiet else data)


@app.command("unconfirm")
def unconfirm(
    id: int = typer.Argument(..., help="申赎ID"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出 message（该端点只回 message）"),
):
    """取消确认"""
    client = APIClient.from_config()
    result = client.post(f"/api/subscriptions/{id}/unconfirm")
    data = result["data"]
    success(data=project_fields(data, QUIET_MESSAGE_FIELDS) if quiet else data)


@app.command("update")
def update(
    id: int = typer.Argument(..., help="申赎ID"),
    amount: Optional[float] = typer.Option(None, "--amount", help="金额"),
    shares: Optional[float] = typer.Option(None, "--shares", help="份额"),
    unit_price: Optional[float] = typer.Option(None, "--unit-price", help="净值"),
    platform_code: Optional[str] = typer.Option(None, "--platform-code", help="平台代码"),
    apply_date: Optional[str] = typer.Option(None, "--apply-date", help="申请日期(YYYY-MM-DD, 须为交易日且晚于最新快照日)"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    json_body: Optional[str] = typer.Option(None, "--json", help="完整 JSON 请求体，优先于逐项参数"),
):
    """更新申赎（仅 pending 可改，confirmed 需先 unconfirm；改申请日时后端自动重算预计确认日）"""
    client = APIClient.from_config()
    body = resolve_body(
        json_body,
        amount=amount,
        shares=shares,
        unit_price=unit_price,
        platform_code=platform_code,
        apply_date=apply_date,
        notes=notes,
    )
    if not body:
        error("VALIDATION_ERROR", "未提供任何更新字段")
    result = client.put(f"/api/subscriptions/{id}", json_data=body)
    success(data=result["data"])


@app.command("delete")
def delete(id: int = typer.Argument(..., help="申赎ID")):
    """删除申赎（仅 pending 可删，confirmed 需先 unconfirm）"""
    client = APIClient.from_config()
    result = client.delete(f"/api/subscriptions/{id}")
    success(data=result["data"])
