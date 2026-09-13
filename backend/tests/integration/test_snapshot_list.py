# ============================================================================
# 集成测试：快照历史列表 + status missing_dates 真值化 (test_snapshot_list.py)
# ============================================================================
# 覆盖 issue #146：
#   GET /api/snapshots/portfolios/{code}/list（倒序/闭区间过滤/limit+total/404/422/viewer）
#   GET /api/snapshots/portfolios/{code}/status 的 missing_dates（首末快照日区间空洞）
# 日期基于 conftest 交易日历（2025-01-01 起、终点滚动，工作日均为交易日）：
#   2025-06-02(一) ~ 2025-06-06(五) 连续 5 个交易日，06-07/06-08 为周末
# ============================================================================

from datetime import date
from decimal import Decimal

from app.models import PortfolioValueSnapshot
from tests.factories import create_portfolio, create_value_snapshot


# 2025-06-02 周一 ~ 2025-06-06 周五
MON = date(2025, 6, 2)
TUE = date(2025, 6, 3)
WED = date(2025, 6, 4)
THU = date(2025, 6, 5)
FRI = date(2025, 6, 6)
NEXT_MON = date(2025, 6, 9)


class TestSnapshotList:
    """GET /api/snapshots/portfolios/{code}/list（issue #146 决策①）"""

    def test_list_desc_order(self, client, admin_headers, test_db):
        """3 日快照 → items 按 snapshot_date 倒序、字段齐全、total=3、limit=500"""
        port = create_portfolio(test_db, code="LIST_DESC", status="active")
        create_value_snapshot(test_db, port.code, MON, total_value=10000, total_shares=10000, unit_price=1.0)
        create_value_snapshot(test_db, port.code, TUE, total_value=10100, total_shares=10000, unit_price=1.01)
        # 手动造一条带非零在途金额的快照，验证 in_transit_total 序列化
        test_db.add(PortfolioValueSnapshot(
            portfolio_code=port.code, snapshot_date=WED,
            total_value=Decimal("10200"), total_shares=Decimal("10000"),
            unit_price=Decimal("1.02"), in_transit_total=Decimal("123.45"),
        ))
        test_db.commit()

        resp = client.get(f"/api/snapshots/portfolios/{port.code}/list", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["portfolio_code"] == port.code
        assert data["total"] == 3
        assert data["limit"] == 500
        assert [i["snapshot_date"] for i in data["items"]] == [
            WED.isoformat(), TUE.isoformat(), MON.isoformat(),
        ]
        first = data["items"][0]
        assert first["unit_price"] == 1.02
        assert first["total_shares"] == 10000
        assert first["total_value"] == 10200
        assert first["in_transit_total"] == 123.45

    def test_list_date_range_filter_closed(self, client, admin_headers, test_db):
        """start/end 闭区间：边界日包含，区间外剔除"""
        port = create_portfolio(test_db, code="LIST_RANGE", status="active")
        for d in (MON, TUE, WED, THU, FRI):
            create_value_snapshot(test_db, port.code, d, total_value=10000, total_shares=10000, unit_price=1.0)

        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/list",
            params={"start_date": TUE.isoformat(), "end_date": THU.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert [i["snapshot_date"] for i in data["items"]] == [
            THU.isoformat(), WED.isoformat(), TUE.isoformat(),
        ]

    def test_list_limit_truncation_total_kept(self, client, admin_headers, test_db):
        """3 快照 + limit=2 → items=2（最新两日）、total=3（防无声截断，决策①）"""
        port = create_portfolio(test_db, code="LIST_LIMIT", status="active")
        for d in (MON, TUE, WED):
            create_value_snapshot(test_db, port.code, d, total_value=10000, total_shares=10000, unit_price=1.0)

        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/list",
            params={"limit": 2},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["limit"] == 2
        assert [i["snapshot_date"] for i in data["items"]] == [WED.isoformat(), TUE.isoformat()]

    def test_list_empty(self, client, admin_headers, test_db):
        """无快照组合 → items=[]、total=0"""
        port = create_portfolio(test_db, code="LIST_EMPTY", status="active")
        resp = client.get(f"/api/snapshots/portfolios/{port.code}/list", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_portfolio_not_found(self, client, admin_headers, test_db):
        """组合不存在 → 404 PORTFOLIO_NOT_FOUND"""
        resp = client.get("/api/snapshots/portfolios/NO_SUCH/list", headers=admin_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "PORTFOLIO_NOT_FOUND"

    def test_list_inverted_range_422(self, client, admin_headers, test_db):
        """start_date > end_date → 422 INVALID_DATE_RANGE"""
        port = create_portfolio(test_db, code="LIST_422", status="active")
        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/list",
            params={"start_date": FRI.isoformat(), "end_date": MON.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_RANGE"

    def test_list_viewer_allowed(self, client, viewer_headers, test_db):
        """viewer 可访问（权限与 status 一致，决策①）"""
        port = create_portfolio(test_db, code="LIST_VIEWER", status="active")
        create_value_snapshot(test_db, port.code, MON, total_value=10000, total_shares=10000, unit_price=1.0)
        resp = client.get(f"/api/snapshots/portfolios/{port.code}/list", headers=viewer_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestSnapshotStatusMissingDates:
    """GET /portfolios/{code}/status 的 missing_dates 真值化（issue #146 决策②）"""

    def test_missing_dates_real_holes(self, client, admin_headers, test_db):
        """周一~周五只造一/二/五 → missing_dates == [周三, 周四]（ISO 升序）"""
        port = create_portfolio(test_db, code="MISS_HOLE", status="active")
        for d in (MON, TUE, FRI):
            create_value_snapshot(test_db, port.code, d, total_value=10000, total_shares=10000, unit_price=1.0)

        resp = client.get(f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["missing_dates"] == [WED.isoformat(), THU.isoformat()]

    def test_missing_dates_weekend_not_counted(self, client, admin_headers, test_db):
        """区间跨周末（周五~下周一）且交易日全有 → []（周末非交易日，天然不入选）"""
        port = create_portfolio(test_db, code="MISS_WEEKEND", status="active")
        for d in (FRI, NEXT_MON):
            create_value_snapshot(test_db, port.code, d, total_value=10000, total_shares=10000, unit_price=1.0)

        resp = client.get(f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["missing_dates"] == []

    def test_missing_dates_continuous_empty(self, client, admin_headers, test_db):
        """全连续（一~五全有）→ []"""
        port = create_portfolio(test_db, code="MISS_CONT", status="active")
        for d in (MON, TUE, WED, THU, FRI):
            create_value_snapshot(test_db, port.code, d, total_value=10000, total_shares=10000, unit_price=1.0)

        resp = client.get(f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["missing_dates"] == []

    def test_missing_dates_no_snapshots(self, client, admin_headers, test_db):
        """无快照 → [] 且 latest/first 为 null（不炸）"""
        port = create_portfolio(test_db, code="MISS_NONE", status="active")
        resp = client.get(f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["missing_dates"] == []
        assert data["latest_snapshot_date"] is None
        assert data["first_snapshot_date"] is None
        assert data["total_snapshots"] == 0

    def test_missing_dates_after_latest_not_counted(self, client, admin_headers, test_db):
        """最新快照日后尚有未生成交易日 → 不计入 missing（区间语义边界）"""
        port = create_portfolio(test_db, code="MISS_TAIL", status="active")
        create_value_snapshot(test_db, port.code, MON, total_value=10000, total_shares=10000, unit_price=1.0)

        resp = client.get(f"/api/snapshots/portfolios/{port.code}/status", headers=admin_headers)
        assert resp.status_code == 200
        # 区间为 [MON, MON] 单日且有快照；周二~周五未生成属 catch-up 语义，不算 missing
        assert resp.json()["missing_dates"] == []
