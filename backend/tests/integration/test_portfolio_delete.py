# ============================================================================
# 集成测试：空壳组合（draft/closed）的删除（issue #820，扩围口径）
# ============================================================================
# 覆盖 SPEC v1.1 §6.1 的验收断言（判据扩围为 status ∈ {draft, closed}）：
# 1. 「status ∈ {draft, closed} **且** 依赖表计数全为 0」才放行——两个条件各自成组，
#    合并测会漏掉「unconfirm 把 active 退回 draft 而申赎行仍在」这一形态（事实 C1）
# 2. 不级联：任一依赖表有行即 409，且父行与子行都完好
# 3. 状态码口径：401/403/404/409 各归其位；重复删除第二次是 404 而非 500
# 4. code 释放：删除后同 code 可立即重建（issue 的核心痛点）
# 5. 审计：成功恰好一条 delete/portfolio 记录；失败路径零审计
#
# 依赖表的「有哪些」由 tests/unit/test_portfolio_delete_dependents.py 钉住，
# 本文件钉「有行即拒 + 拒了不动数据」，两处不可互相替代。
# 造数一律 function 级现造、`PDEL_` 前缀独立码，不动 session 种子。
# ============================================================================

import json
from datetime import date, datetime

import pytest

from app.constants.api_key_scopes import key_allows
from app.models.audit_log import AuditLog
from app.models.investor_holding import InvestorHolding
from app.models.manual_market_value import ManualMarketValue
from app.models.portfolio import Portfolio
from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.share_change_event import ShareChangeEvent
from app.models.subscription import Subscription
from app.models.trade import Trade
from tests.factories import (
    create_investor_holding,
    create_manual_market_value,
    create_portfolio,
    create_position_snapshot,
    create_share_change_event,
    create_subscription,
    create_trade,
    create_value_snapshot,
)

SNAP_DATE = date(2025, 1, 6)

# 7 张依赖表 → 造一行。表名与 `_count_portfolio_dependents` 的元数据派生同集合。
DEPENDENCY_TABLES = {
    "subscription": lambda db, code: create_subscription(
        db, portfolio_code=code, investor_code="ADMIN"
    ),
    "trade": lambda db, code: create_trade(db, code, "000300.OF", "CN_OTC"),
    "portfolio_position": lambda db, code: create_position_snapshot(
        db, code, "000300.OF", "CN_OTC", snapshot_date=SNAP_DATE, shares=100.0
    ),
    "portfolio_value_snapshot": lambda db, code: create_value_snapshot(
        db, code, SNAP_DATE, total_value=1000.0, total_shares=100.0, unit_price=10.0
    ),
    "investor_holding": lambda db, code: create_investor_holding(
        db, code, "ADMIN", SNAP_DATE, shares=100.0
    ),
    "manual_market_value": lambda db, code: create_manual_market_value(
        db, code, "MYCF", "000300.OF", SNAP_DATE, market_value=1000.0
    ),
    "share_change_event": lambda db, code: create_share_change_event(
        db, code, "000300.OF", "CN_OTC", event_type="share_split", ratio=2.0
    ),
}

# 依赖表 → 独立短码。`Portfolio.code` 为 String(20)，MySQL 严格模式下
# 超长会报 DataError(1406)，故此处不直接用表名。
DEPENDENCY_CODES = {
    "subscription": "SUB",
    "trade": "TRD",
    "portfolio_position": "POS",
    "portfolio_value_snapshot": "SNAP",
    "investor_holding": "HOLD",
    "manual_market_value": "MMV",
    "share_change_event": "SCE",
}


def _detail(resp):
    return resp.json()["detail"]


def _audit_rows(db, resource_id):
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "delete",
            AuditLog.resource_type == "portfolio",
            AuditLog.resource_id == resource_id,
        )
        .all()
    )


@pytest.fixture
def draft(test_db):
    """空壳草稿组合（零依赖行）——删除放行的基准形态"""
    return create_portfolio(test_db, code="PDEL_EMPTY", name="待删除草稿", status="draft")


class TestDeleteEmptyDraft:
    def test_success_removes_row_and_echoes_code(self, client, admin_headers, test_db, draft):
        resp = client.delete("/api/portfolios/PDEL_EMPTY", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        # 回执文案口径（#687）：中文 + 回显用户输入的业务码
        assert resp.json() == {"message": "组合 PDEL_EMPTY 删除成功"}

        assert test_db.query(Portfolio).filter(Portfolio.code == "PDEL_EMPTY").first() is None
        # 读侧 404 由 router 的 HTTPException 渲染（裸字符串 detail，见 routers/portfolios.py），
        # 与删除端点经 NotFoundError 的结构化 detail 形态不同
        got = client.get("/api/portfolios/PDEL_EMPTY", headers=admin_headers)
        assert got.status_code == 404
        assert got.json()["detail"] == "组合 PDEL_EMPTY 不存在"

        listing = client.get("/api/portfolios", headers=admin_headers).json()
        assert "PDEL_EMPTY" not in {item["code"] for item in listing["items"]}

    def test_code_is_reusable_after_delete(self, client, admin_headers, draft):
        assert client.delete("/api/portfolios/PDEL_EMPTY", headers=admin_headers).status_code == 200

        again = client.post(
            "/api/portfolios",
            json={"code": "PDEL_EMPTY", "name": "重建的同码组合"},
            headers=admin_headers,
        )
        assert again.status_code == 200, again.text
        assert again.json()["code"] == "PDEL_EMPTY"

    def test_second_delete_is_404_not_500(self, client, admin_headers, draft):
        assert client.delete("/api/portfolios/PDEL_EMPTY", headers=admin_headers).status_code == 200

        second = client.delete("/api/portfolios/PDEL_EMPTY", headers=admin_headers)
        assert second.status_code == 404, second.text
        assert _detail(second)["error"] == "NOT_FOUND"

    def test_records_one_audit_row(self, client, admin_headers, test_db, draft):
        assert client.delete("/api/portfolios/PDEL_EMPTY", headers=admin_headers).status_code == 200

        rows = _audit_rows(test_db, "PDEL_EMPTY")
        assert len(rows) == 1, f"期望恰好 1 条删除审计，实得 {len(rows)}"
        row = rows[0]
        assert row.resource_name == "待删除草稿"
        assert row.new_value is None
        old = json.loads(row.old_value)
        assert old == {
            "code": "PDEL_EMPTY",
            "name": "待删除草稿",
            "status": "draft",
            "started_at": None,
            "closed_at": None,
        }


class TestStatusGate:
    def test_active_rejected_with_409(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="PDEL_ACT", name="活跃组合", status="active")

        resp = client.delete("/api/portfolios/PDEL_ACT", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        detail = _detail(resp)
        assert detail["error"] == "PORTFOLIO_NOT_DELETABLE"
        assert detail["message"] == "仅空壳草稿或已关闭组合可删除，当前状态: active"
        assert detail["details"] == {"status": "active"}

        assert test_db.query(Portfolio).filter(Portfolio.code == "PDEL_ACT").first() is not None
        assert _audit_rows(test_db, "PDEL_ACT") == []

    def test_missing_portfolio_is_404(self, client, admin_headers):
        resp = client.delete("/api/portfolios/PDEL_NONE", headers=admin_headers)
        assert resp.status_code == 404, resp.text
        assert _detail(resp) == {"error": "NOT_FOUND", "message": "组合 PDEL_NONE 不存在"}


class TestDeleteEmptyClosed:
    """扩围口径（Collyn 2026-11-05「空壳组合纳入验收」）：closed 空壳同样可删。

    注意该形态经公共 API **不可构造**——激活必落一行 subscription，close 只改
    status；故 closed 空壳只存在于历史数据/直接造数场景，用 ORM 复现。
    """

    @pytest.fixture
    def closed(self, test_db):
        p = create_portfolio(test_db, code="PDEL_CLS", name="已关闭空壳", status="closed")
        p.closed_at = datetime(2025, 1, 6, 12, 0)
        test_db.commit()
        return p

    def test_success_removes_row_and_echoes_code(self, client, admin_headers, test_db, closed):
        resp = client.delete("/api/portfolios/PDEL_CLS", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"message": "组合 PDEL_CLS 删除成功"}
        assert test_db.query(Portfolio).filter(Portfolio.code == "PDEL_CLS").first() is None

    def test_records_one_audit_row_with_closed_fields(self, client, admin_headers, test_db, closed):
        assert client.delete("/api/portfolios/PDEL_CLS", headers=admin_headers).status_code == 200

        rows = _audit_rows(test_db, "PDEL_CLS")
        assert len(rows) == 1, f"期望恰好 1 条删除审计，实得 {len(rows)}"
        row = rows[0]
        assert row.resource_name == "已关闭空壳"
        assert row.new_value is None
        old = json.loads(row.old_value)
        assert old == {
            "code": "PDEL_CLS",
            "name": "已关闭空壳",
            "status": "closed",
            "started_at": None,
            "closed_at": "2025-01-06T12:00:00",
        }

    def test_code_is_reusable_after_delete(self, client, admin_headers, closed):
        assert client.delete("/api/portfolios/PDEL_CLS", headers=admin_headers).status_code == 200

        again = client.post(
            "/api/portfolios",
            json={"code": "PDEL_CLS", "name": "重建的同码组合"},
            headers=admin_headers,
        )
        assert again.status_code == 200, again.text
        assert again.json()["code"] == "PDEL_CLS"

    def test_second_delete_is_404_not_500(self, client, admin_headers, closed):
        assert client.delete("/api/portfolios/PDEL_CLS", headers=admin_headers).status_code == 200

        second = client.delete("/api/portfolios/PDEL_CLS", headers=admin_headers)
        assert second.status_code == 404, second.text
        assert _detail(second)["error"] == "NOT_FOUND"


class TestDependentDataGate:
    @pytest.mark.parametrize("table", sorted(DEPENDENCY_TABLES))
    def test_dependent_row_blocks_delete_without_cascade(
        self, client, admin_headers, test_db, table
    ):
        code = f"PDEL_{DEPENDENCY_CODES[table]}"
        create_portfolio(test_db, code=code, name="有依赖的草稿", status="draft")
        child = DEPENDENCY_TABLES[table](test_db, code)

        resp = client.delete(f"/api/portfolios/{code}", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        detail = _detail(resp)
        assert detail["error"] == "PORTFOLIO_HAS_DEPENDENT_DATA"
        assert detail["message"] == "组合仍存在关联数据，无法删除"
        assert detail["details"] == {"dependencies": {table: 1}}

        # 父行未被删、子行未被级联清掉、失败不留审计
        assert test_db.query(Portfolio).filter(Portfolio.code == code).first() is not None
        assert test_db.get(type(child), child.id) is not None
        assert _audit_rows(test_db, code) == []

    def test_unconfirmed_first_subscription_leaves_draft_non_empty(
        self, client, admin_headers, test_db
    ):
        """事实 C1：unconfirm 把 active 退回 draft，申赎行仍在——只信 status 会误删。

        用 ORM 直接复现该形态（status=draft + 一行 subscription），删除必须被
        依赖计数拦下，而不是被状态门放行。
        """
        code = "PDEL_UNCONF"
        create_portfolio(test_db, code=code, name="回退成草稿的组合", status="draft")
        create_subscription(test_db, portfolio_code=code, investor_code="ADMIN")

        resp = client.delete(f"/api/portfolios/{code}", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        assert _detail(resp)["error"] == "PORTFOLIO_HAS_DEPENDENT_DATA"
        assert test_db.query(Subscription).filter(Subscription.portfolio_code == code).count() == 1

    def test_closed_with_dependent_data_rejected(self, client, admin_headers, test_db):
        """扩围口径下 closed 的放行同样以「零依赖行」为条件：有账即 409。

        抽 subscription 一表即可——依赖计数与 status 无关，逐表覆盖由上面的
        参数化用例承担。
        """
        code = "PDEL_CLD"
        create_portfolio(test_db, code=code, name="已关闭且有账", status="closed")
        create_subscription(test_db, portfolio_code=code, investor_code="ADMIN")

        resp = client.delete(f"/api/portfolios/{code}", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        detail = _detail(resp)
        assert detail["error"] == "PORTFOLIO_HAS_DEPENDENT_DATA"
        assert detail["details"] == {"dependencies": {"subscription": 1}}
        assert test_db.query(Portfolio).filter(Portfolio.code == code).first() is not None
        assert _audit_rows(test_db, code) == []


class TestPermissions:
    def test_viewer_forbidden(self, client, viewer_headers, draft):
        resp = client.delete("/api/portfolios/PDEL_EMPTY", headers=viewer_headers)
        assert resp.status_code == 403, resp.text
        assert _detail(resp)["message"] == "需要管理员权限"

    def test_unauthenticated(self, client, draft):
        resp = client.delete("/api/portfolios/PDEL_EMPTY")
        assert resp.status_code == 401, resp.text

    def test_api_key_scope_allowlist_not_extended(self):
        """新路由不进白名单即默认拒绝（口径与 `ledger:write`「不追加删除」一致）。

        判据是 scope 表的静态事实——删除端点刻意未被登记，任何后续把它加进
        WRITE_OPERATIONS 的改动都会让本断言翻红，迫使作者显式决策。
        """
        assert key_allows(["ledger:write"], "DELETE", "/api/portfolios/{code}") is False


class TestChildModelsReferenced:
    """参数化名单与子表模型一一对应（防止登记表漂移出「静默少测一张表」）。"""

    def test_registry_covers_seven_child_tables(self):
        assert sorted(DEPENDENCY_TABLES) == [
            "investor_holding",
            "manual_market_value",
            "portfolio_position",
            "portfolio_value_snapshot",
            "share_change_event",
            "subscription",
            "trade",
        ]
        for model in (
            InvestorHolding,
            ManualMarketValue,
            PortfolioPosition,
            PortfolioValueSnapshot,
            ShareChangeEvent,
            Subscription,
            Trade,
        ):
            assert model.__tablename__ in DEPENDENCY_TABLES
