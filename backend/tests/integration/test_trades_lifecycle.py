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
        """无快照依赖时 unconfirm 成功；#493：买入组 CASH 扣款腿保持 confirmed"""
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
        # #493：unconfirm 只回退基金腿；买入扣款腿是既成事实，保持 confirmed
        # （日期与金额同样保持），再次确认时核验一致即可
        fund_leg = test_db.query(Trade).get(trade_id)
        tg = fund_leg.transfer_group
        paired = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert fund_leg.status == "pending"
        assert paired.status == "confirmed"
        assert paired.product_code == "CASH" and paired.trade_type == "sell"
        assert paired.trade_date == date(2025, 10, 6)
        assert paired.confirm_date == date(2025, 10, 6)
        assert float(paired.amount) == 10000.0


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
        # #493：编辑只同步扣款日期/金额，**不得**把买入扣款腿打回 pending——
        # 扣款是既成事实；pending 的调仓 CASH 腿会被可用现金按 trade_date 扣减
        # 却不满足在途口径的 confirmed 前提，等于账上凭空少一笔钱（§3.1.2）
        assert cash_leg.status == "confirmed"

        # #493: confirm → unconfirm 后买入扣款腿保持 confirmed，基金腿回 pending
        # 并按 confirm_days 重算确认日；CASH sell 腿的 T 日扣款日期保持不变
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200
        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 200
        test_db.expire_all()
        fund_leg = test_db.query(Trade).get(trade_id)
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == tg, Trade.id != trade_id
        ).first()
        assert fund_leg.status == "pending" and cash_leg.status == "confirmed"
        assert cash_leg.trade_date == date(2025, 10, 8)
        # #493: 买入扣款腿的现金日恒为下单日 T（不从基金确认日推导）；
        # 基金腿 unconfirm 后按 confirm_days 重算期望确认日
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
        # status 字段被 schema 忽略，基金腿保持 pending；
        # #493：买入扣款腿创建即 confirmed（现金当日实扣），不随基金腿回退
        assert fund_leg.status == "pending"
        assert cash_leg.status == "confirmed"

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


class TestProductInactiveGate:
    """issue #856：停用产品禁止新建调仓（买/卖同挡，PRODUCT_INACTIVE）；
    闸门只在 create_trade 单点——存量 pending 的 confirm/cancel/PUT 不受影响，重新启用即恢复。"""

    def _setup_inactive(self, test_db, tag, trade_type="buy"):
        port, prod, plat = f"IA_P_{tag}", f"ETF_IA_{tag}", f"IA_PLAT_{tag}"
        create_portfolio(test_db, code=port, status="active")
        create_product(test_db, code=prod, market="CN_EXCHANGE", product_type="ETF",
                       asset_class_code="ASSET_STOCK", confirm_days=0, is_active=False)
        create_platform(test_db, code=plat)
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        return port, prod, plat

    def _buy_body(self, port, prod, plat, trade_type="buy"):
        return {
            "portfolio_code": port, "product_code": prod, "market": "CN_EXCHANGE",
            "trade_type": trade_type, "amount": 10000.0, "price": 1.5,
            "platform_code": plat, "trade_date": "2025-10-06",
        }

    def test_buy_blocked(self, client, admin_headers, test_db):
        """停用产品买入 → 422 PRODUCT_INACTIVE，中文 message（闸门在现金检查前，无需资金）"""
        port, prod, plat = self._setup_inactive(test_db, "B")
        resp = client.post("/api/trades", json=self._buy_body(port, prod, plat), headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCT_INACTIVE"
        assert "已停用" in resp.json()["detail"]["message"]

    def test_sell_blocked(self, client, admin_headers, test_db):
        """停用产品卖出 → 422 PRODUCT_INACTIVE（闸门在持仓检查前，买/卖同挡）"""
        port, prod, plat = self._setup_inactive(test_db, "S")
        resp = client.post(
            "/api/trades", json=self._buy_body(port, prod, plat, "sell"), headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCT_INACTIVE"

    def _pending_buy(self, test_db, client, admin_headers, tag):
        """产品 active 时经 API 建一笔 pending 买入（含配对现金腿），返回 (trade_id, product_code)"""
        port, prod, plat = f"IA_P_{tag}", f"ETF_IA_{tag}", f"IA_PLAT_{tag}"
        create_portfolio(test_db, code=port, status="active")
        create_product(test_db, code=prod, market="CN_EXCHANGE", product_type="ETF",
                       asset_class_code="ASSET_STOCK", confirm_days=0)
        create_platform(test_db, code=plat)
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        create_trade(test_db, port, "CASH", "", trade_type="buy", amount=50000.0, price=None,
                     platform_code=plat, trade_date=date(2025, 10, 3),
                     confirm_date=date(2025, 10, 3), status="confirmed")
        resp = client.post("/api/trades", json=self._buy_body(port, prod, plat), headers=admin_headers)
        assert resp.status_code in (200, 201), resp.json()
        return resp.json()["id"], prod

    def test_pending_cancel_unaffected(self, client, admin_headers, test_db):
        # 用场外 OEF：场内 cancel 被既有规则 CANNOT_CANCEL_EXCHANGE 挡（与 #856 无关），
        # 唯有场外能真正走通 cancel 路径，验证停用不影响存量 pending 的取消。
        port, prod, plat = "IA_P_CAN", "FUND_IA_CAN", "IA_PLAT_CAN"
        create_portfolio(test_db, code=port, status="active")
        create_product(test_db, code=prod, market="CN_OTC", product_type="OEF",
                       asset_class_code="ASSET_STOCK", confirm_days=1)
        create_platform(test_db, code=plat)
        ensure_trading_day(test_db, date(2025, 10, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 10, 7), is_open=True)
        create_trade(test_db, port, "CASH", "", trade_type="buy", amount=50000.0, price=None,
                     platform_code=plat, trade_date=date(2025, 10, 3),
                     confirm_date=date(2025, 10, 3), status="confirmed")
        created = client.post("/api/trades", json={
            "portfolio_code": port, "product_code": prod, "market": "CN_OTC",
            "trade_type": "buy", "amount": 10000.0, "platform_code": plat,
            "trade_date": "2025-10-06",
        }, headers=admin_headers)
        assert created.status_code in (200, 201), created.json()
        trade_id = created.json()["id"]
        client.put(f"/api/products/{prod}/CN_OTC", json={"is_active": False}, headers=admin_headers)
        resp = client.post(f"/api/trades/{trade_id}/cancel", headers=admin_headers)
        assert resp.status_code == 200, resp.json()

    def test_pending_update_unaffected(self, client, admin_headers, test_db):
        trade_id, prod = self._pending_buy(test_db, client, admin_headers, "UPD")
        client.put(f"/api/products/{prod}/CN_EXCHANGE", json={"is_active": False}, headers=admin_headers)
        resp = client.put(f"/api/trades/{trade_id}", json={"notes": "停用后编辑"}, headers=admin_headers)
        assert resp.status_code == 200, resp.json()
        assert resp.json()["notes"] == "停用后编辑"

    def test_pending_confirm_unaffected(self, client, admin_headers, test_db):
        trade_id, prod = self._pending_buy(test_db, client, admin_headers, "CONF")
        deact = client.put(f"/api/products/{prod}/CN_EXCHANGE", json={"is_active": False}, headers=admin_headers)
        assert deact.status_code == 200
        resp = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, resp.json()

    def test_reenable_restores_create(self, client, admin_headers, test_db):
        """停用挡买入；重新启用后同一买入恢复成功（停用可逆，闸门只读当前 is_active）"""
        port, prod, plat = self._setup_inactive(test_db, "R")
        create_trade(test_db, port, "CASH", "", trade_type="buy", amount=50000.0, price=None,
                     platform_code=plat, trade_date=date(2025, 10, 3),
                     confirm_date=date(2025, 10, 3), status="confirmed")
        body = self._buy_body(port, prod, plat)
        blocked = client.post("/api/trades", json=body, headers=admin_headers)
        assert blocked.status_code == 422
        assert blocked.json()["detail"]["error"] == "PRODUCT_INACTIVE"
        client.put(f"/api/products/{prod}/CN_EXCHANGE", json={"is_active": True}, headers=admin_headers)
        ok = client.post("/api/trades", json=body, headers=admin_headers)
        assert ok.status_code in (200, 201), ok.json()
