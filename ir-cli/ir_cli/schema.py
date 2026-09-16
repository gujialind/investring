"""
CLI 自描述结构生成

通过 click 反射把整个命令树（命令/参数/枚举/错误码/输出协议）导出为紧凑 JSON，
供 AI agent 通过一次 `ir schema` 调用掌握全部指令，替代逐个 --help 探索。

注意：typer 高版本内嵌自带 click（typer._click），不能用 isinstance(cmd, click.Group)
判断，统一用鸭子类型（commands 属性 / param_type_name）。
"""
from typing import Any, Optional

from ir_cli.hints import ERROR_HINTS
from ir_cli.response_fields import RESPONSE_FIELDS
from ir_cli.utils import ENUMS

# 输出协议与通用约定（与 output.py / client.py / utils.py 保持一致）
PROTOCOL = {
    "output": '成功 {"ok":true,"data":...,"meta"?:...,"hints"?:[...]}; 失败 {"ok":false,"error":{"code","message","details"?,"hints"?}}',
    "exit_codes": {
        "0": "成功",
        "1": "业务错误（可换参数重试）",
        "2": "认证错误（需 ir auth login）",
        "3": "连接/超时错误（可原样重试或检查服务）",
    },
}

CONVENTIONS = {
    "--json": "create/update 类命令可传完整 JSON 请求体，优先于逐项参数",
    "--fields": "list 类命令按逗号分隔字段名裁剪输出",
    "--all": "list 类命令自动翻页获取全部记录",
    "--full": "list 类命令输出全字段（默认仅摘要字段）",
    "--quiet": "trade/sub 写操作仅输出 {id,status,confirm_date}",
    "output.fields": "命令条目 output.fields 为响应字段契约：`*`前缀=默认摘要字段，`?`后缀=可空；notes 含字段级警示（如恒为null的字段）",
    "--index": "ir schema --index 输出极简命令索引（<1KB），再按 ir schema <group> 精确加载，较全量省约59% token",
    "env": ["IR_BASE_URL", "IR_TOKEN", "IR_CONNECT_TIMEOUT", "IR_HTTP_TIMEOUT", "IR_RETRY", "IR_DEBUG"],
}

# 端到端业务配方：多步命令序列 + 关键前置条件，补充 error_hints 无法覆盖的流程知识
WORKFLOWS = {
    "操作前侦察": {
        "steps": ["ir portfolio context <code>"],
        "notes": "一次返回组合详情/最新快照日/可用现金/pending 申赎交易；任何写操作前先执行",
    },
    "申购入金": {
        "steps": [
            "ir sub create --portfolio-code X --investor-code I --type subscribe --amount N --apply-date D --platform-code P",
            "ir sub confirm <id>",
            "ir snapshot generate --portfolio-code X --target-date <confirm_date>",
        ],
        "notes": "apply_date 须为交易日且晚于最新快照日；首次申购净值固定 1.0000 并自动激活组合；确认自动生成配对 CASH trade",
    },
    "赎回出金": {
        "steps": [
            "ir sub create --portfolio-code X --investor-code I --type redeem --shares N --apply-date D --platform-code P",
            "ir sub confirm <id>",
            "ir snapshot generate --portfolio-code X --target-date <confirm_date>",
        ],
        "notes": "赎回输入份额（金额=份额×申请日净值）；投资人可用份额由服务端实时校验，超额报 INSUFFICIENT_SHARES",
    },
    "调仓买入": {
        "steps": [
            "ir position available-cash --portfolio-code X（按扣款平台）",
            "ir trade create --portfolio-code X --product-code F --type buy --trade-date D --actual-amount N [--cash-platform-code P 扣款平台] [--price P 场内必填]",
            "ir trade confirm <id>（到 confirm_date 当日执行，场外需 T 日净值已同步）",
            "ir snapshot generate --portfolio-code X --target-date <confirm_date>",
        ],
        "notes": "创建即扣款：配对 CASH sell 腿直接 confirmed、现金日 = 下单日 T，基金腿 pending 待确认；D 日快照不被 pending 阻断，扣款等额记 IN_TRANSIT_BUY，直到基金腿确认；confirmed ≠ 现金当天可用",
    },
    "调仓卖出": {
        "steps": [
            "ir position available-shares --portfolio-code X --product-code F（卖出前）",
            "ir trade create --portfolio-code X --product-code F --type sell --trade-date D --shares N [--price P 场内必填]（创建期不接受到账信息）",
            "ir trade preview <id> --cash-platform-code P --cash-confirm-date A（可选，核对到账平台/到账日）",
            "ir trade confirm <id> --cash-platform-code P --cash-confirm-date A（到账日缺省 = 基金确认日 C，到账平台缺省同基金腿）",
            "ir snapshot generate --portfolio-code X --target-date <confirm_date>",
        ],
        "notes": "创建只建基金腿；到账平台/到账日在 confirm 录入（create 传入报 CASH_PLATFORM_NOT_ALLOWED / CASH_CONFIRM_DATE_NOT_ALLOWED）；确认时新建 CASH buy 到账腿 confirmed、trade_date = C、confirm_date = A，A > C 时 C 起到账日前的快照记 IN_TRANSIT_SELL；到账日修正走 ir trade update <id> --cash-confirm-date A（仅已确认卖出，可配合 --notes，组内任一腿确认日已有快照先删快照）",
    },
    "补录历史交易": {
        "steps": [
            "ir snapshot delete-bulk <code> <最早影响日> --yes（若历史日已有快照）",
            "ir trade create --trade-date <历史日> ... 或 ir sub create ...",
            "ir trade confirm <id> --confirm-date <实际确认日>",
            "ir snapshot recalculate --start-date <删除起始日> --end-date <今日> --portfolio-code <code>",
        ],
        "notes": "快照只能从尾部删除（连续原则）；recalculate 逐交易日重建并自动重确认当日记录",
    },
    "快照回退重算": {
        "steps": [
            "ir snapshot status <code>（确认当前快照范围）",
            "ir snapshot delete-bulk <code> <from_date> --dry-run（先预览将删除的快照日期）",
            "ir snapshot delete-bulk <code> <from_date> --yes",
            "修改/补录相关记录",
            "ir snapshot recalculate --start-date <from_date> --end-date <最新交易日> --portfolio-code <code>",
        ],
        "notes": "删除自动级联回退：当日确认的申购/事件退回 pending；不可只删中间某日快照",
    },
    "跨平台现金转移": {
        "steps": [
            "ir cash-transfer create --portfolio-code X --from P1 --to P2 --amount N --date D [--cross-day]",
            "ir cash-transfer confirm <transfer_group> --portfolio-code X（仅跨天：到账日执行）",
            "ir snapshot generate ...",
        ],
        "notes": "当天到账两腿立即 confirmed；跨天两腿均 pending，在途期间不计入任何平台可用现金",
    },
    "份额变动事件": {
        "steps": [
            "ir share-event create --event-type <type> --entitlement-date D1 --ex-date D2 ...",
            "确保 entitlement_date 当日快照已存在",
            "ir share-event confirm <id>",
            "ir snapshot generate --target-date <ex_date>",
        ],
        "notes": "ex_date > entitlement_date 且均为交易日；基金级事件（拆分/合并/送股）不传 platform_code，平台级（分红等）必传",
    },
    "快照追平": {
        "steps": [
            "ir snapshot status <code>（查看最新快照日与落后天数）",
            "ir snapshot catch-up --portfolio-code X --to-date <目标日>（批量逐日追平）或 ir snapshot generate-next --portfolio-code X（只推进一个交易日）",
        ],
        "notes": "catch-up 从最新快照日的次一交易日逐日生成到 to_date；组合无任何快照时报 NO_SNAPSHOT_BASELINE，先用 snapshot generate 建首日基线",
    },
    "交易日查询": {
        "steps": [
            "ir system trading-day is-open <date>（判断是否交易日）",
            "ir system trading-day next --from-date D [--days N] / prev --from-date D [--days N]（推算前后第 N 个交易日）",
        ],
        "notes": "用于确定 apply_date/trade_date/confirm_date；年份日历缺失报 CALENDAR_NOT_SYNCED，先 ir system calendar-sync --year <year>",
    },
    "任务诊断": {
        "steps": [
            "ir task list（总览各任务启用状态与最近运行结果）",
            "ir task describe <code>（单任务详情：cron/最近执行/失败原因）",
            "ir task logs <code>（分页执行日志）",
        ],
        "notes": "nav_sync 失败会导致净值中断（快照生成由独立的 snapshot_generate 任务承担），排查后可 ir task run <code> 手动补跑",
    },
    "LOF市场歧义处理": {
        "steps": [
            "ir product get <code> 或 ir trade create 不带 --market（一码一市场时自动解析）",
            "报 MARKET_AMBIGUOUS 时，从 error.details.available_markets 选择并显式加 --market 重试",
        ],
        "notes": "LOF 等一码多市场产品必须显式指定 --market（CN_EX 场内 / CN_OTC 场外），两者净值与确认规则不同",
    },
}


def _param_entry(param: Any) -> Optional[dict]:
    """单个参数 → 紧凑 dict；--help 等内置参数返回 None"""
    if param.name in ("help",):
        return None
    if param.param_type_name == "option":
        # 取最长的选项名（如 --portfolio-code）
        opt = max(param.opts, key=len)
        entry = {"opt": opt, "type": param.type.name.upper()}
        if getattr(param, "is_flag", False):
            entry["type"] = "FLAG"
        if param.required:
            entry["required"] = True
        default = param.default
        # 用身份判断避免 0 == False 被误过滤（如 --fee 默认 0）
        if default is not None and default is not False and not callable(default):
            entry["default"] = default
        help_text = getattr(param, "help", None)
        if help_text:
            entry["help"] = help_text
    else:  # argument
        entry = {"arg": param.name, "type": param.type.name.upper()}
        if param.required:
            entry["required"] = True
    return entry


def _command_entry(cmd: Any, group_name: Optional[str] = None, sub_name: Optional[str] = None) -> dict:
    """单个命令 → {"help", "params", "output"?}；output 为响应字段契约（命中 RESPONSE_FIELDS 时附加）"""
    entry: dict = {}
    if cmd.help:
        # 只取 docstring 首段，保持紧凑
        entry["help"] = cmd.help.strip().split("\n\n")[0].replace("\n", " ")
    params = [e for p in cmd.params if (e := _param_entry(p))]
    if params:
        entry["params"] = params
    if group_name and sub_name:
        output = RESPONSE_FIELDS.get(group_name, {}).get(sub_name)
        if output:
            entry["output"] = output
    return entry


def is_group(cmd: Any) -> bool:
    """命令组判断（兼容 typer 内嵌 click）"""
    return hasattr(cmd, "commands")


def build_schema(root: Any, group_name: Optional[str] = None, index_only: bool = False) -> dict:
    """
    构建 CLI 自描述结构。

    Args:
        root: typer.main.get_command(app) 得到的顶层命令组
        group_name: 仅输出指定命令组；None 输出全量（含协议/枚举/hints）
        index_only: 仅输出极简命令索引（协议退出码 + 命令组索引字符串，<1KB），
                    供 agent 先拿索引再按组加载，节省 token

    Raises:
        KeyError: group_name 不存在（由调用方转 VALIDATION_ERROR）
    """
    groups = {
        name: cmd for name, cmd in root.commands.items()
        if is_group(cmd)
    }
    if index_only:
        # 紧凑编码 "组名:子命令1 子命令2;..."：dict-of-lists 约 1.3KB，此编码 <1KB
        return {
            "protocol": {"exit_codes": "0=成功 1=业务错误 2=认证(ir auth login) 3=连接/超时"},
            "groups": ";".join(
                f"{name}:{' '.join(grp.commands.keys())}" for name, grp in groups.items()
            ),
        }
    if group_name is not None:
        if group_name not in groups:
            raise KeyError(group_name)
        target = groups[group_name]
        return {
            "commands": {
                group_name: {
                    sub: _command_entry(c, group_name, sub)
                    for sub, c in target.commands.items()
                }
            }
        }
    commands = {
        name: {sub: _command_entry(c, name, sub) for sub, c in grp.commands.items()}
        for name, grp in groups.items()
    }
    return {
        "protocol": PROTOCOL,
        "conventions": CONVENTIONS,
        "enums": {k: list(v) for k, v in ENUMS.items()},
        "error_hints": ERROR_HINTS,
        "workflows": WORKFLOWS,
        "commands": commands,
    }
