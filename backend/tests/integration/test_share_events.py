# ============================================================================
# 集成测试：份额变动事件 (test_share_events.py)
# ============================================================================

import pytest
from datetime import date, datetime
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_product, create_platform,
    create_share_change_event, create_position_snapshot,
    create_value_snapshot, ensure_trading_day,
)
from app.models.share_change_event import ShareChangeEvent
from app.models.audit_log import AuditLog
from app.models.portfolio_position import PortfolioPosition
from app.schemas.share_change_event import ShareChangeEventResponse


# ---- issue #461：LOF 一码多市场测试基线（同 code 双市场产品 + 双平台 + ENT/EX 交易日） ----

LOF461_CODE = "LOF461.SZ"
LOF461_ENT = date(2025, 12, 8)   # 权益登记日（基线快照日）
LOF461_EX = date(2025, 12, 10)   # 除息日


def _setup_lof_products(test_db, portfolio_code):
    create_portfolio(test_db, code=portfolio_code, status="active")
    create_product(test_db, code=LOF461_CODE, market="CN_EXCHANGE",
                   product_type="LOF", asset_class_code="ASSET_STOCK")
    create_product(test_db, code=LOF461_CODE, market="CN_OTC",
                   product_type="LOF", asset_class_code="ASSET_STOCK")
    create_platform(test_db, code="MYCF")
    create_platform(test_db, code="HBZQ")
    ensure_trading_day(test_db, LOF461_ENT, is_open=True)
    ensure_trading_day(test_db, LOF461_EX, is_open=True)


def _setup_lof_baseline(test_db, portfolio_code, *, exch_shares=100.0, otc_shares=50.0,
                        exch_platform="MYCF", otc_platform="MYCF"):
    """双市场权益登记日基线；platform 传 None 表示该市场不建持仓行。
    CN_EXCHANGE 行先建——修复前平台级 .first() 会读到它，让用例在修复前真红。"""
    _setup_lof_products(test_db, portfolio_code)
    total = 0.0
    if exch_platform:
        create_position_snapshot(
            test_db, portfolio_code, LOF461_CODE, "CN_EXCHANGE",
            snapshot_date=LOF461_ENT, shares=exch_shares, unit_price=1.0,
            cost_price=1.0, market_value=exch_shares, platform_code=exch_platform,
        )
        total += exch_shares
    if otc_platform:
        create_position_snapshot(
            test_db, portfolio_code, LOF461_CODE, "CN_OTC",
            snapshot_date=LOF461_ENT, shares=otc_shares, unit_price=1.0,
            cost_price=1.0, market_value=otc_shares, platform_code=otc_platform,
        )
        total += otc_shares
    create_value_snapshot(test_db, portfolio_code, LOF461_ENT,
                          total_value=total, total_shares=total, unit_price=1.0)


class TestShareChangeEventCreate:
    """份额变动事件创建测试"""

    def test_create_cash_dividend_event(self, client, admin_headers, test_db):
        """创建现金分红事件"""
        create_portfolio(test_db, code="SCE_P1", status="active")
        create_product(test_db, code="FUND_SC1", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, date(2025, 12, 8), is_open=True)

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCE_P1",
                "product_code": "FUND_SC1",
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-08",
                "event_source": "manual",
                "div_cash": 0.5,
                "platform_code": "MYCF",  # 平台级事件必填
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["event_type"] == "cash_dividend"
        assert data["status"] == "pending"

    def test_create_reinvest_dividend_event(self, client, admin_headers, test_db):
        """创建分红再投资事件"""
        create_portfolio(test_db, code="SCE_P2", status="active")
        create_product(test_db, code="FUND_SC2", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, date(2025, 12, 8), is_open=True)

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCE_P2",
                "product_code": "FUND_SC2",
                "market": "CN_OTC",
                "event_type": "reinvest_dividend",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-08",
                "event_source": "manual",
                "div_cash": 0.5,
                "reinvest_nav": 1.2,
                "platform_code": "MYCF",  # 平台级事件必填
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["event_type"] == "reinvest_dividend"

    def test_create_share_split_event(self, client, admin_headers, test_db):
        """创建份额拆分事件"""
        create_portfolio(test_db, code="SCE_P3", status="active")
        create_product(test_db, code="FUND_SC3", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, date(2025, 12, 8), is_open=True)

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCE_P3",
                "product_code": "FUND_SC3",
                "market": "CN_OTC",
                "event_type": "share_split",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-08",
                "event_source": "manual",
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)

    def test_entitlement_date_non_trading_day_rejected(self, client, admin_headers, test_db):
        """权益登记日非交易日应被拒绝"""
        create_portfolio(test_db, code="SCE_NTD", status="active")
        create_product(test_db, code="FUND_NT", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, date(2025, 12, 6), is_open=False)  # 周六

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCE_NTD",
                "product_code": "FUND_NT",
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-06",
                "event_source": "manual",
                "div_cash": 0.5,
                "platform_code": "MYCF",  # 平台级事件必填
            },
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422)

    def test_platform_coverage_default_blocked(self, client, admin_headers, test_db):
        """#40 改进3：多平台持仓只录 1 平台 → 默认阻断 PLATFORM_NOT_COVERED"""
        create_portfolio(test_db, code="SCE_FC1", status="active")
        create_product(test_db, code="FUND_FC1", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, date(2025, 12, 8), is_open=True)
        # 2 平台均有持仓
        create_position_snapshot(
            test_db, portfolio_code="SCE_FC1", product_code="FUND_FC1",
            market="CN_OTC", snapshot_date=date(2025, 12, 8),
            shares=100.0, platform_code="MYCF", market_value=100.0,
        )
        create_position_snapshot(
            test_db, portfolio_code="SCE_FC1", product_code="FUND_FC1",
            market="CN_OTC", snapshot_date=date(2025, 12, 8),
            shares=200.0, platform_code="HBZQ", market_value=200.0,
        )
        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCE_FC1",
                "product_code": "FUND_FC1",
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-08",
                "event_source": "manual",
                "div_cash": 0.5,
                "platform_code": "MYCF",  # 只覆盖 MYCF，HBZQ 未覆盖
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PLATFORM_NOT_COVERED"

    def test_force_cover_allows_creation(self, client, admin_headers, test_db):
        """#40 改进3：force_cover=true 降为 warning，创建成功"""
        create_portfolio(test_db, code="SCE_FC2", status="active")
        create_product(test_db, code="FUND_FC2", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, date(2025, 12, 8), is_open=True)
        create_position_snapshot(
            test_db, portfolio_code="SCE_FC2", product_code="FUND_FC2",
            market="CN_OTC", snapshot_date=date(2025, 12, 8),
            shares=100.0, platform_code="MYCF", market_value=100.0,
        )
        create_position_snapshot(
            test_db, portfolio_code="SCE_FC2", product_code="FUND_FC2",
            market="CN_OTC", snapshot_date=date(2025, 12, 8),
            shares=200.0, platform_code="HBZQ", market_value=200.0,
        )
        resp = client.post(
            "/api/share-change-events?force_cover=true",
            json={
                "portfolio_code": "SCE_FC2",
                "product_code": "FUND_FC2",
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-08",
                "event_source": "manual",
                "div_cash": 0.5,
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["status"] == "pending"

    def test_platform_coverage_scopes_holdings_to_market(self, client, admin_headers, test_db):
        """#461：另一市场持仓的平台不参与覆盖校验（修复前 held_platforms 跨市场合并 → 假阳性 422）"""
        _setup_lof_baseline(
            test_db, "SCE_FC3",
            exch_shares=100.0, otc_shares=100.0,
            exch_platform="HBZQ", otc_platform="MYCF",
        )
        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCE_FC3",
                "product_code": LOF461_CODE,
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-08",
                "event_source": "manual",
                "div_cash": 0.5,
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"

    def test_platform_coverage_excludes_other_market_events(self, client, admin_headers, test_db):
        """#461：另一市场已录事件不算作本市场平台的覆盖（修复前 existing 跨市场合并 → 假阴性放行）"""
        _setup_lof_products(test_db, "SCE_FC4")
        for platform, shares in (("MYCF", 100.0), ("HBZQ", 200.0)):
            create_position_snapshot(
                test_db, "SCE_FC4", LOF461_CODE, "CN_OTC",
                snapshot_date=LOF461_ENT, shares=shares, unit_price=1.0,
                cost_price=1.0, market_value=shares, platform_code=platform,
            )
        # 存量数据：另一市场（CN_EXCHANGE）的事件恰好落在 HBZQ——修复前会把它误判为已覆盖
        create_share_change_event(
            test_db, "SCE_FC4", LOF461_CODE, "CN_EXCHANGE",
            event_type="cash_dividend", ex_date=LOF461_EX,
            entitlement_date=LOF461_ENT, platform_code="HBZQ",
            div_cash=Decimal("0.5"),
        )
        payload = {
            "portfolio_code": "SCE_FC4",
            "product_code": LOF461_CODE,
            "market": "CN_OTC",
            "event_type": "cash_dividend",
            "ex_date": "2025-12-10",
            "entitlement_date": "2025-12-08",
            "event_source": "manual",
            "div_cash": 0.5,
            "platform_code": "MYCF",
        }
        resp = client.post("/api/share-change-events", json=payload, headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PLATFORM_NOT_COVERED"

        # 补录同市场同 ex_date 的 HBZQ 事件后不再阻断
        create_share_change_event(
            test_db, "SCE_FC4", LOF461_CODE, "CN_OTC",
            event_type="cash_dividend", ex_date=LOF461_EX,
            entitlement_date=LOF461_ENT, platform_code="HBZQ",
            div_cash=Decimal("0.5"),
        )
        resp2 = client.post("/api/share-change-events", json=payload, headers=admin_headers)
        assert resp2.status_code in (200, 201), f"Response: {resp2.status_code} {resp2.json()}"


class TestShareChangeEventList:
    """份额变动事件列表测试"""

    def test_list_events(self, client, admin_headers, test_db):
        """获取事件列表"""
        resp = client.get("/api/share-change-events", headers=admin_headers)
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_viewer_cannot_create_event(self, client, viewer_headers):
        """viewer 不能创建份额变动事件"""
        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "X",
                "product_code": "X",
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": "2025-12-10",
                "entitlement_date": "2025-12-08",
                "event_source": "manual",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestShareChangeEventCancel:
    """份额变动事件取消测试"""

    def test_cancel_pending_event(self, client, admin_headers, test_db):
        """取消 pending 事件"""
        create_portfolio(test_db, code="SCE_CAN", status="active")
        create_product(test_db, code="FUND_CAN", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        event = create_share_change_event(
            test_db, "SCE_CAN", "FUND_CAN", "CN_OTC",
            status="pending",
        )
        resp = client.post(
            f"/api/share-change-events/{event.id}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        updated = test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.id == event.id
        ).first()
        assert updated.status == "cancelled"


class TestShareChangeEventUnconfirm:
    """#38 份额变动事件 unconfirm 接口"""

    def _seed_confirmed_platform_event(self, test_db, portfolio_code="SCE_UNC",
                                        platform_code="SCE_PLAT"):
        create_portfolio(test_db, code=portfolio_code, status="active")
        create_product(test_db, code="FUND_UNC", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code=platform_code)
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 8), is_open=True)
        create_value_snapshot(test_db, portfolio_code, date(2025, 10, 6),
                              total_value=1000, total_shares=1000, unit_price=1.0)
        create_position_snapshot(
            test_db, portfolio_code, "FUND_UNC", "CN_OTC", date(2025, 10, 6),
            shares=1000, platform_code=platform_code,
        )
        event = create_share_change_event(
            test_db, portfolio_code, "FUND_UNC", "CN_OTC",
            event_type="cash_dividend", ex_date=date(2025, 10, 8),
            entitlement_date=date(2025, 10, 6), status="pending",
            platform_code=platform_code, div_cash=Decimal("0.1"),
        )
        event.entitlement_shares = Decimal("1000")
        event.shares_before = Decimal("1000")
        event.cash_change = Decimal("100")
        event.shares_change = Decimal("0")
        event.shares_after = Decimal("1000")
        event.status = "confirmed"
        event.confirmed_at = datetime.now()
        test_db.commit()
        return event

    def test_unconfirm_platform_event_success(self, client, admin_headers, test_db):
        """平台级事件 unconfirm 成功，计算字段清空"""
        event = self._seed_confirmed_platform_event(test_db)
        resp = client.post(
            f"/api/share-change-events/{event.id}/unconfirm",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.expire_all()
        updated = test_db.query(ShareChangeEvent).get(event.id)
        assert updated.status == "pending"
        assert updated.confirmed_at is None
        assert updated.cash_change is None
        assert updated.entitlement_shares is None

    def test_unconfirm_blocked_by_snapshot(self, client, admin_headers, test_db):
        """ex_date 及之后已有快照时拒绝（SNAPSHOT_DEPENDENCY）"""
        event = self._seed_confirmed_platform_event(test_db)
        # 在 ex_date 上生成快照
        create_value_snapshot(test_db, "SCE_UNC", date(2025, 10, 8),
                              total_value=1100, total_shares=1000, unit_price=1.1)
        resp = client.post(
            f"/api/share-change-events/{event.id}/unconfirm",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"

    def test_unconfirm_fund_level_cascades_children(self, client, admin_headers, test_db):
        """基金级父记录 unconfirm 级联删除所有子记录"""
        create_portfolio(test_db, code="SCE_FL", status="active")
        create_product(test_db, code="FUND_FL", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="FL_P1")
        create_platform(test_db, code="FL_P2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 8), is_open=True)
        create_value_snapshot(test_db, "SCE_FL", date(2025, 10, 6),
                              total_value=2000, total_shares=2000, unit_price=1.0)
        create_position_snapshot(
            test_db, "SCE_FL", "FUND_FL", "CN_OTC", date(2025, 10, 6),
            shares=1000, platform_code="FL_P1",
        )
        create_position_snapshot(
            test_db, "SCE_FL", "FUND_FL", "CN_OTC", date(2025, 10, 6),
            shares=1000, platform_code="FL_P2",
        )
        # 父记录
        parent = create_share_change_event(
            test_db, "SCE_FL", "FUND_FL", "CN_OTC",
            event_type="share_split", ex_date=date(2025, 10, 8),
            entitlement_date=date(2025, 10, 6), status="confirmed",
            ratio=2.0,
        )
        # 两个子记录
        for plat in ("FL_P1", "FL_P2"):
            child = create_share_change_event(
                test_db, "SCE_FL", "FUND_FL", "CN_OTC",
                event_type="share_split", ex_date=date(2025, 10, 8),
                entitlement_date=date(2025, 10, 6), status="confirmed",
                platform_code=plat, ratio=2.0, parent_event_id=parent.id,
                entitlement_shares=Decimal("1000"), shares_before=Decimal("1000"),
                shares_change=Decimal("1000"), shares_after=Decimal("2000"),
            )
        before = test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == parent.id
        ).count()
        assert before == 2

        resp = client.post(
            f"/api/share-change-events/{parent.id}/unconfirm",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.expire_all()
        after = test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == parent.id
        ).count()
        assert after == 0
        updated = test_db.query(ShareChangeEvent).get(parent.id)
        assert updated.status == "pending"

    def test_unconfirm_child_rejected(self, client, admin_headers, test_db):
        """子记录单独 unconfirm 拒绝（CANNOT_UNCONFIRM_CHILD）"""
        create_portfolio(test_db, code="SCE_CH", status="active")
        create_product(test_db, code="FUND_CH", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="CH_P1")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 8), is_open=True)
        create_value_snapshot(test_db, "SCE_CH", date(2025, 10, 6),
                              total_value=1000, total_shares=1000, unit_price=1.0)
        create_position_snapshot(
            test_db, "SCE_CH", "FUND_CH", "CN_OTC", date(2025, 10, 6),
            shares=1000, platform_code="CH_P1",
        )
        parent = create_share_change_event(
            test_db, "SCE_CH", "FUND_CH", "CN_OTC",
            event_type="share_split", ex_date=date(2025, 10, 8),
            entitlement_date=date(2025, 10, 6), status="confirmed", ratio=2.0,
        )
        child = create_share_change_event(
            test_db, "SCE_CH", "FUND_CH", "CN_OTC",
            event_type="share_split", ex_date=date(2025, 10, 8),
            entitlement_date=date(2025, 10, 6), status="confirmed",
            platform_code="CH_P1", ratio=2.0, parent_event_id=parent.id,
            entitlement_shares=Decimal("1000"), shares_before=Decimal("1000"),
            shares_change=Decimal("1000"), shares_after=Decimal("2000"),
        )
        resp = client.post(
            f"/api/share-change-events/{child.id}/unconfirm",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CANNOT_UNCONFIRM_CHILD"

    def test_unconfirm_only_confirmed_allowed(self, client, admin_headers, test_db):
        """仅 confirmed 状态可 unconfirm"""
        create_portfolio(test_db, code="SCE_PD", status="active")
        create_product(test_db, code="FUND_PD", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="PD_P1")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 8), is_open=True)
        create_value_snapshot(test_db, "SCE_PD", date(2025, 10, 6),
                              total_value=1000, total_shares=1000, unit_price=1.0)
        create_position_snapshot(
            test_db, "SCE_PD", "FUND_PD", "CN_OTC", date(2025, 10, 6),
            shares=1000, platform_code="PD_P1",
        )
        event = create_share_change_event(
            test_db, "SCE_PD", "FUND_PD", "CN_OTC",
            event_type="cash_dividend", ex_date=date(2025, 10, 8),
            entitlement_date=date(2025, 10, 6), status="pending",
            platform_code="PD_P1", div_cash=Decimal("0.1"),
        )
        resp = client.post(
            f"/api/share-change-events/{event.id}/unconfirm",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_STATUS"


class TestUpdateShareChangeEvent:
    """PUT 更新事件：confirmed 阻断、日期重校验、status 直改忽略"""

    def test_update_confirmed_event_rejected(self, client, admin_headers, test_db):
        """confirmed 事件不可直接修改，须先 unconfirm"""
        create_portfolio(test_db, code="UPE_P1", status="active")
        create_product(test_db, code="FUND_UPE1", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="UPE_PLAT1")
        ensure_trading_day(test_db, date(2025, 11, 10), is_open=True)
        ensure_trading_day(test_db, date(2025, 11, 12), is_open=True)
        event = create_share_change_event(
            test_db, "UPE_P1", "FUND_UPE1", "CN_OTC",
            event_type="cash_dividend", ex_date=date(2025, 11, 12),
            entitlement_date=date(2025, 11, 10), status="confirmed",
            platform_code="UPE_PLAT1", div_cash=Decimal("0.1"),
        )

        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"notes": "try modify confirmed"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CANNOT_MODIFY_CONFIRMED"

    def test_update_pending_event_dates_revalidated(self, client, admin_headers, test_db):
        """pending 事件改日期时重跑创建时的双日期校验"""
        create_portfolio(test_db, code="UPE_P2", status="active")
        create_product(test_db, code="FUND_UPE2", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="UPE_PLAT2")
        ensure_trading_day(test_db, date(2025, 11, 10), is_open=True)
        ensure_trading_day(test_db, date(2025, 11, 12), is_open=True)
        ensure_trading_day(test_db, date(2025, 11, 15), is_open=False)  # 周六
        event = create_share_change_event(
            test_db, "UPE_P2", "FUND_UPE2", "CN_OTC",
            event_type="cash_dividend", ex_date=date(2025, 11, 12),
            entitlement_date=date(2025, 11, 10), status="pending",
            platform_code="UPE_PLAT2", div_cash=Decimal("0.1"),
        )

        # 除息日非交易日
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"ex_date": "2025-11-15"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_EX_DATE"

        # 除息日 <= 权益登记日
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"ex_date": "2025-11-10"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_ORDER"

        # 除息日 <= 最新快照日
        create_value_snapshot(test_db, "UPE_P2", date(2025, 11, 14),
                              total_value=1000, total_shares=1000, unit_price=1.0)
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"ex_date": "2025-11-13"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "DATE_BEFORE_SNAPSHOT"

        # 合法新日期（交易日、晚于登记日与最新快照日）可正常更新
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"ex_date": "2025-11-17"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.expire_all()
        updated = test_db.query(ShareChangeEvent).get(event.id)
        assert updated.ex_date == date(2025, 11, 17)

    def test_update_status_field_ignored(self, client, admin_headers, test_db):
        """PUT 传 status 被忽略（状态流转只走 confirm/cancel/unconfirm 端点）"""
        create_portfolio(test_db, code="UPE_P3", status="active")
        create_product(test_db, code="FUND_UPE3", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="UPE_PLAT3")
        ensure_trading_day(test_db, date(2025, 11, 10), is_open=True)
        ensure_trading_day(test_db, date(2025, 11, 12), is_open=True)
        event = create_share_change_event(
            test_db, "UPE_P3", "FUND_UPE3", "CN_OTC",
            event_type="cash_dividend", ex_date=date(2025, 11, 12),
            entitlement_date=date(2025, 11, 10), status="pending",
            platform_code="UPE_PLAT3", div_cash=Decimal("0.1"),
        )

        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"status": "confirmed", "notes": "n1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.expire_all()
        updated = test_db.query(ShareChangeEvent).get(event.id)
        # status 字段被 schema 忽略，仍为 pending；其余合法字段正常更新
        assert updated.status == "pending"
        assert updated.notes == "n1"


class TestForcedAdjustmentInputValidation:
    """issue #279：forced_adjustment 双空字段与现金型产品份额变动的输入校验"""

    ENT = date(2025, 12, 8)   # 权益登记日（周一）
    EX = date(2025, 12, 10)   # 除息日（周三）

    def _setup(self, test_db, port_code: str):
        create_portfolio(test_db, code=port_code, status="active")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)

    def test_create_double_empty_adjustment_rejected(self, client, admin_headers, test_db):
        """验收：双空 forced_adjustment 创建即拒绝且不落库"""
        self._setup(test_db, "FAV_P1")
        create_product(test_db, code="FUND_FAV1", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "FAV_P1",
                "product_code": "FUND_FAV1",
                "market": "CN_OTC",
                "event_type": "forced_adjustment",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "EMPTY_ADJUSTMENT"
        assert test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.portfolio_code == "FAV_P1"
        ).count() == 0

        # 正向对照：只填一项照常创建
        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "FAV_P1",
                "product_code": "FUND_FAV1",
                "market": "CN_OTC",
                "event_type": "forced_adjustment",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "platform_code": "MYCF",
                "shares_change": 1.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)

    def test_update_to_double_empty_rejected(self, client, admin_headers, test_db):
        """验收：PUT 改成双空被拒（封死 update 绕过）"""
        self._setup(test_db, "FAV_P2")
        create_product(test_db, code="FUND_FAV2", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        event = create_share_change_event(
            test_db, "FAV_P2", "FUND_FAV2", "CN_OTC",
            event_type="forced_adjustment", ex_date=self.EX,
            entitlement_date=self.ENT, status="pending",
            platform_code="MYCF", shares_change=Decimal("1.00"),
        )

        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"shares_change": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "EMPTY_ADJUSTMENT"

        # 正常单字段更新不受影响（回归）
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"notes": "adjust"},
            headers=admin_headers,
        )
        assert resp.status_code == 200

    def test_create_shares_change_on_cash_product_rejected(self, client, admin_headers, test_db):
        """验收：CASH / IN_TRANSIT 产品录入含 shares_change 的事件被拒"""
        self._setup(test_db, "FAV_P3")

        for product_code in ("CASH", "IN_TRANSIT_BUY"):
            resp = client.post(
                "/api/share-change-events",
                json={
                    "portfolio_code": "FAV_P3",
                    "product_code": product_code,
                    "market": "",
                    "event_type": "forced_adjustment",
                    "ex_date": self.EX.isoformat(),
                    "entitlement_date": self.ENT.isoformat(),
                    "platform_code": "MYCF",
                    "shares_change": 1.0,
                },
                headers=admin_headers,
            )
            assert resp.status_code == 422, product_code
            assert resp.json()["detail"]["error"] == "SHARES_CHANGE_ON_CASH_PRODUCT", product_code

    def test_create_structural_event_on_cash_product_rejected(self, client, admin_headers, test_db):
        """结构上必产生份额变动的事件类型在现金型产品上无条件拒绝"""
        self._setup(test_db, "FAV_P4")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "FAV_P4",
                "product_code": "CASH",
                "market": "",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "SHARES_CHANGE_ON_CASH_PRODUCT"

    def test_cash_only_adjustment_on_cash_product_allowed(self, client, admin_headers, test_db):
        """边界：现金型产品上的纯现金调整（无份额语义）仍放行"""
        self._setup(test_db, "FAV_P5")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "FAV_P5",
                "product_code": "CASH",
                "market": "",
                "event_type": "forced_adjustment",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "platform_code": "MYCF",
                "cash_change": -5.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)

    def test_confirm_fallback_validation(self, test_db):
        """验收：确认侧兜底——绕过创建入口直造的脏事件在确认时被拒"""
        from app.services.exceptions import BusinessError
        from app.services.share_change_event_service import confirm_share_change_event

        self._setup(test_db, "FAV_P6")
        create_product(test_db, code="FUND_FAV6", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")

        # 双空 pending（模拟存量脏数据）
        empty_event = create_share_change_event(
            test_db, "FAV_P6", "FUND_FAV6", "CN_OTC",
            event_type="forced_adjustment", ex_date=self.EX,
            entitlement_date=self.ENT, status="pending",
            platform_code="MYCF",
        )
        with pytest.raises(BusinessError) as exc:
            confirm_share_change_event(test_db, empty_event)
        assert exc.value.code == "EMPTY_ADJUSTMENT"

        # 现金型产品带份额变动（模拟存量脏数据）
        cash_event = create_share_change_event(
            test_db, "FAV_P6", "CASH", "",
            event_type="forced_adjustment", ex_date=self.EX,
            entitlement_date=self.ENT, status="pending",
            platform_code="MYCF", shares_change=Decimal("1.00"),
        )
        with pytest.raises(BusinessError) as exc:
            confirm_share_change_event(test_db, cash_event)
        assert exc.value.code == "SHARES_CHANGE_ON_CASH_PRODUCT"

    def test_update_clears_shares_change_on_cash_product_allowed(self, client, admin_headers, test_db):
        """边界：现金型产品存量脏事件（带份额变动）经 PUT 清 null 修正放行"""
        self._setup(test_db, "FAV_P7")
        event = create_share_change_event(
            test_db, "FAV_P7", "CASH", "",
            event_type="forced_adjustment", ex_date=self.EX,
            entitlement_date=self.ENT, status="pending",
            platform_code="MYCF", shares_change=Decimal("1.00"),
            cash_change=Decimal("-5.00"),
        )

        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"shares_change": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.refresh(event)
        assert event.shares_change is None
        assert Decimal(str(event.cash_change)) == Decimal("-5.00")

    def test_update_fills_shares_change_on_cash_product_rejected(self, client, admin_headers, test_db):
        """验收：PUT 给现金型产品补填 shares_change 同样被拒（封死 update 绕过）"""
        self._setup(test_db, "FAV_P8")
        event = create_share_change_event(
            test_db, "FAV_P8", "CASH", "",
            event_type="forced_adjustment", ex_date=self.EX,
            entitlement_date=self.ENT, status="pending",
            platform_code="MYCF", cash_change=Decimal("-5.00"),
        )

        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"shares_change": 2.0},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "SHARES_CHANGE_ON_CASH_PRODUCT"

    def test_reinvest_dividend_on_cash_product_rejected(self, client, admin_headers, test_db):
        """结构型成员完整性：reinvest_dividend（唯一平台级结构型）在现金型产品上无条件拒"""
        self._setup(test_db, "FAV_P9")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "FAV_P9",
                "product_code": "CASH",
                "market": "",
                "event_type": "reinvest_dividend",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "div_cash": 0.5,
                "reinvest_nav": 1.2,
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "SHARES_CHANGE_ON_CASH_PRODUCT"


class TestShareEventMarketResolve:
    """issue #258：事件创建 market 省略补全 / 一码多市场 / 产品存在性（口径同 #83 调仓创建）"""

    ENT = date(2025, 11, 3)
    EX = date(2025, 11, 4)

    def _setup(self, test_db, portfolio_code):
        create_portfolio(test_db, code=portfolio_code, status="active")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)

    def _payload(self, portfolio_code, product_code, market="SENTINEL"):
        body = {
            "portfolio_code": portfolio_code,
            "product_code": product_code,
            "event_type": "forced_adjustment",
            "ex_date": self.EX.isoformat(),
            "entitlement_date": self.ENT.isoformat(),
            "platform_code": "MYCF",
            "shares_change": 1.0,
        }
        if market != "SENTINEL":
            body["market"] = market
        return body

    def test_create_event_market_omitted_auto_completed(self, client, admin_headers, test_db):
        """省略 market：按产品唯一市场自动补全，创建成功"""
        self._setup(test_db, "SMR_P1")
        create_product(test_db, code="SMR_F1", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P1", "SMR_F1"),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["status"] == "pending"
        assert data["market"] == "CN_OTC"

    def test_create_event_market_empty_string_auto_completed(self, client, admin_headers, test_db):
        """前端实际场景：market 传空串同样自动补全（原 500 复现路径）"""
        self._setup(test_db, "SMR_P2")
        create_product(test_db, code="SMR_F2", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P2", "SMR_F2", market=""),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["market"] == "CN_OTC"

    def test_create_event_lof_market_ambiguous(self, client, admin_headers, test_db):
        """一码多市场（LOF）且未传 market：422 MARKET_AMBIGUOUS + available_markets"""
        self._setup(test_db, "SMR_P3")
        create_product(test_db, code="SMR_LOF", market="CN_OTC", product_type="LOF")
        create_product(test_db, code="SMR_LOF", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P3", "SMR_LOF"),
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MARKET_AMBIGUOUS"
        assert detail["details"]["product_code"] == "SMR_LOF"
        assert detail["details"]["available_markets"] == ["CN_EXCHANGE", "CN_OTC"]

    def test_create_event_product_not_found(self, client, admin_headers, test_db):
        """产品代码不存在：404 PRODUCT_NOT_FOUND（不再 500）"""
        self._setup(test_db, "SMR_P4")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P4", "SMR_MISSING"),
            headers=admin_headers,
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "PRODUCT_NOT_FOUND"
        assert detail["details"] == {"product_code": "SMR_MISSING"}

    def test_create_event_explicit_valid_market_unchanged(self, client, admin_headers, test_db):
        """显式传合法 (product_code, market)：行为不变"""
        self._setup(test_db, "SMR_P5")
        create_product(test_db, code="SMR_F5", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P5", "SMR_F5", market="CN_OTC"),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["market"] == "CN_OTC"

    def test_create_event_explicit_invalid_market_combo(self, client, admin_headers, test_db):
        """显式传不存在的 (product_code, market) 组合：404 NOT_FOUND + available_markets"""
        self._setup(test_db, "SMR_P6")
        create_product(test_db, code="SMR_F6", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P6", "SMR_F6", market="CN_EXCHANGE"),
            headers=admin_headers,
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "NOT_FOUND"
        assert detail["details"]["available_markets"] == ["CN_OTC"]


class TestShareEventInputNormalize:
    """issue #343：platform_code 空串归一为 NULL（基金级）；product_code 必填"""

    ENT = date(2025, 11, 5)
    EX = date(2025, 11, 6)

    def _setup(self, test_db, portfolio_code, product_code):
        create_portfolio(test_db, code=portfolio_code, status="active")
        create_product(test_db, code=product_code, market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)

    def test_fund_level_empty_platform_code_normalized(self, client, admin_headers, test_db):
        """前端实际场景：基金级事件传空串 platform_code → 归一为 NULL，创建成功（原 500 复现路径）"""
        self._setup(test_db, "SCN_P1", "SCN_F1")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P1",
                "product_code": "SCN_F1",
                "market": "CN_OTC",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
                "platform_code": "",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["platform_code"] is None
        event = (
            test_db.query(ShareChangeEvent)
            .filter(ShareChangeEvent.portfolio_code == "SCN_P1")
            .first()
        )
        assert event is not None and event.platform_code is None

    def test_platform_level_empty_platform_code_required(self, client, admin_headers, test_db):
        """平台级事件传空串 platform_code：仍报 422 PLATFORM_REQUIRED 而非 500"""
        self._setup(test_db, "SCN_P2", "SCN_F2")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P2",
                "product_code": "SCN_F2",
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "div_cash": 0.5,
                "platform_code": "",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PLATFORM_REQUIRED"

    def test_missing_product_code_required(self, client, admin_headers, test_db):
        """省略 product_code：422 PRODUCT_REQUIRED（原 NOT NULL 外键违约 500）"""
        self._setup(test_db, "SCN_P3", "SCN_F3")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P3",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCT_REQUIRED"

    def test_empty_product_code_required(self, client, admin_headers, test_db):
        """空串 product_code 同样报 PRODUCT_REQUIRED"""
        self._setup(test_db, "SCN_P4", "SCN_F4")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P4",
                "product_code": "",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCT_REQUIRED"


class TestShareChangeEventPreview:
    """issue #424：份额变动事件确认预览 GET /api/share-change-events/{id}/preview。

    核心不变量：预览与真实确认共用同一份计算实现，故四个字段与确认后落库值**逐一相等**；
    预览只读（不改 status、不写库、不产审计）。

    覆盖验收断言：
    1. 平台级分红再投资：四字段 == confirm 后落库值，且 preview 后事件未被修改
    2. #425 口径：5377.61 / 0.0119 / 1.0899 → 58.71（金额先量化到分）
    3. 现金分红：cash_change 预览 == 确认值、shares_change == 0
    4. 拆分/合并/送股：shares_after = 权益份额 × / ÷ ratio、cash_change == 0
    5. 强制调整：回显用户直填值（shares_after 恒 None）+ 无持仓行 422 POSITION_NOT_FOUND
    6. 权益登记日无快照 → 422 MISSING_POSITION_SNAPSHOT
    7. 非 pending → 422 INVALID_STATUS（与 confirm 同码；不服务 confirmed 是刻意决策）
    8. 事件不存在 → 404
    9. 基金级事件：父记录权益份额 = 各平台份额之和，四字段与确认后父记录一致
    """

    ENT = date(2025, 12, 8)   # 权益登记日（基线快照日）
    EX = date(2025, 12, 10)   # 除息日
    FUND = "SPV.F1"

    def _setup(self, test_db, portfolio_code, *, shares=1000.0, product_code=None):
        product_code = product_code or self.FUND
        create_portfolio(test_db, code=portfolio_code, status="active")
        create_product(test_db, code=product_code, market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="MYCF")
        create_platform(test_db, code="SPV_PB")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)
        create_position_snapshot(
            test_db, portfolio_code, product_code, "CN_OTC", snapshot_date=self.ENT,
            shares=shares, unit_price=1.0, cost_price=1.0,
            market_value=shares, platform_code="MYCF",
        )
        create_value_snapshot(test_db, portfolio_code, self.ENT,
                              total_value=shares, total_shares=shares, unit_price=1.0)

    def _create(self, test_db, portfolio_code, *, event_type, product_code=None, **kwargs):
        event = create_share_change_event(
            test_db, portfolio_code, product_code or self.FUND, "CN_OTC",
            event_type=event_type, ex_date=self.EX, entitlement_date=self.ENT,
            **{"status": "pending", **kwargs},
        )
        return event

    def _preview(self, client, admin_headers, event_id):
        resp = client.get(
            f"/api/share-change-events/{event_id}/preview", headers=admin_headers
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        return resp.json()["preview"]

    # ---- 1 & 2：平台级分红再投资（#424 主场景 + #425 口径） ----

    def test_reinvest_preview_matches_confirm_and_keeps_event_untouched(
        self, client, admin_headers, test_db
    ):
        """预览四字段 == 确认落库值；预览本身不改 status、不写 shares_change"""
        self._setup(test_db, "SPV_P1", shares=5377.61)
        event = self._create(
            test_db, "SPV_P1", event_type="reinvest_dividend",
            platform_code="MYCF", div_cash=Decimal("0.0119"),
            reinvest_nav=Decimal("1.0899"),
        )

        preview = self._preview(client, admin_headers, event.id)
        # 未确认前事件字段仍为空（预览不改对象、不落库）
        test_db.refresh(event)
        assert event.status == "pending"
        assert event.entitlement_shares is None
        assert event.shares_change is None
        assert event.cash_change is None
        # 预览无审计痕
        assert test_db.query(AuditLog).filter(
            AuditLog.resource_type == "share_change_event",
            AuditLog.resource_id == str(event.id),
            AuditLog.action == "confirm",
        ).count() == 0

        assert preview["entitlement_shares"] == 5377.61
        assert preview["shares_change"] == 58.71          # #425：金额先量化到分
        assert preview["shares_after"] == 5436.32
        assert preview["cash_change"] == 0.0

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert preview == {
            "entitlement_shares": float(confirmed.entitlement_shares),
            "shares_change": float(confirmed.shares_change),
            "shares_after": float(confirmed.shares_after),
            "cash_change": float(confirmed.cash_change),
        }
        assert Decimal(str(confirmed.shares_change)) == Decimal("58.71")

    def test_preview_not_written_to_db(self, client, admin_headers, test_db):
        """纯只读：连发两次预览，库里字段恒为 NULL"""
        self._setup(test_db, "SPV_P2")
        event = self._create(
            test_db, "SPV_P2", event_type="reinvest_dividend",
            platform_code="MYCF", div_cash=Decimal("0.0119"),
            reinvest_nav=Decimal("1.0899"),
        )
        self._preview(client, admin_headers, event.id)
        self._preview(client, admin_headers, event.id)
        test_db.expire_all()
        row = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert row.shares_change is None
        assert row.shares_after is None
        assert row.cash_change is None

    # ---- 3：现金分红 ----

    def test_cash_dividend_preview(self, client, admin_headers, test_db):
        """现金分红：cash_change = quantize(es × div_cash)，shares_change = 0"""
        self._setup(test_db, "SPV_P3", shares=6837.30)
        event = self._create(
            test_db, "SPV_P3", event_type="cash_dividend",
            platform_code="MYCF", div_cash=Decimal("0.0256"),
        )
        preview = self._preview(client, admin_headers, event.id)
        assert preview["cash_change"] == 175.03      # 6837.30 × 0.0256 = 175.03488
        assert preview["shares_change"] == 0.0
        assert preview["shares_after"] == 6837.3

        client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert float(confirmed.cash_change) == preview["cash_change"]
        assert float(confirmed.shares_after) == preview["shares_after"]

    # ---- 4：拆分/合并/送股 ----

    @pytest.mark.parametrize(
        "event_type,ratio,expected_after",
        [
            ("share_split", Decimal("2"), 2000.0),
            ("share_merge", Decimal("2"), 500.0),
            ("bonus_share", Decimal("0.5"), 1500.0),
        ],
    )
    def test_ratio_events_preview(self, client, admin_headers, test_db,
                                  event_type, ratio, expected_after):
        """份额比例型：shares_after = 权益份额 × 或 ÷ ratio，cash_change = 0"""
        self._setup(test_db, "SPV_P4", shares=1000.0)
        event = self._create(
            test_db, "SPV_P4", event_type=event_type, ratio=ratio,
        )
        preview = self._preview(client, admin_headers, event.id)
        assert preview["entitlement_shares"] == 1000.0
        assert preview["shares_after"] == expected_after
        assert preview["shares_change"] == expected_after - 1000.0
        assert preview["cash_change"] == 0.0

        client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert float(confirmed.shares_after) == preview["shares_after"]
        assert float(confirmed.shares_change) == preview["shares_change"]

    # ---- 5：强制调整 ----

    def test_forced_adjustment_preview_echoes_user_input(self, client, admin_headers, test_db):
        """强制调整：回显用户直填的 shares_change / cash_change（shares_after 恒 None）"""
        self._setup(test_db, "SPV_P5", shares=100.0)
        event = self._create(
            test_db, "SPV_P5", event_type="forced_adjustment", platform_code="MYCF",
            shares_change=Decimal("1.00"), cash_change=Decimal("50.00"),
        )
        preview = self._preview(client, admin_headers, event.id)
        assert preview["entitlement_shares"] == 100.0
        assert preview["shares_change"] == 1.0
        assert preview["cash_change"] == 50.0
        # 确认路径不把 shares_after 纳入计算写回（见 apply_event_fields），预览照实回 None
        assert preview["shares_after"] is None

    def test_forced_adjustment_without_position_rejected(self, client, admin_headers, test_db):
        """强制调整指向无持仓的平台：422 POSITION_NOT_FOUND（与确认同码，提前快失败）"""
        self._setup(test_db, "SPV_P6", shares=100.0)
        event = self._create(
            test_db, "SPV_P6", event_type="forced_adjustment", platform_code="SPV_PB",
            shares_change=Decimal("1.00"),
        )
        resp = client.get(
            f"/api/share-change-events/{event.id}/preview", headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "POSITION_NOT_FOUND"

    # ---- 6：权益登记日无快照 ----

    def test_missing_position_snapshot_rejected(self, client, admin_headers, test_db):
        """权益登记日无任何持仓快照：422 MISSING_POSITION_SNAPSHOT（与确认同码）"""
        self._setup(test_db, "SPV_P7", shares=100.0)
        event = self._create(
            test_db, "SPV_P7", event_type="cash_dividend",
            platform_code="MYCF", div_cash=Decimal("0.5"),
        )
        # 直接清掉权益登记日快照（模拟历史脏数据）
        test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "SPV_P7",
            PortfolioPosition.snapshot_date == self.ENT,
        ).delete(synchronize_session=False)
        test_db.flush()

        resp = client.get(
            f"/api/share-change-events/{event.id}/preview", headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "MISSING_POSITION_SNAPSHOT"

    # ---- 7 & 8：状态门与不存在 ----

    def test_confirmed_event_rejected(self, client, admin_headers, test_db):
        """非 pending 拒绝预览（422 INVALID_STATUS，与 confirm 同码同消息）"""
        self._setup(test_db, "SPV_P8", shares=100.0)
        event = self._create(
            test_db, "SPV_P8", event_type="cash_dividend",
            platform_code="MYCF", div_cash=Decimal("0.5"), status="confirmed",
        )
        resp = client.get(
            f"/api/share-change-events/{event.id}/preview", headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_STATUS"

    def test_event_not_found(self, client, admin_headers, test_db):
        resp = client.get("/api/share-change-events/99999999/preview", headers=admin_headers)
        assert resp.status_code == 404

    def test_requires_auth(self, client, test_db):
        """预览挂 get_current_admin，未认证拒绝（与 confirm 同权限）"""
        resp = client.get("/api/share-change-events/1/preview")
        assert resp.status_code in (401, 403)

    # ---- 9：基金级事件 ----

    def test_fund_level_preview_aggregates_platforms(self, client, admin_headers, test_db):
        """基金级拆分：父记录权益份额 = 各平台份额之和，四字段与确认后父记录一致"""
        self._setup(test_db, "SPV_P9", shares=600.0)
        create_position_snapshot(
            test_db, "SPV_P9", self.FUND, "CN_OTC", snapshot_date=self.ENT,
            shares=400.0, unit_price=1.0, cost_price=1.0,
            market_value=400.0, platform_code="SPV_PB",
        )
        event = self._create(test_db, "SPV_P9", event_type="share_split", ratio=Decimal("2"))

        preview = self._preview(client, admin_headers, event.id)
        assert preview["entitlement_shares"] == 1000.0
        assert preview["shares_after"] == 2000.0
        assert preview["shares_change"] == 1000.0

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert float(confirmed.entitlement_shares) == preview["entitlement_shares"]
        assert float(confirmed.shares_after) == preview["shares_after"]
        assert float(confirmed.shares_change) == preview["shares_change"]
        # 子记录按平台拆分（确认语义不变）
        children = test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == event.id
        ).all()
        assert {c.platform_code for c in children} == {"MYCF", "SPV_PB"}
        assert sum(Decimal(str(c.shares_after)) for c in children) == Decimal("2000.00")

    def test_fund_level_preview_without_shares_rejected(self, client, admin_headers, test_db):
        """基金级事件在权益登记日无份额持仓：预览即拒绝（否则是「假预览」——
        看着能确认、点下去才炸 MISSING_POSITION_SNAPSHOT，与确认同码）"""
        # 只造组合与交易日，不造基金持仓快照（权益登记日无 shares > 0 的行）
        create_portfolio(test_db, code="SPV_P10", status="active")
        create_product(test_db, code=self.FUND, market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_position_snapshot(
            test_db, "SPV_P10", "CASH", "", snapshot_date=self.ENT,
            cash_amount=1000.0, unit_price=None, cost_price=None,
            market_value=1000.0, platform_code="MYCF",
        )
        event = create_share_change_event(
            test_db, "SPV_P10", self.FUND, "CN_OTC", event_type="share_split",
            ex_date=self.EX, entitlement_date=self.ENT, status="pending",
            ratio=Decimal("2"),
        )
        resp = client.get(
            f"/api/share-change-events/{event.id}/preview", headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "MISSING_POSITION_SNAPSHOT"

        # 与确认路径一致：真点确认同样失败（预览未夸大可达性）
        confirm = client.post(
            f"/api/share-change-events/{event.id}/confirm", headers=admin_headers
        )
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_POSITION_SNAPSHOT"


class TestShareChangeEventMarketScoping:
    """issue #461：LOF 一码多市场时事件持仓口径按 `event.market` 收窄。

    确认（_confirm_fund_level_event）、预览（resolve_entitlement_shares）两处必须是
    同一 market 边界——快照应用侧按 (产品, market, 平台) 精确匹配持仓行，跨市场聚合
    会份额错摊（同平台）或快照生成 POSITION_NOT_FOUND（跨平台）。
    """

    def _create(self, test_db, portfolio_code, *, event_type, market="CN_OTC", **kwargs):
        return create_share_change_event(
            test_db, portfolio_code, LOF461_CODE, market,
            event_type=event_type, ex_date=LOF461_EX, entitlement_date=LOF461_ENT,
            **{"status": "pending", **kwargs},
        )

    def _preview(self, client, admin_headers, event_id):
        resp = client.get(
            f"/api/share-change-events/{event_id}/preview", headers=admin_headers
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        return resp.json()["preview"]

    def _children(self, test_db, event_id):
        return test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == event_id
        ).all()

    def test_fund_level_split_same_platform_scopes_event_market(self, client, admin_headers, test_db):
        """同平台双市场（EX 100 / OTC 50）：拆分只摊派 event.market 持仓，子记录恰 1 条"""
        _setup_lof_baseline(test_db, "SPV_M1")  # 两市场同平台 MYCF
        event = self._create(test_db, "SPV_M1", event_type="share_split", ratio=Decimal("2"))

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()

        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert confirmed.entitlement_shares == Decimal("50.00")   # 仅 OTC，修复前 150
        assert confirmed.shares_change == Decimal("50.00")
        assert confirmed.shares_after == Decimal("100.00")

        children = self._children(test_db, event.id)
        assert len(children) == 1
        assert children[0].market == "CN_OTC"
        assert children[0].platform_code == "MYCF"
        assert children[0].entitlement_shares == Decimal("50.00")
        assert children[0].shares_after == Decimal("100.00")

    def test_fund_level_split_cross_platform_confirm_succeeds(self, client, admin_headers, test_db):
        """跨平台双市场（EX/MYCF、OTC/HBZQ）：子记录只覆盖 event.market 的行

        修复前子记录 platform 取自另一市场持仓行（CN_OTC/MYCF），快照应用时
        fund_key 不命中 → POSITION_NOT_FOUND 硬炸。"""
        _setup_lof_baseline(test_db, "SPV_M2", exch_platform="MYCF", otc_platform="HBZQ")
        event = self._create(test_db, "SPV_M2", event_type="share_split", ratio=Decimal("2"))

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()

        children = self._children(test_db, event.id)
        assert {(c.market, c.platform_code) for c in children} == {("CN_OTC", "HBZQ")}
        assert children[0].entitlement_shares == Decimal("50.00")
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert confirmed.entitlement_shares == Decimal("50.00")

    def test_fund_level_preview_scopes_event_market(self, client, admin_headers, test_db):
        """预览只含 event.market 持仓（修复前 150），且与确认落库值逐一相等"""
        _setup_lof_baseline(test_db, "SPV_M3")
        event = self._create(test_db, "SPV_M3", event_type="share_split", ratio=Decimal("2"))

        preview = self._preview(client, admin_headers, event.id)
        assert preview["entitlement_shares"] == 50.0   # 修复前 150（跨市场合计）
        assert preview["shares_change"] == 50.0
        assert preview["shares_after"] == 100.0

        client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert preview == {
            "entitlement_shares": float(confirmed.entitlement_shares),
            "shares_change": float(confirmed.shares_change),
            "shares_after": float(confirmed.shares_after),
            "cash_change": float(confirmed.cash_change),
        }

    @pytest.mark.parametrize(
        "event_type,expected",
        [
            ("cash_dividend", {"cash_change": 25.0, "shares_change": 0.0}),
            ("reinvest_dividend", {"cash_change": 0.0, "shares_change": 25.0}),
        ],
    )
    def test_platform_level_dividend_uses_event_market_shares(
        self, client, admin_headers, test_db, event_type, expected
    ):
        """平台级分红按 (market, platform) 读行：同平台双市场取 OTC 50，不是 EX 100"""
        _setup_lof_baseline(test_db, "SPV_M4")
        event = self._create(
            test_db, "SPV_M4", event_type=event_type, platform_code="MYCF",
            div_cash=Decimal("0.5"), reinvest_nav=Decimal("1"),
        )

        preview = self._preview(client, admin_headers, event.id)
        assert preview["entitlement_shares"] == 50.0   # 修复前 .first() 取 CN_EXCHANGE 100
        assert preview["cash_change"] == expected["cash_change"]
        assert preview["shares_change"] == expected["shares_change"]

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert float(confirmed.entitlement_shares) == preview["entitlement_shares"]
        assert float(confirmed.cash_change) == preview["cash_change"]
        assert float(confirmed.shares_change) == preview["shares_change"]


class TestShareChangeEventListFilter:
    """列表服务端筛选 + 分页（#274，形态对齐调仓列表 #126/#155）"""

    PORT = "SCE_FLT"

    def _seed(self, test_db):
        create_portfolio(test_db, code=self.PORT, status="active")
        create_product(test_db, code="FUND_FLTA", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_product(test_db, code="FUND_FLTB", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="FLT_PA")
        create_platform(test_db, code="FLT_PB")
        # e1：平台级现金分红，pending
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTA", "CN_OTC", event_type="cash_dividend",
            ex_date=date(2025, 11, 3), entitlement_date=date(2025, 10, 31),
            status="pending", platform_code="FLT_PA", div_cash=Decimal("0.1"))
        # e2：基金级拆分，confirmed，platform 为空
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTA", "CN_OTC", event_type="share_split",
            ex_date=date(2025, 11, 10), entitlement_date=date(2025, 11, 7),
            status="confirmed")
        # e3：平台级强制调整，pending，另一产品/平台
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTB", "CN_EXCHANGE", event_type="forced_adjustment",
            ex_date=date(2025, 12, 1), entitlement_date=date(2025, 11, 28),
            status="pending", platform_code="FLT_PB", shares_change=Decimal("10"))
        # e4：平台级现金分红，cancelled
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTB", "CN_EXCHANGE", event_type="cash_dividend",
            ex_date=date(2025, 12, 5), entitlement_date=date(2025, 12, 4),
            status="cancelled", platform_code="FLT_PA", div_cash=Decimal("0.2"))

    def _list(self, client, admin_headers, query=""):
        resp = client.get(
            f"/api/share-change-events?portfolio_code={self.PORT}{query}",
            headers=admin_headers)
        assert resp.status_code == 200
        return resp.json()

    def test_filter_by_status(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&status=pending")
        assert data["total"] == 2
        assert all(i["status"] == "pending" for i in data["items"])

    def test_filter_by_event_type(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&event_type=cash_dividend")
        assert data["total"] == 2

    def test_filter_by_product_code(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&product_code=FUND_FLTA")
        assert data["total"] == 2

    def test_filter_products_multi_pairs(self, client, admin_headers, test_db):
        """products 复合多选命中 (code, market) 精确对；串市场不命中"""
        self._seed(test_db)
        data = self._list(
            client, admin_headers,
            "&products=FUND_FLTA|CN_OTC,FUND_FLTB|CN_EXCHANGE")
        assert data["total"] == 4
        assert self._list(client, admin_headers, "&products=FUND_FLTA|CN_EXCHANGE")["total"] == 0

    def test_filter_products_conflict_with_product_code(self, client, admin_headers, test_db):
        self._seed(test_db)
        resp = client.get(
            "/api/share-change-events?products=FUND_FLTA|CN_OTC&product_code=FUND_FLTA",
            headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCTS_PARAM_CONFLICT"

    def test_filter_by_platform_excludes_fund_level(self, client, admin_headers, test_db):
        """平台筛选只命中平台级记录，基金级父记录（platform 空）不在结果内"""
        self._seed(test_db)
        data = self._list(client, admin_headers, "&platform_code=FLT_PA")
        assert data["total"] == 2
        assert all(i["platform_code"] == "FLT_PA" for i in data["items"])

    def test_filter_ex_date_range_closed(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&ex_date_start=2025-12-01&ex_date_end=2025-12-31")
        assert data["total"] == 2
        assert {i["event_type"] for i in data["items"]} == {"forced_adjustment", "cash_dividend"}

    def test_filter_ex_date_range_invalid(self, client, admin_headers, test_db):
        self._seed(test_db)
        resp = client.get(
            "/api/share-change-events?ex_date_start=2025-12-31&ex_date_end=2025-12-01",
            headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_RANGE"

    def test_combined_filters(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&status=pending&product_code=FUND_FLTA")
        assert data["total"] == 1
        assert data["items"][0]["event_type"] == "cash_dividend"

    def test_pagination_total_is_filtered(self, client, admin_headers, test_db):
        self._seed(test_db)
        page1 = self._list(client, admin_headers, "&status=pending&page_size=1")
        assert page1["total"] == 2 and len(page1["items"]) == 1
        page2 = self._list(client, admin_headers, "&status=pending&page_size=1&page=2")
        assert len(page2["items"]) == 1
        assert page1["items"][0]["id"] != page2["items"][0]["id"]


class TestShareEventListProductName:
    """list 响应读侧派生 product_name（#342，同调仓 #175 口径）"""

    ENT = date(2025, 11, 10)
    EX = date(2025, 11, 11)

    def _create_split(self, client, admin_headers, portfolio_code, product_code):
        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": portfolio_code,
                "product_code": product_code,
                "market": "CN_OTC",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"

    def test_list_events_includes_product_name(self, client, admin_headers, test_db):
        """两个不同产品的基金级事件，list 每条 item 均应带各自 product_name"""
        create_portfolio(test_db, code="EPN_P1", status="active")
        create_product(test_db, code="EPN_F1", market="CN_OTC", name="测试基金一",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_product(test_db, code="EPN_F2", market="CN_OTC", name="测试基金二",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)
        self._create_split(client, admin_headers, "EPN_P1", "EPN_F1")
        self._create_split(client, admin_headers, "EPN_P1", "EPN_F2")

        resp = client.get("/api/share-change-events?portfolio_code=EPN_P1", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        name_by_product = {item["product_code"]: item["product_name"] for item in data["items"]}
        assert name_by_product == {"EPN_F1": "测试基金一", "EPN_F2": "测试基金二"}
        # 字段完整性（#183 口径）：挂 response_model 后响应键与 schema 声明一一对应
        assert set(data["items"][0].keys()) == set(ShareChangeEventResponse.model_fields.keys())


class TestShareEventOpenApiContract:
    """openapi 契约守护（#342，同 #183 口径）：事件列表分页响应结构化"""

    def test_share_events_list_openapi_references_paginated_schema(self, client):
        """openapi.json 中 /api/share-change-events GET 200 应引用
        PaginatedShareEventResponse，且 items 元素指向 ShareChangeEventResponse
        （含 product_name），而非裸 ORM 空 schema。"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()

        get_op = spec["paths"]["/api/share-change-events"]["get"]
        schema_ref = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema_ref == {"$ref": "#/components/schemas/PaginatedShareEventResponse"}

        schemas = spec["components"]["schemas"]
        paginated = schemas["PaginatedShareEventResponse"]
        assert set(paginated["required"]) == {"items", "total", "page", "page_size"}
        assert set(paginated["properties"].keys()) == {
            "items", "total", "page", "page_size",
        }
        assert paginated["properties"]["items"]["items"] == {
            "$ref": "#/components/schemas/ShareChangeEventResponse"
        }

        event_props = schemas["ShareChangeEventResponse"]["properties"]
        assert "product_name" in event_props  # 防止误删读侧派生字段声明
