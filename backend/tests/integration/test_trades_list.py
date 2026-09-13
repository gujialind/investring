# ============ 集成测试：调仓交易 — 列表筛选排序与读侧派生（自 test_trades.py 拆分，issue #469） ============
# 含 TestListTradeFilters（#126 筛选/排序：#155 products 复合多选、同 transfer_group 两腿相邻）、
# TestListTradeProductName（#175 读侧派生 product_name；#183 响应字段以 TradeResponse 为准）。

from datetime import date

from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
    ensure_trading_day,
)
from app.schemas.trade import TradeResponse


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
