# ============ 集成测试：份额变动事件更新与强制调整校验（自 test_share_events.py 拆分，issue #469） ============
# 覆盖类（原文件顺序）：
#   - TestUpdateShareChangeEvent：PUT 更新（confirmed 阻断 CANNOT_MODIFY_CONFIRMED、pending 改日期
#     重跑双日期校验、status 直改忽略）
#   - TestForcedAdjustmentInputValidation（#279）：forced_adjustment 双空与现金型产品份额变动的
#     创建/PUT/确认三入口校验（含函数内局部 import 原样保留）
# 说明：本文件不依赖 #461 LOF 双市场基线，无 share_event_helpers.py 导入。

import pytest
from datetime import date
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_product, create_platform,
    create_share_change_event, create_value_snapshot, ensure_trading_day,
)
from app.models.share_change_event import ShareChangeEvent


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


class TestUpdateEventNullGuard:
    """PUT 显式 null 收口（#579 口径 A，与 #573 同口径）：ex_date /
    entitlement_date 是 NOT NULL 列，显式 null 此前直落 setattr 循环 →
    IntegrityError 500（靠列约束兜底），现 422 拒绝；其余可更新字段均为可空列
    且响应 Optional，null = 清空是 setattr 循环的既有语义、进 allow 显式化，
    清空后的双空终态仍由 #279 合并校验（EMPTY_ADJUSTMENT）兜底"""

    ENT = date(2025, 11, 10)
    EX = date(2025, 11, 12)

    def _pending_event(self, test_db, *, code, event_type="cash_dividend", **kwargs):
        create_portfolio(test_db, code=code, status="active")
        create_product(test_db, code="FUND_NG", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="NG_PLAT")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)
        return create_share_change_event(
            test_db, code, "FUND_NG", "CN_OTC",
            event_type=event_type, ex_date=self.EX,
            entitlement_date=self.ENT, status="pending",
            platform_code="NG_PLAT", **kwargs,
        )

    def test_update_null_ex_date_rejected_not_500(self, client, admin_headers, test_db):
        """NOT NULL 列显式 null：422 拒绝（修复前 IntegrityError 500）且零写入"""
        event = self._pending_event(test_db, code="NG_P1", div_cash=Decimal("0.1"))
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"ex_date": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_PARAM"
        assert "ex_date" in detail["message"]
        # 拒绝即零写入：日期保持原值且仍可读
        test_db.expire_all()
        row = test_db.query(ShareChangeEvent).get(event.id)
        assert row.ex_date == self.EX
        assert client.get(
            f"/api/share-change-events/{event.id}", headers=admin_headers
        ).status_code == 200

    def test_update_null_entitlement_date_rejected(self, client, admin_headers, test_db):
        event = self._pending_event(test_db, code="NG_P2", div_cash=Decimal("0.1"))
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"entitlement_date": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_PARAM"
        assert "entitlement_date" in detail["message"]

    def test_update_null_notes_and_div_cash_clear(self, client, admin_headers, test_db):
        """allow 例外：可空列 null = 清空（既有 setattr 语义，不得被收口误拒）"""
        event = self._pending_event(
            test_db, code="NG_P3", div_cash=Decimal("0.1"), notes="原备注"
        )
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"notes": None, "div_cash": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["notes"] is None
        assert data["div_cash"] is None

    def test_update_adjustment_single_null_clears_double_null_rejected(self, client, admin_headers, test_db):
        """forced_adjustment：清单项合法（另一项仍在）；双 null 由 #279 合并校验
        兜底为 EMPTY_ADJUSTMENT——allow 放行不等于放弃终态校验"""
        event = self._pending_event(
            test_db, code="NG_P4", event_type="forced_adjustment",
            shares_change=Decimal("100"), cash_change=Decimal("50"),
        )
        one = client.put(
            f"/api/share-change-events/{event.id}",
            json={"cash_change": None},
            headers=admin_headers,
        )
        assert one.status_code == 200, one.json()
        assert one.json()["cash_change"] is None
        assert float(one.json()["shares_change"]) == 100.0

        both = client.put(
            f"/api/share-change-events/{event.id}",
            json={"shares_change": None, "cash_change": None},
            headers=admin_headers,
        )
        assert both.status_code == 422
        assert both.json()["detail"]["error"] == "EMPTY_ADJUSTMENT"
        # 拒绝即零写入：shares_change 保持原值
        test_db.expire_all()
        row = test_db.query(ShareChangeEvent).get(event.id)
        assert Decimal(str(row.shares_change)) == Decimal("100")
