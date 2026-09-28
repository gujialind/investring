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


def _seed_nav(test_db, rows):
    """rows: (date, unit_price, accumulated_nav, pct_change) 元组列表；可空用 None。"""
    for d, unit, acc, pct in rows:
        test_db.add(PriceRecord(
            product_code=_CODE, market=_MARKET, price_date=d,
            unit_price=Decimal(str(unit)),
            accumulated_nav=Decimal(str(acc)) if acc is not None else None,
            pct_change=Decimal(str(pct)) if pct is not None else None,
            source="test",
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
        # 首条记录累计净值/日涨跌为空（场内行情源形态）
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


class TestNavAnalysis:
    """净值分析：区间曲线过滤 + 六窗收益率口径（百分数 4dp，不足窗口期 None）"""

    @pytest.fixture
    def seeded(self, client, viewer_headers, test_db):
        _seed_nav(test_db, _FULL_ROWS)
        return client, viewer_headers

    def test_curve_filters_by_range(self, seeded):
        client, headers = seeded
        # 最新 2025-12-01；近 3 月起点 2025-09-01（恰在窗口内）
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-analysis",
            params={"range": "3m"},
            headers=headers,
        )
        assert resp.status_code == 200
        curve = resp.json()["curve"]
        assert [p["date"] for p in curve] == [
            "2025-09-01", "2025-10-08", "2025-11-03", "2025-12-01",
        ]
        assert curve[0]["accumulated_nav"] == 4.0

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

    def test_invalid_range_422(self, client, viewer_headers):
        resp = client.get(
            f"/api/market-data/products/{_CODE}/{_MARKET}/nav-analysis",
            params={"range": "5y"},
            headers=viewer_headers,
        )
        assert resp.status_code == 422
