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
    "SNAPSHOT_DEPENDENCY": "记录已被快照纳入，先删除确认日及之后快照: ir snapshot delete-bulk <portfolio_code> <date> --yes，操作完成后需重新生成/重算快照",
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
    "CASH_TRADE_FORBIDDEN": "禁止直接创建 CASH 交易，现金变动须走 ir sub（申赎）/ ir cash-transfer（跨平台转移）/ 基金调仓自动配对",
    "PRICE_NAV_MISMATCH": "传入价格与 T 日净值不一致: ir market price 查询净值核对，或省略 --price 由后端取 T 日净值",
    "MISSING_NAV": "T 日净值尚未同步: ir market sync-history <product_code> <market> 回填后重试，或 ir trade confirm <id> --sync-nav 自动同步并确认",
    "MISSING_OR_INVALID_PRICE": "场内交易必须传入有效 --price（成交价）",
    "CANNOT_CANCEL_EXCHANGE": "场内交易当天确认、不可 cancel；已确认的用 unconfirm 回退",
    "TRANSFER_NOT_READY": "转移未到确认日，到 confirm_date 当日再执行 ir cash-transfer confirm",
    "MISSING_POSITION_SNAPSHOT": "权益登记日持仓快照缺失，先生成该日快照: ir snapshot generate",
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
    "CONFIRM_REQUIRED": "不可逆操作需 --yes 确认，可先加 --dry-run 预览影响范围",
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
    return ERROR_HINTS.get(code)
