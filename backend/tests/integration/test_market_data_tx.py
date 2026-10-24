# ============================================================================
# 集成测试：market-data / trading-calendar REST 事务边界（issue #58 附带整改）
# ============================================================================
# service 去 commit 后由 router 统一提交，验证：
# - 价格同步失败时 _mark_failed 的失败状态经 router commit 仍持久化
# - 交易日历 REST sync 后日历记录落库
# ============================================================================

from datetime import date
from unittest.mock import patch

from app.models import Product, TradingCalendar


class TestSyncPriceDataTx:
    """价格同步端点的事务边界"""

    @patch("app.services.market_data_service.get_fund_daily")
    def test_sync_success_persists_prices(self, mock_daily, client, admin_headers, test_db):
        mock_daily.return_value = [
            {"trade_date": "20250606", "close": 4.0, "pre_close": 3.9, "pct_chg": 2.56},
        ]
        resp = client.post(
            "/api/market-data/products/510300.SH/CN_EXCHANGE/sync-price-data",
            json={"start_date": "2025-06-06", "end_date": "2025-06-06"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        product = test_db.query(Product).filter(
            Product.code == "510300.SH", Product.market == "CN_EXCHANGE"
        ).first()
        test_db.refresh(product)
        assert product.data_source_status == "success"

    @patch("app.services.market_data_service.get_fund_daily")
    def test_sync_failure_persists_failed_status(self, mock_daily, client, admin_headers, test_db):
        """数据源异常 → 400，但 _mark_failed 的失败标记经 router commit 持久化"""
        mock_daily.side_effect = Exception("tushare boom")
        resp = client.post(
            "/api/market-data/products/510300.SH/CN_EXCHANGE/sync-price-data",
            json={"start_date": "2025-06-06", "end_date": "2025-06-06"},
            headers=admin_headers,
        )
        assert resp.status_code == 400

        product = test_db.query(Product).filter(
            Product.code == "510300.SH", Product.market == "CN_EXCHANGE"
        ).first()
        test_db.refresh(product)
        assert product.data_source_status == "failed"
        assert "boom" in (product.sync_error or "")


class TestTradingCalendarSyncTx:
    """交易日历同步端点的事务边界"""

    # #777：sync 缺省同步 SSE + HKEX 两套，HKEX 客户端一并 mock（空返回 = 0 条），
    # 本用例焦点仍是「SSE 行落库 + 计数正确」的事务边界语义
    @patch("app.services.trading_calendar_service.get_hk_trade_calendar")
    @patch("app.services.trading_calendar_service.get_trade_calendar")
    def test_sync_persists_calendar(self, mock_cal, mock_hk, client, admin_headers, test_db):
        mock_cal.return_value = [
            {"date": "2031-01-02", "is_open": True},
            {"date": "2031-01-03", "is_open": True},
            {"date": "2031-01-04", "is_open": False},
        ]
        mock_hk.return_value = []
        resp = client.post(
            "/api/trading-calendar/sync",
            json={"year": 2031},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["synced_count"] == 3

        rows = test_db.query(TradingCalendar).filter(
            TradingCalendar.calendar_date >= date(2031, 1, 1),
            TradingCalendar.calendar_date <= date(2031, 1, 31),
        ).all()
        assert len(rows) == 3
