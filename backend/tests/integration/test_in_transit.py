# ============================================================================
# 集成测试：在途资金模型 (Issue #93)
# ============================================================================
# 覆盖 IN_TRANSIT_BUY / IN_TRANSIT_SELL 虚拟产品的完整生命周期：
# - 买入在途：T 日扣款 → T+N 基金确认 → 在途消失、基金出现
# - 卖出在途：T+N 基金卖出确认 → T+M 现金到账 → 在途消失、CASH 增加
# - 无 cash_confirm_date 的卖出：无延迟窗口，不产生在途
# - total_value 连续性：在途金额填平价值缺口
# - 跨天现金转移：cross_day 产生在途 → confirm_cash_transfer 消除在途
# ============================================================================

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot, Trade
from tests.factories import (
    create_portfolio,
    create_product,
    create_platform,
    create_trade,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
    ensure_trading_day,
    create_price_record,
)


# ---------------------------------------------------------------------------
# 日期常量（基于 conftest 交易日历：工作日为交易日）
# D0 = 2025-06-06 (周五)
# T  = 2025-06-09 (周一) — 下一交易日
# T1 = 2025-06-10 (周二)
# T2 = 2025-06-11 (周三)
# T3 = 2025-06-12 (周四)
# ---------------------------------------------------------------------------

D0 = date(2025, 6, 6)
T = date(2025, 6, 9)
T1 = date(2025, 6, 10)
T2 = date(2025, 6, 11)
T3 = date(2025, 6, 12)

FUND_PRICE = 1.25


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _ensure_all_trading_days(db):
    """确保 T ~ T3 均为交易日"""
    for d in [D0, T, T1, T2, T3]:
        ensure_trading_day(db, d, is_open=True)


def _setup_cash_snapshot(db, portfolio_code, snapshot_date, amount, platform_code):
    """创建仅含 CASH 持仓的前日完整三表快照"""
    create_position_snapshot(
        db, portfolio_code, "CASH", "",
        snapshot_date=snapshot_date,
        cash_amount=amount, unit_price=None, cost_price=None,
        market_value=amount, platform_code=platform_code,
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=amount, total_shares=amount, unit_price=1.0,
    )
    create_investor_holding(
        db, portfolio_code, "VIEWER", snapshot_date, shares=amount,
    )


def _setup_full_snapshot(db, portfolio_code, snapshot_date,
                         cash_amount, fund_code, fund_market,
                         fund_shares, platform_code, fund_price=FUND_PRICE):
    """创建含 CASH + 基金持仓的前日完整三表快照"""
    fund_value = fund_shares * fund_price
    total_value = cash_amount + fund_value

    create_position_snapshot(
        db, portfolio_code, "CASH", "",
        snapshot_date=snapshot_date,
        cash_amount=cash_amount, unit_price=None, cost_price=None,
        market_value=cash_amount, platform_code=platform_code,
    )
    create_position_snapshot(
        db, portfolio_code, fund_code, fund_market,
        snapshot_date=snapshot_date,
        shares=fund_shares, unit_price=fund_price, cost_price=fund_price,
        market_value=fund_value, platform_code=platform_code,
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=total_value, total_shares=total_value, unit_price=1.0,
    )
    create_investor_holding(
        db, portfolio_code, "VIEWER", snapshot_date, shares=total_value,
    )


def _gen_snapshot(client, headers, portfolio_code, target_date):
    """调用快照生成 API 并断言成功"""
    resp = client.post(
        "/api/snapshots/generate",
        json={"portfolio_code": portfolio_code, "target_date": target_date.isoformat()},
        headers=headers,
    )
    assert resp.status_code == 200, f"Snapshot gen failed: {resp.status_code} {resp.json()}"
    assert resp.json()["success"] is True
    return resp


def _get_positions(db, portfolio_code, snapshot_date):
    """查询指定日期的持仓快照"""
    return db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == portfolio_code,
        PortfolioPosition.snapshot_date == snapshot_date,
    ).all()


def _get_value_snapshot(db, portfolio_code, snapshot_date):
    """查询指定日期的市值快照"""
    return db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == portfolio_code,
        PortfolioValueSnapshot.snapshot_date == snapshot_date,
    ).first()


def _create_paired_buy_trades(db, portfolio_code, platform_code,
                              fund_code, fund_market,
                              trade_date, fund_confirm_date,
                              amount, price, shares,
                              transfer_group=None):
    """创建已确认的基金买入 + 配对 CASH 卖出（T 日扣款）"""
    tg = transfer_group or f"rebal_{uuid.uuid4().hex[:12]}"

    # 基金买入腿：confirm_date = T+N
    create_trade(
        db, portfolio_code, fund_code, fund_market,
        trade_type="buy", amount=amount, shares=shares, price=price,
        platform_code=platform_code, trade_date=trade_date,
        confirm_date=fund_confirm_date, status="confirmed",
        actual_amount=amount, transfer_group=tg,
    )
    # CASH 卖出腿：confirm_date = trade_date（T 日扣款）
    create_trade(
        db, portfolio_code, "CASH", "",
        trade_type="sell", amount=amount, price=1.0,
        platform_code=platform_code, trade_date=trade_date,
        confirm_date=trade_date, status="confirmed",
        actual_amount=amount, transfer_group=tg,
    )
    return tg


def _create_paired_sell_trades(db, portfolio_code, platform_code,
                               fund_code, fund_market,
                               trade_date, fund_confirm_date,
                               cash_confirm_date,
                               shares, price, amount,
                               transfer_group=None):
    """创建已确认的基金卖出 + 配对 CASH 买入（到账日 = cash_confirm_date）"""
    tg = transfer_group or f"rebal_{uuid.uuid4().hex[:12]}"

    # 基金卖出腿：confirm_date = T+N
    create_trade(
        db, portfolio_code, fund_code, fund_market,
        trade_type="sell", amount=amount, shares=shares, price=price,
        platform_code=platform_code, trade_date=trade_date,
        confirm_date=fund_confirm_date, status="confirmed",
        actual_amount=amount, transfer_group=tg,
    )
    # CASH 买入腿：confirm_date = cash_confirm_date（到账日）
    create_trade(
        db, portfolio_code, "CASH", "",
        trade_type="buy", amount=amount, price=1.0,
        platform_code=platform_code, trade_date=trade_date,
        confirm_date=cash_confirm_date, status="confirmed",
        actual_amount=amount, transfer_group=tg,
    )
    return tg


# ============================================================================
# Scenario 1 & 2: 买入在途生命周期
# ============================================================================

class TestBuyInTransit:
    """买入在途：T 日扣款产生 IN_TRANSIT_BUY，T+1 基金确认后消失"""

    FUND_CODE = "FUND_IT_BUY"
    FUND_MARKET = "CN_OTC"
    PLATFORM = "IT_BUY_PLAT"
    BUY_AMOUNT = 10000.0
    BUY_SHARES = 8000.0  # 10000 / 1.25

    def _setup(self, db, portfolio_code="IT_BUY_P1"):
        _ensure_all_trading_days(db)
        create_portfolio(db, code=portfolio_code, status="active")
        create_product(db, code=self.FUND_CODE, market=self.FUND_MARKET,
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1, is_qdii=False)
        create_platform(db, code=self.PLATFORM)
        # D0 快照：CASH = 50000
        _setup_cash_snapshot(db, portfolio_code, D0, 50000.0, self.PLATFORM)
        # T 日净值
        create_price_record(db, self.FUND_CODE, self.FUND_MARKET, T, FUND_PRICE)
        # T+1 日净值（#96 严格匹配：基金持仓出现于 T+1，快照要求当日净值，不再回退）
        create_price_record(db, self.FUND_CODE, self.FUND_MARKET, T1, FUND_PRICE)
        # 创建已确认配对交易：基金买入 T+1 确认，CASH 卖出 T 日扣款
        _create_paired_buy_trades(
            db, portfolio_code, self.PLATFORM,
            self.FUND_CODE, self.FUND_MARKET,
            trade_date=T, fund_confirm_date=T1,
            amount=self.BUY_AMOUNT, price=FUND_PRICE, shares=self.BUY_SHARES,
        )

    def test_t_day_snapshot_shows_in_transit_buy(self, client, admin_headers, test_db):
        """Scenario 1: T 日快照含 IN_TRANSIT_BUY，CASH 已扣减，基金未出现"""
        port = "IT_BUY_P1"
        self._setup(test_db, port)

        _gen_snapshot(client, admin_headers, port, T)
        test_db.expire_all()

        positions = _get_positions(test_db, port, T)

        # IN_TRANSIT_BUY 存在且 cash_amount > 0
        in_transit = [p for p in positions if p.product_code == "IN_TRANSIT_BUY"]
        assert len(in_transit) == 1
        assert Decimal(str(in_transit[0].cash_amount)) == Decimal(str(self.BUY_AMOUNT))

        # CASH 已扣减
        cash = [p for p in positions if p.product_code == "CASH"]
        assert len(cash) == 1
        assert Decimal(str(cash[0].cash_amount)) == Decimal("50000") - Decimal(str(self.BUY_AMOUNT))

        # 基金未出现
        fund = [p for p in positions if p.product_code == self.FUND_CODE]
        assert len(fund) == 0

    def test_t1_day_snapshot_in_transit_disappears_fund_appears(self, client, admin_headers, test_db):
        """Scenario 2: T+1 快照无 IN_TRANSIT_BUY，基金持仓出现"""
        port = "IT_BUY_P2"
        self._setup(test_db, port)

        _gen_snapshot(client, admin_headers, port, T)
        _gen_snapshot(client, admin_headers, port, T1)
        test_db.expire_all()

        positions = _get_positions(test_db, port, T1)

        # 无 IN_TRANSIT_BUY
        in_transit = [p for p in positions if p.product_code == "IN_TRANSIT_BUY"]
        assert len(in_transit) == 0

        # 基金持仓出现
        fund = [p for p in positions if p.product_code == self.FUND_CODE]
        assert len(fund) == 1
        assert Decimal(str(fund[0].shares)) == Decimal(str(self.BUY_SHARES))

        # CASH 仍为扣减后金额
        cash = [p for p in positions if p.product_code == "CASH"]
        assert len(cash) == 1
        assert Decimal(str(cash[0].cash_amount)) == Decimal("50000") - Decimal(str(self.BUY_AMOUNT))

    def test_total_value_continuity(self, client, admin_headers, test_db):
        """Scenario 6: total_value 在 T 和 T+1 保持连续，无价值缺口"""
        port = "IT_BUY_P3"
        self._setup(test_db, port)

        _gen_snapshot(client, admin_headers, port, T)
        _gen_snapshot(client, admin_headers, port, T1)
        test_db.expire_all()

        snap_t = _get_value_snapshot(test_db, port, T)
        snap_t1 = _get_value_snapshot(test_db, port, T1)

        # T: CASH(40000) + IN_TRANSIT_BUY(10000) = 50000
        assert Decimal(str(snap_t.total_value)) == Decimal("50000")
        assert Decimal(str(snap_t.in_transit_total)) == Decimal(str(self.BUY_AMOUNT))

        # T+1: CASH(40000) + 基金市值(8000*1.25=10000) = 50000
        assert Decimal(str(snap_t1.total_value)) == Decimal("50000")
        assert Decimal(str(snap_t1.in_transit_total)) == Decimal("0")


# ============================================================================
# Scenario 3 & 4: 卖出在途生命周期
# ============================================================================

class TestSellInTransit:
    """卖出在途：T+1 基金卖出确认，T+3 现金到账，中间产生 IN_TRANSIT_SELL"""

    FUND_CODE = "FUND_IT_SELL"
    FUND_MARKET = "CN_OTC"
    PLATFORM = "IT_SELL_PLAT"
    FUND_SHARES = 10000.0
    SELL_SHARES = 10000.0
    SELL_AMOUNT = 12500.0  # 10000 * 1.25

    def _setup(self, db, portfolio_code="IT_SELL_P1"):
        _ensure_all_trading_days(db)
        create_portfolio(db, code=portfolio_code, status="active")
        create_product(db, code=self.FUND_CODE, market=self.FUND_MARKET,
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1, is_qdii=False)
        create_platform(db, code=self.PLATFORM)
        # D0 快照：CASH=10000 + 基金 10000 份
        _setup_full_snapshot(db, portfolio_code, D0,
                             cash_amount=10000.0,
                             fund_code=self.FUND_CODE,
                             fund_market=self.FUND_MARKET,
                             fund_shares=self.FUND_SHARES,
                             platform_code=self.PLATFORM)
        # 净值记录
        create_price_record(db, self.FUND_CODE, self.FUND_MARKET, T, FUND_PRICE)
        create_price_record(db, self.FUND_CODE, self.FUND_MARKET, T1, FUND_PRICE)
        create_price_record(db, self.FUND_CODE, self.FUND_MARKET, T2, FUND_PRICE)
        create_price_record(db, self.FUND_CODE, self.FUND_MARKET, T3, FUND_PRICE)
        # 创建已确认配对交易：基金卖出 T+1 确认，CASH 买入 T+3 到账
        _create_paired_sell_trades(
            db, portfolio_code, self.PLATFORM,
            self.FUND_CODE, self.FUND_MARKET,
            trade_date=T, fund_confirm_date=T1,
            cash_confirm_date=T3,
            shares=self.SELL_SHARES, price=FUND_PRICE,
            amount=self.SELL_AMOUNT,
        )

    def test_t1_day_snapshot_shows_in_transit_sell(self, client, admin_headers, test_db):
        """Scenario 3: T+1 快照含 IN_TRANSIT_SELL，基金份额归零，CASH 未增加"""
        port = "IT_SELL_P1"
        self._setup(test_db, port)

        _gen_snapshot(client, admin_headers, port, T)
        _gen_snapshot(client, admin_headers, port, T1)
        test_db.expire_all()

        positions = _get_positions(test_db, port, T1)

        # IN_TRANSIT_SELL 存在且 cash_amount > 0
        in_transit = [p for p in positions if p.product_code == "IN_TRANSIT_SELL"]
        assert len(in_transit) == 1
        assert Decimal(str(in_transit[0].cash_amount)) == Decimal(str(self.SELL_AMOUNT))

        # 基金份额已扣减至 0（持仓行被跳过）
        fund = [p for p in positions if p.product_code == self.FUND_CODE]
        assert len(fund) == 0

        # CASH 未增加（confirm_date=T3 > T1）
        cash = [p for p in positions if p.product_code == "CASH"]
        assert len(cash) == 1
        assert Decimal(str(cash[0].cash_amount)) == Decimal("10000")

    def test_t3_day_snapshot_in_transit_disappears_cash_increases(self, client, admin_headers, test_db):
        """Scenario 4: T+3 快照无 IN_TRANSIT_SELL，CASH 已增加"""
        port = "IT_SELL_P2"
        self._setup(test_db, port)

        _gen_snapshot(client, admin_headers, port, T)
        _gen_snapshot(client, admin_headers, port, T1)
        _gen_snapshot(client, admin_headers, port, T2)
        _gen_snapshot(client, admin_headers, port, T3)
        test_db.expire_all()

        positions = _get_positions(test_db, port, T3)

        # 无 IN_TRANSIT_SELL
        in_transit = [p for p in positions if p.product_code == "IN_TRANSIT_SELL"]
        assert len(in_transit) == 0

        # CASH 已增加
        cash = [p for p in positions if p.product_code == "CASH"]
        assert len(cash) == 1
        assert Decimal(str(cash[0].cash_amount)) == Decimal("10000") + Decimal(str(self.SELL_AMOUNT))

    def test_total_value_continuity(self, client, admin_headers, test_db):
        """Scenario 6: total_value 在 T+1 和 T+3 保持连续，无价值缺口"""
        port = "IT_SELL_P3"
        self._setup(test_db, port)

        _gen_snapshot(client, admin_headers, port, T)
        _gen_snapshot(client, admin_headers, port, T1)
        _gen_snapshot(client, admin_headers, port, T2)
        _gen_snapshot(client, admin_headers, port, T3)
        test_db.expire_all()

        d0_total = Decimal("22500")  # 10000 + 10000*1.25
        snap_t1 = _get_value_snapshot(test_db, port, T1)
        snap_t3 = _get_value_snapshot(test_db, port, T3)

        # T+1: CASH(10000) + IN_TRANSIT_SELL(12500) = 22500
        assert Decimal(str(snap_t1.total_value)) == d0_total
        assert Decimal(str(snap_t1.in_transit_total)) == Decimal(str(self.SELL_AMOUNT))

        # T+3: CASH(10000+12500=22500) = 22500
        assert Decimal(str(snap_t3.total_value)) == d0_total
        assert Decimal(str(snap_t3.in_transit_total)) == Decimal("0")


# ============================================================================
# Scenario 5: 无 cash_confirm_date 的卖出 — 不产生在途
# ============================================================================

class TestSellNoCashConfirmDate:
    """卖出无 cash_confirm_date：CASH 到账日 = 基金确认日，无延迟窗口"""

    FUND_CODE = "FUND_IT_NCD"
    FUND_MARKET = "CN_OTC"
    PLATFORM = "IT_NCD_PLAT"
    FUND_SHARES = 10000.0
    SELL_SHARES = 10000.0
    SELL_AMOUNT = 12500.0

    def test_no_in_transit_without_cash_confirm_date(self, client, admin_headers, test_db):
        """Scenario 5: 无 cash_confirm_date → CASH confirm_date = 基金确认日，无在途"""
        port = "IT_NCD_P1"
        _ensure_all_trading_days(test_db)
        create_portfolio(test_db, code=port, status="active")
        create_product(test_db, code=self.FUND_CODE, market=self.FUND_MARKET,
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1, is_qdii=False)
        create_platform(test_db, code=self.PLATFORM)
        _setup_full_snapshot(test_db, port, D0,
                              cash_amount=10000.0,
                              fund_code=self.FUND_CODE,
                              fund_market=self.FUND_MARKET,
                              fund_shares=self.FUND_SHARES,
                              platform_code=self.PLATFORM)
        create_price_record(test_db, self.FUND_CODE, self.FUND_MARKET, T, FUND_PRICE)
        create_price_record(test_db, self.FUND_CODE, self.FUND_MARKET, T1, FUND_PRICE)

        # 基金卖出 T+1 确认，CASH 买入也是 T+1 到账（无延迟）
        _create_paired_sell_trades(
            test_db, port, self.PLATFORM,
            self.FUND_CODE, self.FUND_MARKET,
            trade_date=T, fund_confirm_date=T1,
            cash_confirm_date=T1,  # 与基金确认日一致，无延迟
            shares=self.SELL_SHARES, price=FUND_PRICE,
            amount=self.SELL_AMOUNT,
        )

        _gen_snapshot(client, admin_headers, port, T)
        _gen_snapshot(client, admin_headers, port, T1)
        test_db.expire_all()

        positions = _get_positions(test_db, port, T1)

        # 无 IN_TRANSIT_SELL
        in_transit = [p for p in positions if p.product_code == "IN_TRANSIT_SELL"]
        assert len(in_transit) == 0

        # CASH 已增加
        cash = [p for p in positions if p.product_code == "CASH"]
        assert len(cash) == 1
        assert Decimal(str(cash[0].cash_amount)) == Decimal("10000") + Decimal(str(self.SELL_AMOUNT))

        # total_value 连续
        snap = _get_value_snapshot(test_db, port, T1)
        assert Decimal(str(snap.total_value)) == Decimal("22500")
        assert Decimal(str(snap.in_transit_total)) == Decimal("0")


# ============================================================================
# Scenario 7 & 8: 跨天现金转移在途
# ============================================================================

class TestCrossDayCashTransfer:
    """跨天现金转移：cross_day 产生 IN_TRANSIT_BUY，confirm 后消除"""

    PLATFORM_A = "IT_CT_PLAT_A"
    PLATFORM_B = "IT_CT_PLAT_B"
    TRANSFER_AMOUNT = 20000.0
    INITIAL_CASH = 50000.0

    def _setup_transfer(self, db, client, headers, portfolio_code, cross_day=True):
        _ensure_all_trading_days(db)
        create_portfolio(db, code=portfolio_code, status="active")
        create_platform(db, code=self.PLATFORM_A)
        create_platform(db, code=self.PLATFORM_B)
        # D0 快照：CASH = 50000 at platform A
        _setup_cash_snapshot(db, portfolio_code, D0, self.INITIAL_CASH, self.PLATFORM_A)

        # 通过 API 创建跨天现金转移
        resp = client.post(
            f"/api/portfolios/{portfolio_code}/cash-transfer",
            json={
                "from_platform": self.PLATFORM_A,
                "to_platform": self.PLATFORM_B,
                "amount": self.TRANSFER_AMOUNT,
                "cross_day": cross_day,
                "transfer_date": T.isoformat(),
            },
            headers=headers,
        )
        assert resp.status_code == 200, f"Transfer failed: {resp.status_code} {resp.json()}"
        return resp.json()

    def test_cross_day_transfer_creates_in_transit_buy(self, client, admin_headers, test_db):
        """Scenario 7: 跨天转移后 T 日快照含 IN_TRANSIT_BUY at platform B"""
        port = "IT_CT_P1"
        result = self._setup_transfer(test_db, client, admin_headers, port)

        # 验证非对称状态
        assert result["sell_status"] == "confirmed"
        assert result["buy_status"] == "pending"

        # 查询交易记录验证
        test_db.expire_all()
        tg = result["transfer_group"]
        sell_trade = test_db.query(Trade).filter(
            Trade.transfer_group == tg,
            Trade.trade_type == "sell",
        ).first()
        buy_trade = test_db.query(Trade).filter(
            Trade.transfer_group == tg,
            Trade.trade_type == "buy",
        ).first()
        assert sell_trade.status == "confirmed"
        assert buy_trade.status == "pending"

        # 生成 T 日快照
        _gen_snapshot(client, admin_headers, port, T)
        test_db.expire_all()

        positions = _get_positions(test_db, port, T)

        # IN_TRANSIT_BUY at platform B
        in_transit = [p for p in positions if p.product_code == "IN_TRANSIT_BUY"]
        assert len(in_transit) == 1
        assert in_transit[0].platform_code == self.PLATFORM_B
        assert Decimal(str(in_transit[0].cash_amount)) == Decimal(str(self.TRANSFER_AMOUNT))

        # CASH at platform A 已扣减
        cash_a = [p for p in positions
                  if p.product_code == "CASH" and p.platform_code == self.PLATFORM_A]
        assert len(cash_a) == 1
        assert Decimal(str(cash_a[0].cash_amount)) == \
            Decimal(str(self.INITIAL_CASH)) - Decimal(str(self.TRANSFER_AMOUNT))

        # CASH at platform B 不存在（buy 腿 pending，未确认）
        cash_b = [p for p in positions
                  if p.product_code == "CASH" and p.platform_code == self.PLATFORM_B]
        assert len(cash_b) == 0

        # total_value 连续
        snap = _get_value_snapshot(test_db, port, T)
        assert Decimal(str(snap.total_value)) == Decimal(str(self.INITIAL_CASH))
        assert Decimal(str(snap.in_transit_total)) == Decimal(str(self.TRANSFER_AMOUNT))

    def test_confirm_cash_transfer_confirms_pending_legs(self, client, admin_headers, test_db):
        """Scenario 8: confirm_cash_transfer 后 buy 腿确认，快照无在途、CASH 增加"""
        port = "IT_CT_P2"
        result = self._setup_transfer(test_db, client, admin_headers, port)
        tg = result["transfer_group"]

        # 生成 T 日快照（含在途）
        _gen_snapshot(client, admin_headers, port, T)

        # 确认跨天转移
        resp = client.post(
            f"/api/portfolios/{port}/cash-transfer/{tg}/confirm",
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Confirm failed: {resp.status_code} {resp.json()}"

        # 验证 buy 腿已确认
        test_db.expire_all()
        buy_trade = test_db.query(Trade).filter(
            Trade.transfer_group == tg,
            Trade.trade_type == "buy",
        ).first()
        assert buy_trade.status == "confirmed"

        # 生成 T+1 快照（到账日）
        _gen_snapshot(client, admin_headers, port, T1)
        test_db.expire_all()

        positions = _get_positions(test_db, port, T1)

        # 无 IN_TRANSIT_BUY
        in_transit = [p for p in positions if p.product_code == "IN_TRANSIT_BUY"]
        assert len(in_transit) == 0

        # CASH at platform B 已增加
        cash_b = [p for p in positions
                  if p.product_code == "CASH" and p.platform_code == self.PLATFORM_B]
        assert len(cash_b) == 1
        assert Decimal(str(cash_b[0].cash_amount)) == Decimal(str(self.TRANSFER_AMOUNT))

        # CASH at platform A 仍为扣减后金额
        cash_a = [p for p in positions
                  if p.product_code == "CASH" and p.platform_code == self.PLATFORM_A]
        assert len(cash_a) == 1
        assert Decimal(str(cash_a[0].cash_amount)) == \
            Decimal(str(self.INITIAL_CASH)) - Decimal(str(self.TRANSFER_AMOUNT))

        # total_value 连续
        snap = _get_value_snapshot(test_db, port, T1)
        assert Decimal(str(snap.total_value)) == Decimal(str(self.INITIAL_CASH))
        assert Decimal(str(snap.in_transit_total)) == Decimal("0")


class TestCashTransferListFiltering:
    PORTFOLIO = "IT_CT_LIST"
    OTHER_PORTFOLIO = "IT_CT_LIST_OTHER"
    PLATFORM_A = "IT_CT_LIST_A"
    PLATFORM_B = "IT_CT_LIST_B"
    TRANSFER_AMOUNT = 1234.56

    @pytest.fixture(scope="function")
    def cash_transfer_groups(self, test_db):
        create_portfolio(test_db, code=self.PORTFOLIO, status="active")
        create_portfolio(test_db, code=self.OTHER_PORTFOLIO, status="active")
        create_platform(test_db, code=self.PLATFORM_A)
        create_platform(test_db, code=self.PLATFORM_B)
        groups = [uuid.uuid4().hex[:12] for _ in range(51)]
        for index, group in enumerate(groups):
            created_at = datetime(2025, 6, 9, 9) + timedelta(minutes=index)
            for trade_type, platform, status, confirm_date in (
                ("sell", self.PLATFORM_A, "confirmed", T),
                ("buy", self.PLATFORM_B, "pending", T1),
            ):
                trade = create_trade(
                    test_db, self.PORTFOLIO, "CASH", "",
                    trade_type=trade_type, platform_code=platform,
                    amount=self.TRANSFER_AMOUNT, actual_amount=self.TRANSFER_AMOUNT,
                    price=1.0, fee=0.0, trade_date=T, confirm_date=confirm_date,
                    status=status, transfer_group=group,
                )
                trade.created_at = created_at
        test_db.flush()
        return groups

    def test_group_filter_precedes_pagination(self, client, admin_headers, cash_transfer_groups):
        url = f"/api/portfolios/{self.PORTFOLIO}/cash-transfers"
        target = cash_transfer_groups[0]
        first = client.get(url, params={"page": 1, "page_size": 50}, headers=admin_headers)
        assert first.status_code == 200, first.text
        first_data = first.json()
        assert first_data["total"] == 51
        assert first_data["page"] == 1
        assert first_data["page_size"] == 50
        assert len(first_data["items"]) == 50
        assert [item["transfer_group"] for item in first_data["items"]] == list(
            reversed(cash_transfer_groups[1:])
        )
        assert target not in {item["transfer_group"] for item in first_data["items"]}

        second = client.get(url, params={"page": 2, "page_size": 50}, headers=admin_headers)
        assert second.status_code == 200, second.text
        second_data = second.json()
        assert second_data["total"] == 51
        assert second_data["page"] == 2
        assert second_data["page_size"] == 50
        assert [item["transfer_group"] for item in second_data["items"]] == [target]

        filtered = client.get(
            url, params={"page": 1, "page_size": 50, "transfer_group": target},
            headers=admin_headers,
        )
        assert filtered.status_code == 200, filtered.text
        expected_item = {
            "transfer_group": target,
            "from_platform": self.PLATFORM_A,
            "to_platform": self.PLATFORM_B,
            "amount": self.TRANSFER_AMOUNT,
            "cross_day": True,
            "sell_status": "confirmed",
            "buy_status": "pending",
            "transfer_date": T.isoformat(),
            "sell_confirm_date": T.isoformat(),
            "buy_confirm_date": T1.isoformat(),
            "notes": None,
        }
        assert filtered.json() == {
            "items": [expected_item], "total": 1, "page": 1, "page_size": 50,
        }
        assert second_data["items"] == [expected_item]

    def test_default_page_size_unchanged(self, client, admin_headers, cash_transfer_groups):
        response = client.get(
            f"/api/portfolios/{self.PORTFOLIO}/cash-transfers", headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 51
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert len(data["items"]) == 20
        assert [item["transfer_group"] for item in data["items"]] == list(
            reversed(cash_transfer_groups)
        )[:20]

    @pytest.mark.parametrize("other_portfolio", [False, True])
    def test_unknown_or_other_portfolio_group_returns_empty(
        self, client, admin_headers, cash_transfer_groups, other_portfolio,
    ):
        portfolio = self.OTHER_PORTFOLIO if other_portfolio else self.PORTFOLIO
        group = cash_transfer_groups[0] if other_portfolio else "unknown_transfer_group"
        response = client.get(
            f"/api/portfolios/{portfolio}/cash-transfers",
            params={"transfer_group": group}, headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}

    @pytest.mark.parametrize("filtered", [False, True])
    def test_viewer_still_forbidden(self, client, viewer_headers, cash_transfer_groups, filtered):
        params = {"transfer_group": cash_transfer_groups[0]} if filtered else {}
        response = client.get(
            f"/api/portfolios/{self.PORTFOLIO}/cash-transfers",
            params=params, headers=viewer_headers,
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "FORBIDDEN"
