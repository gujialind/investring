"""
错误码 → 补救指引映射

后端业务错误码是稳定契约（全量清单见 docs/reference/business-constraints.md「错误码总表」，AST 守门测试保证同步），此表将常见错误码映射为
下一步可直接执行的 ir 命令或操作指引，随错误 JSON 一并输出（error.hints），
帮助 AI agent 一次失败即收敛到正确路径，无需反复试错。

get_hint() 在静态表基础上按 error.details 动态插值（issue #86）：
如 MARKET_AMBIGUOUS 直接列出可选市场、INSUFFICIENT_SHARES 直接给出当前
可用份额，让提示"就近"贴合本次失败的具体上下文。
"""
from typing import Optional

ERROR_HINTS: dict = {
    "DATE_BEFORE_SNAPSHOT": "日期须晚于最新快照日，用 ir snapshot status <portfolio_code> 查询最新快照日",
    "SNAPSHOT_DEPENDENCY": "记录（或其派生的在途/应收金额）已被快照纳入，先删除错误信息点名日期及之后的快照: ir snapshot delete-bulk <portfolio_code> <date> --yes，操作完成后需重新生成/重算快照；份额事件未确认但已设 share_confirm_date 时，pending 状态的改/取消/删除同样受此守卫（应收已进快照）",
    "SNAPSHOT_NOT_CONTINUOUS": "目标日须为最新快照日或其下一交易日，用 ir snapshot status <portfolio_code> 查询最新快照日后，执行 ir snapshot catch-up --portfolio-code <code> --to-date <date> 逐交易日追平",
    "NAV_NOT_AVAILABLE": "申请日组合净值快照缺失: ir snapshot status <portfolio_code> 查询最新快照日后，用 ir snapshot catch-up --portfolio-code <code> --to-date <申请日> 追平至申请日",
    "NO_SNAPSHOT_BASELINE": "组合尚无任何快照基线，先生成首日快照: ir snapshot generate --portfolio-code <code> --target-date <首个交易日>",
    "NON_TRADING_DAY": "仅交易日可操作: ir system calendar 查询交易日历",
    "CALENDAR_NOT_SYNCED": "交易日历未同步: ir system calendar-sync --year <year> 同步后重试",
    "INSUFFICIENT_CASH": "ir position available-cash --portfolio-code <code> 查询实时可用现金；pending 卖出不增加可用现金，须先卖出确认后再买入；现金在其他平台时先转入对应平台: ir cash-transfer create",
    "DUPLICATE_TRADE": "疑似重复交易（details.existing_trade_id 为已存在记录），先 ir trade list --portfolio-code <code> 核查；确需重复录入时加 --allow-duplicate 重发",
    "INSUFFICIENT_SHARES": "ir position available-shares --portfolio-code <code> --product-code <product_code> 查询实时可用份额",
    "CANNOT_MODIFY_CONFIRMED": "confirmed 记录不可直接修改，先执行 unconfirm 回退至 pending",
    "CANNOT_DELETE_CONFIRMED": "confirmed 记录不可直接删除，先执行 unconfirm 回退至 pending",
    "INVALID_STATUS": "记录状态不允许该操作（confirm/cancel 仅限 pending，unconfirm 仅限 confirmed，cancelled 不可修改）：先查询当前状态（ir trade list / ir sub list / ir share-event list --portfolio-code <code>）再按状态处理",
    "PENDING_TRANSACTIONS_EXIST": "存在 pending 申赎/交易，先逐笔 confirm 或 cancel 后重试",
    "CASH_TRADE_FORBIDDEN": "禁止直接创建 CASH 交易，现金变动须走 ir sub（申赎）/ ir cash-transfer（跨平台转移）/ 基金调仓自动配对；调仓 CASH 腿的财务字段与生命周期只能由基金腿驱动",
    "CASH_PLATFORM_NOT_ALLOWED": "现金腿平台在不允许的时机传入：卖出到账平台改在确认时传 ir trade confirm <id> --cash-platform-code <平台>；买入扣款平台在创建时用 ir trade create --cash-platform-code <平台> 确定后不可改",
    "CASH_CONFIRM_DATE_NOT_ALLOWED": "现金日在不允许的时机传入：卖出到账日改在确认时传 ir trade confirm <id> --cash-confirm-date <到账日>（缺省 = 基金确认日）；买入扣款日固定为下单日 T，不可改",
    "CASH_LEG_MISSING": "该卖出没有配对 CASH 到账腿，无法改到账日：先 ir trade unconfirm <id> 再 ir trade confirm <id> --cash-confirm-date <到账日> 重建到账腿",
    "PRICE_NAV_MISMATCH": "传入价格与 T 日净值不一致: ir market price 查询净值核对，或省略 --price 由后端取 T 日净值",
    "MISSING_NAV": "净值缺失: 增量同步已回看最近 10 个交易日（#698），迟披露缺口会在下一次 nav_sync 后自动回补；需即刻处理按错误信息点名的日期定点回补 ir market sync <product_code> <market> --start-date <缺失日> --end-date <缺失日> 后重试，或 ir trade confirm <id> --sync-nav 自动同步并确认",
    "MISSING_OR_INVALID_PRICE": "场内交易必须传入有效 --price（成交价）",
    "CANNOT_CANCEL_EXCHANGE": "场内交易当天确认、不可 cancel；已确认的用 unconfirm 回退",
    "TRANSFER_NOT_READY": "转移未到确认日，到 confirm_date 当日再执行 ir cash-transfer confirm",
    "TRANSFER_ALREADY_CANCELLED": "该转移已撤销，无需重复操作：ir cash-transfer list --portfolio-code <code> 查看当前状态",
    "MISSING_POSITION_SNAPSHOT": "权益登记日持仓快照缺失，先生成该日快照: ir snapshot generate",
    "DIVIDEND_METHOD_REQUIRED": "请先选择分红方式：ir share-event confirm <id> --event-type cash_dividend 或 ir share-event confirm <id> --event-type reinvest_dividend；可先用 ir share-event preview <id> 携带相同候选参数核对；适用日期省略默认除息日，保留原日期须显式传入",
    "MANUAL_OVERRIDE_NOT_FOUND": "未找到对应现金覆盖记录: ir position list-cash-overrides --portfolio-code <code> 核查后重试",
    "PLATFORM_NOT_COVERED": "平台级事件未覆盖全部有持仓平台，补录其余平台事件，或传 --force-cover 降级为 warning",
    "PLATFORM_REQUIRED": "该平台操作必须指定平台: 加 --platform-code <平台代码>，用 ir platform list 查询可用平台",
    "PLATFORM_NOT_FOUND": "平台不存在: 用 ir platform list 查询可用平台代码",
    "CANNOT_UNCONFIRM_CHILD": "基金级事件的子记录不可单独 unconfirm，请对父事件执行 unconfirm",
    "AUTH_REQUIRED": "执行 ir auth login 登录后重试",
    "NOT_FOUND": "检查资源标识拼写；产品类先用 ir product list 查询可用产品代码与市场",
    "PRODUCT_NOT_FOUND": "检查产品代码与市场，如 --product-code 022959.OF --market CN_OTC，用 ir product list 查询可用产品",
    "PRODUCT_REQUIRED": "该操作必须指定产品: 加 --product-code <产品代码>，用 ir product list 查询可用产品",
    "MARKET_AMBIGUOUS": "产品存在多个市场，请指定 --market；用 ir product list 查询产品可用市场",
    "INVALID_NAV_LAG_DAYS": "nav_lag_days 必须 >=0；场内（market=CN_EXCHANGE）必须为 0；显式传 null 拒绝；修正后重试: ir product update <code> <market> --nav-lag-days <N>",
    "INVALID_CONFIRM_DAYS": "confirm_days 必须 >=0；场内（market=CN_EXCHANGE）必须为 0；显式传 null 拒绝；修正后重试: ir product update <code> <market> --confirm-days <N>",
    "INVALID_SHARE_CONFIRM_DATE": "再投资份额确认日非法：仅 reinvest_dividend 可设、须为交易日（ir system trading-day is-open <date> 查询）且晚于除息日；改正后重试，或清空改按除息日应用份额: ir share-event update <id> --json '{\"share_confirm_date\": null}'",
    "CONFIRM_REQUIRED": "破坏性操作未确认已被拒绝：先用命令给出的 get/status 目标核实影响面，确认无误后追加 --yes 重跑",
    # CLI 侧错误码（后端不产出）：#520 用法错误改由入口统一收敛为 JSON + exit 64
    "USAGE_ERROR": "选项/参数不存在或必填缺失，命令未执行：先 ir schema --index 定位命令，再 ir <组> <子命令> --help 看该命令全部选项",
    # #798：构造期异常（exit 3）与全局兜底（exit 1），保证任何异常路径都走 JSON 错误出口
    "CLIENT_INIT_ERROR": "HTTP 客户端初始化失败：检查 IR_BASE_URL 是否为合法 URL（含 http(s):// 协议头）；本 CLI 不读取代理环境变量，无需排查 no_proxy/http_proxy；仍失败请附本条 JSON 与 ir --version 输出反馈维护者",
    "INTERNAL_ERROR": "未预期内部错误：请附本条 JSON、完整命令行与 ir --version 输出反馈维护者；可先用 ir schema --index 核对命令用法后重试",
}


def get_hint(code: str, details: Optional[dict] = None) -> Optional[str]:
    """按 code+details 动态生成就近提示，无匹配动态规则时回退静态表（issue #86）"""
    details = details or {}
    if code == "MARKET_AMBIGUOUS":
        markets = details.get("available_markets")
        if markets:
            return f"产品存在多个市场，请指定 --market，可选: {', '.join(markets)}"
    if code in ("PRODUCT_NOT_FOUND", "NOT_FOUND"):
        markets = details.get("available_markets")
        if markets:
            product = details.get("product_code") or "<product_code>"
            return f"产品 {product} 在指定市场不存在，可选市场: {', '.join(markets)}，请改用 --market 指定"
    if code == "INSUFFICIENT_SHARES":
        shares = details.get("available_shares")
        if shares is not None:
            return (
                f"当前可用份额为 {shares}，请调低份额后重试；"
                "实时查询: ir position available-shares --portfolio-code <code> --product-code <product_code>"
            )
    # INVALID_PARAM 是多处入参校验共用的兜底码，只对能给出具体补救动作的字段插值（#764）
    if code == "INVALID_PARAM" and details.get("field") == "div_cash":
        return (
            "每份分红金额必须大于 0：创建时传 --div-cash <正数>；"
            "已落库的存量值改 ir share-event update <id> --div-cash <正数>"
        )
    # 份额变动事件（红利再投资）的净值错误带 event_id，与调仓同码但补救命令不同（#744）
    event_id = details.get("event_id")
    if code == "DIVIDEND_METHOD_REQUIRED" and event_id is not None:
        return ERROR_HINTS[code].replace("<id>", str(event_id))
    if code == "MISSING_NAV" and event_id is not None:
        product = details.get("product_code") or "<product_code>"
        market = details.get("market") or "<market>"
        nav_date = details.get("nav_date") or "<除息日>"
        return (
            "除息日净值未同步: 在原确认命令上追加 --sync-nav 自动同步并确认"
            f"（ir share-event confirm {event_id} --sync-nav）；"
            "若原命令带 --event-type 及日期参数，重试须保留完整候选；待定分红仍须显式选择方式。"
            f"或 ir market sync {product} {market} --start-date {nav_date} --end-date {nav_date} 回补后携带原候选参数重试"
        )
    if code == "PRICE_NAV_MISMATCH" and event_id is not None:
        return (
            f"手填再投资净值与除息日净值不一致: 核对后修改，或清空改由确认时取数据源净值 "
            f"ir share-event update {event_id} --json '{{\"reinvest_nav\": null}}'"
        )
    return ERROR_HINTS.get(code)
