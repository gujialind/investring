# ============ 集成测试：调仓交易 — 生命周期与配对同步（自 test_trades.py 拆分，issue #469） ============
# 含 TestUnconfirmTradeSnapshotProtection / TestUpdateDeletePairedSync / TestUpdateAmountSyncCashLeg。
# #25 unconfirm 快照保护：confirm_date 及之后已有快照 -> SNAPSHOT_DEPENDENCY。
# #26 PUT/DELETE 配对 CASH 腿同步：基金腿状态/日期/金额变化时 CASH 腿随动、删除级联；
#   但各腿保持独立 confirm_date——#93 起 CASH sell 腿独立取 trade_date（T 日扣款），不再互相同步。
# #46 PUT 改金额后配对 CASH 腿金额同步。

from datetime import date

from tests.factories import (
    create_portfolio, create_product, create_platform, create_trade,
    create_value_snapshot, ensure_trading_day,
)
from app.models.trade import Trade


class TestUnconfirmTradeSnapshotProtection:
    """#25 unconfirm_trade 快照保护"""

    def test_unconfirm_blocked_by_snapshot(self, client, admin_headers, test_db):
        """confirm_date 及之后已有快照时，unconfirm 返回 SNAPSHOT_DEPENDENCY"""
        create_portfolio(test_db, code="UC_P1", status="active")
        create_product(test_db, code="ETF_UC", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UC_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "UC_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UC_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 创建并确认一笔场内 ETF 买入（当天确认）
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UC_P1",
                "product_code": "ETF_UC",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UC_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200
        confirmed_trade = conf.json()["trade"]
        # 在 confirm_date 上生成快照
        create_value_snapshot(
            test_db, "UC_P1", date(2025, 10, 6),
            total_value=60000, total_shares=60000, unit_price=1.0,
        )

        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 422
        assert unconf.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"

    def test_unconfirm_ok_without_snapshot(self, client, admin_headers, test_db):
        """无快照依赖时，unconfirm 成功且配对 CASH 腿同步回 pending"""
        create_portfolio(test_db, code="UC_P2", status="active")
        create_product(test_db, code="ETF_UC2", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UC_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "UC_P2", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UC_PLAT2", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UC_P2",
                "product_code": "ETF_UC2",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UC_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200

        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 200
        # 验证主腿与配对 CASH 腿均回 pending（按 transfer_group 过滤，排除预置现金腿）
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert fund_leg.status == "pending"
        assert paired.status == "pending"


class TestUpdateDeletePairedSync:
    """#26 PUT/DELETE 配对 CASH 腿同步"""

    def test_update_trade_date_syncs_cash_leg(self, client, admin_headers, test_db):
        """update 改动 trade_date 时 confirm_date 联动重算，并同步配对 CASH 腿"""
        create_portfolio(test_db, code="UPD_P1", status="active")
        create_product(test_db, code="ETF_UPD", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UPD_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 8), is_open=True)
        create_trade(
            test_db, "UPD_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UPD_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UPD_P1",
                "product_code": "ETF_UPD",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UPD_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        # #93: CASH sell 腿独立确认日 = trade_date（T日扣款），不依赖基金腿
        assert cash_leg.confirm_date == date(2025, 10, 6)

        # confirm_date 不开放直改：额外字段被 schema 忽略，不产生变更
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"confirm_date": "2025-10-08", "notes": "try direct set"},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        assert fund_leg.confirm_date == date(2025, 10, 6)

        # 改 trade_date → 基金腿按 confirm_days 联动重算；CASH sell 腿独立取 trade_date（#93）
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"trade_date": "2025-10-08"},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        assert fund_leg.confirm_date == date(2025, 10, 8)
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        # #93: CASH sell 腿独立确认日 = 新 trade_date
        assert cash_leg.confirm_date == date(2025, 10, 8)
        # CASH 腿 trade_date 随基金腿同步（组内不变量）
        assert cash_leg.trade_date == date(2025, 10, 8)

        # #93: confirm → unconfirm 后各腿独立回退默认确认日
        # CASH sell 腿回退到 trade_date（T日扣款），基金腿按 confirm_days 重算
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200
        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert fund_leg.status == "pending" and cash_leg.status == "pending"
        assert cash_leg.trade_date == date(2025, 10, 8)
        # #93: CASH sell 腿独立确认日 = trade_date，基金腿独立按 confirm_days 重算
        # 此处 confirm_days=0 所以两值相等，但语义独立（不再互相同步）
        assert cash_leg.confirm_date == date(2025, 10, 8)  # = trade_date（T日扣款）
        assert fund_leg.confirm_date == date(2025, 10, 8)  # = T+0（confirm_days=0）

    def test_update_trade_status_field_ignored(self, client, admin_headers, test_db):
        """PUT 传 status 被忽略（状态流转只走 confirm/cancel/unconfirm 端点）"""
        create_portfolio(test_db, code="UPD_P2", status="active")
        create_product(test_db, code="ETF_UPD2", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="UPD_PLAT2")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "UPD_P2", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="UPD_PLAT2", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "UPD_P2",
                "product_code": "ETF_UPD2",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "UPD_PLAT2",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]

        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"status": "confirmed"},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == fund_leg.transfer_group,
            Trade.id != trade_id,
        ).first()
        # status 字段被 schema 忽略，两腿均保持 pending
        assert fund_leg.status == "pending"
        assert cash_leg.status == "pending"

    def test_delete_trade_cascades_cash_leg(self, client, admin_headers, test_db):
        """delete 主腿时级联删除配对 CASH 腿"""
        create_portfolio(test_db, code="DEL_P1", status="active")
        create_product(test_db, code="ETF_DEL", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="DEL_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "DEL_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="DEL_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "DEL_P1",
                "product_code": "ETF_DEL",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "DEL_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        before = test_db.query(Trade).filter(Trade.transfer_group == tg).count()
        assert before == 2

        dele = client.delete(f"/api/trades/{trade_id}", headers=admin_headers)
        assert dele.status_code == 200
        test_db.expire_all()
        after = test_db.query(Trade).filter(Trade.transfer_group == tg).count()
        assert after == 0


class TestUpdateAmountSyncCashLeg:
    """#46 PUT 改金额后配对 CASH 腿同步"""

    def test_update_actual_amount_syncs_cash_leg(self, client, admin_headers, test_db):
        """修改基金腿 actual_amount 后，配对 CASH 腿 amount 同步更新"""
        create_portfolio(test_db, code="AMT_P1", status="active")
        create_product(test_db, code="ETF_AMT", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_platform(test_db, code="AMT_PLAT")
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(
            test_db, "AMT_P1", "CASH", "",
            trade_type="buy", amount=50000.0, price=None,
            platform_code="AMT_PLAT", trade_date=date(2025, 10, 3),
            confirm_date=date(2025, 10, 3), status="confirmed",
        )
        # 创建基金买入（自动生成配对 CASH 腿）
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "AMT_P1",
                "product_code": "ETF_AMT",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "AMT_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        trade_id = resp.json()["id"]
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert paired is not None
        original_paired_amount = float(paired.amount)

        # PUT 修改 actual_amount
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"actual_amount": 8000.0},
            headers=admin_headers,
        )
        assert upd.status_code == 200
        test_db.expire_all()
        paired_after = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        # 配对 CASH 腿金额应同步为新的 actual_amount
        assert float(paired_after.amount) == 8000.0
        assert float(paired_after.actual_amount) == 8000.0
        assert float(paired_after.amount) != original_paired_amount
