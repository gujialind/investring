# ============ 集成测试：份额变动事件确认预览（自 test_share_events.py 拆分，issue #469） ============
# 覆盖类（原文件顺序）：
#   - TestShareChangeEventPreview（#424）：GET /{id}/preview 与 confirm 共用同一计算实现，
#     预览四字段与确认落库值逐一相等；纯只读（不改 status、不写库、不产审计）；
#     覆盖 #425 金额先量化到分口径（5377.61/0.0119/1.0899 → 58.71）
# 说明：本文件不依赖 #461 LOF 双市场基线，无 share_event_helpers.py 导入。

import pytest
from datetime import date
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_product, create_platform,
    create_share_change_event, create_position_snapshot,
    create_value_snapshot, ensure_trading_day,
)
from app.models.share_change_event import ShareChangeEvent
from app.models.audit_log import AuditLog
from app.models.portfolio_position import PortfolioPosition


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
