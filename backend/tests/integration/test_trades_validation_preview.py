# ============ 集成测试：调仓交易 — 校验与确认前预览（自 test_trades.py 拆分，issue #469） ============
# 含 TestAsOfDateSellValidation / TestCancelExchangeErrorMessage / TestCashTradeForbidden / TestTradePreview。
# #47 补录历史卖出时 as_of_date 排除后续 confirmed，不被 INSUFFICIENT_SHARES 误拒。
# #49 场内 cancel 拒绝信息包含 PUT/DELETE 修正路径；#53 禁止直接创建裸 CASH 交易。
# #65 preview 与真实 confirm 完全一致且零副作用；T 日缺净值 -> MISSING_NAV。
# #428 传入价与 T 日净值比较：两侧先 quantize_nav 到 4 位 HALF_UP 再精确比较（无容差）。

from datetime import date
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
    create_position_snapshot, create_value_snapshot, ensure_trading_day,
    create_price_record,
)
from app.models.trade import Trade


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
