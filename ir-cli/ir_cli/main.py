"""
InvestRing CLI - HTTP Client 入口

轻量 HTTP 客户端版 CLI，通过 REST API 与后端通信。
仅需 typer + httpx 两个依赖，可在任意设备上安装使用。
"""
import importlib
import sys
from typing import Optional

import typer
import typer.core

from ir_cli.output import EXIT_USAGE, error


def _click_exc_class(name: str) -> Optional[type]:
    """取 Click 的异常类（UsageError / NoArgsIsHelpError）。

    typer >= 0.25 把 click 复制进 `typer._click` 且不再声明 click 依赖，抛出的异常与
    顶层 `click` 包里的同名类**不是同一个类型**（实测 `click.UsageError is
    typer._click.exceptions.UsageError` → False），`except click.UsageError` 抓不到；
    typer < 0.25 反过来只有 `click` 可用。按顺序探测两边，都取不到返回 None，
    届时入口退回 Click 缺省行为（人读文本 + exit 2），不报错。
    """
    for module_name in ("typer._click.exceptions", "click.exceptions"):
        try:
            return getattr(importlib.import_module(module_name), name)
        except (ImportError, AttributeError):
            continue
    return None


# 取不到类时退化成空元组：`except ()` 合法且永不匹配，等价于不接管
_USAGE_ERROR = _click_exc_class("UsageError") or ()
_NO_ARGS_IS_HELP = _click_exc_class("NoArgsIsHelpError") or ()


class IrTyperGroup(typer.core.TyperGroup):
    """根命令组：把 Click 的用法错误收敛成机读 JSON + exit 64（#520）。

    为什么要以 `standalone_mode=False` 复跑 `super().main()`：standalone 模式下 Click
    自己吞掉 UsageError，只往 stderr 打一行人读的 `Error: No such option: ...` 再
    exit 2，异常不会浮到我们能接手的地方。而 2 在协议里是「认证错误」，脚本/agent 会
    去跑 `ir auth login`，永远修不好拼错的选项。接管后未知选项/缺参数与后端业务错误
    同构：stdout 一条 JSON、退出码独立取 sysexits.h 的 64。

    只挂在根组就够：19 个子命令组的解析异常都在同一次 main() 调用栈内抛出。
    """

    def main(
        self,
        args=None,
        prog_name=None,
        complete_var=None,
        standalone_mode=True,
        **extra,
    ):
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                **extra,
            )
        except _USAGE_ERROR as exc:
            if isinstance(exc, _NO_ARGS_IS_HELP):
                # 无参数 = 打印帮助（Click 缺省：stderr + exit 2），不是用法错误
                exc.show()
                sys.exit(exc.exit_code)
            error("USAGE_ERROR", exc.format_message(), exit_code=EXIT_USAGE)
        except typer.Abort:
            # Ctrl-C / 交互中断：与 Click standalone 行为一致
            typer.echo("Aborted!", err=True)
            sys.exit(1)
        if not standalone_mode:
            return result
        # standalone_mode=False 下 --help 等路径由 Click 返回退出码，而不是自行 sys.exit
        sys.exit(result if isinstance(result, int) else 0)


_PROTOCOL_HELP = """InvestRing CLI - HTTP Client

输出协议: 成功 {"ok":true,"data":...,"meta"?,"hints"?} / 失败 {"ok":false,"error":{"code","message","hints"?}}

退出码: 0=成功 1=业务错误(可换参重试) 2=认证错误(ir auth login) 3=连接/超时 64=用法错误(选项/参数不存在，命令未执行)

诊断信息输出至 stderr，脚本/Agent 解析请只读取 stdout

通用约定: --json 直传请求体 | --fields 裁剪输出 | --all 自动翻页 | --full 全字段 | --quiet 精简输出

执行 `ir schema` 一次性获取全部命令/参数/枚举/错误码的机读 JSON 结构。
"""

app = typer.Typer(
    name="ir",
    help=_PROTOCOL_HELP,
    no_args_is_help=True,
    rich_markup_mode=None,  # plain help 输出（无框线/ANSI），降低 AI agent token 消耗；子命令组继承此设置
    cls=IrTyperGroup,
)


def _version_callback(value: bool):
    if value:
        from importlib.metadata import PackageNotFoundError, version

        from ir_cli.output import success

        try:
            ver = version("investring-cli")
        except PackageNotFoundError:
            ver = "unknown"  # 未安装包直接源码运行时
        success(data={"name": "investring-cli", "version": ver})


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True,
        help="显示版本号并退出",
    ),
):
    pass


# 注册 19 个命令组
from ir_cli.commands import (
    asset_classifications,
    auth,
    config_cmd,
    investors,
    portfolios,
    positions,
    subscriptions,
    trades,
    share_events,
    market_data,
    products,
    platforms,
    system,
    logs,
    tasks,
    snapshots,
    cash_transfers,
    sync_jobs,
    notifications,
)

app.add_typer(auth.app, name="auth", help="认证管理")
app.add_typer(config_cmd.app, name="config", help="本地配置管理")
app.add_typer(investors.app, name="investor", help="投资人管理")
app.add_typer(portfolios.app, name="portfolio", help="组合管理")
app.add_typer(positions.app, name="position", help="持仓管理")
app.add_typer(subscriptions.app, name="sub", help="申购赎回管理")
app.add_typer(trades.app, name="trade", help="调仓交易管理")
app.add_typer(share_events.app, name="share-event", help="份额变动事件管理")
app.add_typer(market_data.app, name="market", help="市场数据")
app.add_typer(products.app, name="product", help="产品管理")
app.add_typer(platforms.app, name="platform", help="平台管理")
app.add_typer(system.app, name="system", help="系统管理")
app.add_typer(logs.app, name="log", help="日志管理")
app.add_typer(tasks.app, name="task", help="任务管理")
app.add_typer(snapshots.app, name="snapshot", help="快照管理")
app.add_typer(cash_transfers.app, name="cash-transfer", help="现金转移管理")
app.add_typer(sync_jobs.app, name="sync-job", help="同步任务管理")
app.add_typer(notifications.app, name="notification", help="通知管理")
app.add_typer(asset_classifications.app, name="asset-classification", help="资产分类维度字典管理")


@app.command("schema")
def schema(
    group: Optional[str] = typer.Argument(None, help="仅输出指定命令组（如 trade）"),
    index: bool = typer.Option(
        False, "--index",
        help="仅输出极简命令索引（<1KB）；与命令组参数互斥，同时传报 VALIDATION_ERROR",
    ),
):
    """输出全 CLI 机读结构（命令/参数/枚举/错误码/输出协议/响应字段契约），供 AI agent 一次性了解全部指令"""
    from ir_cli.output import error, success
    from ir_cli.schema import build_schema, is_group

    if index and group:
        error("VALIDATION_ERROR", "--index 与命令组参数互斥：先 ir schema --index 拿索引，再 ir schema <group> 按组加载")
    root = typer.main.get_command(app)
    try:
        result = build_schema(root, group, index_only=index)
    except KeyError:
        groups = sorted(name for name, cmd in root.commands.items() if is_group(cmd))
        error("VALIDATION_ERROR", f"命令组 '{group}' 不存在，可用命令组: {', '.join(groups)}")
    success(data=result)
