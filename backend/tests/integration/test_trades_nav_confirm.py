# ============ 集成测试：调仓交易 — 权限与净值确认（自 test_trades.py 拆分，issue #469） ============
# 含 TestTradePermissions / TestNonQDIIrigorousNav / TestHKMutualConfirmNav。
# #24 非 QDII 场外确认净值严格取 T 日：缺失即 MISSING_NAV，禁止向前回退。
# HK_MUTUAL 同 CN_OTC 口径：确认时取 T 日净值重算 shares/amount，缺价拒绝、不空确认、无脏写；
#   sync_nav=True 的回填对默认 tushare 数据源静默跳过（该源不支持 HK_MUTUAL），错误码契约不变。

import pytest
from datetime import date
from unittest.mock import patch

from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
    create_position_snapshot, ensure_trading_day, create_price_record,
)
from app.models.trade import Trade


class TestTradePermissions:
    """调仓交易权限测试"""

    def test_viewer_cannot_trade(self, client, viewer_headers):
        """viewer 不能提交调仓交易"""
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "X",
                "product_code": "X",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 1000,
                "price": 1.0,
                "trade_date": "2025-10-06",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_list_trades(self, client, admin_headers):
        """获取调仓交易列表"""
        resp = client.get("/api/trades", headers=admin_headers)
        assert resp.status_code == 200
        assert "items" in resp.json()


class TestNonQDIIrigorousNav:
    """#24 非QDII净值严格 T 日：禁止向前查找"""

    def test_confirm_oef_missing_t_nav_rejected(self, client, admin_headers, test_db):
        """场外基金确认时，T 日净值缺失应拒绝（不再向前回溯）"""
        create_portfolio(test_db, code="NAV_P1", status="active")
        create_product(test_db, code="FUND_NAV1", market="CN_OTC",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="NAV_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        # 提供现金
        create_trade(
            test_db, "NAV_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="NAV_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 仅写入 T-1 净值，T 日缺失
        create_price_record(test_db, "FUND_NAV1", "CN_OTC",
                            date(2025, 10, 3), unit_price=1.2)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "NAV_P1",
                "product_code": "FUND_NAV1",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "NAV_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        trade_id = resp.json()["id"]

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_NAV"


class TestHKMutualConfirmNav:
    """HK_MUTUAL（香港互认）基金确认同 CN_OTC 口径取 T 日净值重算 shares/amount

    事故复刻：市场白名单曾仅含 CN_OTC，HK_MUTUAL 落入「不重算」分支，
    确认后 price/shares 为空（即使 price_record 已有 T 日净值）。
    """

    def test_confirm_hk_mutual_uses_t_nav(self, client, admin_headers, test_db):
        """HK_MUTUAL 买入确认：取 T 日净值回填 price 并计算 shares"""
        create_portfolio(test_db, code="HK_P1", status="active")
        create_product(test_db, code="1001767346", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        # 提供现金
        create_trade(
            test_db, "HK_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # T 日净值（market=HK_MUTUAL）
        create_price_record(test_db, "1001767346", "HK_MUTUAL",
                            date(2025, 10, 6), unit_price=1.25)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P1",
                "product_code": "1001767346",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]
        # 创建时无价格：price 为空、shares 未计算（序列化可能为 0.0）
        assert resp.json()["price"] is None
        assert resp.json()["shares"] in (None, 0.0)

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 200, confirm.json()
        assert confirm.json()["status"] == "confirmed"
        # confirm 端点返回精简结构，回查 DB 断言净值/份额已回填
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert float(t.price) == 1.25
        # shares = (actual_amount - fee) / nav = 10000 / 1.25
        assert float(t.shares) == 8000.0

    def test_confirm_hk_mutual_missing_t_nav_rejected(self, client, admin_headers, test_db):
        """HK_MUTUAL 缺 T 日净值同样拒绝确认（不回退、不空确认、无脏写）"""
        create_portfolio(test_db, code="HK_P2", status="active")
        create_product(test_db, code="1001767344", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "HK_P2", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT2", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 仅写入 T-1 净值，T 日缺失：验证严格 T 日匹配、禁止向前回退（同 CN_OTC 口径）
        create_price_record(test_db, "1001767344", "HK_MUTUAL",
                            date(2025, 10, 3), unit_price=1.24)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P2",
                "product_code": "1001767344",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_NAV"
        # 失败路径状态守卫：trade 保持 pending、无字段脏写（shares 保持创建时默认 0）
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert t.status == "pending"
        assert t.price is None
        assert float(t.shares) == 0.0

    def test_confirm_hk_mutual_sell_uses_t_nav(self, client, admin_headers, test_db):
        """HK_MUTUAL 卖出确认：amount = shares × T 日净值重算，配对 CASH 腿镜像金额"""
        create_portfolio(test_db, code="HK_P3", status="active")
        create_product(test_db, code="1001767348", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT3")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        # 先有持仓可卖（10-03 快照 1000 份）
        create_position_snapshot(
            test_db, "HK_P3", "1001767348", "HK_MUTUAL",
            snapshot_date=date(2025, 10, 3),
            shares=1000.0, unit_price=1.2, cost_price=1.2,
            market_value=1200.0, platform_code="HK_PLAT3",
        )
        # T 日净值（market=HK_MUTUAL）
        create_price_record(test_db, "1001767348", "HK_MUTUAL",
                            date(2025, 10, 6), unit_price=1.25)

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P3",
                "product_code": "1001767348",
                "market": "HK_MUTUAL",
                "trade_type": "sell",
                "shares": 400.0,
                "platform_code": "HK_PLAT3",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        confirm = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert confirm.status_code == 200, confirm.json()
        assert confirm.json()["status"] == "confirmed"
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert float(t.price) == 1.25
        assert float(t.shares) == 400.0
        # amount = quantize(400 × 1.25) = 500，fee=0 → actual_amount = 500
        assert float(t.amount) == 500.0
        assert float(t.actual_amount) == 500.0
        # 配对 CASH 腿（卖出到账 = CASH buy）镜像 actual_amount
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == t.transfer_group,
            Trade.id != trade_id,
        ).first()
        assert paired is not None
        assert paired.product_code == "CASH"
        assert paired.trade_type == "buy"
        assert paired.status == "confirmed"
        assert float(paired.amount) == 500.0

    def test_confirm_hk_mutual_sync_nav_backfill(self, client, admin_headers, test_db):
        """sync_nav=True：MISSING_NAV 时自动回填 HK_MUTUAL 净值后重试确认成功（mock 同步）"""
        create_portfolio(test_db, code="HK_P4", status="active")
        create_product(test_db, code="1001767350", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT4")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        create_trade(
            test_db, "HK_P4", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT4", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 不预置净值：首次确认命中 MISSING_NAV 触发 sync_nav 回填
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P4",
                "product_code": "1001767350",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT4",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        def fake_sync(db, code, market, start, end):
            create_price_record(db, code, market, date(2025, 10, 6), unit_price=1.5)
            return {"success": True, "synced_count": 1}

        with patch(
            "app.services.market_data_service.sync_price_data",
            side_effect=fake_sync,
        ) as mock_sync:
            confirm = client.post(
                f"/api/trades/{trade_id}/confirm",
                params={"sync_nav": "true"},
                headers=admin_headers,
            )
        assert confirm.status_code == 200, confirm.json()
        mock_sync.assert_called_once()
        # 同步调用携带正确的产品代码与 HK_MUTUAL 市场
        assert mock_sync.call_args[0][1] == "1001767350"
        assert mock_sync.call_args[0][2] == "HK_MUTUAL"
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert t.status == "confirmed"
        assert float(t.price) == 1.5
        assert float(t.shares) == pytest.approx(6666.67)  # 10000 / 1.5

    def test_confirm_hk_mutual_sync_nav_tushare_skip_still_missing(
        self, client, admin_headers, test_db
    ):
        """sync_nav=True 但产品为默认 tushare 数据源：同步静默跳过，仍 MISSING_NAV

        运维盲区复刻：tushare 不支持 HK_MUTUAL（sync_product_prices 直接 skip 不抛错），
        回填承诺对默认数据源永远无效，但错误码契约不变（不吞错、无脏写）。
        """
        create_portfolio(test_db, code="HK_P5", status="active")
        # 工厂默认 data_source="tushare"（Product 模型默认值）
        create_product(test_db, code="1001767352", market="HK_MUTUAL",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_platform(test_db, code="HK_PLAT5")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "HK_P5", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="HK_PLAT5", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )

        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "HK_P5",
                "product_code": "1001767352",
                "market": "HK_MUTUAL",
                "trade_type": "buy",
                "amount": 10000.0,
                "platform_code": "HK_PLAT5",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]

        confirm = client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"sync_nav": "true"},
            headers=admin_headers,
        )
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_NAV"
        test_db.expire_all()
        t = test_db.query(Trade).filter(Trade.id == trade_id).first()
        assert t.status == "pending"
        assert t.price is None
