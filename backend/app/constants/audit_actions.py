"""审计日志 action / resource_type 枚举常量（issue #405）。

单一事实来源：`audit_service.record_audit` 与各 service 埋点均引用本模块，
禁止在埋点处写字面量。列宽约束（`audit_log.action` String(20)、
`resource_type` String(50)、`investor_code` String(20)）由
`tests/unit/test_audit_service.py` 守门。

命名约定：
- action 用动词原形，≤20 字符；
- resource_type 用蛇形名词，≤50 字符；
- 后台执行体（调度器、线程池）无请求上下文时 actor 落 `SYSTEM_ACTOR`。
"""

# ---- actions (≤20 chars) ----

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_CONFIRM = "confirm"
ACTION_UNCONFIRM = "unconfirm"
ACTION_CANCEL = "cancel"
ACTION_DELETE = "delete"
ACTION_GENERATE = "generate"
ACTION_RECALCULATE = "recalculate"
ACTION_CASCADE_UNCONFIRM = "cascade_unconfirm"

# ---- resource types (≤50 chars) ----

RESOURCE_SUBSCRIPTION = "subscription"
RESOURCE_TRADE = "trade"
RESOURCE_CASH_TRANSFER = "cash_transfer"
RESOURCE_SHARE_CHANGE_EVENT = "share_change_event"
RESOURCE_SNAPSHOT = "snapshot"
RESOURCE_MANUAL_MARKET_VALUE = "manual_market_value"

# ---- sentinel ----

SYSTEM_ACTOR = "SYSTEM"
