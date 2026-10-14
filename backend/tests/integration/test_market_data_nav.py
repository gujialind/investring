# ============================================================================
# 集成测试：产品详情页净值数据（#595 §5.3）——历史净值分页 + 净值分析
# （区间累计净值曲线 / 六窗区间收益率，口径与 performance_service 同构）
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from app.models import PriceRecord

# 与 test_market_data_coverage.py 同一种子产品（session 种子已存在）
_CODE = "510300.SH"
_MARKET = "CN_EXCHANGE"


def _seed_nav(test_db, rows, *, code=_CODE, market=_MARKET, source="test"):
    """rows: (date, unit_price, accumulated_nav, pct_change) 元组列表；可空用 None。"""
    for d, unit, acc, pct in rows:
        test_db.add(PriceRecord(
            product_code=code, market=market, price_date=d,
            unit_price=Decimal(str(unit)),
            accumulated_nav=Decimal(str(acc)) if acc is not None else None,
            pct_change=Decimal(str(pct)) if pct is not None else None,
            source=source,
        ))
    test_db.commit()


# 2024-11 起约 13 个月的月度锚点，覆盖六窗全部命中（含 ytd 跨年基准）
_FULL_ROWS = [
    (date(2024, 11, 1), "3.5000", "3.5000", None),
    (date(2024, 12, 2), "3.5600", "3.5600", "1.7143"),
    (date(2025, 1, 2), "3.6000", "3.6000", "1.1236"),
    (date(2025, 2, 3), "3.6600", "3.6600", "1.6667"),
    (date(2025, 3, 3), "3.7000", "3.7000", "1.0929"),
    (date(2025, 4, 1), "3.7500", "3.7500", "1.3514"),
    (date(2025, 5, 6), "3.8000", "3.8000", "1.3333"),
    (date(2025, 6, 3), "3.8500", "3.8500", "1.3158"),
    (date(2025, 7, 1), "3.9000", "3.9000", "1.2987"),
    (date(2025, 8, 1), "3.9500", "3.9500", "1.2821"),
    (date(2025, 9, 1), "4.0000", "4.0000", "1.2658"),
    (date(2025, 10, 8), "4.0500", "4.0500", "1.2500"),
    (date(2025, 11, 3), "4.1000", "4.1000", "1.2346"),
    (date(2025, 12, 1), "4.1500", "4.1500", "1.2195"),
]


class TestNavHistoryPage:
    """历史净值分页：日期降序、total 正确、可空字段照传"""

    def test_pagination_desc_and_total(self, client, viewer_headers, test_db):
        _seed_nav(test_db, _FULL_ROWS)
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-history",
            params={"page": 1, "page_size": 5},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 14
        # 信封回显 page/page_size（#637 L2 评审 S1，与全仓 Paginated*Response 对齐）
        assert body["page"] == 1
        assert body["page_size"] == 5
        items = body["items"]
        assert len(items) == 5
        dates = [item["price_date"] for item in items]
        assert dates == sorted(dates, reverse=True)
        assert items[0]["price_date"] == "2025-12-01"
        assert items[0]["unit_price"] == 4.15
        assert items[0]["accumulated_nav"] == 4.15
        assert items[0]["pct_change"] == 1.2195

    def test_second_page(self, client, viewer_headers, test_db):
        _seed_nav(test_db, _FULL_ROWS)
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-history",
            params={"page": 2, "page_size": 5},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 14
        assert len(body["items"]) == 5
        assert body["items"][0]["price_date"] == "2025-07-01"

    def test_nullable_fields_pass_through(self, client, viewer_headers, test_db):
        # 首条记录累计净值/日涨跌为空。两列的可空原因不同（#692 更正归因）：累计净值
        # 是场内行情源压根不给；日涨跌是这条行本身无基准/上游未提供，与是不是场内无关。
        _seed_nav(test_db, [
            (date(2025, 6, 3), "4.2000", None, None),
            (date(2025, 6, 4), "4.2100", "4.2100", "0.2381"),
        ])
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-history",
            params={"page": 1, "page_size": 5},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        by_date = {item["price_date"]: item for item in items}
        assert by_date["2025-06-03"]["accumulated_nav"] is None
        assert by_date["2025-06-03"]["pct_change"] is None
        assert by_date["2025-06-04"]["accumulated_nav"] == 4.21

    def test_date_filter(self, client, viewer_headers, test_db):
        _seed_nav(test_db, _FULL_ROWS)
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-history",
            params={
                "start_date": "2025-11-01", "end_date": "2025-12-31",
                "page": 1, "page_size": 10,
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert [item["price_date"] for item in body["items"]] == [
            "2025-12-01", "2025-11-03",
        ]

    def test_unknown_product_returns_empty(self, client, viewer_headers):
        resp = client.get(
            "/api/market-data/products/NOPE.XX/CN_EXCHANGE/nav-history",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 5}


class TestNavHistoryPctByMarket:
    """#692：日涨跌的可空性按市场分别成立，且分页翻页后仍有值。

    修复前场外与港互认的 `pct_change` 恒为 NULL（写侧从未写入），历史页该列全是 `--`；
    这里按三种上游形态各钉一条，防止「只在第一页有值」或「某一市场又退回全空」。
    """

    @staticmethod
    def _page(client, viewer_headers, code, market, page):
        resp = client.get(
            f"/api/market-data/products/{code}/{market}/nav-history",
            params={"page": page, "page_size": 3},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        return resp.json()

    def test_cn_otc_tushare_rows_carry_derived_pct(self, client, viewer_headers, test_db):
        """tushare 场外：accum 连续、日涨跌由写侧推导，首条无基准为 None。"""
        rows = [
            (date(2026, 9, 14), "1.5000", "1.5000", None),
            (date(2026, 9, 15), "1.5300", "1.5300", "2.0000"),
            (date(2026, 9, 16), "1.5147", "1.5147", "-1.0000"),
            (date(2026, 9, 17), "1.5200", "1.5200", "0.3516"),
        ]
        _seed_nav(test_db, rows, code="000300.OF", market="CN_OTC", source="tushare")

        first = self._page(client, viewer_headers, "000300.OF", "CN_OTC", 1)
        assert first["total"] == 4
        assert [item["pct_change"] for item in first["items"]] == [0.3516, -1.0, 2.0]
        # 翻到第二页仍有值：分页不是「只有首页被回填」的遮羞布
        second = self._page(client, viewer_headers, "000300.OF", "CN_OTC", 2)
        assert second["items"][0]["price_date"] == "2026-09-14"
        assert second["items"][0]["pct_change"] is None  # 首条无基准，不是丢值

    def test_cn_otc_flat_day_reads_zero_not_null(self, client, viewer_headers, test_db):
        """平盘日必须读成 0.0：折成 None 前端会显示 `--`，与「无数据」无法区分。"""
        _seed_nav(test_db, [
            (date(2026, 9, 15), "1.5000", "1.5000", None),
            (date(2026, 9, 16), "1.5000", "1.5000", "0.0000"),
        ], code="000300.OF", market="CN_OTC", source="tushare")

        body = self._page(client, viewer_headers, "000300.OF", "CN_OTC", 1)
        assert body["items"][0]["pct_change"] == 0.0

    def test_hk_mutual_rows_carry_upstream_pct_without_accum(
        self, client, viewer_headers, test_db
    ):
        """港互认：日涨跌来自上游 NAVCHGRT；本形态（968050）累计净值整列为 None。

        #703 起写侧按行读上游 `ACCNAV`（968024 形态有值），但缺值基金仍整列 NULL——
        那条形态由 nav-analysis 的 unit 回退承接，见 `TestNavAnalysis`。
        """
        _seed_nav(test_db, [
            (date(2026, 9, 14), "10.5700", None, None),
            (date(2026, 9, 15), "10.5400", None, "-0.4762"),
        ], code="1001767344", market="HK_MUTUAL", source="akshare")

        body = self._page(client, viewer_headers, "1001767344", "HK_MUTUAL", 1)
        by_date = {item["price_date"]: item for item in body["items"]}
        assert by_date["2026-09-15"]["pct_change"] == -0.4762
        assert by_date["2026-09-14"]["pct_change"] is None
        assert all(item["accumulated_nav"] is None for item in body["items"])


class TestNavAnalysis:
    """净值分析：区间曲线过滤 + 六窗收益率口径（百分数 4dp，不足窗口期 None）"""

    @pytest.fixture
    def seeded(self, client, viewer_headers, test_db):
        _seed_nav(test_db, _FULL_ROWS)
        return client, viewer_headers

    @staticmethod
    def _analysis(client, headers, range_code, code=_CODE, market=_MARKET):
        resp = client.get(
            f"/api/market-data/products/{code}/{market}/nav-analysis",
            params={"range": range_code},
            headers=headers,
        )
        assert resp.status_code == 200
        return resp.json()

    def test_curve_filters_by_range(self, seeded):
        client, headers = seeded
        # 最新 2025-12-01；近 3 月起点 2025-09-01（恰在窗口内）
        body = self._analysis(client, headers, "3m")
        curve = body["curve"]
        assert [p["date"] for p in curve] == [
            "2025-09-01", "2025-10-08", "2025-11-03", "2025-12-01",
        ]
        assert curve[0]["accumulated_nav"] == 4.0
        assert body["nav_basis"] == "accumulated"

    def test_interval_returns_all_windows(self, seeded):
        client, headers = seeded
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-analysis",
            params={"range": "6m"},
            headers=headers,
        )
        assert resp.status_code == 200
        returns = resp.json()["interval_returns"]
        # 窗口锚点与 performance_service 组合级对齐（#637 L2 评审 S2）：
        # 基准取「窗口起点当日或之后首条」；12-01 最新 4.15
        # m1: 起点 11-01（-30d）→ 基准 11-03(4.10) → 4.15/4.10-1 = 1.2195%
        assert returns["m1"] == 1.2195
        # m3: 起点 09-02（-90d）→ 基准 10-08(4.05) → 2.4691%
        assert returns["m3"] == 2.4691
        # m6: 起点 06-01（-6 日历月）→ 基准 06-03(3.85) → 7.7922%
        assert returns["m6"] == 7.7922
        # ytd: 起点 2025-01-01 → 基准 01-02(3.60) → 15.2778%
        assert returns["ytd"] == 15.2778
        # y1: 起点 2024-12-01（-1 年）→ 基准 12-02(3.56) → 16.5730%
        assert returns["y1"] == 16.573
        # all: 首条 2024-11-01(3.50) → 18.5714%
        assert returns["all"] == 18.5714

    def test_short_history_returns_none_not_distorted(
        self, client, viewer_headers, test_db
    ):
        """固定长度窗口历史不足返 None；ytd 为变长窗口，全部历史落在本年时
        从首条起算——本年新成立产品 ytd == all，不得占位（#637 L2 评审 S3）。"""
        _seed_nav(test_db, [
            (date(2025, 12, 20), "1.0000", "1.0000", None),
            (date(2025, 12, 22), "1.0100", "1.0100", "1.0000"),
        ])
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-analysis",
            params={"range": "1m"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        returns = resp.json()["interval_returns"]
        assert returns["m1"] is None
        assert returns["m3"] is None
        assert returns["m6"] is None
        assert returns["y1"] is None
        assert returns["ytd"] == 1.0
        assert returns["all"] == 1.0

    def test_unknown_product_returns_empty(self, client, viewer_headers):
        resp = client.get(
            "/api/market-data/products/NOPE.XX/CN_EXCHANGE/nav-analysis",
            params={"range": "1m"},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["curve"] == []
        assert body["interval_returns"] == {
            "m1": None, "m3": None, "m6": None,
            "y1": None, "ytd": None, "all": None,
        }
        # 空态不给前端第三种 basis 取值（#703）：无数据与口径无关。
        assert body["nav_basis"] == "accumulated"

    def test_interval_returns_are_accum_basis_when_accum_exists(
        self, client, viewer_headers, test_db
    ):
        """累计净值有值时不消费单位净值（#703）：两列取值刻意拉开，串了立刻可见。"""
        _seed_nav(test_db, [
            (date(2025, 10, 1), "4.0000", "4.8000", None),
            (date(2025, 11, 3), "4.1000", "5.0000", None),
            (date(2025, 12, 1), "4.1500", "5.2500", "1.2195"),
        ])

        body = self._analysis(client, viewer_headers, "1m")

        assert body["nav_basis"] == "accumulated"
        assert [p["accumulated_nav"] for p in body["curve"]] == [5.0, 5.25]
        # m1 基准 11-03：5.25/5.00-1 = 5.0%；若误用单位净值会算成 1.2195%
        assert body["interval_returns"]["m1"] == 5.0

    def test_unit_price_fallback_when_accum_all_null(
        self, client, viewer_headers, test_db
    ):
        """#703 本体：累计净值整列为 NULL 时回退单位净值，两张卡不再永久空白。

        这是 issue 点名的零覆盖形态——港互认缺值基金与场内行情源该列恒 NULL，此前
        `accumulated_nav IS NOT NULL` 把 1773 行完好的 `unit_price` 整段过滤掉，响应
        仍是 200（前端失败态不触发），曲线与六窗永久空。
        """
        _seed_nav(test_db, [
            (date(2024, 11, 1), "3.5000", None, None),
            (date(2025, 11, 3), "4.1000", None, None),
            (date(2025, 12, 1), "4.1500", None, "1.2195"),
        ])

        body = self._analysis(client, viewer_headers, "6m")

        assert body["nav_basis"] == "unit"
        assert [p["date"] for p in body["curve"]] == ["2025-11-03", "2025-12-01"]
        assert [p["accumulated_nav"] for p in body["curve"]] == [4.1, 4.15]
        returns = body["interval_returns"]
        # 六窗与曲线同 basis：m1/m3/m6 的窗口起点都落在 11-03 之后 → 同一基准 4.10
        assert returns["m1"] == 1.2195
        assert returns["m3"] == 1.2195
        assert returns["m6"] == 1.2195
        # all 以首条（2024-11-01，3.50）为基准：4.15/3.50-1 = 18.5714%
        assert returns["all"] == 18.5714

    def test_partial_accum_keeps_accumulated_basis(
        self, client, viewer_headers, test_db
    ):
        """覆盖度过半维持 accumulated（#703 L2 Blocker 2 的阈值边界）：2 行里 1 行有
        累计净值恰在「不足半数」之上（2*1 < 2 为假），不回退；不按行混用两种口径。
        稀疏与未回补形态（覆盖远不足半数）由下面三条钉住。"""
        _seed_nav(test_db, [
            (date(2025, 11, 3), "4.1000", None, None),
            (date(2025, 12, 1), "4.1500", "5.2500", None),
        ])

        body = self._analysis(client, viewer_headers, "6m")

        assert body["nav_basis"] == "accumulated"
        assert [p["date"] for p in body["curve"]] == ["2025-12-01"]
        # 只有一条累计净值记录：基准不足，六窗全 None（原语义，回退不由此触发）
        assert body["interval_returns"]["all"] is None
        assert body["interval_returns"]["m1"] is None

    def test_incremental_backfill_gap_falls_back_to_unit(
        self, client, viewer_headers, test_db
    ):
        """存量 NULL + 新行有值（#703 L2 Blocker 2 的增量同步形态）→ 回退单位净值。

        模拟部署后：日同步只写新行（`task_runner` 的 `start_date = latest + 1`），存量
        历史行的 `accumulated_nav` 仍是 NULL，只有部署后新增的几行有累计净值。旧判据
        （累计净值集合非空即钉 accumulated）会让曲线只剩那两行、「成立以来」错按部署日
        为基准。覆盖度判据把这一形态切回 unit，用完整的 `unit_price` 历史算曲线与六窗。
        两列取值刻意拉开：unit 走 4.10/4.15、accum 走 5.00/5.25，串列立刻可见。
        """
        _seed_nav(test_db, [
            (date(2020, 1, 2), "1.0000", None, None),
            (date(2020, 6, 1), "1.1000", None, None),
            (date(2021, 1, 4), "1.2000", None, None),
            (date(2022, 1, 3), "1.3000", None, None),
            (date(2023, 1, 3), "1.4000", None, None),
            # 部署后新增：accum 有值（2 行），存量 5 行 accum 仍 NULL → 2*2 < 7 触发回退
            (date(2025, 11, 3), "4.1000", "5.0000", None),
            (date(2025, 12, 1), "4.1500", "5.2500", "1.2195"),
        ])

        body = self._analysis(client, viewer_headers, "6m")

        assert body["nav_basis"] == "unit"
        # 曲线装的是单位净值（4.10/4.15），不是累计净值（5.00/5.25）
        assert [p["date"] for p in body["curve"]] == ["2025-11-03", "2025-12-01"]
        assert [p["accumulated_nav"] for p in body["curve"]] == [4.1, 4.15]
        # 「成立以来」以最早的单位净值行（2020-01-02，1.00）为基准，而非部署日：
        # 4.15/1.00-1 = 315.0%；若错钉 accumulated 会算成 5.25/5.00-1 = 5.0%。
        assert body["interval_returns"]["all"] == 315.0

    def test_sparse_accum_falls_back_to_unit(
        self, client, viewer_headers, test_db
    ):
        """稀疏 ACCNAV（#703 L2 Blocker 2，issue 表格 968015 形态）→ 回退单位净值。

        累计净值非空但只覆盖零星几行（此处 2/6，远低于半数）。旧判据会因「集合非空」
        钉死 accumulated、曲线只剩两点、六窗按这两点算；覆盖度判据切回 unit。这一支即使
        做过全量回补也不会好转（上游本就稀疏），故必须由读侧兜住。
        """
        _seed_nav(test_db, [
            (date(2024, 1, 2), "2.0000", None, None),
            (date(2024, 4, 1), "2.1000", "9.9000", None),   # 稀疏 accum
            (date(2024, 7, 1), "2.2000", None, None),
            (date(2024, 10, 1), "2.3000", None, None),
            (date(2025, 1, 2), "2.4000", "9.9500", None),   # 稀疏 accum
            (date(2025, 4, 1), "2.5000", None, None),
        ])

        body = self._analysis(client, viewer_headers, "1y")

        assert body["nav_basis"] == "unit"
        # 「成立以来」用完整单位净值历史：2.50/2.00-1 = 25.0%；
        # 若错钉 accumulated 会按两条稀疏累计净值算成 9.95/9.90-1 ≈ 0.5051%。
        assert body["interval_returns"]["all"] == 25.0

    def test_dropped_accum_column_tolerated_keeps_accumulated(
        self, client, viewer_headers, test_db
    ):
        """#701 非正丢列容差反例：个别累计净值缺值不得触发回退。

        写侧 `_bulk_upsert_prices` 对非正的 `accumulated_nav` 只丢这一列（unit_price 已
        合格，这一天确实有价），于是 OTC 产品会出现个别行 accum 为 NULL。覆盖度判据用
        「过半」而非严格计数相等：此处 6/7 行有累计净值（远高于半数），维持 accumulated，
        不因个别坏值把整支产品误切到单位净值口径。
        """
        _seed_nav(test_db, [
            (date(2025, 6, 2), "3.0000", "3.0000", None),
            (date(2025, 7, 1), "3.1000", "3.1000", None),
            (date(2025, 8, 1), "3.2000", None, None),        # #701 非正丢列 → 个别缺值
            (date(2025, 9, 1), "3.3000", "3.3000", None),
            (date(2025, 10, 8), "3.4000", "3.4000", None),
            (date(2025, 11, 3), "3.5000", "3.5000", None),
            (date(2025, 12, 1), "3.6000", "3.6000", "2.8571"),
        ])

        body = self._analysis(client, viewer_headers, "6m")

        assert body["nav_basis"] == "accumulated"
        # 「成立以来」按累计净值序列（跳过缺值行）：3.60/3.00-1 = 20.0%。
        assert body["interval_returns"]["all"] == 20.0

    def test_invalid_range_422(self, client, viewer_headers):
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-analysis",
            params={"range": "5y"},
            headers=viewer_headers,
        )
        assert resp.status_code == 422
