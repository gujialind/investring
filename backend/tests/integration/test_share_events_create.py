# ============ 集成测试：份额变动事件创建/列表/取消/反确认（自 test_share_events.py 拆分，issue #469） ============
# 覆盖类（原文件顺序）：
#   - TestShareChangeEventCreate：创建（#40 平台覆盖阻断/force_cover，#461 跨市场覆盖校验两条回归）
#   - TestShareChangeEventList：列表读取与 viewer 创建 403
#   - TestShareChangeEventCancel：取消 pending 事件
#   - TestShareChangeEventUnconfirm：#38 unconfirm 状态门 / 快照保护 SNAPSHOT_DEPENDENCY / 父子级联
# #461 依赖：TestShareChangeEventCreate 的两条 LOF 用例基于一码多市场基线（LOF461_CODE 同 code
# 双市场产品 + MYCF/HBZQ 双平台 + ENT/EX = 2025-12-08/2025-12-10），常量与 _setup_lof_baseline
# 由 share_event_helpers.py 提供；事件按 event.market 收窄，两市场须分别录入事件。
# LOF 双市场基线的完整背景见 test_share_events_market_semantics.py 头部（#461）

from datetime import date, datetime
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_product, create_platform,
    create_share_change_event, create_position_snapshot,
    create_value_snapshot, ensure_trading_day,
)
from app.models.share_change_event import ShareChangeEvent

from tests.integration.share_event_helpers import (
    LOF461_CODE, LOF461_ENT, LOF461_EX,
    _setup_lof_products, _setup_lof_baseline,
)


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
