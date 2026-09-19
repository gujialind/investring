# ============ 集成测试：调仓交易 — 直改校验与契约守护（自 test_trades.py 拆分，issue #469） ============
# 含 TestTradesOpenApiContract（#183 openapi 分页响应结构化守护）、
# TestUpdateTradeValidation（#182 PUT 直改校验：可用量/交易日/CASH 腿/防重/零部分写入）、
# TestPlatformRequired（#209 platform_code 必填 + 存在性，service 层统一校验）。

from datetime import date

from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
    create_position_snapshot, create_value_snapshot, ensure_trading_day,
    create_price_record,
)
from app.models.trade import Trade
from app.models.portfolio import Portfolio


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
        # #565：price/fee 变动后份额按创建同口径重算 (5000−10)/1.6
        assert float(data["shares"]) == 3118.75

    # ---- #565 买入份额纯派生（与创建同口径）----

    def test_update_buy_price_only_rederives_shares(self, client, admin_headers, test_db):
        """仅改 price：份额按 (actual_amount−fee)/新价 重算，金额列不动（#565 主场景）"""
        self._setup(test_db, code="U182_P14", cash=5000.0)
        trade_id = self._create_buy(client, admin_headers, code="U182_P14", amount=5000.0)
        # 创建口径：shares = 5000/1.5 = 3333.33
        got = client.get(f"/api/trades/{trade_id}", headers=admin_headers)
        assert float(got.json()["shares"]) == 3333.33

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"price": 1.6},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        data = upd.json()
        assert float(data["shares"]) == 3125.0  # 5000/1.6
        assert float(data["actual_amount"]) == 5000.0
        assert float(data["amount"]) == 5000.0

    def test_update_buy_fee_rederives_shares(self, client, admin_headers, test_db):
        """仅改 fee：份额按新净额/旧价重算（#565）"""
        self._setup(test_db, code="U182_P15", cash=5000.0)
        trade_id = self._create_buy(client, admin_headers, code="U182_P15", amount=5000.0)

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"fee": 10.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        data = upd.json()
        assert float(data["actual_amount"]) == 5000.0
        assert float(data["amount"]) == 4990.0
        assert float(data["shares"]) == 3326.67  # 4990/1.5

    def test_update_buy_price_and_amount_together(self, client, admin_headers, test_db):
        """price 与 amount 同改：份额按终值 (新actual−fee)/新价 重算（#565）"""
        self._setup(test_db, code="U182_P16", cash=5000.0)
        trade_id = self._create_buy(client, admin_headers, code="U182_P16", amount=5000.0)

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"price": 2.0, "amount": 4000.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        data = upd.json()
        assert float(data["actual_amount"]) == 4000.0
        assert float(data["shares"]) == 2000.0  # 4000/2.0

    def test_update_buy_explicit_shares_overrides_derivation(self, client, admin_headers, test_db):
        """price 与 shares 同传：显式 shares 优先于派生（保持 API 契约）"""
        self._setup(test_db, code="U182_P17", cash=5000.0)
        trade_id = self._create_buy(client, admin_headers, code="U182_P17", amount=5000.0)

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"price": 2.0, "shares": 1234.5},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        assert float(upd.json()["shares"]) == 1234.5

    def test_update_otc_buy_no_price_keeps_shares_zero(self, client, admin_headers, test_db):
        """场外无价买入改金额：份额保持占位 0，待确认按净值自愈（#565 不破坏 OTC 路径）"""
        create_portfolio(test_db, code="U182_P18", status="active")
        create_product(test_db, code="FUND_565", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="U182_P18_PL")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "U182_P18", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="U182_P18_PL", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "U182_P18",
                "product_code": "FUND_565",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "U182_P18_PL",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        trade_id = resp.json()["id"]

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"amount": 12000.0, "fee": 200.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200, f"Response: {upd.status_code} {upd.json()}"
        data = upd.json()
        assert float(data["actual_amount"]) == 12000.0
        assert float(data["amount"]) == 11800.0
        assert float(data["shares"]) == 0.0

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
