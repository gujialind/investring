# ============ 集成测试：调仓交易 — 列表筛选排序与读侧派生（自 test_trades.py 拆分，issue #469） ============
# 含 TestListTradeFilters（#126 筛选/排序：#155 products 复合多选、同 transfer_group 两腿相邻）、
# TestListTradeProductName（#175 读侧派生 product_name；#183 响应字段以 TradeResponse 为准）、
# TestListGroupAnchorSort（#734 组锚点排序）、TestListGroupPagination（#752 按组分页）。

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
    `test_equal_anchors_fall_back_to_transfer_group` 是绿的——它钉的是二级键，对新旧
    首键都不敏感，改排序键时不会替你发现问题。分页侧的用例（含 #734 那条页边界拆组
    用例，#752 落地后已改写判据）在 `TestListGroupPagination`。
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


class TestListGroupPagination:
    """按组分页（issue #752）：分页单位仍是**行**，但同组两腿恒同页。

    页尾若还有同组剩余腿则整组带上（一页最多 `page_size + 组大小 - 1` 行），下一页从该组
    之后开始 ⇒ 页间不重叠、不漏行；`total` 保持行数语义（`ir trade list --all` 的
    `len(items) >= meta.total` 收尾条件不变），组口径另以 `total_groups` / `total_pages`
    给出。`transfer_group` 为空串的行各自独立成组（singleton）。

    本类的前身是 `TestListGroupAnchorSort::test_group_split_across_page_boundary_keeps_paired_leg`
    （#734 钉「两腿分居两页仍各带 paired_leg」）——按组分页落地后那个形态不再可达，
    按 issue #752 的要求**改写判据**（同页 + 不重复）并连同新增用例一起移入本类。
    """

    def test_group_at_page_boundary_stays_on_same_page(
        self, client, admin_headers, test_db
    ):
        """(#752) 组压在页边界上时整组进同一页、下一页从该组之后开始，不重叠不重复。

        原用例（#734）钉的是「两腿分居两页、各自带 paired_leg 派生兜底」——按组分页
        落地后该形态不再可达，故按 issue #752 的要求**改写判据**而非删除：现在断言
        两腿同页相邻、第 1 页因此多带一行（`page_size + 组大小 - 1`）、第 2 页只剩
        组外的行；`total` 仍是行数（4）、不因分页或 outerjoin 变化。
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
        # 组：solo_late / rebal_ga5 / solo_early ⇒ 3 组；page_size=10 一页装完
        assert full["total_groups"] == 3
        assert full["total_pages"] == 1

        # page_size=2 时组的两腿压在页边界上：整组带上 ⇒ 第 1 页 3 行（= page_size + 1）
        p1 = client.get(
            "/api/trades?portfolio_code=LT_GA5&page=1&page_size=2", headers=admin_headers
        ).json()
        p2 = client.get(
            "/api/trades?portfolio_code=LT_GA5&page=2&page_size=2", headers=admin_headers
        ).json()
        # total = 分页前行数，两页一致、不因整组进页变化
        assert p1["total"] == p2["total"] == 4
        assert p1["total_pages"] == p2["total_pages"] == 2
        assert p1["total_groups"] == p2["total_groups"] == 3
        assert [i["id"] for i in p1["items"]] == [
            solo_late.id, cash_leg.id, fund_leg.id
        ]
        # 末页短页：只剩组外那一行（< page_size），且不含上一页已给过的任何行
        assert [i["id"] for i in p2["items"]] == [solo_early.id]
        # 两腿同页相邻 ⇒ paired_leg 指向的对手腿就在本页结果集内（#719 退为防御层）
        cash_on_p1 = next(i for i in p1["items"] if i["id"] == cash_leg.id)
        fund_on_p1 = next(i for i in p1["items"] if i["id"] == fund_leg.id)
        assert cash_on_p1["paired_leg"]["id"] == fund_leg.id
        assert fund_on_p1["paired_leg"]["id"] == cash_leg.id
        page1_ids = {i["id"] for i in p1["items"]}
        assert cash_on_p1["paired_leg"]["id"] in page1_ids
        assert fund_on_p1["paired_leg"]["id"] in page1_ids

    def test_empty_transfer_group_rows_are_singletons(
        self, client, admin_headers, test_db
    ):
        """(#752) `transfer_group` 为空串的行在**分页**上各自独立成组（singleton），不被当成
        一整块不可拆的行；但排序锚点仍是共享的——两者是不同层面的口径，别混为一谈。

        三行空组号（日期 9/5、9/4、9/3）若在分页上被当成一组，`page_size=2` 会把三行整块塞进
        第 1 页（limit 3、total_pages 1、total_groups 1）；按 singleton 则是 [1,1,1]，第 1 页
        恰好 2 行、共 2 页 3 组 —— total_groups / total_pages 两个断言即可区分这两种形态。
        注意「独立成组」只作用于**分页分组**、不改锚点：组锚点按 (组合, transfer_group) 取
        max(trade_date)（#734），"" 也是一个 transfer_group 值，故三行共享锚点 9/5、同锚点内
        落到 id DESC ⇒ 全序是 [id3, id2, id1]（= rows[2], rows[1], rows[0]）而非按各自日期。
        """
        create_portfolio(test_db, code="LT_GS1", status="active")
        # uq_trade_transfer_group = (transfer_group, product_code, trade_type)，故空组号的
        # 多行必须靠产品/方向区分才写得进去（这本身也说明空组号不是正常写入路径的形态）
        rows = []
        for product, market, trade_type, day in [
            ("CASH", "", "buy", 5),
            ("CASH", "", "sell", 4),
            ("510300.SH", "CN_EXCHANGE", "buy", 3),
        ]:
            trade = create_trade(test_db, "LT_GS1", product, market,
                                 trade_type=trade_type,
                                 trade_date=date(2025, 9, day))
            trade.transfer_group = ""
            test_db.flush()
            rows.append(trade)

        p1 = client.get(
            "/api/trades?portfolio_code=LT_GS1&page=1&page_size=2", headers=admin_headers
        ).json()
        p2 = client.get(
            "/api/trades?portfolio_code=LT_GS1&page=2&page_size=2", headers=admin_headers
        ).json()
        assert p1["total"] == 3
        assert p1["total_groups"] == 3
        assert p1["total_pages"] == 2
        # 共享退化锚点 9/5 ⇒ 同锚点内 id DESC：全序 [rows2, rows1, rows0]，故 p1=[3,2]、p2=[1]
        assert [i["id"] for i in p1["items"]] == [rows[2].id, rows[1].id]
        assert [i["id"] for i in p2["items"]] == [rows[0].id]
        # singleton 分页的核心不变量（与具体行序无关）：页间不重叠、合起来正好全集
        p1_ids = {i["id"] for i in p1["items"]}
        p2_ids = {i["id"] for i in p2["items"]}
        assert p1_ids & p2_ids == set()
        assert p1_ids | p2_ids == {r.id for r in rows}

    def test_paging_through_all_pages_neither_duplicates_nor_drops_rows(
        self, client, admin_headers, test_db
    ):
        """(#752) 连续翻页拼起来 == 一次取全，且 total_groups / total_pages 与组形态相符。

        数据集刻意混排：3 个跨日两腿组（锚点互不相同）+ 2 条独占行 ⇒ 8 行 5 组，
        排序序下的组大小 = [2, 2, 2, 1, 1]。`page_size=3` 逐页装（装满 3 行时若下一行
        是同组剩余腿则整组带上）：p1 = 2+2 = **4** 行、p2 = 2+1 = 3 行、p3 = 1 行
        ⇒ total_pages = 3。末页 1 行 < page_size，是「末页可短」的正常形态；p1 的 4 行
        是「一页最多 page_size + 组大小 - 1」的可执行形式。
        """
        create_portfolio(test_db, code="LT_GP1", status="active")
        expected: list[int] = []

        def add_group(fund_day: int, cash_day: int, group: str):
            fund = create_trade(test_db, "LT_GP1", "510300.SH", "CN_EXCHANGE",
                                trade_type="sell", trade_date=date(2025, 9, fund_day),
                                transfer_group=group)
            cash = create_trade(test_db, "LT_GP1", "CASH", "",
                                trade_type="buy", trade_date=date(2025, 9, cash_day),
                                transfer_group=group)
            # 组内 id DESC ⇒ 后建的现金腿在前
            return [cash.id, fund.id]

        # 锚点 = 组内 max(trade_date)：a=max(9/9,9/6)=9/9、b=max(9/5,9/8)=9/8、
        # c=max(9/4,9/7)=9/7；两腿刻意跨日，让「按行自身日期分页」必然拆组
        expected += add_group(9, 6, "rebal_gpa")
        expected += add_group(5, 8, "rebal_gpb")
        expected += add_group(4, 7, "rebal_gpc")
        solo_late = create_trade(test_db, "LT_GP1", "CASH", "",
                                 trade_date=date(2025, 9, 6))
        solo_early = create_trade(test_db, "LT_GP1", "CASH", "",
                                 trade_type="sell", trade_date=date(2025, 9, 5))

        # 锚点 DESC：a(9/9) → b(9/8) → c(9/7) → solo_late(9/6) → solo_early(9/5)
        expected += [solo_late.id, solo_early.id]

        full = client.get(
            "/api/trades?portfolio_code=LT_GP1&page_size=100", headers=admin_headers
        ).json()
        assert full["total"] == 8
        assert full["total_groups"] == 5
        assert full["total_pages"] == 1
        assert [i["id"] for i in full["items"]] == expected

        paged: list[int] = []
        page_no = 1
        lengths = []
        while True:
            data = client.get(
                f"/api/trades?portfolio_code=LT_GP1&page={page_no}&page_size=3",
                headers=admin_headers,
            ).json()
            assert data["total"] == 8
            assert data["total_groups"] == 5
            assert data["total_pages"] == 3
            ids = [i["id"] for i in data["items"]]
            assert ids, f"第 {page_no} 页为空但 total_pages=3，翻页会死循环"
            lengths.append(len(ids))
            paged += ids
            if page_no >= data["total_pages"]:
                break
            page_no += 1

        assert lengths == [4, 3, 1]
        assert paged == expected, "连续翻页必须无重复、无遗漏，且顺序与一次取全一致"
