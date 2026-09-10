# ============================================================================
# 集成测试：调仓交易 (test_trades.py)
# ============================================================================

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
    create_position_snapshot, create_value_snapshot, create_investor_holding,
    create_investor, ensure_trading_day, create_price_record,
)
from app.models.trade import Trade
from app.models.portfolio import Portfolio
from app.schemas.trade import TradeResponse


class TestBuyTrade:
    """买入交易测试"""

    def test_create_buy_trade_pending(self, client, admin_headers, test_db):
        """买入交易创建后应为 pending"""
        create_portfolio(test_db, code="TRD_P1", status="active")
        create_product(test_db, code="ETF01", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="TRD_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        # 提供可用现金：通过 confirmed CASH buy trade 表示现金流入（如申购确认）
        create_trade(
            test_db, "TRD_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="TRD_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "TRD_P1",
                "product_code": "ETF01",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "TRD_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        data = resp.json()
        assert data["status"] == "pending"
        assert data["trade_type"] == "buy"

    def test_buy_insufficient_cash_rejected(self, client, admin_headers, test_db):
        """买入金额超过可用现金应被拒绝"""
        create_portfolio(test_db, code="TRD_NC", status="active")
        create_product(test_db, code="ETF02", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="TRD_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        # 少量现金
        create_value_snapshot(test_db, "TRD_NC", date(2025, 10, 3),
                              total_value=100, total_shares=100, unit_price=1.0)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "TRD_NC",
                "product_code": "ETF02",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 999999.0,
                "price": 1.5,
                "platform_code": "TRD_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_buy_zero_amount_rejected(self, client, admin_headers, test_db):
        """买入金额为 0 应被拒绝"""
        create_portfolio(test_db, code="TRD_Z", status="active")
        create_product(test_db, code="ETF03", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="TRD_PLAT3")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "TRD_Z",
                "product_code": "ETF03",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 0,
                "price": 1.5,
                "platform_code": "TRD_PLAT3",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422)

    def test_buy_succeeds_with_manual_override(self, client, admin_headers, test_db):
        """manual 覆盖 baked in 快照后，买入金额在覆盖值内应成功（回归 issue #52）"""
        create_portfolio(test_db, code="TRD_OVR", status="active")
        create_product(test_db, code="ETF_OVR", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="TRD_OVR_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        # 快照日 + 持仓快照（模拟快照生成时已 bake in manual 覆盖值 6001.39）
        create_value_snapshot(test_db, "TRD_OVR", date(2025, 10, 3),
                              total_value=6001.39, total_shares=6001.39, unit_price=1.0)
        create_position_snapshot(
            test_db, "TRD_OVR", "CASH", "",
            snapshot_date=date(2025, 10, 3),
            cash_amount=6001.39, unit_price=None, cost_price=None,
            market_value=6001.39, platform_code="TRD_OVR_PLAT",
        )

        # 买入 6001（< 快照基线 6001.39）→ 应成功
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "TRD_OVR",
                "product_code": "ETF_OVR",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 6001.0,
                "price": 1.5,
                "platform_code": "TRD_OVR_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"


class TestSellTrade:
    """卖出交易测试"""

    def test_create_sell_trade_pending(self, client, admin_headers, test_db):
        """卖出交易创建后应为 pending"""
        create_portfolio(test_db, code="SEL_P1", status="active")
        create_product(test_db, code="ETF04", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="SEL_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        # 先有持仓
        create_position_snapshot(
            test_db, "SEL_P1", "ETF04", "CN_EXCHANGE",
            snapshot_date=date(2025, 10, 3),
            shares=1000.0, unit_price=1.5, cost_price=1.5,
            market_value=1500.0, platform_code="SEL_PLAT",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "SEL_P1",
                "product_code": "ETF04",
                "market": "CN_EXCHANGE",
                "trade_type": "sell",
                "shares": 500.0,
                "price": 1.6,
                "platform_code": "SEL_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["status"] == "pending"
        assert data["trade_type"] == "sell"

    def test_sell_exceeds_available_shares_rejected(self, client, admin_headers, test_db):
        """卖出份额超过可用份额应被拒绝"""
        create_portfolio(test_db, code="SEL_EX", status="active")
        create_product(test_db, code="ETF05", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="SEL_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        create_position_snapshot(
            test_db, "SEL_EX", "ETF05", "CN_EXCHANGE",
            snapshot_date=date(2025, 10, 3),
            shares=100.0, unit_price=1.5, cost_price=1.5,
            market_value=150.0, platform_code="SEL_PLAT2",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "SEL_EX",
                "product_code": "ETF05",
                "market": "CN_EXCHANGE",
                "trade_type": "sell",
                "shares": 99999.0,
                "price": 1.6,
                "platform_code": "SEL_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422


class TestExchangeSellAmountDerivation:
    """#190 场内卖出金额推导 + 一致性校验"""

    def _setup(self, client, test_db, code="ES_P1", product="ETF_ES", plat="ES_PLAT"):
        create_portfolio(test_db, code=code, status="active")
        create_product(test_db, code=product, market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code=plat)
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_position_snapshot(
            test_db, code, product, "CN_EXCHANGE",
            snapshot_date=date(2025, 10, 3),
            shares=10000.0, unit_price=0.8, cost_price=0.8,
            market_value=8000.0, platform_code=plat,
        )

    def _sell_payload(self, **overrides):
        payload = {
            "portfolio_code": "ES_P1",
            "product_code": "ETF_ES",
            "market": "CN_EXCHANGE",
            "trade_type": "sell",
            "shares": 7800.0,
            "price": 0.802,
            "fee": 0.63,
            "platform_code": "ES_PLAT",
            "trade_date": "2025-10-06",
        }
        payload.update(overrides)
        return payload

    def _cash_leg(self, test_db):
        return test_db.query(Trade).filter(
            Trade.portfolio_code == "ES_P1", Trade.product_code == "CASH"
        ).first()

    def test_create_derives_amount_and_cash_leg(self, client, admin_headers, test_db):
        """不传 actual_amount：amount/actual_amount 由 shares×price 推导，CASH 腿镜像净额"""
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        assert resp.status_code in (200, 201), resp.json()
        data = resp.json()
        assert data["amount"] == 6255.60
        assert data["actual_amount"] == 6254.97

        cash_leg = self._cash_leg(test_db)
        assert cash_leg is not None
        assert float(cash_leg.amount) == 6254.97
        assert cash_leg.status == "pending"

    def test_confirm_keeps_derived_amount_and_cash_leg(self, client, admin_headers, test_db):
        """确认（不传价）：金额保持推导值，CASH 腿 confirmed 且 = 净额"""
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        trade_id = resp.json()["id"]

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        assert conf.json()["trade"]["amount"] == 6255.60
        assert conf.json()["trade"]["actual_amount"] == 6254.97

        cash_leg = self._cash_leg(test_db)
        assert cash_leg.status == "confirmed"
        assert float(cash_leg.amount) == 6254.97

    def test_explicit_actual_amount_consistent_passes(self, client, admin_headers, test_db):
        """显式传与推导一致的 actual_amount：通过，落库仍为推导值"""
        self._setup(client, test_db)
        resp = client.post(
            "/api/trades",
            json=self._sell_payload(actual_amount=6254.97),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["amount"] == 6255.60
        assert resp.json()["actual_amount"] == 6254.97

    def test_explicit_actual_amount_mismatch_rejected(self, client, admin_headers, test_db):
        """显式传与推导不一致的 actual_amount：抛 AMOUNT_MISMATCH"""
        self._setup(client, test_db)
        resp = client.post(
            "/api/trades",
            json=self._sell_payload(actual_amount=6000.0),
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "AMOUNT_MISMATCH"

    def test_amount_alias_acts_as_actual_amount(self, client, admin_headers, test_db):
        """amount 与 actual_amount 同义（#190 意见2）：仅传 amount 也作校验基准"""
        self._setup(client, test_db)
        resp = client.post(
            "/api/trades",
            json=self._sell_payload(amount=6254.97),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["amount"] == 6255.60
        assert resp.json()["actual_amount"] == 6254.97

    # ---- #190 后续：边界校验 + PUT 同口径 ----

    def test_create_fee_not_less_than_gross_rejected(self, client, admin_headers, test_db):
        """fee 不小于毛额：推导净额非正 -> INVALID_AMOUNT"""
        self._setup(client, test_db)
        resp = client.post(
            "/api/trades",
            json=self._sell_payload(fee=7000.0),
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_AMOUNT"

    def test_negative_price_rejected(self, client, admin_headers, test_db):
        """任意市场显式传非正价格 -> MISSING_OR_INVALID_PRICE"""
        self._setup(client, test_db)
        sell = client.post(
            "/api/trades",
            json=self._sell_payload(price=-0.5),
            headers=admin_headers,
        )
        assert sell.status_code == 422
        assert sell.json()["detail"]["error"] == "MISSING_OR_INVALID_PRICE"

        buy = client.post(
            "/api/trades",
            json={
                "portfolio_code": "ES_P1", "product_code": "ETF_ES",
                "market": "CN_EXCHANGE", "trade_type": "buy",
                "amount": 1000.0, "price": -1.5,
                "platform_code": "ES_PLAT", "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert buy.status_code == 422
        assert buy.json()["detail"]["error"] == "MISSING_OR_INVALID_PRICE"

    def test_put_shares_rederives_amount_and_cash_leg(self, client, admin_headers, test_db):
        """PUT 改份额：amount/actual_amount 随动重推导，CASH 腿镜像新净额"""
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        trade_id = resp.json()["id"]

        upd = client.put(
            f"/api/trades/{trade_id}", json={"shares": 6000.0}, headers=admin_headers,
        )
        assert upd.status_code == 200, upd.json()
        assert upd.json()["amount"] == 4812.00
        assert upd.json()["actual_amount"] == 4811.37

        test_db.expire_all()
        cash_leg = self._cash_leg(test_db)
        assert float(cash_leg.amount) == 4811.37

    def test_put_explicit_amount_reconciliation(self, client, admin_headers, test_db):
        """PUT 显式金额：一致通过且落库保持推导值；超差 -> AMOUNT_MISMATCH"""
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        trade_id = resp.json()["id"]

        ok = client.put(
            f"/api/trades/{trade_id}", json={"amount": 6254.97}, headers=admin_headers,
        )
        assert ok.status_code == 200, ok.json()
        assert ok.json()["amount"] == 6255.60
        assert ok.json()["actual_amount"] == 6254.97

        bad = client.put(
            f"/api/trades/{trade_id}", json={"amount": 6000.0}, headers=admin_headers,
        )
        assert bad.status_code == 422
        assert bad.json()["detail"]["error"] == "AMOUNT_MISMATCH"

    def test_put_fee_rederives_net_amount(self, client, admin_headers, test_db):
        """PUT 改 fee：毛额不变、净额 = 毛额 − 新 fee，CASH 腿镜像"""
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        trade_id = resp.json()["id"]

        upd = client.put(
            f"/api/trades/{trade_id}", json={"fee": 10.0}, headers=admin_headers,
        )
        assert upd.status_code == 200, upd.json()
        assert upd.json()["amount"] == 6255.60
        assert upd.json()["actual_amount"] == 6245.60

        test_db.expire_all()
        cash_leg = self._cash_leg(test_db)
        assert float(cash_leg.amount) == 6245.60


class TestOtcSellAmountDerivation:
    """#190 后续：场外传价卖出仅推导不强对账；无价格占位单 PUT 保持输入为准"""

    def _setup(self, client, test_db):
        create_portfolio(test_db, code="OD_P1", status="active")
        create_product(test_db, code="FUND_OD", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="OD_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_position_snapshot(
            test_db, "OD_P1", "FUND_OD", "CN_OTC",
            snapshot_date=date(2025, 10, 3),
            shares=10000.0, unit_price=1.2, cost_price=1.2,
            market_value=12000.0, platform_code="OD_PLAT",
        )

    def test_otc_priced_sell_derives_without_mismatch(self, client, admin_headers, test_db):
        """场外传价卖出：显式金额与推导不一致也不拒绝，落推导值"""
        self._setup(client, test_db)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "OD_P1", "product_code": "FUND_OD",
                "market": "CN_OTC", "trade_type": "sell",
                "shares": 5000.0, "price": 1.234, "fee": 1.0,
                "actual_amount": 5000.0,
                "platform_code": "OD_PLAT", "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["amount"] == 6170.00
        assert resp.json()["actual_amount"] == 6169.00

    def test_otc_placeholder_put_amount_input_authoritative(self, client, admin_headers, test_db):
        """场外无价格占位单：PUT 直改金额仍按输入为准（旧口径保留）"""
        self._setup(client, test_db)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "OD_P1", "product_code": "FUND_OD",
                "market": "CN_OTC", "trade_type": "sell",
                "shares": 5000.0, "fee": 1.2, "actual_amount": 5000.0,
                "platform_code": "OD_PLAT", "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]
        assert resp.json()["amount"] == 5001.20
        assert resp.json()["actual_amount"] == 5000.00

        upd = client.put(
            f"/api/trades/{trade_id}", json={"amount": 6000.0}, headers=admin_headers,
        )
        assert upd.status_code == 200, upd.json()
        assert upd.json()["actual_amount"] == 6000.00
        assert upd.json()["amount"] == 6001.20


class TestTradePermissions:
    """调仓交易权限测试"""

    def test_viewer_cannot_trade(self, client, viewer_headers):
        """viewer 不能提交调仓交易"""
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "X",
                "product_code": "X",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 1000,
                "price": 1.0,
                "trade_date": "2025-10-06",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_list_trades(self, client, admin_headers):
        """获取调仓交易列表"""
        resp = client.get("/api/trades", headers=admin_headers)
        assert resp.status_code == 200
        assert "items" in resp.json()


class TestNonQDIIrigorousNav:
    """#24 非QDII净值严格 T 日：禁止向前查找"""

    def test_confirm_oef_missing_t_nav_rejected(self, client, admin_headers, test_db):
        """场外基金确认时，T 日净值缺失应拒绝（不再向前回溯）"""
        create_portfolio(test_db, code="NAV_P1", status="active")
        create_product(test_db, code="FUND_NAV1", market="CN_OTC",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="NAV_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        # 提供现金
        create_trade(
            test_db, "NAV_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="NAV_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 仅写入 T-1 净值，T 日缺失
        create_price_record(test_db, "FUND_NAV1", "CN_OTC",
                            date(2025, 10, 3), unit_price=1.2)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "NAV_P1",
                "product_code": "FUND_NAV1",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "NAV_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        trade_id = resp.json()["id"]

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_NAV"


class TestHKMutualConfirmNav:
    """HK_MUTUAL（香港互认）基金确认同 CN_OTC 口径取 T 日净值重算 shares/amount

    事故复刻：市场白名单曾仅含 CN_OTC，HK_MUTUAL 落入「不重算」分支，
    确认后 price/shares 为空（即使 price_record 已有 T 日净值）。
    """

    def test_confirm_hk_mutual_uses_t_nav(self, client, admin_headers, test_db):
        """HK_MUTUAL 买入确认：取 T 日净值回填 price 并计算 shares"""
        create_portfolio(test_db, code="HK_P1", status="active")
        create_product(test_db, code="1001767346", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        # 提供现金
        create_trade(
            test_db, "HK_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # T 日净值（market=HK_MUTUAL）
        create_price_record(test_db, "1001767346", "HK_MUTUAL",
                            date(2025, 10, 6), unit_price=1.25)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P1",
                "product_code": "1001767346",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]
        # 创建时无价格：price 为空、shares 未计算（序列化可能为 0.0）
        assert resp.json()["price"] is None
        assert resp.json()["shares"] in (None, 0.0)

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 200, confirm.json()
        assert confirm.json()["status"] == "confirmed"
        # confirm 端点返回精简结构，回查 DB 断言净值/份额已回填
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert float(t.price) == 1.25
        # shares = (actual_amount - fee) / nav = 10000 / 1.25
        assert float(t.shares) == 8000.0

    def test_confirm_hk_mutual_missing_t_nav_rejected(self, client, admin_headers, test_db):
        """HK_MUTUAL 缺 T 日净值同样拒绝确认（不回退、不空确认、无脏写）"""
        create_portfolio(test_db, code="HK_P2", status="active")
        create_product(test_db, code="1001767344", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "HK_P2", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT2", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 仅写入 T-1 净值，T 日缺失：验证严格 T 日匹配、禁止向前回退（同 CN_OTC 口径）
        create_price_record(test_db, "1001767344", "HK_MUTUAL",
                            date(2025, 10, 3), unit_price=1.24)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P2",
                "product_code": "1001767344",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_NAV"
        # 失败路径状态守卫：trade 保持 pending、无字段脏写（shares 保持创建时默认 0）
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert t.status == "pending"
        assert t.price is None
        assert float(t.shares) == 0.0

    def test_confirm_hk_mutual_sell_uses_t_nav(self, client, admin_headers, test_db):
        """HK_MUTUAL 卖出确认：amount = shares × T 日净值重算，配对 CASH 腿镜像金额"""
        create_portfolio(test_db, code="HK_P3", status="active")
        create_product(test_db, code="1001767348", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT3")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        # 先有持仓可卖（10-03 快照 1000 份）
        create_position_snapshot(
            test_db, "HK_P3", "1001767348", "HK_MUTUAL",
            snapshot_date=date(2025, 10, 3),
            shares=1000.0, unit_price=1.2, cost_price=1.2,
            market_value=1200.0, platform_code="HK_PLAT3",
        )
        # T 日净值（market=HK_MUTUAL）
        create_price_record(test_db, "1001767348", "HK_MUTUAL",
                            date(2025, 10, 6), unit_price=1.25)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P3",
                "product_code": "1001767348",
                "market": "HK_MUTUAL",
                "trade_type": "sell",
                "shares": 400.0,
                "platform_code": "HK_PLAT3",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 200, confirm.json()
        assert confirm.json()["status"] == "confirmed"
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert float(t.price) == 1.25
        assert float(t.shares) == 400.0
        # amount = quantize(400 × 1.25) = 500，fee=0 → actual_amount = 500
        assert float(t.amount) == 500.0
        assert float(t.actual_amount) == 500.0
        # 配对 CASH 腿（卖出到账 = CASH buy）镜像 actual_amount
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == t.transfer_group,
            Trade.id != trade_id,
        ).first()
        assert paired is not None
        assert paired.product_code == "CASH"
        assert paired.trade_type == "buy"
        assert paired.status == "confirmed"
        assert float(paired.amount) == 500.0

    def test_confirm_hk_mutual_sync_nav_backfill(self, client, admin_headers, test_db):
        """sync_nav=True：MISSING_NAV 时自动回填 HK_MUTUAL 净值后重试确认成功（mock 同步）"""
        create_portfolio(test_db, code="HK_P4", status="active")
        create_product(test_db, code="1001767350", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT4")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        create_trade(
            test_db, "HK_P4", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT4", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 不预置净值：首次确认命中 MISSING_NAV 触发 sync_nav 回填
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P4",
                "product_code": "1001767350",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT4",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        def fake_sync(db, code, market, start, end):
            create_price_record(db, code, market, date(2025, 10, 6), unit_price=1.5)
            return {"success": True, "synced_count": 1}

        with patch(
            "app.services.market_data_service.sync_price_data",
            side_effect=fake_sync,
        ) as mock_sync:
            confirm = client.post(
                f"/api/trades/{trade_id}/confirm",
                params={"sync_nav": "true"},
                headers=admin_headers,
            )
        assert confirm.status_code == 200, confirm.json()
        mock_sync.assert_called_once()
        # 同步调用携带正确的产品代码与 HK_MUTUAL 市场
        assert mock_sync.call_args[0][1] == "1001767350"
        assert mock_sync.call_args[0][2] == "HK_MUTUAL"
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert t.status == "confirmed"
        assert float(t.price) == 1.5
        assert float(t.shares) == pytest.approx(6666.67)  # 10000 / 1.5

    def test_confirm_hk_mutual_sync_nav_tushare_skip_still_missing(
        self, client, admin_headers, test_db
    ):
        """sync_nav=True 但产品为默认 tushare 数据源：同步静默跳过，仍 MISSING_NAV

        运维盲区复刻：tushare 不支持 HK_MUTUAL（sync_product_prices 直接 skip 不抛错），
        回填承诺对默认数据源永远无效，但错误码契约不变（不吞错、无脏写）。
        """
        create_portfolio(test_db, code="HK_P5", status="active")
        # 工厂默认 data_source="tushare"（Product 模型默认值）
        create_product(test_db, code="1001767352", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT5")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "HK_P5", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT5", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P5",
                "product_code": "1001767352",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT5",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        confirm = client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"sync_nav": "true"},
            headers=admin_headers,
        )
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_NAV"
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert t.status == "pending"
        assert t.price is None


class TestUnconfirmTradeSnapshotProtection:
    """#25 unconfirm_trade 快照保护"""

    def test_unconfirm_blocked_by_snapshot(self, client, admin_headers, test_db):
        """confirm_date 及之后已有快照时，unconfirm 返回 SNAPSHOT_DEPENDENCY"""
        create_portfolio(test_db, code="UC_P1", status="active")
        create_product(test_db, code="ETF_UC", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UC_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "UC_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UC_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 创建并确认一笔场内 ETF 买入（当天确认）
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UC_P1",
                "product_code": "ETF_UC",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UC_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200
        confirmed_trade = conf.json()["trade"]
        # 在 confirm_date 上生成快照
        create_value_snapshot(
            test_db, "UC_P1", date(2025, 10, 6),
            total_value=60000, total_shares=60000, unit_price=1.0,
        )

        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 422
        assert unconf.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"

    def test_unconfirm_ok_without_snapshot(self, client, admin_headers, test_db):
        """无快照依赖时，unconfirm 成功且配对 CASH 腿同步回 pending"""
        create_portfolio(test_db, code="UC_P2", status="active")
        create_product(test_db, code="ETF_UC2", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UC_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "UC_P2", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UC_PLAT2", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UC_P2",
                "product_code": "ETF_UC2",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UC_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200

        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 200
        # 验证主腿与配对 CASH 腿均回 pending（按 transfer_group 过滤，排除预置现金腿）
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert fund_leg.status == "pending"
        assert paired.status == "pending"


class TestUpdateDeletePairedSync:
    """#26 PUT/DELETE 配对 CASH 腿同步"""

    def test_update_trade_date_syncs_cash_leg(self, client, admin_headers, test_db):
        """update 改动 trade_date 时 confirm_date 联动重算，并同步配对 CASH 腿"""
        create_portfolio(test_db, code="UPD_P1", status="active")
        create_product(test_db, code="ETF_UPD", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UPD_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 8), is_open=True)
        create_trade(
            test_db, "UPD_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UPD_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UPD_P1",
                "product_code": "ETF_UPD",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UPD_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        # #93: CASH sell 腿独立确认日 = trade_date（T日扣款），不依赖基金腿
        assert cash_leg.confirm_date == date(2025, 10, 6)

        # confirm_date 不开放直改：额外字段被 schema 忽略，不产生变更
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"confirm_date": "2025-10-08", "notes": "try direct set"},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        assert fund_leg.confirm_date == date(2025, 10, 6)

        # 改 trade_date → 基金腿按 confirm_days 联动重算；CASH sell 腿独立取 trade_date（#93）
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"trade_date": "2025-10-08"},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        assert fund_leg.confirm_date == date(2025, 10, 8)
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        # #93: CASH sell 腿独立确认日 = 新 trade_date
        assert cash_leg.confirm_date == date(2025, 10, 8)
        # CASH 腿 trade_date 随基金腿同步（组内不变量）
        assert cash_leg.trade_date == date(2025, 10, 8)

        # #93: confirm → unconfirm 后各腿独立回退默认确认日
        # CASH sell 腿回退到 trade_date（T日扣款），基金腿按 confirm_days 重算
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200
        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert fund_leg.status == "pending" and cash_leg.status == "pending"
        assert cash_leg.trade_date == date(2025, 10, 8)
        # #93: CASH sell 腿独立确认日 = trade_date，基金腿独立按 confirm_days 重算
        # 此处 confirm_days=0 所以两值相等，但语义独立（不再互相同步）
        assert cash_leg.confirm_date == date(2025, 10, 8)  # = trade_date（T日扣款）
        assert fund_leg.confirm_date == date(2025, 10, 8)  # = T+0（confirm_days=0）

    def test_update_trade_status_field_ignored(self, client, admin_headers, test_db):
        """PUT 传 status 被忽略（状态流转只走 confirm/cancel/unconfirm 端点）"""
        create_portfolio(test_db, code="UPD_P2", status="active")
        create_product(test_db, code="ETF_UPD2", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UPD_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "UPD_P2", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UPD_PLAT2", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UPD_P2",
                "product_code": "ETF_UPD2",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UPD_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"status": "confirmed"},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == fund_leg.transfer_group,
            Trade.id != trade_id,
        ).first()
        # status 字段被 schema 忽略，两腿均保持 pending
        assert fund_leg.status == "pending"
        assert cash_leg.status == "pending"

    def test_delete_trade_cascades_cash_leg(self, client, admin_headers, test_db):
        """delete 主腿时级联删除配对 CASH 腿"""
        create_portfolio(test_db, code="DEL_P1", status="active")
        create_product(test_db, code="ETF_DEL", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="DEL_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "DEL_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="DEL_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "DEL_P1",
                "product_code": "ETF_DEL",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "DEL_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        before = test_db.query(Trade).filter(Trade.transfer_group == tg).count()
        assert before == 2

        dele = client.delete(f"/api/trades/{trade_id}", headers=admin_headers)
        assert dele.status_code == 200
        test_db.expire_all()
        after = test_db.query(Trade).filter(Trade.transfer_group == tg).count()
        assert after == 0


class TestUpdateAmountSyncCashLeg:
    """#46 PUT 改金额后配对 CASH 腿同步"""

    def test_update_actual_amount_syncs_cash_leg(self, client, admin_headers, test_db):
        """修改基金腿 actual_amount 后，配对 CASH 腿 amount 同步更新"""
        create_portfolio(test_db, code="AMT_P1", status="active")
        create_product(test_db, code="ETF_AMT", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="AMT_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "AMT_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="AMT_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 创建基金买入（自动生成配对 CASH 腿）
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "AMT_P1",
                "product_code": "ETF_AMT",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "AMT_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        trade_id = resp.json()["id"]
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert paired is not None
        original_paired_amount = float(paired.amount)

        # PUT 修改 actual_amount
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"actual_amount": 8000.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        paired_after = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        # 配对 CASH 腿金额应同步为新的 actual_amount
        assert float(paired_after.amount) == 8000.0
        assert float(paired_after.actual_amount) == 8000.0
        assert float(paired_after.amount) != original_paired_amount


class TestAsOfDateSellValidation:
    """#47 补录历史卖出时 as_of_date 排除后续 confirmed"""

    def test_backfill_sell_not_blocked_by_later_confirmed(self, client, admin_headers, test_db):
        """补录历史日卖出，后续 confirmed 卖出不应计入扣减"""
        create_portfolio(test_db, code="ASOF_P1", status="active")
        create_product(test_db, code="FUND_ASOF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1)
        create_platform(test_db, code="ASOF_PLAT")
        ensure_trading_day(test_db, date(2025, 1, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 1, 7), is_open=True)
        ensure_trading_day(test_db, date(2025, 1, 8), is_open=True)
        ensure_trading_day(test_db, date(2025, 1, 9), is_open=True)
        ensure_trading_day(test_db, date(2025, 1, 10), is_open=True)
        # 快照：shares=1000
        create_value_snapshot(test_db, "ASOF_P1", date(2025, 1, 6),
                              total_value=1000, total_shares=1000, unit_price=1.0)
        create_position_snapshot(
            test_db, "ASOF_P1", "FUND_ASOF", "CN_OTC", date(2025, 1, 6),
            shares=1000, platform_code="ASOF_PLAT",
        )
        # 后续 confirmed 卖出 800（confirm_date=1/9，快照后）
        create_trade(
            test_db, "ASOF_P1", "FUND_ASOF", "CN_OTC",
            trade_type="sell", shares=800, status="confirmed",
            trade_date=date(2025, 1, 8), confirm_date=date(2025, 1, 9),
            platform_code="ASOF_PLAT",
        )
        # 补录 1/7 卖出 500：as_of=1/7 时后续 1/9 confirmed 不计入，可用=1000
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "ASOF_P1",
                "product_code": "FUND_ASOF",
                "market": "CN_OTC",
                "trade_type": "sell",
                "shares": 500,
                "price": 1.0,
                "platform_code": "ASOF_PLAT",
                "trade_date": "2025-01-07",
            },
            headers=admin_headers,
        )
        # 不应被 INSUFFICIENT_SHARES 拒绝
        assert resp.status_code in (200, 201), f"Expected success, got {resp.status_code}: {resp.json()}"


class TestCancelExchangeErrorMessage:
    """#49 场内 cancel 错误信息包含修正路径"""

    def test_cancel_exchange_message_has_correction_path(self, client, admin_headers, test_db):
        """场内交易 cancel 拒绝时 message 包含 PUT 和 DELETE 关键词"""
        create_portfolio(test_db, code="MSG_P1", status="active")
        create_product(test_db, code="ETF_MSG", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="MSG_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "MSG_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="MSG_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 创建场内 pending trade（手动插入，因为场内创建后一般当天确认）
        from decimal import Decimal
        t = Trade(
            portfolio_code="MSG_P1", product_code="ETF_MSG", market="CN_EXCHANGE",
            platform_code="MSG_PLAT", trade_type="buy",
            amount=Decimal("5000"), price=Decimal("1.5"),
            fee=Decimal("0"), actual_amount=Decimal("5000"),
            trade_date=date(2025, 10, 6), status="pending",
            transfer_group="rebal_msgtest001",
        )
        test_db.add(t)
        test_db.commit()
        test_db.refresh(t)

        resp = client.post(f"/api/trades/{t.id}/cancel", headers=admin_headers)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "CANNOT_CANCEL_EXCHANGE"
        assert "PUT" in detail["message"]
        assert "DELETE" in detail["message"]


class TestCashTradeForbidden:
    """#53 禁止直接创建裸 CASH 交易"""

    def test_create_cash_trade_rejected(self, client, admin_headers, test_db):
        """REST POST /api/trades 传 product_code=CASH 应被 422 CASH_TRADE_FORBIDDEN 拒绝"""
        create_portfolio(test_db, code="CASH_FBD", status="active")
        create_platform(test_db, code="CASH_FBD_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "CASH_FBD",
                "product_code": "CASH",
                "market": "",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "CASH_FBD_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json()["detail"]["error"] == "CASH_TRADE_FORBIDDEN"

    def test_create_fund_buy_generates_paired_cash_leg(self, client, admin_headers, test_db):
        """REST 基金买入自动生成共享 transfer_group 的配对 CASH 腿"""
        create_portfolio(test_db, code="PAIR_P1", status="active")
        create_product(test_db, code="ETF_PAIR", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="PAIR_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "PAIR_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="PAIR_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "PAIR_P1",
                "product_code": "ETF_PAIR",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "PAIR_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        fund_id = resp.json()["id"]
        fund_leg = test_db.query(Trade).get(fund_id)
        assert fund_leg.transfer_group is not None
        assert fund_leg.transfer_group.startswith("rebal_")
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == fund_leg.transfer_group,
            Trade.id != fund_id,
        ).all()
        assert len(paired) == 1
        assert paired[0].product_code == "CASH"
        assert paired[0].trade_type == "sell"
        assert float(paired[0].amount) == 10000.0


class TestTradePreview:
    """#65 确认前预览：GET /api/trades/{id}/preview 与真实 confirm 完全一致"""

    def _setup_base(self, test_db, *, portfolio, product, platform,
                    confirm_days=1, is_qdii=False, nav=None):
        """创建组合/产品/平台/交易日/可用现金，可选写入 T 日净值"""
        create_portfolio(test_db, code=portfolio, status="active")
        create_product(test_db, code=product, market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=confirm_days, is_qdii=is_qdii)
        create_platform(test_db, code=platform)
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 8), is_open=True)
        create_trade(
            test_db, portfolio, "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code=platform, trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        if nav is not None:
            create_price_record(test_db, product, "CN_OTC", date(2025, 10, 6), unit_price=nav)

    def _create_otc_buy(self, client, admin_headers, *, portfolio, product, platform,
                        amount=10000.0, fee=0.0):
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": portfolio,
                "product_code": product,
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": amount,
                "fee": fee,
                "platform_code": platform,
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        return resp.json()["id"]

    def test_preview_matches_confirm_otc_buy(self, client, admin_headers, test_db):
        """场外 OEF 买入：preview 各字段与 confirm 后 trade 逐一相等，CASH 腿金额 == paired_cash_amount"""
        self._setup_base(test_db, portfolio="PRV_B1", product="FUND_PRV1",
                         platform="PRV_PLAT1", nav=1.25)
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_B1", product="FUND_PRV1", platform="PRV_PLAT1",
        )

        prev = client.get(f"/api/trades/{trade_id}/preview", headers=admin_headers)
        assert prev.status_code == 200, f"Response: {prev.status_code} {prev.json()}"
        data = prev.json()
        preview = data["preview"]
        assert preview["is_otc_nav_fund"] is True
        assert preview["nav_date"] == "2025-10-06"

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        confirmed = conf.json()["trade"]

        # preview 与真实确认逐字段一致
        assert float(preview["price"]) == float(confirmed["price"])
        assert float(preview["shares"]) == float(confirmed["shares"])
        assert float(preview["amount"]) == float(confirmed["amount"])
        assert float(preview["actual_amount"]) == float(confirmed["actual_amount"])
        assert preview["confirm_date"] == confirmed["confirm_date"]

        # 配对 CASH 腿金额 == paired_cash_amount
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == fund_leg.transfer_group,
            Trade.id != trade_id,
        ).first()
        assert float(paired.amount) == float(data["paired_cash_amount"])

    def test_preview_matches_confirm_otc_sell(self, client, admin_headers, test_db):
        """场外 OEF 卖出：preview 与 confirm 结果一致"""
        self._setup_base(test_db, portfolio="PRV_S1", product="FUND_PRV2",
                         platform="PRV_PLAT2", nav=1.25)
        # 先有持仓可卖
        create_position_snapshot(
            test_db, "PRV_S1", "FUND_PRV2", "CN_OTC",
            snapshot_date=date(2025, 10, 3),
            shares=1000.0, unit_price=1.2, cost_price=1.2,
            market_value=1200.0, platform_code="PRV_PLAT2",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "PRV_S1",
                "product_code": "FUND_PRV2",
                "market": "CN_OTC",
                "trade_type": "sell",
                "shares": 400.0,
                "platform_code": "PRV_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        trade_id = resp.json()["id"]

        prev = client.get(f"/api/trades/{trade_id}/preview", headers=admin_headers)
        assert prev.status_code == 200, f"Response: {prev.status_code} {prev.json()}"
        data = prev.json()
        preview = data["preview"]
        # 卖出：amount = 400 × 1.25 = 500，actual = 500 - 0
        assert float(preview["amount"]) == 500.0
        assert float(preview["actual_amount"]) == 500.0

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        confirmed = conf.json()["trade"]
        assert float(preview["price"]) == float(confirmed["price"])
        assert float(preview["shares"]) == float(confirmed["shares"])
        assert float(preview["amount"]) == float(confirmed["amount"])
        assert float(preview["actual_amount"]) == float(confirmed["actual_amount"])
        assert preview["confirm_date"] == confirmed["confirm_date"]

        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == fund_leg.transfer_group,
            Trade.id != trade_id,
        ).first()
        assert float(paired.amount) == float(data["paired_cash_amount"])

    def test_preview_qdii_confirm_date_consistent(self, client, admin_headers, test_db):
        """QDII（confirm_days=2）：preview 的 confirm_date 与 confirm 后一致（T+2）"""
        self._setup_base(test_db, portfolio="PRV_Q1", product="FUND_PRVQ",
                         platform="PRV_PLATQ", confirm_days=2, is_qdii=True, nav=1.25)
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_Q1", product="FUND_PRVQ", platform="PRV_PLATQ",
        )

        prev = client.get(f"/api/trades/{trade_id}/preview", headers=admin_headers)
        assert prev.status_code == 200, f"Response: {prev.status_code} {prev.json()}"
        preview = prev.json()["preview"]
        assert preview["confirm_date"] == "2025-10-08"

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        assert preview["confirm_date"] == conf.json()["trade"]["confirm_date"]

    def test_preview_missing_nav_rejected(self, client, admin_headers, test_db):
        """T 日净值缺失：preview 返回 422 MISSING_NAV"""
        self._setup_base(test_db, portfolio="PRV_N1", product="FUND_PRVN",
                         platform="PRV_PLATN", nav=None)
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_N1", product="FUND_PRVN", platform="PRV_PLATN",
        )

        prev = client.get(f"/api/trades/{trade_id}/preview", headers=admin_headers)
        assert prev.status_code == 422
        assert prev.json()["detail"]["error"] == "MISSING_NAV"

    def test_preview_price_nav_mismatch_rejected(self, client, admin_headers, test_db):
        """传入与 T 日净值不一致的 price：preview 返回 422 PRICE_NAV_MISMATCH"""
        self._setup_base(test_db, portfolio="PRV_M1", product="FUND_PRVM",
                         platform="PRV_PLATM", nav=1.25)
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_M1", product="FUND_PRVM", platform="PRV_PLATM",
        )

        prev = client.get(
            f"/api/trades/{trade_id}/preview", params={"price": 1.30},
            headers=admin_headers,
        )
        assert prev.status_code == 422
        assert prev.json()["detail"]["error"] == "PRICE_NAV_MISMATCH"

    # #428：对账比较改为 quantize_nav（两侧归一到 4 位 HALF_UP）后的边界契约。
    # nav 是 Numeric(10,4)，本来恰为 4 位；传入价是用户输入、标度不限——
    # 「先量化到 4 位再精确比较（无容差）」这条口径由下面两例锁死。
    def test_preview_price_matching_four_decimal_nav_passes(
        self, client, admin_headers, test_db
    ):
        """净值 1.2345（4 位）：传入同值放行，多写尾随零（1.23450）同样放行——标度无关"""
        self._setup_base(test_db, portfolio="PRV_Q4", product="FUND_PRVQ4",
                         platform="PRV_PLATQ4", nav=Decimal("1.2345"))
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_Q4", product="FUND_PRVQ4", platform="PRV_PLATQ4",
        )

        for price in ("1.2345", "1.23450"):
            prev = client.get(
                f"/api/trades/{trade_id}/preview", params={"price": price},
                headers=admin_headers,
            )
            assert prev.status_code == 200, (
                f"price={price} 应放行，实际 {prev.status_code} {prev.json()}"
            )
            assert prev.json()["preview"]["price"] is not None

    def test_preview_price_differing_in_fourth_decimal_rejected(
        self, client, admin_headers, test_db
    ):
        """净值 1.2345：第 4 位起就不同（1.2346）即拒绝——4 位口径无容差"""
        self._setup_base(test_db, portfolio="PRV_Q5", product="FUND_PRVQ5",
                         platform="PRV_PLATQ5", nav=Decimal("1.2345"))
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_Q5", product="FUND_PRVQ5", platform="PRV_PLATQ5",
        )

        prev = client.get(
            f"/api/trades/{trade_id}/preview", params={"price": "1.2346"},
            headers=admin_headers,
        )
        assert prev.status_code == 422
        assert prev.json()["detail"]["error"] == "PRICE_NAV_MISMATCH"

    def test_preview_confirmed_trade_rejected(self, client, admin_headers, test_db):
        """对已 confirmed 交易 preview：422 INVALID_STATUS"""
        self._setup_base(test_db, portfolio="PRV_C1", product="FUND_PRVC",
                         platform="PRV_PLATC", nav=1.25)
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_C1", product="FUND_PRVC", platform="PRV_PLATC",
        )
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200

        prev = client.get(f"/api/trades/{trade_id}/preview", headers=admin_headers)
        assert prev.status_code == 422
        assert prev.json()["detail"]["error"] == "INVALID_STATUS"

    def test_preview_has_zero_side_effects(self, client, admin_headers, test_db):
        """preview 后 trade 仍 pending、price 仍 null，CASH 腿状态/金额未变"""
        self._setup_base(test_db, portfolio="PRV_Z1", product="FUND_PRVZ",
                         platform="PRV_PLATZ", nav=1.25)
        trade_id = self._create_otc_buy(
            client, admin_headers,
            portfolio="PRV_Z1", product="FUND_PRVZ", platform="PRV_PLATZ",
        )
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        cash_before = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        cash_status_before = cash_before.status
        cash_amount_before = float(cash_before.amount)

        prev = client.get(f"/api/trades/{trade_id}/preview", headers=admin_headers)
        assert prev.status_code == 200

        # 重新 GET trade：零副作用
        got = client.get(f"/api/trades/{trade_id}", headers=admin_headers)
        assert got.status_code == 200
        assert got.json()["status"] == "pending"
        assert got.json()["price"] is None

        test_db.expire_all()
        cash_after = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert cash_after.status == cash_status_before
        assert float(cash_after.amount) == cash_amount_before


class TestListTradeFilters:
    """调仓列表筛选/排序（issue #126）"""

    def test_filter_by_status(self, client, admin_headers, test_db):
        """三种状态各造 1 条，分别过滤只回目标状态"""
        create_portfolio(test_db, code="LT_P1", status="active")
        create_trade(test_db, "LT_P1", "CASH", "", trade_type="buy", status="pending",
                     trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P1", "CASH", "", trade_type="buy", status="confirmed",
                     trade_date=date(2025, 9, 2), confirm_date=date(2025, 9, 2))
        create_trade(test_db, "LT_P1", "CASH", "", trade_type="buy", status="cancelled",
                     trade_date=date(2025, 9, 3))

        for st in ("pending", "confirmed", "cancelled"):
            resp = client.get(f"/api/trades?status={st}", headers=admin_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert all(item["status"] == st for item in data["items"])

    def test_filter_by_trade_type_and_platform(self, client, admin_headers, test_db):
        """trade_type + platform_code 组合过滤取交集"""
        create_portfolio(test_db, code="LT_P2", status="active")
        create_platform(test_db, code="LT_PLAT")
        create_trade(test_db, "LT_P2", "CASH", "", trade_type="sell",
                     platform_code="LT_PLAT", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P2", "CASH", "", trade_type="sell",
                     platform_code="MYCF", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P2", "CASH", "", trade_type="buy",
                     platform_code="LT_PLAT", trade_date=date(2025, 9, 1))

        resp = client.get(
            "/api/trades?trade_type=sell&platform_code=LT_PLAT",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["trade_type"] == "sell"
        assert data["items"][0]["platform_code"] == "LT_PLAT"

    def test_filter_trade_date_range_closed(self, client, admin_headers, test_db):
        """交易日期区间为闭区间：边界日记录包含"""
        create_portfolio(test_db, code="LT_P3", status="active")
        create_trade(test_db, "LT_P3", "CASH", "", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P3", "CASH", "", trade_date=date(2025, 9, 5))
        create_trade(test_db, "LT_P3", "CASH", "", trade_date=date(2025, 9, 10))

        resp = client.get(
            "/api/trades?trade_date_start=2025-09-01&trade_date_end=2025-09-05",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        dates = sorted(item["trade_date"] for item in data["items"])
        assert dates == ["2025-09-01", "2025-09-05"]

    def test_filter_confirm_date_range(self, client, admin_headers, test_db):
        """确认日期区间过滤（含 pending 预计确认日）"""
        create_portfolio(test_db, code="LT_P8", status="active")
        create_trade(test_db, "LT_P8", "CASH", "", status="pending",
                     trade_date=date(2025, 9, 1), confirm_date=date(2025, 9, 2))
        create_trade(test_db, "LT_P8", "CASH", "", status="confirmed",
                     trade_date=date(2025, 9, 3), confirm_date=date(2025, 9, 4))

        resp = client.get(
            "/api/trades?confirm_date_start=2025-09-02&confirm_date_end=2025-09-02",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "pending"
        assert data["items"][0]["confirm_date"] == "2025-09-02"

    def test_inverted_range_returns_422(self, client, admin_headers, test_db):
        """start > end 返回 422 INVALID_DATE_RANGE（trade/confirm 两组同理）"""
        resp = client.get(
            "/api/trades?trade_date_start=2025-09-10&trade_date_end=2025-09-01",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_RANGE"

        resp = client.get(
            "/api/trades?confirm_date_start=2025-09-10&confirm_date_end=2025-09-01",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_RANGE"

    def test_sort_trade_date_desc(self, client, admin_headers, test_db):
        """排序 trade_date DESC（不同日期降序）"""
        create_portfolio(test_db, code="LT_P4", status="active")
        t1 = create_trade(test_db, "LT_P4", "CASH", "", trade_date=date(2025, 9, 1))
        t2 = create_trade(test_db, "LT_P4", "CASH", "", trade_date=date(2025, 9, 10))
        t3 = create_trade(test_db, "LT_P4", "CASH", "", trade_date=date(2025, 9, 5))

        resp = client.get("/api/trades", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        assert ids == [t2.id, t3.id, t1.id]

    def test_sort_groups_adjacent(self, client, admin_headers, test_db):
        """同 transfer_group 两腿在结果中相邻（排序键含 transfer_group，决策⑪）"""
        create_portfolio(test_db, code="LT_P5", status="active")
        # 同交易日两个组：trade_date 相同时按 transfer_group 聚集
        fund_leg = create_trade(test_db, "LT_P5", "510300.SH", "CN_EXCHANGE",
                                trade_type="buy", trade_date=date(2025, 9, 1),
                                transfer_group="rebal_adj01")
        cash_leg = create_trade(test_db, "LT_P5", "CASH", "",
                                trade_type="sell", trade_date=date(2025, 9, 1),
                                transfer_group="rebal_adj01")
        other = create_trade(test_db, "LT_P5", "CASH", "",
                             trade_type="buy", trade_date=date(2025, 9, 1),
                             transfer_group="rebal_adj02")

        resp = client.get("/api/trades", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        # 同组两腿相邻，且整组排在 adj02 组之前（transfer_group 升序）
        assert abs(ids.index(fund_leg.id) - ids.index(cash_leg.id)) == 1
        assert ids.index(fund_leg.id) < ids.index(other.id)
        assert ids.index(cash_leg.id) < ids.index(other.id)

    def test_filter_product_code_only_matches_all_markets(self, client, admin_headers, test_db):
        """仅 product_code 时 LOF 一码多市场全命中"""
        create_portfolio(test_db, code="LT_P6", status="active")
        create_product(test_db, code="LOF01", market="CN_EXCHANGE", product_type="LOF",
                       confirm_days=0)
        create_product(test_db, code="LOF01", market="CN_OTC", product_type="LOF")
        create_trade(test_db, "LT_P6", "LOF01", "CN_EXCHANGE", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P6", "LOF01", "CN_OTC", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P6", "CASH", "", trade_date=date(2025, 9, 1))

        resp = client.get("/api/trades?product_code=LOF01", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        markets = sorted(item["market"] for item in data["items"])
        assert markets == ["CN_EXCHANGE", "CN_OTC"]

    def test_filter_product_code_with_market_exact(self, client, admin_headers, test_db):
        """product_code + market 精确过滤（LOF 只命中指定市场）"""
        create_portfolio(test_db, code="LT_P7", status="active")
        create_product(test_db, code="LOF02", market="CN_EXCHANGE", product_type="LOF",
                       confirm_days=0)
        create_product(test_db, code="LOF02", market="CN_OTC", product_type="LOF")
        create_trade(test_db, "LT_P7", "LOF02", "CN_EXCHANGE", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P7", "LOF02", "CN_OTC", trade_date=date(2025, 9, 1))

        resp = client.get(
            "/api/trades?product_code=LOF02&market=CN_OTC",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["market"] == "CN_OTC"

    def test_filter_products_multi_pairs(self, client, admin_headers, test_db):
        """products=A|CN_OTC,B|CN_EXCHANGE 复合多选命中两笔，LOF 不串市场（issue #155）"""
        create_portfolio(test_db, code="LT_P9", status="active")
        create_product(test_db, code="LOF03", market="CN_EXCHANGE", product_type="LOF",
                       confirm_days=0)
        create_product(test_db, code="LOF03", market="CN_OTC", product_type="LOF")
        create_product(test_db, code="ETF03", market="CN_EXCHANGE", product_type="ETF",
                       confirm_days=0)
        create_trade(test_db, "LT_P9", "LOF03", "CN_OTC", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P9", "LOF03", "CN_EXCHANGE", trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P9", "ETF03", "CN_EXCHANGE", trade_date=date(2025, 9, 1))

        resp = client.get(
            "/api/trades?products=LOF03|CN_OTC,ETF03|CN_EXCHANGE",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        pairs = sorted((item["product_code"], item["market"]) for item in data["items"])
        assert pairs == [("ETF03", "CN_EXCHANGE"), ("LOF03", "CN_OTC")]

    def test_filter_products_empty_market_segment(self, client, admin_headers, test_db):
        """products 单值 code|（空 market 段）匹配 market="" 的 CASH 腿"""
        create_portfolio(test_db, code="LT_P10", status="active")
        create_trade(test_db, "LT_P10", "CASH", "", trade_type="sell",
                     trade_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P10", "510300.SH", "CN_EXCHANGE",
                     trade_date=date(2025, 9, 1))

        resp = client.get("/api/trades?products=CASH|", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["product_code"] == "CASH"
        assert data["items"][0]["market"] == ""

    def test_filter_products_conflict_with_single_params(self, client, admin_headers, test_db):
        """products 与 product_code/market 同传 → 422 PRODUCTS_PARAM_CONFLICT"""
        resp = client.get(
            "/api/trades?products=CASH|&product_code=CASH",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCTS_PARAM_CONFLICT"

        resp = client.get(
            "/api/trades?products=CASH|&market=CN_OTC",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCTS_PARAM_CONFLICT"

    def test_filter_products_with_date_and_status(self, client, admin_headers, test_db):
        """products 与既有日期/状态筛选 AND 叠加"""
        create_portfolio(test_db, code="LT_P11", status="active")
        create_trade(test_db, "LT_P11", "CASH", "", status="confirmed",
                     trade_date=date(2025, 9, 1), confirm_date=date(2025, 9, 1))
        create_trade(test_db, "LT_P11", "CASH", "", status="pending",
                     trade_date=date(2025, 9, 5))
        create_trade(test_db, "LT_P11", "510300.SH", "CN_EXCHANGE", status="confirmed",
                     trade_date=date(2025, 9, 3), confirm_date=date(2025, 9, 3))

        resp = client.get(
            "/api/trades?products=CASH|,510300.SH|CN_EXCHANGE"
            "&status=confirmed&trade_date_start=2025-09-01&trade_date_end=2025-09-02",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["product_code"] == "CASH"
        assert data["items"][0]["status"] == "confirmed"


class TestListTradeProductName:
    """list 响应读侧派生 product_name（issue #175）"""

    def test_list_trades_includes_product_name(self, client, admin_headers, test_db):
        """基金买入产生基金腿 + 配对 CASH 腿，list 每条 item 均应带 product_name
        （基金腿=基金名，CASH 腿=CASH 种子产品名）。
        注：>100 产品的名称回退是前端分页映射问题，后端 join 与产品总数无关，
        无需构造 100+ 产品。"""
        create_portfolio(test_db, code="PN_P1", status="active")
        create_product(test_db, code="ETF_PN", market="CN_EXCHANGE",
                       name="测试ETF产品", product_type="ETF",
                       asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="PN_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "PN_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="PN_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "PN_P1",
                "product_code": "ETF_PN",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "PN_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"

        resp = client.get("/api/trades?portfolio_code=PN_P1", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        # 现金流入 1 条 + 基金腿 1 条 + 配对 CASH 腿 1 条
        assert data["total"] == 3
        name_by_leg = {
            (item["product_code"], item["trade_type"]): item["product_name"]
            for item in data["items"]
        }
        assert all(item["product_name"] for item in data["items"])
        assert name_by_leg[("ETF_PN", "buy")] == "测试ETF产品"
        assert name_by_leg[("CASH", "sell")] == "现金类资产"
        assert name_by_leg[("CASH", "buy")] == "现金类资产"
        # 字段完整性（issue #183）：挂 response_model 后字段过滤以 TradeResponse
        # 为准，断言实际响应键与 schema 声明一一对应、无字段丢失
        assert set(data["items"][0].keys()) == set(TradeResponse.model_fields.keys())


class TestTradesOpenApiContract:
    """openapi 契约守护（issue #183）：GET /api/trades 分页响应结构化"""

    def test_trades_list_openapi_references_paginated_schema(self, client):
        """openapi.json 中 /api/trades GET 200 应引用 PaginatedTradeResponse，
        且 items 元素指向 TradeResponse（含 product_name），而非空 schema。"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()

        get_op = spec["paths"]["/api/trades"]["get"]
        schema_ref = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema_ref == {"$ref": "#/components/schemas/PaginatedTradeResponse"}

        schemas = spec["components"]["schemas"]
        paginated = schemas["PaginatedTradeResponse"]
        assert set(paginated["required"]) == {"items", "total", "page", "page_size"}
        assert set(paginated["properties"].keys()) == {
            "items", "total", "page", "page_size",
        }
        assert paginated["properties"]["items"]["items"] == {
            "$ref": "#/components/schemas/TradeResponse"
        }

        trade_props = schemas["TradeResponse"]["properties"]
        assert "product_name" in trade_props  # 防止误删读侧派生字段声明


class TestUpdateTradeValidation:
    """#182 PUT 直改校验：可用量/交易日/CASH 腿/防重/零部分写入"""

    PRODUCT = "ETF_182"

    def _setup(self, test_db, *, code, cash=10000.0, shares=None):
        """组合 + 产品 + 平台 + 交易日 + 可用现金（confirmed CASH buy），可选持仓快照"""
        create_portfolio(test_db, code=code, status="active")
        create_product(test_db, code=self.PRODUCT, market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code=f"{code}_PL")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, code, "CASH", "",
            trade_type="buy", amount=cash, price=None,
            platform_code=f"{code}_PL", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        if shares is not None:
            create_position_snapshot(
                test_db, code, self.PRODUCT, "CN_EXCHANGE",
                snapshot_date=date(2025, 10, 3),
                shares=shares, platform_code=f"{code}_PL",
            )

    def _create_buy(self, client, admin_headers, *, code, amount=1000.0):
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code,
                "product_code": self.PRODUCT,
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": amount,
                "price": 1.5,
                "platform_code": f"{code}_PL",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        return resp.json()["id"]

    def _create_sell(self, client, admin_headers, *, code, shares=500.0):
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code,
                "product_code": self.PRODUCT,
                "market": "CN_EXCHANGE",
                "trade_type": "sell",
                "shares": shares,
                "price": 1.5,
                "platform_code": f"{code}_PL",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        return resp.json()["id"]

    def _cash_leg(self, test_db, trade_id):
        fund = test_db.query(Trade).get(trade_id)
        return test_db.query(Trade).filter(
            Trade.transfer_group == fund.transfer_group,
            Trade.id != trade_id,
        ).first()

    # ---- buy 可用现金 ----

    def test_update_buy_amount_exceeds_cash_rejected(self, client, admin_headers, test_db):
        """PUT 改买入金额超可用现金 -> 422 INSUFFICIENT_CASH，零部分写入"""
        self._setup(test_db, code="U182_P1", cash=5000.0)
        trade_id = self._create_buy(client, admin_headers, code="U182_P1", amount=1000.0)

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"amount": 999999.0, "fee": 50.0},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "INSUFFICIENT_CASH"

        # 零部分写入：金额/手续费与配对 CASH 腿均保持原值
        got = client.get(f"/api/trades/{trade_id}", headers=admin_headers)
        assert got.status_code == 200
        data = got.json()
        assert float(data["actual_amount"]) == 1000.0
        assert float(data["amount"]) == 1000.0
        assert float(data["fee"]) == 0.0
        test_db.expire_all()
        cash_leg = self._cash_leg(test_db, trade_id)
        assert float(cash_leg.amount) == 1000.0

    def test_update_buy_amount_reduce_with_self_addback(self, client, admin_headers, test_db):
        """可用现金已被自身旧值占用（5000 全额买入），改小（5000->4000）放行并联动"""
        self._setup(test_db, code="U182_P2", cash=5000.0)
        trade_id = self._create_buy(client, admin_headers, code="U182_P2", amount=5000.0)

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"amount": 4000.0, "fee": 100.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        data = upd.json()
        # D1 语义：amount 输入为含费现金支出 X -> actual_amount=X、amount=X-fee
        assert float(data["actual_amount"]) == 4000.0
        assert float(data["amount"]) == 3900.0
        assert float(data["fee"]) == 100.0
        test_db.expire_all()
        cash_leg = self._cash_leg(test_db, trade_id)
        assert float(cash_leg.amount) == 4000.0
        assert float(cash_leg.actual_amount) == 4000.0

    def test_update_buy_amount_semantics_confirm_flow(self, client, admin_headers, test_db):
        """buy 编辑金额对 OTC 基金真实生效：confirm 后份额/金额与编辑值一致（#174 断裂修复）"""
        create_portfolio(test_db, code="U182_P3", status="active")
        create_product(test_db, code="FUND_182", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="U182_P3_PL")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "U182_P3", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="U182_P3_PL", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        create_price_record(test_db, "FUND_182", "CN_OTC",
                            date(2025, 10, 6), unit_price=1.25)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "U182_P3",
                "product_code": "FUND_182",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "U182_P3_PL",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        trade_id = resp.json()["id"]

        # 编辑为含费支出 12000（fee 200）-> 净额 11800
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"amount": 12000.0, "fee": 200.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        assert float(upd.json()["actual_amount"]) == 12000.0
        assert float(upd.json()["amount"]) == 11800.0

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        trade = conf.json()["trade"]
        # confirm 按 T 日净值 1.25 重算：shares = 11800/1.25 = 9440
        assert float(trade["shares"]) == 9440.0
        assert float(trade["amount"]) == 11800.0
        assert float(trade["actual_amount"]) == 12000.0
        assert float(trade["fee"]) == 200.0
        # CASH 腿随确认镜像新含费支出
        test_db.expire_all()
        cash_leg = self._cash_leg(test_db, trade_id)
        assert float(cash_leg.amount) == 12000.0

    # ---- sell 可用份额 ----

    def test_update_sell_shares_exceeds_rejected(self, client, admin_headers, test_db):
        """PUT 改卖出份额超可用份额 -> 422 INSUFFICIENT_SHARES，零部分写入"""
        self._setup(test_db, code="U182_P4", shares=1000.0)
        trade_id = self._create_sell(client, admin_headers, code="U182_P4", shares=500.0)

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"shares": 99999.0},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "INSUFFICIENT_SHARES"

        got = client.get(f"/api/trades/{trade_id}", headers=admin_headers)
        assert float(got.json()["shares"]) == 500.0

    def test_update_sell_shares_reduce_with_self_addback(self, client, admin_headers, test_db):
        """份额已被自身 pending 卖出占用（1000 持仓卖 500），改小（500->300）放行"""
        self._setup(test_db, code="U182_P5", shares=1000.0)
        trade_id = self._create_sell(client, admin_headers, code="U182_P5", shares=500.0)

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"shares": 300.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        assert float(upd.json()["shares"]) == 300.0

    # ---- trade_date 校验 ----

    def test_update_trade_date_non_trading_day_rejected(self, client, admin_headers, test_db):
        """PUT 改 trade_date 为非交易日 -> 422 NON_TRADING_DAY（不再静默滚交易日）"""
        self._setup(test_db, code="U182_P6")
        trade_id = self._create_buy(client, admin_headers, code="U182_P6")

        # 2025-10-05 为周日（conftest 基础日历工作日开市，周末必非交易日）
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"trade_date": "2025-10-05"},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "NON_TRADING_DAY"

        # confirm_date 不被静默重算，trade_date 保持原值
        got = client.get(f"/api/trades/{trade_id}", headers=admin_headers)
        assert got.json()["trade_date"] == "2025-10-06"
        assert got.json()["confirm_date"] == "2025-10-06"

    def test_update_trade_date_before_snapshot_rejected(self, client, admin_headers, test_db):
        """PUT 改 trade_date 为早于/等于最新快照日 -> 422 DATE_BEFORE_SNAPSHOT"""
        self._setup(test_db, code="U182_P6")
        ensure_trading_day(test_db, date(2025, 10, 3), is_open=True)
        trade_id = self._create_buy(client, admin_headers, code="U182_P6")

        # 10-06 生成快照后，改 trade_date 回 10-03（< 最新快照日）应被拒
        create_value_snapshot(
            test_db, "U182_P6", date(2025, 10, 6),
            total_value=10000, total_shares=10000, unit_price=1.0,
        )
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"trade_date": "2025-10-03"},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "DATE_BEFORE_SNAPSHOT"

    # ---- 仅 price/fee/notes ----

    def test_update_price_fee_notes_only_no_availability_check(self, client, admin_headers, test_db):
        """仅改 price/fee/notes 不触发可用量校验（现金已被占满仍可保存）"""
        self._setup(test_db, code="U182_P8", cash=5000.0)
        trade_id = self._create_buy(client, admin_headers, code="U182_P8", amount=5000.0)
        # 追加一笔 pending CASH sell 占用现金：此时若触发可用量校验必失败
        create_trade(
            test_db, "U182_P8", "CASH", "",
            trade_type="sell", amount=2000.0, status="pending",
            trade_date=date(2025, 10, 6), platform_code="U182_P8_PL",
            transfer_group="test_u182_drain",
        )

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"price": 1.6, "fee": 10.0, "notes": "仅改价格费率与备注"},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        data = upd.json()
        assert float(data["price"]) == 1.6
        assert float(data["fee"]) == 10.0
        assert data["notes"] == "仅改价格费率与备注"
        # fee 联动重算净额列（actual_amount 不变）
        assert float(data["actual_amount"]) == 5000.0
        assert float(data["amount"]) == 4990.0

    # ---- 状态/CASH 腿拦截 ----

    def test_update_cancelled_trade_rejected(self, client, admin_headers, test_db):
        """cancelled 交易不可编辑 -> 422 INVALID_STATUS"""
        create_portfolio(test_db, code="U182_P9", status="active")
        create_product(test_db, code="FUND_182C", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1)
        create_platform(test_db, code="U182_P9_PL")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        create_trade(
            test_db, "U182_P9", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="U182_P9_PL", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "U182_P9",
                "product_code": "FUND_182C",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": 1000.0,
                "platform_code": "U182_P9_PL",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        cancel = client.post(f"/api/trades/{trade_id}/cancel", headers=admin_headers)
        assert cancel.status_code == 200

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"notes": "x"},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "INVALID_STATUS"

    def test_update_cash_leg_rejected(self, client, admin_headers, test_db):
        """CASH 腿直改被拒（CASH_TRADE_FORBIDDEN）；仅改 notes 放行"""
        self._setup(test_db, code="U182_P10")
        trade_id = self._create_buy(client, admin_headers, code="U182_P10")
        test_db.expire_all()
        cash_leg = self._cash_leg(test_db, trade_id)

        upd = client.put(
            f"/api/trades/{cash_leg.id}",
            json={"amount": 1.0},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "CASH_TRADE_FORBIDDEN"

        notes_only = client.put(
            f"/api/trades/{cash_leg.id}",
            json={"notes": "现金腿备注"},
            headers=admin_headers,
        )
        assert notes_only.status_code == 200
        assert notes_only.json()["notes"] == "现金腿备注"

    def test_update_closed_portfolio_rejected(self, client, admin_headers, test_db):
        """已关闭组合的交易不可编辑 -> 422 PORTFOLIO_NOT_ACTIVE"""
        self._setup(test_db, code="U182_P11")
        trade_id = self._create_buy(client, admin_headers, code="U182_P11")
        test_db.expire_all()
        portfolio = test_db.query(Portfolio).filter(
            Portfolio.code == "U182_P11"
        ).first()
        portfolio.status = "closed"
        test_db.commit()

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"notes": "x"},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "PORTFOLIO_NOT_ACTIVE"

    # ---- 防重 ----

    def test_update_to_duplicate_rejected(self, client, admin_headers, test_db):
        """编辑成与另一笔撞自然键 -> 422 DUPLICATE_TRADE（比对排除自身）"""
        self._setup(test_db, code="U182_P12", cash=10000.0)
        self._create_buy(client, admin_headers, code="U182_P12", amount=2000.0)
        other_id = self._create_buy(client, admin_headers, code="U182_P12", amount=3000.0)

        # 把 3000 那笔编辑成 2000 -> 与第一笔撞自然键
        upd = client.put(
            f"/api/trades/{other_id}",
            json={"amount": 2000.0},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "DUPLICATE_TRADE"

    # ---- confirm 路径卖出份额校验 ----

    def test_confirm_sell_insufficient_shares_rejected(self, client, admin_headers, test_db):
        """份额被其他 pending 卖出消耗后 confirm -> 422 INSUFFICIENT_SHARES（#182 漏洞回归）"""
        self._setup(test_db, code="U182_P13", shares=1000.0)
        trade_id = self._create_sell(client, admin_headers, code="U182_P13", shares=800.0)
        # 追加一笔 pending 卖出占用剩余份额（绕过创建侧校验构造存量）
        create_trade(
            test_db, "U182_P13", self.PRODUCT, "CN_EXCHANGE",
            trade_type="sell", shares=500.0, price=1.5, status="pending",
            trade_date=date(2025, 10, 6), confirm_date=date(2025, 10, 6),
            platform_code="U182_P13_PL",
            transfer_group="test_u182_consume",
        )

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 422
        assert conf.json()["detail"]["error"] == "INSUFFICIENT_SHARES"
        # 交易保持 pending
        got = client.get(f"/api/trades/{trade_id}", headers=admin_headers)
        assert got.json()["status"] == "pending"


class TestPlatformRequired:
    """#209 调仓交易 platform_code 必填 + 存在性（service 层统一校验）"""

    def test_create_without_platform_rejected(self, client, admin_headers, test_db):
        """买入不传 platform_code -> 422 PLATFORM_REQUIRED，基金腿与配对 CASH 腿均不落库"""
        create_portfolio(test_db, code="PLR_P1", status="active")
        create_product(test_db, code="ETF_PLR1", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="PLR_PLAT1")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        # 提供可用现金，确保平台之外一切合法（不被现金校验抢先拦截）
        create_trade(
            test_db, "PLR_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="PLR_PLAT1", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        base_count = test_db.query(Trade).filter_by(portfolio_code="PLR_P1").count()

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "PLR_P1",
                "product_code": "ETF_PLR1",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json()["detail"]["error"] == "PLATFORM_REQUIRED"
        # 基金腿与配对 CASH 腿均不落库（base 为 fixture 的现金入账记录）
        assert test_db.query(Trade).filter_by(portfolio_code="PLR_P1").count() == base_count

    def test_create_sell_without_platform_rejected(self, client, admin_headers, test_db):
        """卖出不传 platform_code -> 422 PLATFORM_REQUIRED（覆盖 CASH buy 腿平台继承路径）"""
        create_portfolio(test_db, code="PLR_P2", status="active")
        create_product(test_db, code="ETF_PLR2", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="PLR_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        # 先有持仓，确保平台之外一切合法（不被份额校验抢先拦截）
        create_position_snapshot(
            test_db, "PLR_P2", "ETF_PLR2", "CN_EXCHANGE",
            snapshot_date=date(2025, 10, 3),
            shares=1000.0, unit_price=1.5, cost_price=1.5,
            market_value=1500.0, platform_code="PLR_PLAT2",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "PLR_P2",
                "product_code": "ETF_PLR2",
                "market": "CN_EXCHANGE",
                "trade_type": "sell",
                "shares": 500.0,
                "price": 1.6,
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json()["detail"]["error"] == "PLATFORM_REQUIRED"
        assert test_db.query(Trade).filter_by(portfolio_code="PLR_P2").count() == 0

    def test_create_with_unknown_platform_rejected(self, client, admin_headers, test_db):
        """传不存在平台 -> 404 PLATFORM_NOT_FOUND（顺带修复 FK IntegrityError 爆 500 的潜在问题）"""
        create_portfolio(test_db, code="PLR_P3", status="active")
        create_product(test_db, code="ETF_PLR3", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code="PLR_PLAT3")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)

        create_trade(
            test_db, "PLR_P3", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="PLR_PLAT3", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        base_count = test_db.query(Trade).filter_by(portfolio_code="PLR_P3").count()

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "PLR_P3",
                "product_code": "ETF_PLR3",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "NO_SUCH_PLATFORM",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json()["detail"]["error"] == "PLATFORM_NOT_FOUND"
        assert test_db.query(Trade).filter_by(portfolio_code="PLR_P3").count() == base_count

