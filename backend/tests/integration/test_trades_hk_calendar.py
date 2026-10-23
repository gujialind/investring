# ============ 集成测试：HK_MUTUAL 调仓交易日/确认日按 HKEX 日历（issue #777） ============
# 基金申赎由调仓交易承载（场外净值型腿）：港股休市日基金暂停申赎且当日净值
# 永不发布，旧行为在 SSE 日历上放行下单 → 确认期永久 MISSING_NAV。
# 新行为：HK_MUTUAL 的下单日校验与确认间隔推算走 HKEX 日历；A 股产品不变；
# 卖出到账日/现金腿保持 SSE（决策A：现金在内地平台清算）。

from datetime import date

import pytest

from app.models.trade import Trade
from app.models import TradingCalendar
from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
)
from tests.integration.calendar_helpers import (
    mirror_hkex_calendar, remove_hkex_calendar,
)

HK_CLOSE = date(2026, 10, 19)  # SSE 开市、HKEX 休市（重阳节翌日）
TRADE_DAY = date(2026, 10, 16)  # 周五：两市场均开市
CASH_SEED_DAY = date(2026, 10, 15)


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


def _seed(test_db, port_code, product_code, market, platform_code):
    create_portfolio(test_db, code=port_code, status="active")
    create_product(
        test_db, code=product_code, market=market, product_type="OEF",
        asset_class_code="ASSET_STOCK", confirm_days=1,
        is_qdii=False, nav_lag_days=1,
    )
    create_platform(test_db, code=platform_code)
    # 现金基线：直写 ORM 的 confirmed CASH 腿（工厂不做日历校验）
    create_trade(
        test_db, port_code, "CASH", "",
        trade_type="buy", amount=50000.0, price=None,
        platform_code=platform_code, trade_date=CASH_SEED_DAY,
        confirm_date=CASH_SEED_DAY, status="confirmed",
    )


def _create_buy(client, admin_headers, port_code, product_code, market,
                platform_code, trade_date):
    return client.post(
        "/api/trades",
        json={
            "portfolio_code": port_code,
            "product_code": product_code,
            "market": market,
            "trade_type": "buy",
            "amount": 10000.0,
            "platform_code": platform_code,
            "trade_date": trade_date.isoformat(),
        },
        headers=admin_headers,
    )


class TestHkMutualTradeCalendar:
    def test_create_on_hk_holiday_rejected(
        self, client, admin_headers, test_db, hkex_oct_2026,
    ):
        """SSE 开市但港股休市：HK_MUTUAL 下单被 NON_TRADING_DAY 拒绝。"""
        _seed(test_db, "HK_TC1", "1001777001", "HK_MUTUAL", "HK_TCP1")

        resp = _create_buy(
            client, admin_headers, "HK_TC1", "1001777001", "HK_MUTUAL",
            "HK_TCP1", HK_CLOSE,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "NON_TRADING_DAY"

    def test_confirm_date_follows_hkex_calendar(
        self, client, admin_headers, test_db, hkex_oct_2026,
    ):
        """HK_MUTUAL 确认间隔在 HKEX 日历上推算：T=10-16 → C 跳过休市的 10-19 落 10-20。"""
        _seed(test_db, "HK_TC2", "1001777002", "HK_MUTUAL", "HK_TCP2")

        resp = _create_buy(
            client, admin_headers, "HK_TC2", "1001777002", "HK_MUTUAL",
            "HK_TCP2", TRADE_DAY,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["confirm_date"].startswith("2026-10-20")

    def test_cn_otc_confirm_date_still_sse(
        self, client, admin_headers, test_db, hkex_oct_2026,
    ):
        """对照组：CN_OTC 确认日仍按 SSE 推算（T=10-16 → C=10-19），双日历并存不串。"""
        _seed(test_db, "HK_TC3", "CTLOTC2.OF", "CN_OTC", "HK_TCP3")

        resp = _create_buy(
            client, admin_headers, "HK_TC3", "CTLOTC2.OF", "CN_OTC",
            "HK_TCP3", TRADE_DAY,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["confirm_date"].startswith("2026-10-19")

    def test_cn_otc_on_hk_holiday_still_allowed(
        self, client, admin_headers, test_db, hkex_oct_2026,
    ):
        """对照组：港股独有休市日不阻断 A 股产品下单（SSE 开市即合法）。"""
        _seed(test_db, "HK_TC4", "CTLOTC3.OF", "CN_OTC", "HK_TCP4")

        resp = _create_buy(
            client, admin_headers, "HK_TC4", "CTLOTC3.OF", "CN_OTC",
            "HK_TCP4", HK_CLOSE,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade = test_db.query(Trade).filter(Trade.id == resp.json()["id"]).first()
        assert trade.trade_date == HK_CLOSE
