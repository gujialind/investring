# ============ 集成测试：申购赎回·确认前预览（自 test_subscriptions.py 拆分，issue #469） ============
# 原「集成测试：申购赎回」套件的确认前预览部分；其余三个文件同源拆分。
# 本文件覆盖：
#   - TestSubscriptionPreview：issue #248 确认前预览（章节 banner 见下方）
# 共享工厂 _confirmed_sub_with_cash_leg 见 tests/integration/subscription_helpers.py（原属 #180 章节）。

from datetime import date

from tests.factories import (
    create_portfolio, create_investor, create_investor_holding,
    create_value_snapshot, ensure_trading_day, create_position_snapshot,
)
from tests.integration.subscription_helpers import _confirmed_sub_with_cash_leg
from app.models.trade import Trade


# ============================================================================
# issue #248：确认前预览 GET /api/subscriptions/{id}/preview
# （与真实 confirm 共用 calculate_subscription_confirm_preview 实现）
# ============================================================================

class TestSubscriptionPreview:
    """#248 确认前预览：预览值与真实确认完全一致，且零副作用"""

    def _create_subscribe(self, client, admin_headers, *, portfolio, investor,
                          amount=10000.0, apply_date="2025-09-01"):
        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": portfolio,
                "investor_code": investor,
                "sub_type": "subscribe",
                "amount": amount,
                "apply_date": apply_date,
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        return resp.json()["id"]

    def test_preview_matches_confirm_subscribe(self, client, admin_headers, test_db):
        """申购：预览净值/份额/确认日与确认后落库值逐项一致"""
        create_portfolio(test_db, code="SPV_P1", status="active")
        create_investor(test_db, code="SPV_I1")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        sub_id = self._create_subscribe(
            client, admin_headers, portfolio="SPV_P1", investor="SPV_I1",
        )
        # 创建后补申请日快照（模拟快照生成先于确认的正常流程）
        create_value_snapshot(test_db, "SPV_P1", date(2025, 9, 1),
                              total_value=12500, total_shares=10000, unit_price=1.25)

        prev = client.get(f"/api/subscriptions/{sub_id}/preview", headers=admin_headers)
        assert prev.status_code == 200, f"Response: {prev.status_code} {prev.json()}"
        preview = prev.json()["preview"]
        # 份额 = 10000 / 1.25 = 8000，确认日 = T+1
        assert float(preview["nav"]) == 1.25
        assert float(preview["shares"]) == 8000.0
        assert float(preview["amount"]) == 10000.0
        assert preview["confirm_date"] == "2025-09-02"
        assert preview["is_first"] is True

        conf = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        confirmed = conf.json()
        # preview 与真实确认逐字段一致
        assert float(preview["nav"]) == float(confirmed["unit_price"])
        assert float(preview["shares"]) == float(confirmed["shares"])
        assert float(preview["amount"]) == float(confirmed["amount"])
        assert preview["confirm_date"] == confirmed["confirm_date"]

    def test_preview_matches_confirm_redeem(self, client, admin_headers, test_db):
        """赎回：预览金额 = 份额 × 预览净值（2 位量化），与确认后一致"""
        create_portfolio(test_db, code="SPV_P2", status="active")
        create_investor(test_db, code="SPV_I2")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        # 先有持仓份额才能赎回
        create_value_snapshot(test_db, "SPV_P2", date(2025, 8, 29),
                              total_value=10000, total_shares=10000, unit_price=1.0)
        create_investor_holding(test_db, "SPV_P2", "SPV_I2", date(2025, 8, 29), shares=10000)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "SPV_P2", "investor_code": "SPV_I2",
                "sub_type": "redeem", "shares": 4000.0,
                "apply_date": "2025-09-01", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        sub_id = resp.json()["id"]
        create_value_snapshot(test_db, "SPV_P2", date(2025, 9, 1),
                              total_value=12500, total_shares=10000, unit_price=1.25)

        prev = client.get(f"/api/subscriptions/{sub_id}/preview", headers=admin_headers)
        assert prev.status_code == 200, f"Response: {prev.status_code} {prev.json()}"
        preview = prev.json()["preview"]
        # 金额 = 4000 × 1.25 = 5000
        assert float(preview["nav"]) == 1.25
        assert float(preview["shares"]) == 4000.0
        assert float(preview["amount"]) == 5000.0
        assert preview["confirm_date"] == "2025-09-02"

        # #203 消费点校验：赎回确认需平台可用现金 ≥ 赎回金额；
        # 最新快照日（申请日）的 CASH 行即可用现金基线，预置恰好 5000（精确比较边界，无容差）
        create_position_snapshot(
            test_db, "SPV_P2", "CASH", "", snapshot_date=date(2025, 9, 1),
            cash_amount=5000.0, unit_price=None, cost_price=None,
            market_value=5000.0, platform_code="MYCF",
        )

        conf = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        confirmed = conf.json()
        assert float(preview["nav"]) == float(confirmed["unit_price"])
        assert float(preview["shares"]) == float(confirmed["shares"])
        assert float(preview["amount"]) == float(confirmed["amount"])
        assert preview["confirm_date"] == confirmed["confirm_date"]

    def test_preview_initial_window_nav_1(self, client, admin_headers, test_db):
        """初始窗口（无任何快照与到账）：预览净值恒 1.0000，份额 = 金额"""
        create_portfolio(test_db, code="SPV_P3", status="active")
        create_investor(test_db, code="SPV_I3")
        for d in (1, 2):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        sub_id = self._create_subscribe(
            client, admin_headers, portfolio="SPV_P3", investor="SPV_I3",
            amount=12345.67,
        )

        prev = client.get(f"/api/subscriptions/{sub_id}/preview", headers=admin_headers)
        assert prev.status_code == 200, f"Response: {prev.status_code} {prev.json()}"
        preview = prev.json()["preview"]
        assert float(preview["nav"]) == 1.0
        assert float(preview["shares"]) == 12345.67

        conf = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200
        assert float(preview["shares"]) == float(conf.json()["shares"])

    def test_preview_nav_not_available(self, client, admin_headers, test_db):
        """已有到账但申请日无快照：预览返回 422 NAV_NOT_AVAILABLE"""
        from app.models.portfolio import Portfolio
        create_portfolio(test_db, code="SPV_P4", status="active")
        create_investor(test_db, code="SPV_I4")
        for d in (1, 2, 3, 4, 5):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        # 已到账的 confirmed 申购（confirm_date 9/2 <= 新单申请日 9/4）
        _confirmed_sub_with_cash_leg(
            test_db, "SPV_P4", "SPV_I4", 10000.0, date(2025, 9, 1), date(2025, 9, 2))
        p = test_db.query(Portfolio).filter(Portfolio.code == "SPV_P4").first()
        p.started_at = date(2025, 9, 2)
        test_db.commit()

        sub_id = self._create_subscribe(
            client, admin_headers, portfolio="SPV_P4", investor="SPV_I4",
            apply_date="2025-09-04",
        )

        prev = client.get(f"/api/subscriptions/{sub_id}/preview", headers=admin_headers)
        assert prev.status_code == 422
        assert prev.json()["detail"]["error"] == "NAV_NOT_AVAILABLE"

    def test_preview_confirmed_subscription_rejected(self, client, admin_headers, test_db):
        """对已 confirmed 申赎预览：422 INVALID_STATUS"""
        create_portfolio(test_db, code="SPV_P5", status="active")
        create_investor(test_db, code="SPV_I5")
        for d in (1, 2):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        sub_id = self._create_subscribe(
            client, admin_headers, portfolio="SPV_P5", investor="SPV_I5",
        )
        conf = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200

        prev = client.get(f"/api/subscriptions/{sub_id}/preview", headers=admin_headers)
        assert prev.status_code == 422
        assert prev.json()["detail"]["error"] == "INVALID_STATUS"

    def test_preview_has_zero_side_effects(self, client, admin_headers, test_db):
        """预览后申赎仍 pending、字段未变、无配对 CASH trade 生成"""
        create_portfolio(test_db, code="SPV_P6", status="active")
        create_investor(test_db, code="SPV_I6")
        for d in (1, 2):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        sub_id = self._create_subscribe(
            client, admin_headers, portfolio="SPV_P6", investor="SPV_I6",
        )

        prev = client.get(f"/api/subscriptions/{sub_id}/preview", headers=admin_headers)
        assert prev.status_code == 200

        got = client.get(f"/api/subscriptions/{sub_id}", headers=admin_headers)
        assert got.status_code == 200
        data = got.json()
        assert data["status"] == "pending"
        assert data["unit_price"] is None
        assert data["shares"] is None

        test_db.expire_all()
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == f"sub_{sub_id}"
        ).first()
        assert cash_leg is None
