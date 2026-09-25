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
#   - service_guard：服务层显式拒绝 null。按字段登记守门模块与形态
#     （SERVICE_GUARD_SITES），实调守门 + 更新路径调用点 AST 双向核验。
#   - known_gap：**已实测存在未修缺口**——响应字段不接受 None，而真实更新路径能
#     把 NULL 写进库（一次 PUT 即持久化，此后该行 GET 恒 500）。理由必须挂
#     follow-up issue 号，由 _check_known_gap 机器复核。
# 行失效（对应字段已改成 Optional、或列已 NOT NULL）会被 stale 断言抓住。
# ============================================================================

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.asset_classification import AssetClassification
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
from app.models.price_record import PriceRecord
from app.models.trading_calendar import TradingCalendar
from app.models.trade import Trade
from app.schemas.asset_classification import AssetClassificationDetail
from app.schemas.investor import InvestorResponse
from app.schemas.market_data import PriceDataResponse
from app.schemas.log import AuditLogResponse, LoginLogResponse, SystemErrorLogResponse
from app.schemas.notification import NotificationResponse
from app.schemas.platform import PlatformResponse
from app.schemas.portfolio import (
    NavHistoryRecord,
    PortfolioResponse,
    PortfolioValueSnapshotResponse,
)
from app.schemas.position import PositionResponse
from app.schemas.product import ProductResponse
from app.schemas.share_change_event import ShareChangeEventResponse
from app.schemas.snapshot import SnapshotGenerateNextResult, SnapshotGenerationResult
from app.schemas.subscription import SubscriptionResponse
from app.schemas.sync_job import NavSyncDetailResponse, SyncJobResponse
from app.schemas.task import TaskDetailResponse, TaskExecutionLogResponse, TaskResponse
from app.schemas.trading_calendar import TradingCalendarResponse
from app.schemas.trade import TradeResponse

# ORM 模型 ↔ 响应 schema 直配对（同一实体在 REST 出口的收窄口径）
ORM_SCHEMA_PAIRS: list[tuple[type, type]] = [
    # #573 纳入：PUT /api/asset-classifications/{code} 的 sort_order 同属「可空列 +
    # 响应字段不接受 None」形态（此前未配对，缺口未被扫描覆盖）
    (AssetClassification, AssetClassificationDetail),
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
    # #580 纳入：GET /api/trading-calendar 有 REST 出口（response_model=
    # List[TradingCalendarResponse]）却此前未配对——与 #573 漏报 AssetClassification
    # 同属「清单漏项」盲区。响应不暴露可空列 exchange，参与核对的同名字段为
    # calendar_date / is_open / created_at（3 个，均无违规：前两列 NOT NULL、
    # created_at 有 server_default），收录无害且把该出口纳入棘轮保护
    (TradingCalendar, TradingCalendarResponse),
    # #580 纳入：GET /api/products/{code}/{market}/price-data 有 REST 出口
    # （response_model=List[PriceDataResponse]）却此前未配对，是本次新发现的漏项。
    # unit_price 是本类的主干——它可空时路由里的 float(r.unit_price) 抛 TypeError
    # 变 500；本轮把列收口为 NOT NULL（迁移 0017）且写入侧滤掉无价行，收录把它钉住、
    # 防止日后有人把列改回可空却没人发现。参与核对的同名字段 4 个（product_code /
    # market / price_date 三列均 NOT NULL，unit_price 已随本轮改为 NOT NULL）。
    (PriceRecord, PriceDataResponse),
    # #580 收录面互证发现的第二批漏项：守门按「路由出口的响应 schema 与 ORM 同名字段
    # ≥3」扫描得出。其中 TaskDetailResponse 与 ScheduledTask 同名字段多达 11 个却从未
    # 参与核对——它与已收录的 TaskResponse 是同一模型的两张出口，漏掉一张等于漏掉一半。
    (ScheduledTask, TaskDetailResponse),
    (PortfolioValueSnapshot, NavHistoryRecord),
    (PortfolioValueSnapshot, SnapshotGenerationResult),
    (PortfolioValueSnapshot, SnapshotGenerateNextResult),
]

# (模型名, 字段名) -> (kind, 理由)。kind 的机制必须能被 MECHANISM_CHECKERS 机器复核。
EXCEPTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("AssetClassification", "sort_order"): (
        "service_guard",
        "#573：PUT /api/asset-classifications/{code} 的更新路径经 null_guard.reject_explicit_nulls "
        "拒绝显式 null（仅 description 放行——列可空且响应 Optional，null = 清除描述）→ 不会再落 "
        "NULL。本配对由 #573 一并纳入（此前不在 ORM_SCHEMA_PAIRS，该缺口未被扫描覆盖）；"
        "创建路径由 schema 缺省 0 + ORM default=0 兜底。",
    ),
    ("Investor", "role"): (
        "service_guard",
        "#573：PUT /api/investors/{code} 的更新路径经 null_guard.reject_explicit_nulls 拒绝显式 "
        "null（仅 phone/email 放行——列可空且响应 Optional，null = 清除）→ 不会再落 NULL。"
        "本条此前登记为 known_gap（单次 PUT 致该行持久不可读），由 #573 收口修掉。",
    ),
    ("Product", "confirm_days"): (
        "service_guard",
        "列无 ORM default；创建/更新均经 product_service.validate_confirm_days 显式拒绝 null，"
        "显式传入走校验、缺省时按市场推导 0/1/2",
    ),
    ("Product", "is_qdii"): (
        "service_guard",
        "#573：PUT /api/products/{code}/{market} 的更新路径经 null_guard.reject_explicit_nulls "
        "拒绝显式 null（五个维度标签放行——null = 清标签，合并终态仍交 validate_dimension_tags）"
        " → 不会再落 NULL。本条此前登记为 known_gap，由 #573 收口修掉。",
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
        "ORM default=0；创建按金额推导或显式传入。更新路径显式 null 由 "
        "null_guard.reject_explicit_nulls 拒绝 422（#579），清零须显式传 0；"
        "未提供时 _dec() 保持原值，仅 fee_input 非 None 才 setattr",
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
# 204 → 210（#573 纳入 AssetClassification ↔ AssetClassificationDetail 配对，6 个同名字段）。
# 210 → 213（#580 纳入 TradingCalendar ↔ TradingCalendarResponse 配对，3 个同名字段：
# calendar_date / is_open / created_at；exchange 未在响应暴露、不参与核对）。
MIN_CHECKED_FIELDS = 241

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# kind=service_guard 的字段级登记（#573 起按字段核对，不再写死单一校验器；
# #580 起增列**函数名**——模块级并集可被「诱饵调用」空转：把更新函数里的调用
# 删掉、在同模块别处放一个同名调用，旧实现照样全绿，台账结论静默失实）：
# (模型, 字段) -> (守门模块点分路径, 函数名, 形态)。两种形态都**机器可复核**：
#   - "reject"：登记的更新函数体内 null_guard.reject_explicit_nulls 调用的 allow
#     字面量必须不含该字段——AST 定位到 FunctionDef 内核对，且首个位置实参必须是
#     `updates`（守门对象是 exclude_unset 后的更新字典，异对象同名调用不算证据）：
#     一旦把字段加进 allow（显式 null 放行）或调用点被挪走/诱饵化，台账即失实；
#   - "validator"：字段由专用校验器 validate_<字段> 收口（保留专用错误码），
#     登记函数名必须与命名约定一致且存在，缺失即失败。
# 两种形态都实调守门并断言 BusinessError（reject 形态带 AST 解析出的真实 allow 集，
# 不带 allow 的实调恒抛、与 test_null_guard.py 等价，不构成路径证据）——
# 「拒绝 null」是实测结果，不是注释承诺。
SERVICE_GUARD_SITES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("AssetClassification", "sort_order"): ("app.services.asset_classification_service", "update_classification", "reject"),
    ("Investor", "role"): ("app.services.investor_service", "update_investor", "reject"),
    ("Product", "is_qdii"): ("app.services.product_service", "update_product", "reject"),
    ("Product", "confirm_days"): ("app.services.product_service", "validate_confirm_days", "validator"),
}


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

    def test_service_guard_sites_match_ledger(self):
        """守门登记表与台账精确对齐（#573）：漏登记与多登记都判红"""
        ledger_guard = {key for key, (kind, _) in EXCEPTIONS.items() if kind == "service_guard"}
        registered = set(SERVICE_GUARD_SITES)
        assert ledger_guard == registered, (
            f"台账有、登记表缺：{sorted(ledger_guard - registered)}；"
            f"登记表有、台账已非 service_guard：{sorted(registered - ledger_guard)}"
        )

    def test_allow_literal_parser_does_not_degrade(self):
        """allow 字面量解析必须真取到例外字段（#573；#580 起按登记函数解析）

        解析退化为空集会静默放过「字段被加进 allow（显式 null 放行）」——即守门
        最该抓的形态。故对 reject 形态设下限：当前三处均非空例外集。
        """
        for key, (module_name, function_name, mechanism) in SERVICE_GUARD_SITES.items():
            if mechanism != "reject":
                continue
            assert _explicit_null_allow_literals(module_name, function_name), (
                f"{key}：{module_name}::{function_name} 的 allow 字面量解析为空——若该函数确实没有例外字段，"
                "需先把「解析失败」与「确为空集」区分开（当前解析器两者同形，会静默失效）"
            )


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


def _explicit_null_allow_literals(module_name: str, function_name: str) -> set[str] | None:
    """AST 取登记函数体内 reject_explicit_nulls 调用 allow= 字面量集的并集；函数内无调用点返回 None。

    函数级定位（#580，PR #576 评审 🟡5）：旧实现是模块级并集——把更新函数里的
    调用删掉、在同模块别处放一个同名诱饵调用，照样返回非空 allow 集而全绿，台账
    「更新路径挂了守门」的结论静默失效。现只认登记 FunctionDef 内的调用点，并校验
    首个位置实参是 `updates`（守门对象是 exclude_unset 后的更新字典，异对象的同名
    调用不构成证据）。函数不存在 = 登记过期，直接判红（与「函数内无调用」区分）。
    只认字面量集合：`allow=SOME_CONST` 这类间接形态会让核对退化为空话，直接判红。
    """
    path = BACKEND_ROOT / (module_name.replace(".", "/") + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    func_def = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    assert func_def is not None, (
        f"{module_name} 内不存在函数 {function_name}——SERVICE_GUARD_SITES 登记过期"
        "（函数更名/迁移未同步登记）"
    )
    allowed: set[str] = set()
    found = False
    for node in ast.walk(func_def):
        if not isinstance(node, ast.Call):
            continue
        func_name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if func_name != "reject_explicit_nulls":
            continue
        assert node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "updates", (
            f"{module_name}.{function_name} 的 reject_explicit_nulls 调用首参不是 `updates`——"
            "守门对象不是更新字典（或为诱饵调用），不能作为台账证据"
        )
        found = True
        for keyword in node.keywords:
            if keyword.arg != "allow":
                continue
            assert isinstance(keyword.value, (ast.Set, ast.Tuple, ast.List)), (
                f"{module_name} 的 reject_explicit_nulls allow= 不是字面量集合，"
                "台账无法核对（改用字面量或扩展本解析器）"
            )
            allowed |= {ast.literal_eval(element) for element in keyword.value.elts}
    return allowed if found else None


def _check_service_guard(_column, key, _reason) -> None:
    """kind=service_guard 的字段级机制复核（#573；#580 起函数级定位）。

    两半证据缺一不可：① 更新路径确实挂了守门（reject 形态 AST 定位到登记函数体内
    核对首参 `updates` + allow 字面量、validator 形态核对校验器存在且合命名约定）；
    ② 该字段传 None 必备拒（实调守门，断言 BusinessError；reject 形态带 AST 解析
    出的真实 allow 集——不带 allow 的旧形态恒抛、与 test_null_guard.py 完全重复，
    不构成「路径上真实守门」的证据）。只做①会放过「挂了锁但字段被 allow」的形态，
    只做②会放过「守门存在但更新路径没调用」的形态。
    """
    from app.services.exceptions import BusinessError

    site = SERVICE_GUARD_SITES.get(key)
    assert site is not None, (
        f"{key} 声明 kind=service_guard，但未登记 SERVICE_GUARD_SITES——"
        "守门依据必须机器可复核，不接受「仅在理由里说明」"
    )
    module_name, function_name, mechanism = site
    field = key[1]

    if mechanism == "reject":
        from app.services.null_guard import reject_explicit_nulls

        allowed = _explicit_null_allow_literals(module_name, function_name)
        assert allowed is not None, (
            f"{key}：{module_name}::{function_name} 内找不到 reject_explicit_nulls 调用点——"
            "更新路径未挂共享收口"
        )
        assert field not in allowed, (
            f"{key}：{module_name}::{function_name} 的 reject_explicit_nulls allow 集合含该字段，"
            "显式 null 实际被放行——台账理由（显式拒绝 null）不成立"
        )
        with pytest.raises(BusinessError):
            reject_explicit_nulls({field: None}, allow=allowed)
    elif mechanism == "validator":
        assert function_name == f"validate_{field}", (
            f"{key}：登记的校验器 {function_name} 偏离 validate_{field} 同名约定——"
            "刻意改名时须同步更新约定与本断言"
        )
        module = importlib.import_module(module_name)
        validator = getattr(module, function_name, None)
        assert validator is not None, f"{key}：{module_name} 缺专用校验器 {function_name}"
        parameters = {name: None for name in inspect.signature(validator).parameters}
        with pytest.raises(BusinessError):
            validator(**parameters)
    else:
        raise AssertionError(f"{key}：SERVICE_GUARD_SITES 的形态未知：{mechanism!r}")


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


# --------------------------------------------------------------------------- #
# 收录面互证（issue #580）
# --------------------------------------------------------------------------- #
# `ORM_SCHEMA_PAIRS` 是一份手写清单，而手写清单的失效方式是**静默**的：少登记一对，
# 那一对就不会被扫描，缺口继续存在而 CI 全绿——#573 漏掉 AssetClassification、
# 本次又漏掉 PriceRecord/TradingCalendar/TaskDetailResponse，都是同一个形态。
# 这里把「清单漏项」从盲区变成红灯：凡是**挂了 REST 出口**（response_model 用到）
# 且与某个 ORM 模型同名字段足够多的响应 schema，都必须在清单里；否则要求收录或
# 显式豁免并注明理由。阈值取 3 是经验值——低于它多半是汇总/统计类的响应，压根
# 没有对应的 ORM 行可谈。

ROUTERS_DIR = Path(__file__).resolve().parents[2] / "app" / "routers"
MIN_SHARED_FIELDS = 3


def _all_orm_models() -> list:
    import pkgutil
    import importlib

    import app.models as models_pkg

    found = []
    for _, name, _ in pkgutil.iter_modules(models_pkg.__path__):
        if name == "base":
            continue
        module = importlib.import_module(f"app.models.{name}")
        for obj in vars(module).values():
            if isinstance(obj, type) and hasattr(obj, "__table__"):
                found.append(obj)
    return found


def _response_schemas_from_routers() -> dict:
    """路由 `response_model` 里出现的 schema 名 → 出处；抽不到就红，绝不静默跳过"""
    import ast

    names = {}
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                for kw in dec.keywords:
                    if kw.arg != "response_model":
                        continue
                    target = kw.value
                    if isinstance(target, ast.Subscript):  # List[X] / Optional[X]
                        target = target.slice
                    if isinstance(target, ast.Name):
                        names.setdefault(target.id, f"{path.name}:{node.lineno}")
    return names


def _unenrolled(router_schemas: dict, orm_models: list, pairs: set) -> dict:
    """返回「有 ORM 孪生却未收录」的 {schema 名: 说明}"""
    import pkgutil
    import importlib

    import app.schemas as schemas_pkg

    index = {}
    for _, name, _ in pkgutil.iter_modules(schemas_pkg.__path__):
        module = importlib.import_module(f"app.schemas.{name}")
        for attr, obj in vars(module).items():
            if isinstance(obj, type) and hasattr(obj, "model_fields"):
                index.setdefault(attr, obj)

    missing = {}
    for name, where in sorted(router_schemas.items()):
        schema = index.get(name)
        if schema is None:
            continue
        fields = set(schema.model_fields)
        twin = max(
            ((m, len(fields & set(m.__table__.columns.keys()))) for m in orm_models),
            key=lambda x: x[1],
            default=(None, 0),
        )
        if twin[0] is None or twin[1] < MIN_SHARED_FIELDS:
            continue
        if (twin[0].__name__, name) not in pairs:
            missing[name] = f"{twin[0].__name__}（同名字段 {twin[1]}，{where}）"
    return missing


class TestEnrollmentCompleteness:
    def test_every_schema_with_an_orm_twin_is_enrolled(self):
        router_schemas = _response_schemas_from_routers()
        # fail-closed：一个都抽不到说明装饰器写法变了或扫描失效，那比漏报更危险
        assert router_schemas, "没能从 routers 抽出任何 response_model——守门正在空转"

        pairs = {(m.__name__, s.__name__) for m, s in ORM_SCHEMA_PAIRS}
        missing = _unenrolled(router_schemas, _all_orm_models(), pairs)
        assert missing == {}, (
            f"这些响应 schema 有 ORM 孪生却未收录（#580）：{missing}。"
            "补进 ORM_SCHEMA_PAIRS，或说明它为何不该纳入"
        )

    def test_guard_detects_a_missing_pair(self):
        """恒真的守门等于没有守门：抽掉一条已收录配对，本断言必须变红"""
        router_schemas = _response_schemas_from_routers()
        orm_models = _all_orm_models()
        pairs = {(m.__name__, s.__name__) for m, s in ORM_SCHEMA_PAIRS}
        assert _unenrolled(router_schemas, orm_models, pairs) == {}

        # 必须挑一条「守门真的在比对」的收录项：集合顺序不定，随手取第一条可能落在
        # 没挂 REST 出口或同名字段不够的配对上，去掉它守门自然没反应（那是假绿）。
        dropped = next(
            (c for c in sorted(pairs) if _unenrolled(router_schemas, orm_models, pairs - {c})),
            None,
        )
        assert dropped, "去掉任何一条收录守门都毫无反应——它并不真的在比对"
