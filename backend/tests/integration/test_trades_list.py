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
        """排序主序仍是日期 DESC（不同日期降序）。

        #734 起分档用的是**组锚点**而非行自身日期；本例三条腿各由 factory 分到独占组
        （`test_*`），锚点 = 自身 trade_date，故主序语义与改动前逐字一致（验收 #3）。
        """
        create_portfolio(test_db, code="LT_P4", status="active")
        t1 = create_trade(test_db, "LT_P4", "CASH", "", trade_date=date(2025, 9, 1))
        t2 = create_trade(test_db, "LT_P4", "CASH", "", trade_date=date(2025, 9, 10))
        t3 = create_trade(test_db, "LT_P4", "CASH", "", trade_date=date(2025, 9, 5))

        resp = client.get("/api/trades", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        assert ids == [t2.id, t3.id, t1.id]

    def test_sort_groups_adjacent(self, client, admin_headers, test_db):
        """同 transfer_group 两腿在结果中相邻，即使两腿 trade_date 天然不同（#734）。

        真实卖出组的日期口径（attach_paired_cash_leg，#493）：基金腿 trade_date =
        下单日 T，配对现金到账腿 trade_date = 基金腿确认日 C，两腿日期必然分叉。
        旧排序键 trade_date DESC 会把中间日期的干扰腿插进同组两腿之间（#734 缺陷），
        修复后按组锚点（组内 max(trade_date)）分档，同组两腿恒相邻。
        """
        create_portfolio(test_db, code="LT_P5", status="active")
        # 模拟卖出组：基金腿下单日 9/1 早、现金到账腿 = 确认日 9/3 晚；
        # 干扰腿取中间日 9/2，旧排序键下必然插进两腿之间
        fund_leg = create_trade(test_db, "LT_P5", "510300.SH", "CN_EXCHANGE",
                                trade_type="sell", trade_date=date(2025, 9, 1),
                                transfer_group="rebal_adj01")
        cash_leg = create_trade(test_db, "LT_P5", "CASH", "",
                                trade_type="buy", trade_date=date(2025, 9, 3),
                                transfer_group="rebal_adj01")
        other = create_trade(test_db, "LT_P5", "CASH", "",
                             trade_type="buy", trade_date=date(2025, 9, 2),
                             transfer_group="rebal_adj02")

        resp = client.get("/api/trades", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        # adj01 组锚点 = max(9/1, 9/3) = 9/3 → 整组排最前；组内同 transfer_group
        # 按 id DESC，cash_leg 后插入 id 更大 → 先现金腿后基金腿；
        # adj02 独占组锚点 = 自身日期 9/2 → 排后
        assert ids == [cash_leg.id, fund_leg.id, other.id]

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


class TestListGroupAnchorSort:
    """组锚点排序边界（issue #734）：锚点 = 同组合内同 transfer_group 的 max(trade_date)。

    「无组可锚」在 DB 层不可达（`trade.transfer_group` NOT NULL，见 `models/trade.py`），
    可达形态是**独占组**（组内只有它自己 → 锚点 = 自身日期），由
    `test_solo_rows_interleave_with_grouped_legs` 覆盖。实现里的 `coalesce` 分支同样
    不可达（外层每行都把自己的键贡献进 GROUP BY ⇒ join 恒命中），保留它只为不赌各方言
    的 NULL 排序位置，不是防御真实存量数据。

    判别力（把 ORDER BY 首键改回旧排序键 `trade_date DESC` 后实跑本文件 +
    `test_trades_paired_leg.py`：5 failed / 29 passed）。红的是本类前三例、
    `test_anchor_decoupled_from_date_filter` 和别处的
    `TestListTradeFilters::test_sort_groups_adjacent`；
    `test_equal_anchors_fall_back_to_transfer_group` 与
    `test_group_split_across_page_boundary_keeps_paired_leg` 是绿的——它们钉的是二级键
    与分页契约，对新旧首键都不敏感，改排序键时不会替你发现问题。
    """

    def test_solo_rows_interleave_with_grouped_legs(self, client, admin_headers, test_db):
        """独占组的单腿行按自身日期落档，与多腿组交错而不拆散组"""
        create_portfolio(test_db, code="LT_GA1", status="active")
        solo_late = create_trade(test_db, "LT_GA1", "CASH", "",
                                 trade_date=date(2025, 9, 5))
        fund_leg = create_trade(test_db, "LT_GA1", "510300.SH", "CN_EXCHANGE",
                                trade_type="sell", trade_date=date(2025, 9, 1),
                                transfer_group="rebal_ga1")
        cash_leg = create_trade(test_db, "LT_GA1", "CASH", "",
                                trade_type="buy", trade_date=date(2025, 9, 4),
                                transfer_group="rebal_ga1")
        solo_early = create_trade(test_db, "LT_GA1", "CASH", "",
                                  trade_date=date(2025, 9, 2))

        resp = client.get("/api/trades?portfolio_code=LT_GA1", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        # 锚点：solo_late=9/5、rebal_ga1=max(9/1,9/4)=9/4、solo_early=9/2；
        # solo_early 的自身日期落在组两腿（9/1、9/4）之间，但组恒相邻不被拆开
        assert ids == [solo_late.id, cash_leg.id, fund_leg.id, solo_early.id]

    def test_group_spanning_months_stays_adjacent(self, client, admin_headers, test_db):
        """同组两腿跨月（卖出组：下单日 8 月、到账腿 = 确认日 9 月）仍相邻同档"""
        create_portfolio(test_db, code="LT_GA2", status="active")
        fund_leg = create_trade(test_db, "LT_GA2", "510300.SH", "CN_EXCHANGE",
                                trade_type="sell", trade_date=date(2025, 8, 28),
                                transfer_group="rebal_ga2")
        cash_leg = create_trade(test_db, "LT_GA2", "CASH", "",
                                trade_type="buy", trade_date=date(2025, 9, 2),
                                transfer_group="rebal_ga2")
        solo = create_trade(test_db, "LT_GA2", "CASH", "",
                            trade_date=date(2025, 9, 1))

        resp = client.get("/api/trades?portfolio_code=LT_GA2", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        # 组锚点 9/2 > solo 9/1 → 整组（含 8 月的基金腿）排在 solo 之前；组内 id DESC
        assert ids == [cash_leg.id, fund_leg.id, solo.id]

    def test_multiple_groups_interleaved_dates(self, client, admin_headers, test_db):
        """多组日期交错时按各组锚点分档，组内两腿恒相邻"""
        create_portfolio(test_db, code="LT_GA3", status="active")
        fund_a = create_trade(test_db, "LT_GA3", "510300.SH", "CN_EXCHANGE",
                              trade_type="sell", trade_date=date(2025, 9, 1),
                              transfer_group="rebal_ga3a")
        cash_a = create_trade(test_db, "LT_GA3", "CASH", "",
                              trade_type="buy", trade_date=date(2025, 9, 5),
                              transfer_group="rebal_ga3a")
        fund_b = create_trade(test_db, "LT_GA3", "510300.SH", "CN_EXCHANGE",
                              trade_type="sell", trade_date=date(2025, 9, 2),
                              transfer_group="rebal_ga3b")
        cash_b = create_trade(test_db, "LT_GA3", "CASH", "",
                              trade_type="buy", trade_date=date(2025, 9, 4),
                              transfer_group="rebal_ga3b")
        solo_c = create_trade(test_db, "LT_GA3", "CASH", "",
                              trade_date=date(2025, 9, 3),
                              transfer_group="rebal_ga3c")

        resp = client.get("/api/trades?portfolio_code=LT_GA3", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        # 锚点：a=9/5、b=9/4、c=9/3；b 组两腿（9/2、9/4）跨过 c 的 9/3，仍不被拆散
        assert ids == [cash_a.id, fund_a.id, cash_b.id, fund_b.id, solo_c.id]

    def test_equal_anchors_fall_back_to_transfer_group(self, client, admin_headers, test_db):
        """锚点相同时二级键 transfer_group 升序、组内 id DESC"""
        create_portfolio(test_db, code="LT_GA4", status="active")
        leg_t2 = create_trade(test_db, "LT_GA4", "CASH", "",
                              trade_date=date(2025, 9, 6),
                              transfer_group="rebal_ga4b")
        leg_t1 = create_trade(test_db, "LT_GA4", "CASH", "",
                              trade_date=date(2025, 9, 6),
                              transfer_group="rebal_ga4a")

        resp = client.get("/api/trades?portfolio_code=LT_GA4", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        # 同锚点 9/6：transfer_group 升序 → rebal_ga4a 在前（与旧排序键的二级键一致）
        assert ids == [leg_t1.id, leg_t2.id]

    def test_group_split_across_page_boundary_keeps_paired_leg(
        self, client, admin_headers, test_db
    ):
        """(#734) 组锚点只保证结果集内相邻，不保证同页：page_size 把组推到页边界时
        两腿分居两页，各自仍带 paired_leg（由 #719 派生兜底），total 不随分页变化。

        这是当前分页契约的显式钉住——分组分页/游标（follow-up #752）落地后本用例即
        变更点：两腿恒同页成立时改写断言，不是删掉。
        """
        create_portfolio(test_db, code="LT_GA5", status="active")
        # 结果集顺序（锚点 DESC）：solo_late(9/8) → 组 rebal_ga5(锚点 max(9/1,9/5)=9/5,
        # 组内 id DESC → cash_leg 后建 id 更大在前) → solo_early(8/29)
        solo_late = create_trade(test_db, "LT_GA5", "CASH", "",
                                 trade_date=date(2025, 9, 8))
        fund_leg = create_trade(test_db, "LT_GA5", "510300.SH", "CN_EXCHANGE",
                                trade_type="sell", trade_date=date(2025, 9, 1),
                                transfer_group="rebal_ga5")
        cash_leg = create_trade(test_db, "LT_GA5", "CASH", "",
                                trade_type="buy", trade_date=date(2025, 9, 5),
                                transfer_group="rebal_ga5")
        solo_early = create_trade(test_db, "LT_GA5", "CASH", "",
                                  trade_date=date(2025, 8, 29))

        full = client.get(
            "/api/trades?portfolio_code=LT_GA5&page_size=10", headers=admin_headers
        ).json()
        assert [i["id"] for i in full["items"]] == [
            solo_late.id, cash_leg.id, fund_leg.id, solo_early.id
        ]
        assert full["total"] == 4

        # page_size=2 把组的两腿切到第 1、2 页：第 1 页末 = cash_leg，第 2 页首 = fund_leg
        p1 = client.get(
            "/api/trades?portfolio_code=LT_GA5&page=1&page_size=2", headers=admin_headers
        ).json()
        p2 = client.get(
            "/api/trades?portfolio_code=LT_GA5&page=2&page_size=2", headers=admin_headers
        ).json()
        # total = 分页前总数，两页一致、不因拆组变化
        assert p1["total"] == p2["total"] == 4
        assert [i["id"] for i in p1["items"]] == [solo_late.id, cash_leg.id]
        assert [i["id"] for i in p2["items"]] == [fund_leg.id, solo_early.id]
        # 两腿分居两页、对手腿不在本页结果集内，各自仍指向对方（#719 派生 pair 兜底）
        cash_on_p1 = next(i for i in p1["items"] if i["id"] == cash_leg.id)
        fund_on_p2 = next(i for i in p2["items"] if i["id"] == fund_leg.id)
        assert cash_on_p1["paired_leg"]["id"] == fund_leg.id
        assert fund_on_p2["paired_leg"]["id"] == cash_leg.id

    def test_anchor_decoupled_from_date_filter(self, client, admin_headers, test_db):
        """锚点取全组 max(trade_date)，与日期筛选区间解耦（#734 刻意取舍）。

        组腿 8/1 + 9/9、solo 9/5，请求 `trade_date_end=9/6` 把现金腿挡在区间外：
        锚点仍是全组 9/9 ⇒ 可见的基金腿排在 solo 之前，筛选下不按自身 trade_date 单调。
        把 `trade_date_*` 下推进锚点子查询（为性能看起来更"顺"）会让锚点变成 8/1、
        顺序翻成 [solo, fund_leg]——本例是那条改动的唯一哨兵，其余用例在下推后全绿。
        """
        create_portfolio(test_db, code="LT_GA6", status="active")
        fund_leg = create_trade(test_db, "LT_GA6", "510300.SH", "CN_EXCHANGE",
                                trade_type="sell", trade_date=date(2025, 8, 1),
                                transfer_group="rebal_ga6")
        create_trade(test_db, "LT_GA6", "CASH", "", trade_type="buy",
                     trade_date=date(2025, 9, 9), transfer_group="rebal_ga6")
        solo = create_trade(test_db, "LT_GA6", "CASH", "",
                            trade_date=date(2025, 9, 5))

        resp = client.get(
            "/api/trades?portfolio_code=LT_GA6&trade_date_end=2025-09-06",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        assert ids == [fund_leg.id, solo.id]
