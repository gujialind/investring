# ============ 集成测试：港互认快照估值按 HKEX 日历解析 T-N（issue #777） ============
# PORT005 事故形态复刻：SSE 开市、HKEX 休市日（2026-10-19 重阳节翌日）无净值，
# 旧行为在 SSE 日历上取 T-1=10-19 → MISSING_NAV 永久卡死；
# 新行为在 HKEX 日历上取 T-1=10-16（最近港股交易日的已发布净值，休市日估值冻结）。
# 同时钉死三条不变的严格性：开市缺价仍 MISSING_NAV、HKEX 日历缺失仍
# CALENDAR_NOT_SYNCED、A 股产品（CN_OTC）取价行为零变化。

from datetime import date
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot, TradingCalendar
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
    create_product,
    create_price_record,
)
from tests.integration.calendar_helpers import (
    mirror_hkex_calendar,
    remove_hkex_calendar,
)

HK_CLOSE = date(2026, 10, 19)  # 周一：SSE 开市（种子工作日规则）、HKEX 休市
T1_HK = date(2026, 10, 16)     # 周五：HKEX T-1（新行为取价日）
BASE = date(2026, 10, 19)      # 基线快照日（TARGET 前最近 SSE 交易日）
TARGET = date(2026, 10, 20)    # 周二：快照目标日，两市场均开市


@pytest.fixture
def hkex_oct_2026(test_db):
    """镜像 2026-10 的 HKEX 日历并把 10-19 置为休市；teardown 删除（共享库契约）。"""
    mirror_hkex_calendar(test_db, date(2026, 10, 1), date(2026, 10, 31))
    row = test_db.query(TradingCalendar).filter(
        TradingCalendar.calendar_date == HK_CLOSE,
        TradingCalendar.exchange == "HKEX",
    ).one()
    row.is_open = False
    test_db.commit()
    yield
    remove_hkex_calendar(test_db, date(2026, 10, 1), date(2026, 10, 31))


def _seed_product(test_db, code, market, nav_lag_days=1):
    create_product(
        test_db, code=code, market=market, product_type="OEF",
        asset_class_code="ASSET_STOCK", confirm_days=1,
        is_qdii=False, nav_lag_days=nav_lag_days,
    )


def _seed_baseline(test_db, portfolio_code, product_code, market):
    """BASE 日完整三表基线快照（100 份、净值 1.0），满足生成 TARGET 的连续性。"""
    create_position_snapshot(
        test_db, portfolio_code, product_code, market,
        snapshot_date=BASE, shares=100.0, unit_price=1.0,
        cost_price=1.0, market_value=100.0, platform_code="MYCF",
    )
    create_value_snapshot(
        test_db, portfolio_code, BASE,
        total_value=100.0, total_shares=100.0, unit_price=1.0,
    )
    create_investor_holding(test_db, portfolio_code, "VIEWER", BASE, shares=100.0)


def _generate(client, admin_headers, portfolio_code):
    return client.post(
        "/api/snapshots/generate",
        json={"portfolio_code": portfolio_code, "target_date": TARGET.isoformat()},
        headers=admin_headers,
    )


class TestHkMutualSnapshotCalendar:
    def test_hk_holiday_falls_back_to_last_hk_trading_day(
        self, client, admin_headers, test_db, hkex_oct_2026,
    ):
        """港股休市日估值冻结：T-1 在 HKEX 日历上解析为 10-16，快照成功。"""
        port = create_portfolio(test_db, code="HK_CAL1", status="active")
        _seed_product(test_db, "HKMR777", "HK_MUTUAL")
        _seed_baseline(test_db, port.code, "HKMR777", "HK_MUTUAL")
        create_price_record(test_db, "HKMR777", "HK_MUTUAL", T1_HK, 2.0)

        resp = _generate(client, admin_headers, port.code)
        assert resp.status_code == 200, resp.json()

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.product_code == "HKMR777",
            PortfolioPosition.snapshot_date == TARGET,
        ).first()
        assert pos is not None
        assert Decimal(str(pos.unit_price)) == Decimal("2.0")
        assert Decimal(str(pos.market_value)) == Decimal("200.0")

    def test_hk_open_day_missing_nav_still_rejected(
        self, client, admin_headers, test_db, hkex_oct_2026,
    ):
        """取价日（10-16）港股开市但无净值 → 仍 MISSING_NAV，且明细指向 HKEX 解析日。"""
        port = create_portfolio(test_db, code="HK_CAL2", status="active")
        _seed_product(test_db, "HKMR778", "HK_MUTUAL")
        _seed_baseline(test_db, port.code, "HKMR778", "HK_MUTUAL")
        # 刻意只放 10-19（HKEX 休市日）的价：不得被当作合法取价日
        create_price_record(test_db, "HKMR778", "HK_MUTUAL", HK_CLOSE, 9.9)

        resp = _generate(client, admin_headers, port.code)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MISSING_NAV"
        assert "HKMR778(HK_MUTUAL)" in detail["message"]
        assert "[T-1=2026-10-16]" in detail["message"]
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == TARGET,
        ).first() is None

    def test_hkex_calendar_missing_raises_not_synced(
        self, client, admin_headers, test_db,
    ):
        """HKEX 日历未同步 → CALENDAR_NOT_SYNCED（#591 语义延伸到 HKEX），不回退。"""
        port = create_portfolio(test_db, code="HK_CAL3", status="active")
        _seed_product(test_db, "HKMR779", "HK_MUTUAL")
        _seed_baseline(test_db, port.code, "HKMR779", "HK_MUTUAL")
        create_price_record(test_db, "HKMR779", "HK_MUTUAL", T1_HK, 2.0)
        # 不挂 hkex_oct_2026：库内无任何 HKEX 行

        resp = _generate(client, admin_headers, port.code)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "CALENDAR_NOT_SYNCED"
        assert detail["details"]["exchange"] == "HKEX"

    def test_cn_otc_pricing_unchanged_under_dual_calendar(
        self, client, admin_headers, test_db, hkex_oct_2026,
    ):
        """对照组：CN_OTC nav_lag_days=1 仍按 SSE 取 T-1=10-19，HKEX 行不影响。"""
        port = create_portfolio(test_db, code="HK_CAL4", status="active")
        _seed_product(test_db, "CTLOTC.OF", "CN_OTC")
        _seed_baseline(test_db, port.code, "CTLOTC.OF", "CN_OTC")
        create_price_record(test_db, "CTLOTC.OF", "CN_OTC", BASE, 2.5)

        resp = _generate(client, admin_headers, port.code)
        assert resp.status_code == 200, resp.json()

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.product_code == "CTLOTC.OF",
            PortfolioPosition.snapshot_date == TARGET,
        ).first()
        assert Decimal(str(pos.unit_price)) == Decimal("2.5")
