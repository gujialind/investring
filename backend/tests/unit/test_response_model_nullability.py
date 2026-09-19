# ============================================================================
# 单元测试守门：ORM 可空列 ↔ 响应模型字段的静态可空性核对（issue #513）
# ============================================================================
# 背景：#513 第 1 条 comment 要求守门覆盖「真实形态行可校验」——声明了
# response_model 还不够，若响应模型字段不接受 None 而 ORM 列可空，库中真存在
# 的 NULL 行会在序列化时 500（#511 的 ProductResponse.data_source_status 即
# 此形态：`str = "pending"` 只在显式传 None 时才暴露，is_required() 判不出）。
#
# 判据（对「同名列」直配对，派生/嵌套字段无同名列自然跳过）：
#   字段不接受显式 None（`TypeAdapter(annotation).validate_python(None)` 抛错）
#   + 列 nullable=True + 无 server_default  →  违规，除非登记在 EXCEPTIONS。
# server_default 的列由 DB 兜底（如 created_at），不在判据内。
#
# EXCEPTIONS 是**只减不增**的台账：每条给出 kind + 理由，kind 的机制由本文件
# 机器复核：
#   - orm_default：列确有 ORM 侧 default。**注意机制边界**——它只证明「ORM 写入
#     路径有默认值兜底」，不证明「库中真实行非 NULL」：裸 SQL（迁移/脚本用显式
#     列清单 INSERT）会绕过 ORM default，#511 的生产 500 正是这个形态（0006 裸
#     插入的 product.data_source_status 为 NULL）。故每条 orm_default 另以人工
#     核实「全仓无绕过 ORM default 写该列的裸 SQL 路径」**当前提**——全仓裸 SQL
#     仅 0006/0008/0009，触及台账列的那处（product.data_source_status）已在 #511
#     改为 Optional，其余台账列无裸写路径。
#   - service_guard：服务层显式拒绝 null（实调校验器核验）。
#   - known_gap：**已实测存在未修缺口**——响应字段不接受 None，而真实更新路径能
#     把 NULL 写进库（一次 PUT 即持久化，此后该行 GET 恒 500）。理由必须挂
#     follow-up issue 号，由 _check_known_gap 机器复核。
# 行失效（对应字段已改成 Optional、或列已 NOT NULL）会被 stale 断言抓住。
# ============================================================================

import re

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.audit_log import AuditLog
from app.models.investor import Investor
from app.models.login_log import LoginLog
from app.models.nav_sync_detail import NavSyncDetail
from app.models.notification import Notification
from app.models.platform import Platform
from app.models.portfolio import Portfolio
from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.product import Product
from app.models.scheduled_task import ScheduledTask
from app.models.share_change_event import ShareChangeEvent
from app.models.subscription import Subscription
from app.models.sync_job import SyncJob
from app.models.system_error_log import SystemErrorLog
from app.models.task_execution_log import TaskExecutionLog
from app.models.trade import Trade
from app.schemas.investor import InvestorResponse
from app.schemas.log import AuditLogResponse, LoginLogResponse, SystemErrorLogResponse
from app.schemas.notification import NotificationResponse
from app.schemas.platform import PlatformResponse
from app.schemas.portfolio import PortfolioResponse, PortfolioValueSnapshotResponse
from app.schemas.position import PositionResponse
from app.schemas.product import ProductResponse
from app.schemas.share_change_event import ShareChangeEventResponse
from app.schemas.subscription import SubscriptionResponse
from app.schemas.sync_job import NavSyncDetailResponse, SyncJobResponse
from app.schemas.task import TaskExecutionLogResponse, TaskResponse
from app.schemas.trade import TradeResponse

# ORM 模型 ↔ 响应 schema 直配对（同一实体在 REST 出口的收窄口径）
ORM_SCHEMA_PAIRS: list[tuple[type, type]] = [
    (Investor, InvestorResponse),
    (Product, ProductResponse),
    (Subscription, SubscriptionResponse),
    (Platform, PlatformResponse),
    (PortfolioPosition, PositionResponse),
    (Notification, NotificationResponse),
    (ScheduledTask, TaskResponse),
    (TaskExecutionLog, TaskExecutionLogResponse),
    (LoginLog, LoginLogResponse),
    (AuditLog, AuditLogResponse),
    (SystemErrorLog, SystemErrorLogResponse),
    (Trade, TradeResponse),
    (Portfolio, PortfolioResponse),
    (PortfolioValueSnapshot, PortfolioValueSnapshotResponse),
    (ShareChangeEvent, ShareChangeEventResponse),
    (SyncJob, SyncJobResponse),
    (NavSyncDetail, NavSyncDetailResponse),
]

# (模型名, 字段名) -> (kind, 理由)。kind 的机制必须能被 MECHANISM_CHECKERS 机器复核。
EXCEPTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("Investor", "role"): (
        "known_gap",
        "#573：PUT /api/investors/{code} 接受显式 null（InvestorUpdate.role 为 Optional，"
        "router 用 exclude_unset 故 null 算「已设置」，investor_service 无差别 setattr）"
        "→ NULL 落库且先于响应序列化 commit → ResponseValidationError 500，此后该行 GET 恒 500。"
        "本台账原写「应用写入不显式传 None」与代码事实不符，已按实测更正；修复见 #573。",
    ),
    ("Product", "confirm_days"): (
        "service_guard",
        "列无 ORM default；创建/更新均经 product_service.validate_confirm_days 显式拒绝 null，"
        "显式传入走校验、缺省时按市场推导 0/1/2",
    ),
    ("Product", "is_qdii"): (
        "known_gap",
        "#573：PUT /api/products/{code}/{market} 接受显式 null（ProductUpdate.is_qdii 为 Optional，"
        "product_service.update_product 的 setattr 循环只对 confirm_days/nav_lag_days 收口）"
        "→ NULL 落库 → ResponseValidationError 500。本台账原写「创建路径由服务层推导后显式传布尔值」"
        "只覆盖创建、未覆盖更新，已按实测更正；修复见 #573。",
    ),
    ("Subscription", "status"): (
        "orm_default",
        "ORM default='pending'；SubscriptionUpdate 不含 status（更新 schema 无法传该字段），"
        "状态流转走 confirm/cancel/unconfirm 端点写具体值",
    ),
    ("Notification", "level"): ("orm_default", "ORM default='info'；应用无写入该列的端点（router 仅 GET 与 read/read-all）"),
    ("Notification", "channel"): ("orm_default", "ORM default='in_app'；同上，无写入该列的端点"),
    ("Notification", "status"): (
        "orm_default",
        "ORM default='pending'；无更新端点（NotificationUpdate 虽存在但 router 未使用），"
        "已读流转走 POST /{id}/read 与 /read-all 写具体值",
    ),
    ("ScheduledTask", "is_enabled"): (
        "orm_default",
        "ORM default=True；init_scheduled_tasks 种子与 enable/disable 端点显式写布尔值"
        "（router 无 PUT，无法传该字段）",
    ),
    ("ScheduledTask", "timeout_seconds"): (
        "orm_default",
        "ORM default=300；init_scheduled_tasks 种子写显式值，router 无更新端点",
    ),
    ("Trade", "fee"): (
        "orm_default",
        "ORM default=0；创建按金额推导或显式传入。更新路径显式 null 安全："
        "trade_service.update_trade 用 _dec() 把 null 归为「未提供」，仅 fee_input 非 None 才 setattr",
    ),
    ("Trade", "status"): (
        "orm_default",
        "ORM default='pending'；TradeUpdate 不含 status（更新 schema 无法传该字段），"
        "状态流转走 lifecycle 端点",
    ),
    ("Portfolio", "status"): (
        "orm_default",
        "ORM default='draft'；PortfolioUpdate 不含 status，close/reactivate 端点写具体值",
    ),
    ("ShareChangeEvent", "status"): (
        "orm_default",
        "ORM default='pending'；ShareChangeEventUpdate 不含 status，状态流转走 confirm/cancel/unconfirm 端点",
    ),
    ("SyncJob", "total"): ("orm_default", "ORM default=0；构造点不显式传计数，INSERT 时由 ORM default 落 0"),
    ("SyncJob", "done"): ("orm_default", "ORM default=0；构造点不显式传计数，INSERT 时由 ORM default 落 0"),
    ("SyncJob", "success_count"): ("orm_default", "ORM default=0；构造点不显式传计数，INSERT 时由 ORM default 落 0"),
    ("SyncJob", "failed_count"): ("orm_default", "ORM default=0；构造点不显式传计数，INSERT 时由 ORM default 落 0"),
    ("SyncJob", "skipped_count"): ("orm_default", "ORM default=0；构造点不显式传计数，INSERT 时由 ORM default 落 0"),
    ("SyncJob", "triggered_by"): ("orm_default", "ORM default='manual'；提交路径显式传触发来源"),
    ("NavSyncDetail", "synced_count"): ("orm_default", "ORM default=0；无更新端点，执行中累加"),
}

# 防扫描退化的下限（**棘轮：等于当前实核值**，只升不降）。低于此值说明配对表被
# 静默删行（曾实测：下限留 54 字段松弛时，删掉 4 对、17 对里 7 对可静默消失而
# 断言全绿）；正常增删模型时按实测值同步上调/下调，并在 PR 里说明。
MIN_CHECKED_FIELDS = 204


def _accepts_none(annotation) -> bool:
    """显式 None 是否能通过字段校验（is_required() 判不出默认值场景，必须实校验）

    只捕 ValidationError：annotation 本身无法构造 TypeAdapter（代码写错）属另一类
    失败，若一并吞掉会被误判成「不接受 None」而报假红。
    """
    try:
        TypeAdapter(annotation).validate_python(None)
    except ValidationError:
        return False
    return True


def _scan_violations() -> dict[tuple[str, str], tuple[type, object, object]]:
    """返回 {(模型名, 字段名): (模型, 列, 字段)}，即当前全部违规点"""
    violations: dict[tuple[str, str], tuple[type, object, object]] = {}
    for model, schema in ORM_SCHEMA_PAIRS:
        columns = {c.name: c for c in model.__table__.columns}
        for field_name, field in schema.model_fields.items():
            column = columns.get(field_name)
            if column is None:
                continue  # 派生/嵌套字段无同名列
            if _accepts_none(field.annotation):
                continue
            if not column.nullable:
                continue
            if column.server_default is not None:
                continue
            violations[(model.__name__, field_name)] = (model, column, field)
    return violations


def _checked_field_count() -> int:
    total = 0
    for model, schema in ORM_SCHEMA_PAIRS:
        column_names = {c.name for c in model.__table__.columns}
        total += sum(1 for name in schema.model_fields if name in column_names)
    return total


class TestNullabilityRegistry:
    """登记表自身健全性（防空转）"""

    def test_registry_pairs_are_orm_and_schema(self):
        from pydantic import BaseModel
        from sqlalchemy import inspect as sa_inspect

        for model, schema in ORM_SCHEMA_PAIRS:
            assert sa_inspect(model).local_table is not None, f"{model} 不是 ORM 模型"
            assert issubclass(schema, BaseModel), f"{schema} 不是 Pydantic 模型"

    def test_every_pair_checks_at_least_one_field(self):
        empty = []
        for model, schema in ORM_SCHEMA_PAIRS:
            column_names = {c.name for c in model.__table__.columns}
            if not any(name in column_names for name in schema.model_fields):
                empty.append(f"{model.__name__} ↔ {schema.__name__}")
        assert not empty, f"以下配对没有任何同名字段被核对（配对表写错了？）：{empty}"

    def test_checked_field_floor(self):
        assert _checked_field_count() >= MIN_CHECKED_FIELDS, (
            f"同名字段核对数 {_checked_field_count()} 低于下限 {MIN_CHECKED_FIELDS}，"
            "疑似配对表或字段集合被静默清空"
        )


class TestNullabilityLedger:
    """违规集合与例外台账必须精确相等（双向）"""

    def test_unledgered_violations(self):
        unexpected = set(_scan_violations()) - set(EXCEPTIONS)
        assert not unexpected, (
            "以下「可空列 + 响应字段不接受 None」组合未登记例外：\n"
            + "\n".join(f"    (\"{m}\", \"{f}\")," for m, f in sorted(unexpected))
            + "\n处理方式：优先把响应字段改为 Optional[X]（对齐库中真实形态）；"
            "确认应用写入路径不会落 NULL 才登记 EXCEPTIONS 并写明 kind 与理由。"
        )

    def test_stale_exception_entries(self):
        stale = set(EXCEPTIONS) - set(_scan_violations())
        assert not stale, (
            "以下例外已失效（字段已接受 None、或列已 NOT NULL、或配对被移除），请删除对应行：\n"
            + "\n".join(f"    (\"{m}\", \"{f}\")," for m, f in sorted(stale))
        )

    def test_exception_kind_mechanism_holds(self):
        """每条例外的 kind 必须在当前代码里可机器复核（台账不许口说无凭）"""
        violations = _scan_violations()
        for key, (kind, reason) in EXCEPTIONS.items():
            assert kind in _MECHANISM_CHECKERS, f"{key} 的 kind 未知：{kind}"
            assert key in violations, (
                f"{key}（kind={kind}）已不在违规集里——台账行应删除"
                "（字段已接受 None、或列已 NOT NULL）；stale 断言本应先报，此处兜底给可读原因"
            )
            _model, column, _field = violations[key]
            _MECHANISM_CHECKERS[kind](column, key, reason)

    def test_exception_reasons_not_empty(self):
        blank = [key for key, (_kind, reason) in EXCEPTIONS.items() if not reason.strip()]
        assert not blank, f"以下例外缺少理由：{blank}"


def _check_orm_default(column, key, _reason) -> None:
    """kind=orm_default 的机制复核。

    **只证明 ORM 写入路径有默认值兜底**（ORM 显式传 None 也会落 default），不证明
    库中真实行非 NULL——裸 SQL（迁移/脚本的显式列清单 INSERT）会绕过它，而 #511 的
    生产 500 正是这个形态。故该 kind 成立还需一条人工核实的前提：全仓不存在绕过
    ORM default 写该列的裸 SQL 写入路径（文件头注释记录了当前核实口径）。
    """
    assert column.default is not None, (
        f"{key} 声明 kind=orm_default，但列上没有 ORM 侧 default（机制不成立）"
    )


def _check_service_guard(_column, key, _reason) -> None:
    from app.services.exceptions import BusinessError
    from app.services.product_service import validate_confirm_days

    with pytest.raises(BusinessError):
        validate_confirm_days(market=None, confirm_days=None)


def _check_known_gap(_column, key, reason) -> None:
    """kind=known_gap 的机制复核：未修缺口必须挂 follow-up issue（防「登记即遗忘」）。

    机制本身（真实写入路径能落 NULL）由人工按 issue 里的复现步骤核实——本检查
    保证的是「缺口有主」，不是「缺口已修」。
    """
    assert re.search(r"#[0-9]+", reason), (
        f"{key} 声明 kind=known_gap，但理由里没有 follow-up issue 号（#NNN）——"
        "未修缺口必须挂 issue，否则登记即遗忘"
    )


_MECHANISM_CHECKERS = {
    "orm_default": _check_orm_default,
    "service_guard": _check_service_guard,
    "known_gap": _check_known_gap,
}
