# ============================================================================
# 集成测试：market-data / trading-calendar REST 事务边界（issue #58 附带整改）
# ============================================================================
# service 去 commit 后由 router 统一提交，验证：
# - 价格同步失败时 _mark_failed 的失败状态经 router commit 仍持久化
# - 交易日历 REST sync 后日历记录落库
# ============================================================================

from datetime import date, timedelta
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


class TestSyncHistoryTx:
    """issue #856：sync-history 可选周期 body 的契约。

    mock 掉 router 命名空间的 sync_price_data，隔离 #856 改动的 router 周期逻辑
    （start/end 推导与非法区间拒绝）与真实数据源；响应形状原样透传不变。
    """

    RESULT = {"success": True, "message": "同步完成", "synced_count": 5, "new_count": 2}
    URL = "/api/market-data/products/510300.SH/CN_EXCHANGE/sync-history"

    @patch("app.routers.market_data.sync_price_data")
    def test_no_body_full_history(self, mock_sync, client, admin_headers, test_db):
        """无请求体（request=None）= 成立以来全量：start None、end 今天，响应形状不变"""
        mock_sync.return_value = dict(self.RESULT)
        resp = client.post(self.URL, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == self.RESULT
        args = mock_sync.call_args.args
        assert args[1] == "510300.SH" and args[2] == "CN_EXCHANGE"
        assert args[3] is None
        assert args[4] == date.today()

    @patch("app.routers.market_data.sync_price_data")
    def test_empty_body_full_history(self, mock_sync, client, admin_headers, test_db):
        """空 body {}（CLI 无日期形态）= 全量，修复此前 PriceDataSyncRequest 必填 422"""
        mock_sync.return_value = dict(self.RESULT)
        resp = client.post(self.URL, json={}, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        args = mock_sync.call_args.args
        assert args[3] is None and args[4] == date.today()

    @patch("app.routers.market_data.sync_price_data")
    def test_single_start_date(self, mock_sync, client, admin_headers, test_db):
        """只传 start_date → 从该日到今天（end 缺省补今天）"""
        mock_sync.return_value = dict(self.RESULT)
        resp = client.post(self.URL, json={"start_date": "2025-06-01"}, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        args = mock_sync.call_args.args
        assert args[3] == date(2025, 6, 1)
        assert args[4] == date.today()

    @patch("app.routers.market_data.sync_price_data")
    def test_both_dates(self, mock_sync, client, admin_headers, test_db):
        """双日期 → 原样下传限定区间"""
        mock_sync.return_value = dict(self.RESULT)
        resp = client.post(
            self.URL, json={"start_date": "2025-06-01", "end_date": "2025-06-06"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        args = mock_sync.call_args.args
        assert args[3] == date(2025, 6, 1) and args[4] == date(2025, 6, 6)

    @patch("app.routers.market_data.sync_price_data")
    def test_start_after_end_400(self, mock_sync, client, admin_headers, test_db):
        """start > end → 400 裸字符串错误（沿用本 router 形状，不新增错误码），不下传同步"""
        resp = client.post(
            self.URL, json={"start_date": "2025-06-10", "end_date": "2025-06-01"},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "开始日期不能晚于结束日期"
        mock_sync.assert_not_called()

    @patch("app.routers.market_data.sync_price_data")
    def test_future_start_400(self, mock_sync, client, admin_headers, test_db):
        """未来 start（end 缺省今天）→ start > end → 400"""
        future = date.today() + timedelta(days=10)
        resp = client.post(self.URL, json={"start_date": future.isoformat()}, headers=admin_headers)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "开始日期不能晚于结束日期"
        mock_sync.assert_not_called()
