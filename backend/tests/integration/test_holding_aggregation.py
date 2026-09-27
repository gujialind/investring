"""#595 步骤①：持仓聚合 API（组合详情页「按产品 / 按平台」双视图）。

覆盖：跨平台聚合与平台切片、现金聚合卡、在途计入总市值但不出卡、
#598 累计收益三粒度接入与无市值快照降级、spec §6 过滤、双日 daily_profit、
空组合 / 未知组合 / viewer 读权限。
"""

import pytest
from datetime import date

from tests.factories import (
    create_platform,
    create_portfolio,
    create_position_snapshot,
    create_product,
    create_trade,
    create_value_snapshot,
)

D1 = date(2025, 11, 3)
D2 = date(2025, 11, 4)
PORT = "HAGG_PORT"
PA, PB = "HAGG_A", "HAGG_B"
F1, F1M = "HAGG_F1", "CN_OTC"
F2, F2M = "HAGG_F2", "CN_EXCHANGE"


@pytest.fixture
def base_portfolio(test_db):
    """两平台三产品（含现金）+ 在途行，最新快照日 D1，带市值快照与确认交易。

    F1 跨平台：A(120, 100份@1.0) + B(60, 50份@1.0)；F2 仅 A(300, 200份@2.0)；
    CASH：A 1000 + B 500；IN_TRANSIT_BUY A 300（计入总市值、不出产品卡）。
    总市值 2280。confirmed 买入：F1/A 100、F1/B 50、F2/A 400（confirm_date=D1）。
    """
    create_portfolio(test_db, code=PORT, status="active")
    create_platform(test_db, code=PA, name="平台A")
    create_platform(test_db, code=PB, name="平台B")
    create_product(test_db, code=F1, market=F1M, name="聚合基金一号")
    create_product(test_db, code=F2, market=F2M, name="聚合基金二号", product_type="ETF")

    create_position_snapshot(
        test_db, PORT, F1, F1M, snapshot_date=D1, shares=100.0, cost_price=1.0,
        unit_price=1.2, market_value=120.0, platform_code=PA,
    )
    create_position_snapshot(
        test_db, PORT, F1, F1M, snapshot_date=D1, shares=50.0, cost_price=1.0,
        unit_price=1.2, market_value=60.0, platform_code=PB,
    )
    create_position_snapshot(
        test_db, PORT, F2, F2M, snapshot_date=D1, shares=200.0, cost_price=2.0,
        unit_price=1.5, market_value=300.0, platform_code=PA,
    )
    create_position_snapshot(
        test_db, PORT, "CASH", "", snapshot_date=D1, cash_amount=1000.0,
        unit_price=None, cost_price=None, market_value=1000.0, platform_code=PA,
    )
    create_position_snapshot(
        test_db, PORT, "CASH", "", snapshot_date=D1, cash_amount=500.0,
        unit_price=None, cost_price=None, market_value=500.0, platform_code=PB,
    )
    create_position_snapshot(
        test_db, PORT, "IN_TRANSIT_BUY", "", snapshot_date=D1, cash_amount=300.0,
        unit_price=None, cost_price=None, market_value=300.0, platform_code=PA,
    )
    create_value_snapshot(
        test_db, portfolio_code=PORT, snapshot_date=D1,
        total_value=2280.0, total_shares=2280.0, unit_price=1.0,
    )
    for product, market, platform, actual in (
        (F1, F1M, PA, 100.0), (F1, F1M, PB, 50.0), (F2, F2M, PA, 400.0),
    ):
        create_trade(
            test_db, portfolio_code=PORT, product_code=product, market=market,
            platform_code=platform, trade_type="buy", status="confirmed",
            trade_date=D1, confirm_date=D1, amount=actual, actual_amount=actual,
        )
    # 双层账本闭环：申购存入（sub_ 前缀 CASH buy）+ 调仓买入扣款（rebal_ 前缀
    # CASH sell）配对腿，使现金累计收益口径 ≈ 0（与生产写入方形态一致）。
    for platform, deposit, outflow in ((PA, 1500.0, 500.0), (PB, 550.0, 50.0)):
        create_trade(
            test_db, portfolio_code=PORT, product_code="CASH", market="",
            platform_code=platform, trade_type="buy", status="confirmed",
            trade_date=D1, confirm_date=D1, amount=deposit, actual_amount=deposit,
            transfer_group=f"sub_hagg_{platform}",
        )
        create_trade(
            test_db, portfolio_code=PORT, product_code="CASH", market="",
            platform_code=platform, trade_type="sell", status="confirmed",
            trade_date=D1, confirm_date=D1, amount=outflow, actual_amount=outflow,
            transfer_group=f"rebal_hagg_{platform}",
        )
    return PORT


def _by_product(client, headers, code=PORT):
    resp = client.get(f"/api/positions/portfolio/{code}/holdings/by-product",
                      headers=headers)
    assert resp.status_code == 200
    return resp.json()


def _by_platform(client, headers, code=PORT):
    resp = client.get(f"/api/positions/portfolio/{code}/holdings/by-platform",
                      headers=headers)
    assert resp.status_code == 200
    return resp.json()


class TestByProduct:
    def test_aggregates_across_platforms_with_slices(
        self, client, admin_headers, base_portfolio
    ):
        data = _by_product(client, admin_headers)
        assert data["portfolio_code"] == PORT
        assert data["snapshot_date"] == str(D1)
        assert data["total_market_value"] == 2280.0
        # 排序：市值降序 CASH(1500) > F2(300) > F1(180)
        assert [p["product_code"] for p in data["products"]] == ["CASH", F2, F1]

        f1 = data["products"][2]
        assert f1["market"] == F1M
        assert f1["product_name"] == "聚合基金一号"
        assert f1["market_value"] == 180.0
        assert f1["shares"] == 150.0
        assert f1["cash_amount"] is None
        assert f1["holding_profit"] == 30.0          # 180 − 150×1.0
        assert f1["holding_profit_percent"] == 20.0  # 30/150
        assert f1["ratio"] == 0.0789                 # 180/2280
        assert f1["cumulative_profit"] == 30.0       # #598：180 − 150
        # 维度元数据（create_product 默认字典值）
        assert f1["asset_class_code"] == "ASSET_STOCK"
        assert f1["asset_class_name"] == "股票"
        assert f1["region_name"] == "中国"
        # 平台切片（按市值降序：A 120 → B 60）
        assert [(s["platform_code"], s["market_value"]) for s in f1["platforms"]] == [
            (PA, 120.0), (PB, 60.0),
        ]
        assert f1["platforms"][0]["platform_name"] == "平台A"
        assert f1["platforms"][0]["shares"] == 100.0
        assert f1["platforms"][0]["holding_profit"] == 20.0
        assert f1["platforms"][0]["cumulative_profit"] == 20.0   # 120 − 100
        assert f1["platforms"][0]["ratio_in_product"] == 0.6667  # 120/180
        assert f1["platforms"][1]["cumulative_profit"] == 10.0   # 60 − 50
        assert f1["platforms"][1]["ratio_in_product"] == 0.3333

        f2 = data["products"][1]
        assert f2["market_value"] == 300.0
        assert f2["holding_profit"] == -100.0        # 300 − 200×2.0
        assert f2["holding_profit_percent"] == -25.0
        assert f2["cumulative_profit"] == -100.0     # 300 − 400

    def test_cash_aggregate_card(self, client, admin_headers, base_portfolio):
        data = _by_product(client, admin_headers)
        cash = data["products"][0]
        assert cash["product_code"] == "CASH"
        assert cash["market"] == ""
        assert cash["market_value"] == 1500.0
        assert cash["shares"] is None
        assert cash["cash_amount"] == 1500.0
        assert cash["ratio"] == 0.6579               # 1500/2280
        assert cash["asset_class_code"] == "ASSET_CASH"
        assert cash["holding_profit"] == 0.0         # 存入/扣款闭环后现金收益 ≈ 0
        assert cash["cumulative_profit"] == 0.0      # 1500 − 净存入 1500（#598）
        assert [(s["platform_code"], s["cash_amount"]) for s in cash["platforms"]] == [
            (PA, 1000.0), (PB, 500.0),
        ]

    def test_in_transit_excluded_from_cards_but_counted_in_total(
        self, client, admin_headers, base_portfolio
    ):
        data = _by_product(client, admin_headers)
        codes = {p["product_code"] for p in data["products"]}
        assert "IN_TRANSIT_BUY" not in codes
        assert data["total_market_value"] == 2280.0  # 含在途 300

    def test_cumulative_none_without_value_snapshot(
        self, client, admin_headers, test_db, base_portfolio
    ):
        # 删掉市值快照后累计收益降级 None，视图其余字段不受影响
        from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
        test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == PORT
        ).delete()
        test_db.commit()
        data = _by_product(client, admin_headers)
        assert data["total_market_value"] == 2280.0
        for p in data["products"]:
            assert p["cumulative_profit"] is None
            for s in p["platforms"]:
                assert s["cumulative_profit"] is None

    def test_zero_value_and_zero_shares_filtered(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="HAGG_Z", status="active")
        create_platform(test_db, code="HAGG_Z_PLAT")
        create_product(test_db, code=F1, market=F1M)
        create_product(test_db, code=F2, market=F2M, product_type="ETF")
        # 市值 0 的基金行、份额 0 但市值非 0 的脏数据行、市值 0 的现金行 → 全部不展示
        create_position_snapshot(
            test_db, "HAGG_Z", F1, F1M, snapshot_date=D1, shares=0.0,
            cost_price=1.0, market_value=0.0, platform_code="HAGG_Z_PLAT",
        )
        create_position_snapshot(
            test_db, "HAGG_Z", F2, F2M, snapshot_date=D1, shares=0.0,
            cost_price=1.0, market_value=100.0, platform_code="HAGG_Z_PLAT",
        )
        create_position_snapshot(
            test_db, "HAGG_Z", "CASH", "", snapshot_date=D1, cash_amount=0.0,
            unit_price=None, cost_price=None, market_value=0.0,
            platform_code="HAGG_Z_PLAT",
        )
        data = _by_product(client, admin_headers, code="HAGG_Z")
        assert data["products"] == []
        assert data["total_market_value"] == 100.0

    def test_daily_profit_aggregated_on_second_day(
        self, client, admin_headers, test_db
    ):
        create_portfolio(test_db, code="HAGG_D", status="active")
        create_platform(test_db, code="HAGG_D_PLAT")
        create_product(test_db, code=F1, market=F1M)
        for day, mv in ((D1, 100.0), (D2, 110.0)):
            create_position_snapshot(
                test_db, "HAGG_D", F1, F1M, snapshot_date=day, shares=100.0,
                cost_price=1.0, unit_price=1.0, market_value=mv,
                platform_code="HAGG_D_PLAT",
            )
        data = _by_product(client, admin_headers, code="HAGG_D")
        assert data["snapshot_date"] == str(D2)
        f1 = data["products"][0]
        assert f1["market_value"] == 110.0
        assert f1["daily_profit"] == 10.0            # 110 − 100，D2 无确认净买入

    def test_empty_portfolio_returns_empty(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="HAGG_E", status="active")
        data = _by_product(client, admin_headers, code="HAGG_E")
        assert data["snapshot_date"] is None
        assert data["total_market_value"] == 0.0
        assert data["products"] == []

    def test_unknown_portfolio_404(self, client, admin_headers, test_db):
        resp = client.get("/api/positions/portfolio/HAGG_NOPE/holdings/by-product",
                          headers=admin_headers)
        assert resp.status_code == 404

    def test_viewer_can_read(self, client, viewer_headers, base_portfolio):
        resp = client.get(f"/api/positions/portfolio/{PORT}/holdings/by-product",
                          headers=viewer_headers)
        assert resp.status_code == 200


class TestByPlatform:
    def test_platform_aggregates(self, client, admin_headers, base_portfolio):
        data = _by_platform(client, admin_headers)
        assert data["snapshot_date"] == str(D1)
        assert data["total_market_value"] == 2280.0
        # 排序：市值降序 A(1720，含在途 300) > B(560)
        assert [p["platform_code"] for p in data["platforms"]] == [PA, PB]

        a = data["platforms"][0]
        assert a["platform_name"] == "平台A"
        assert a["market_value"] == 1720.0
        assert a["cash_balance"] == 1000.0
        assert a["product_count"] == 2               # 只计非现金非在途（F1、F2）
        assert a["holding_profit"] == -80.0          # 20 + (−100) + 现金 0
        assert a["cumulative_profit"] == -80.0       # 20 − 100 + 现金 0
        assert a["ratio"] == 0.7544                  # 1720/2280

        b = data["platforms"][1]
        assert b["market_value"] == 560.0
        assert b["cash_balance"] == 500.0
        assert b["product_count"] == 1
        assert b["holding_profit"] == 10.0
        assert b["cumulative_profit"] == 10.0        # 10 + 现金 0
        assert b["ratio"] == 0.2456

    def test_cumulative_none_without_value_snapshot(
        self, client, admin_headers, test_db, base_portfolio
    ):
        from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
        test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == PORT
        ).delete()
        test_db.commit()
        data = _by_platform(client, admin_headers)
        for p in data["platforms"]:
            assert p["cumulative_profit"] is None
            assert p["market_value"] > 0

    def test_zero_value_platform_filtered(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="HAGG_ZP", status="active")
        create_platform(test_db, code="HAGG_ZP_A")
        create_platform(test_db, code="HAGG_ZP_B")
        create_product(test_db, code=F1, market=F1M)
        create_position_snapshot(
            test_db, "HAGG_ZP", "CASH", "", snapshot_date=D1, cash_amount=0.0,
            unit_price=None, cost_price=None, market_value=0.0,
            platform_code="HAGG_ZP_A",
        )
        create_position_snapshot(
            test_db, "HAGG_ZP", F1, F1M, snapshot_date=D1, shares=100.0,
            cost_price=1.0, market_value=120.0, platform_code="HAGG_ZP_B",
        )
        data = _by_platform(client, admin_headers, code="HAGG_ZP")
        assert [p["platform_code"] for p in data["platforms"]] == ["HAGG_ZP_B"]

    def test_empty_portfolio_returns_empty(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="HAGG_EP", status="active")
        data = _by_platform(client, admin_headers, code="HAGG_EP")
        assert data["snapshot_date"] is None
        assert data["total_market_value"] == 0.0
        assert data["platforms"] == []

    def test_unknown_portfolio_404(self, client, admin_headers, test_db):
        resp = client.get("/api/positions/portfolio/HAGG_NOPE/holdings/by-platform",
                          headers=admin_headers)
        assert resp.status_code == 404

    def test_viewer_can_read(self, client, viewer_headers, base_portfolio):
        resp = client.get(f"/api/positions/portfolio/{PORT}/holdings/by-platform",
                          headers=viewer_headers)
        assert resp.status_code == 200
