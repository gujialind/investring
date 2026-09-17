# ============ 集成测试：调仓交易 — 金额推导（自 test_trades.py 拆分，issue #469） ============
# 含 TestBuyTrade / TestSellTrade / TestExchangeSellAmountDerivation / TestOtcSellAmountDerivation。
# #190（场内卖出金额推导 + 一致性校验）：amount/actual_amount 是纯派生量，
#   amount = quantize(shares × price)、actual_amount = amount − fee；显式金额仅作对账校验。
# #190 后续：场外传价卖出仅推导不强对账；无价格占位单 PUT 仍以输入金额为准。
# 买入 amount 为含费现金支出的净额，配对 CASH 腿镜像净额。

from datetime import date

from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
    create_position_snapshot, create_value_snapshot, ensure_trading_day,
)
from app.models.trade import Trade


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

    def test_create_derives_amount_without_cash_leg(self, client, admin_headers, test_db):
        """不传 actual_amount：amount/actual_amount 由 shares×price 推导

        #493：卖出创建期**不建** CASH 腿（到账日/平台在确认时录入），故此处断言
        组内只有基金腿、派生现金字段为 null；到账腿的镜像由确认路径落定
        （见 test_confirm_keeps_derived_amount_and_cash_leg）。
        """
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        assert resp.status_code in (200, 201), resp.json()
        data = resp.json()
        assert data["amount"] == 6255.60
        assert data["actual_amount"] == 6254.97
        # 无配对现金腿 → 只读派生字段为 null
        assert data["cash_platform_code"] is None
        assert data["cash_confirm_date"] is None

        assert self._cash_leg(test_db) is None
        fund_leg = test_db.query(Trade).get(data["id"])
        assert fund_leg.transfer_group.startswith("rebal_")
        assert test_db.query(Trade).filter(
            Trade.transfer_group == fund_leg.transfer_group
        ).count() == 1

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
        """PUT 改份额：amount/actual_amount 随动重推导，确认后 CASH 腿镜像新净额"""
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        trade_id = resp.json()["id"]

        upd = client.put(
            f"/api/trades/{trade_id}", json={"shares": 6000.0}, headers=admin_headers,
        )
        assert upd.status_code == 200, upd.json()
        assert upd.json()["amount"] == 4812.00
        assert upd.json()["actual_amount"] == 4811.37

        # #493：pending 卖出无 CASH 腿，确认时按本次净额建腿并镜像
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        cash_leg = self._cash_leg(test_db)
        assert float(cash_leg.amount) == 4811.37
        assert float(cash_leg.actual_amount) == 4811.37
        assert cash_leg.status == "confirmed"

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
        """PUT 改 fee：毛额不变、净额 = 毛额 − 新 fee，确认后 CASH 腿镜像"""
        self._setup(client, test_db)
        resp = client.post("/api/trades", json=self._sell_payload(), headers=admin_headers)
        trade_id = resp.json()["id"]

        upd = client.put(
            f"/api/trades/{trade_id}", json={"fee": 10.0}, headers=admin_headers,
        )
        assert upd.status_code == 200, upd.json()
        assert upd.json()["amount"] == 6255.60
        assert upd.json()["actual_amount"] == 6245.60

        # #493：pending 卖出无 CASH 腿，确认时按本次净额建腿并镜像
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        cash_leg = self._cash_leg(test_db)
        assert float(cash_leg.amount) == 6245.60
        assert float(cash_leg.actual_amount) == 6245.60


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
