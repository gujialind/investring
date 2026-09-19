# ============================================================================
# 集成测试守门：返回 JSON 的端点必须声明 response_model（issue #513）
# ============================================================================
# 背景：#487 的 ORM 泄漏根因是端点没声明 response_model——FastAPI 走
# `jsonable_encoder` 把 ORM 行全列直吐（password_hash 外流）。静态扫描证明
# 未声明端点会随接口增长悄悄变多，故把判据固化为守门：
#
# - 扫描器复用 tests/integration/route_scan_helpers.py（#306 的版本无关
#   duck-typing 反射；不要用 isinstance(route, APIRoute)——0.141 顶层是懒
#   物化代理，会命中 0 条静默假通过）。
# - 判「返回 JSON」：动态流（SSE/JSON stream）与显式非 JSONResponse 的
#   response_class 除外（这两类没有 ORM 直吐问题）。
# - 白名单逐条给理由，**只减不增**：删掉已声明端点的行会触发 stale 断言，
#   新增未声明端点必须有新行，否则红。
# - 互证：与 checked-in `backend/openapi.json` 的「200 无 JSON schema」集合
#   对齐（漂移由 backend/check_openapi.py 单独守门，本文件只读该文件、不在
#   测试里调 app.openapi() 生成）。`include_in_schema=False` 的端点不在
#   openapi 里，故不参与互证（当前为 0 条）。
# ============================================================================

import json
from pathlib import Path

from app.main import app
from tests.integration.route_scan_helpers import scan_operations

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi.json"

# 白名单：(METHODS, path) -> 未声明 response_model 的理由。METHODS 为逗号连接
# 的升序方法名（与扫描器 _key 同口径）。理由必须写清「响应体的形态为何不适合
# 单一模型」——纯消息型、包装型、计算派生型、守卫型四类，逐条以 handler 实际
# 返回语句为准（勿照抄分类猜）。
WHITELIST: dict[tuple[str, str], str] = {
    # ---- 应用元信息（非 /api，探针/欢迎语，固定键）----
    ("GET", "/"): "应用欢迎语，固定 {'message'}；非业务数据",
    ("GET", "/health"): "健康检查，固定 {'status': 'healthy'}；Docker/CI 探针契约",
    # ---- 纯消息确认型：动作回执，无业务数据体 ----
    ("POST", "/api/auth/logout"): "登出回执，仅 {'message'}",
    ("PUT", "/api/auth/password"): "改密回执，仅 {'message'}",
    ("DELETE", "/api/investors/{code}"): "删除回执，仅 {'message'}",
    ("POST", "/api/portfolios/{code}/close"): "关闭回执，仅 {'message'}",
    ("POST", "/api/portfolios/{code}/reactivate"): "重开回执，仅 {'message'}",
    ("DELETE", "/api/platforms/{code}"): "删除回执，仅 {'message'}",
    ("DELETE", "/api/products/{code}/{market}"): "删除回执，仅 {'message'}",
    ("DELETE", "/api/share-change-events/{id}"): "删除回执，仅 {'message'}",
    ("POST", "/api/share-change-events/{id}/cancel"): "取消回执，仅 {'message'}",
    ("DELETE", "/api/subscriptions/{id}"): "删除回执，仅 {'message'}",
    ("POST", "/api/subscriptions/{id}/cancel"): "取消回执，仅 {'message'}",
    ("POST", "/api/subscriptions/{id}/unconfirm"): "回退确认回执，仅 {'message'}",
    ("POST", "/api/system/notifications/read-all"): "全部已读回执，仅 {'message'}",
    ("POST", "/api/system/notifications/{id}/read"): "已读回执，仅 {'message'}",
    ("POST", "/api/system/tasks/{code}/enable"): "启用回执，仅 {'message'}",
    ("POST", "/api/system/tasks/{code}/disable"): "停用回执，仅 {'message'}",
    ("DELETE", "/api/trades/{id}"): "删除回执，仅 {'message'}",
    ("POST", "/api/trades/{id}/cancel"): "取消回执，仅 {'message'}",
    ("POST", "/api/trades/{id}/unconfirm"): "回退确认回执，仅 {'message'}",
    ("DELETE", "/api/snapshots/{portfolio_code}/{snapshot_date}"):
        "删除回执，message 文本内嵌级联计数，键随级联分支可变（cascaded_subscriptions/events）",
    ("DELETE", "/api/snapshots/{portfolio_code}/bulk/{from_date}"):
        "同路径两形态：dry_run 预览与实际删除的键不同（dry_run/snapshot_dates vs details/deleted_count）",
    # ---- 计算/派生型：service 计算的即席 dict，键随业务分支变化 ----
    ("GET", "/api/market-data/products/{code}/{market}/nav-coverage"):
        "覆盖率计算结果 dict，键随区间与缺失数据变化（service 拼装）",
    ("POST", "/api/market-data/products/{code}/{market}/sync-price-data"):
        "同步结果 dict（source/message/计数），随数据源与成功失败分支变化",
    ("POST", "/api/market-data/products/{code}/{market}/sync-history"):
        "同 sync-price-data 的同步结果 dict（历史区间口径）",
    ("GET", "/api/portfolios/{code}/cash-flow"):
        "现金流计算结果 dict，随期间与交易类型变化（service 拼装）",
    ("GET", "/api/portfolios/{code}/returns"):
        "收益计算结果 dict（累计/年化），随快照区间变化（service 拼装）",
    ("GET", "/api/positions/portfolio/{portfolio_code}/available-cash"):
        "即席可用现金 dict；platform_code 键仅在传参时出现",
    ("GET", "/api/positions/portfolio/{portfolio_code}/product/{product_code}/available-shares"):
        "即席可用份额 dict（4 键），market 可为 None，无对应 schema",
    ("GET", "/api/positions/portfolio/{portfolio_code}/investor/{investor_code}/available-shares"):
        "即席投资人可用份额 dict（3 键），无对应 schema",
    ("DELETE", "/api/positions/portfolio/{portfolio_code}/cash-position"):
        "删除现金覆盖回执 dict（success/message/回退量），随删除结果变化",
    ("POST", "/api/positions/portfolio/{portfolio_code}/cash-position"):
        "写现金覆盖回执 dict；warnings 随同日已确认现金交易分支可变",
    ("GET", "/api/positions/portfolio/{portfolio_code}/cash-position"):
        "覆盖记录列表 {'items','total'}，非标准分页（无 page/page_size），元素为 service dict",
    ("GET", "/api/portfolios/{portfolio_code}/cash-transfers"):
        "转移记录分页；元素已由 CashTransferListItem 收窄后 model_dump，但无分页集合 schema",
    ("POST", "/api/portfolios/{portfolio_code}/cash-transfer/{transfer_group}/confirm"):
        "确认回执 dict（message + transfer_group/confirmed_count/ISO 日期）",
    ("POST", "/api/snapshots/recalculate-async"):
        "异步受理回执（job_id/status/message），与 sync-jobs/price 同型",
    ("POST", "/api/sync-jobs/price"):
        "异步受理回执（job_id/status/message），与 snapshots/recalculate-async 同型",
    ("GET", "/api/system/data-sources"):
        "数据源列表，元素是脱敏 api_key 后的即席 dict（无 ORM 行泄漏）",
    ("PUT", "/api/system/data-sources/{name}"):
        "更新回执 dict（message/name/is_enabled），随数据源分支变化",
    ("POST", "/api/system/tasks/{code}/run"):
        "#406 刻意保持 message + 任务 runner 结果的动态键（log_cleanup 与其它任务两套形态）",
    # ---- 包装型：message + 既有 Response 嵌套，非单一模型 ----
    ("POST", "/api/subscriptions/{id}/confirm"):
        "回执 message + SubscriptionResponse 嵌在 subscription 键，另平铺 7 个标量",
    ("POST", "/api/trades/{id}/confirm"):
        "回执 message + TradeResponse 嵌在 trade 键，另平铺 id/portfolio_code/trade_type/status/confirm_date",
    ("POST", "/api/share-change-events/{id}/confirm"):
        "回执 message + ShareChangeEventResponse 嵌在 event 键",
    ("POST", "/api/share-change-events/{id}/unconfirm"):
        "回执 message + ShareChangeEventResponse 嵌在 event 键",
    ("GET", "/api/sync-jobs/{job_id}/details"):
        "job(SyncJobResponse) + details(NavSyncDetailResponse[]) 双键包装，非单一模型",
    # ---- 守卫型：无成功响应体 ----
    ("DELETE", "/api/positions/{id}"):
        "恒抛 422 POSITION_TABLE_PROTECTED（快照表禁手改），无成功响应体",
}

_JSON_METHODS = {"get", "post", "put", "delete", "patch"}


def _key(op) -> tuple[str, str]:
    return (",".join(sorted(op.methods)), op.path)


def _missing_response_model(*, in_schema_only: bool = False) -> set[tuple[str, str]]:
    return {
        _key(op)
        for op in scan_operations(app)
        if op.returns_json() and op.response_model is None
        and (op.include_in_schema or not in_schema_only)
    }


def _format(keys) -> str:
    return "\n".join(f'    ("{m}", "{p}"): "…",' for m, p in sorted(keys))


def _empty_200_ops_from_spec() -> set[tuple[str, str]]:
    """checked-in openapi.json 中「200 响应无 JSON schema」的操作集合

    未声明 response_model 时 FastAPI 仍写 `content.application/json.schema = {}`
    （content 存在但 schema 为空壳），故判据是 schema 为空而非 content 缺失。
    """
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    found = set()
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in _JSON_METHODS:
                continue  # 跳过 path 级 parameters/summary 等非操作键
            response_200 = (operation.get("responses") or {}).get("200") or {}
            content = response_200.get("content") or {}
            schema = (content.get("application/json") or {}).get("schema")
            if not schema:
                found.add((method.upper(), path))
    return found


class TestResponseModelGuard:
    """返回 JSON 的端点：要么声明 response_model，要么在白名单里附理由"""

    def test_unregistered_endpoint_without_response_model(self):
        """新端点未声明 response_model 且未登记 → 红"""
        undeclared = _missing_response_model() - set(WHITELIST)
        assert not undeclared, (
            "以下端点返回 JSON 但未声明 response_model，且不在白名单中：\n"
            f"{_format(undeclared)}\n"
            "收口方式：声明 response_model（推荐，参照 routers/investors.py 的 "
            "PaginatedInvestorResponse 写法）；确属动态/包装响应体才登记白名单并写明理由。"
        )

    def test_whitelist_entry_is_stale(self):
        """白名单行对应的端点已声明 response_model 或已删除 → 红（棘轮只减不增）"""
        stale = set(WHITELIST) - _missing_response_model()
        assert not stale, (
            "以下白名单行已失效（端点已声明 response_model 或端点已删除），请删除对应行：\n"
            f"{_format(stale)}"
        )

    def test_whitelist_reasons_not_empty(self):
        """白名单必须逐条写理由，禁止占位"""
        blank = [key for key, reason in WHITELIST.items() if not reason.strip()]
        assert not blank, f"白名单理由为空：{_format(blank)}"

    def test_openapi_empty_200_ops_match_whitelist(self):
        """与 checked-in openapi.json 互证：无 200 JSON schema 的操作恰为登记集合

        两个独立来源（app 反射扫描 vs 契约快照）必须一致：扫描器若退化扫不到
        端点，这里会多出 openapi 侧的条目而报红。`include_in_schema=False` 的
        端点不在 openapi 内，不参与互证（当前为 0 条）。
        """
        actual_in_schema = _missing_response_model(in_schema_only=True)
        spec_ops = _empty_200_ops_from_spec()
        only_spec = spec_ops - actual_in_schema
        only_scan = actual_in_schema - spec_ops
        assert not only_spec and not only_scan, (
            "app 扫描与 backend/openapi.json 口径不一致：\n"
            f"  仅在 openapi（扫描器漏扫或该端点未声明却已被登记遗漏）：\n{_format(only_spec)}\n"
            f"  仅在扫描（openapi 已带 schema，说明重导未做或声明失效）：\n{_format(only_scan)}\n"
            "若刚改过 router/schema，请按 backend/check_openapi.py 头部步骤重导 openapi.json。"
        )
