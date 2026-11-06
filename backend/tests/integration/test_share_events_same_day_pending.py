"""#766：同日 dividend_pending 的闸门放行、派生在途与确认链路。

检测器对同日公告（`ex_date == record_date`）建 `dividend_pending`（方式未定）。
为破除「闸门阻断除息日快照 ↔ 确认需除息日基数快照」的死锁：
- 闸门对同日 `dividend_pending` 放行除息日快照，从 D+1 起阻断；
- 除息日起派生现金在途对冲净值缺口（金额与现金/再投同源），确认切换类型后退出。

本文件钉住这三处行为，不重复覆盖非同日 pending（见 test_share_events_cash_same_day.py
与 test_share_events_reinvest_receivable.py）。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.share_change_event import ShareChangeEvent
from app.services.reinvest_receivable import list_cash_dividend_in_transit
from app.services.share_change_event_service import (
    DividendSelection,
    confirm_share_change_event,
    create_share_change_event,
)
from app.services.snapshot_service import _check_share_change_events
from app.utils.quantize import quantize_amount
from tests.factories import (
    create_investor_holding,
    create_platform,
    create_portfolio,
    create_position_snapshot,
    create_price_record,
    create_product,
    create_subscription,
    create_trade,
    create_value_snapshot,
    ensure_trading_day,
)

MARKET = "CN_OTC"
BASE = date(2026, 10, 16)    # 基线快照日（周五）
EX = date(2026, 10, 19)      # 除息日 = 权益登记日（周一）
MID = date(2026, 10, 20)     # 除息日后一交易日（周二）
PAY = date(2026, 10, 21)     # 到账/确认日（周三）

NAV = Decimal("1.5")
SHARES = Decimal("1000")
DIV_CASH = Decimal("0.05")
CASH_AMOUNT = quantize_amount(SHARES * DIV_CASH)  # 50.00


def _env(db, suffix):
    port, plat, fund = f"S766_{suffix}", f"SP_{suffix}", f"SF_{suffix}"
    capital = quantize_amount(SHARES * NAV)

    create_portfolio(db, code=port, status="active")
    create_platform(db, code=plat)
    create_product(db, code=fund, market=MARKET, product_type="OEF", confirm_days=0)
    for day in (BASE, EX, MID, PAY):
        ensure_trading_day(db, day, is_open=True)
    create_position_snapshot(
        db, port, fund, MARKET, snapshot_date=BASE, platform_code=plat,
        shares=float(SHARES), market_value=float(SHARES * NAV),
        unit_price=float(NAV), cost_price=float(NAV),
    )
    create_position_snapshot(
        db, port, "CASH", "", snapshot_date=BASE, platform_code=plat,
        cash_amount=0, market_value=0,
    )
    create_value_snapshot(db, port, BASE, total_value=float(SHARES * NAV),
                          total_shares=float(capital), unit_price=1)
    create_investor_holding(db, port, "VIEWER", BASE, shares=float(capital))
    create_subscription(
        db, port, "VIEWER", amount=float(capital), shares=float(capital), unit_price=1,
        platform_code=plat, apply_date=date(2026, 10, 15), confirm_date=BASE, status="confirmed",
    )
    for day in (BASE, EX, MID, PAY):
        create_price_record(db, fund, MARKET, day, float(NAV))
    create_trade(
        db, port, "CASH", "", trade_type="buy", platform_code=plat,
        trade_date=date(2026, 10, 15), confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital), price=1,
        transfer_group=f"sub_s766_{suffix}",
    )
    group = f"rebal_s766_{suffix}"
    create_trade(
        db, port, "CASH", "", trade_type="sell", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital), price=1, transfer_group=group,
    )
    create_trade(
        db, port, fund, MARKET, trade_type="buy", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital),
        price=float(NAV), shares=float(SHARES), transfer_group=group,
    )
    db.commit()
    return port, plat, fund


def _pending_event(db, port, fund, plat):
    """模拟检测器建的同日 dividend_pending（直造 ORM，不经服务层）。"""
    event = ShareChangeEvent(
        portfolio_code=port, product_code=fund, market=MARKET, platform_code=plat,
        event_type="dividend_pending", status="pending", event_source="tushare",
        ex_date=EX, entitlement_date=EX, div_cash=DIV_CASH,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


class TestSameDayPendingGate:
    def test_ex_date_snapshot_passes_gate(self, test_db):
        port, plat, fund = _env(test_db, "GATE")
        _pending_event(test_db, port, fund, plat)

        result = _check_share_change_events(test_db, port, EX)
        assert result["status"] == "passed"

    def test_day_after_ex_is_blocked(self, test_db):
        port, plat, fund = _env(test_db, "GATE2")
        _pending_event(test_db, port, fund, plat)

        result = _check_share_change_events(test_db, port, MID)
        assert result["status"] == "failed"
        assert "分红方式待定" in result["message"]


class TestSameDayPendingInTransit:
    def test_cash_in_transit_applied_at_ex_date(self, test_db):
        port, plat, fund = _env(test_db, "TRANSIT")
        _pending_event(test_db, port, fund, plat)

        # 快照生成时传入内存持仓（除息日持仓尚未落库，与 _compute_in_transit_amounts 同口径）
        live = {(fund, MARKET, plat): {"shares": float(SHARES)}}
        rows = list_cash_dividend_in_transit(test_db, port, EX, live_positions=live)
        assert len(rows) == 1
        assert rows[0].amount == CASH_AMOUNT
        assert rows[0].cash_pay_date is None  # 方式未定，无上界

    def test_non_same_day_pending_not_in_cash_transit(self, test_db):
        """非同日 dividend_pending 仍不派生现金在途（走原闸门阻断路径）。"""
        port, plat, fund = _env(test_db, "NONSAME")
        event = ShareChangeEvent(
            portfolio_code=port, product_code=fund, market=MARKET, platform_code=plat,
            event_type="dividend_pending", status="pending", event_source="tushare",
            ex_date=MID, entitlement_date=EX, div_cash=DIV_CASH,
        )
        test_db.add(event)
        test_db.commit()

        assert list_cash_dividend_in_transit(test_db, port, MID) == []


class TestSameDayPendingConfirmation:
    def _ex_position(self, db, port, fund, plat):
        """模拟除息日快照已生成（闸门放行后），写入除息日持仓快照供确认取基数。"""
        create_position_snapshot(
            db, port, fund, MARKET, snapshot_date=EX, platform_code=plat,
            shares=float(SHARES), market_value=float(SHARES * NAV),
            unit_price=float(NAV), cost_price=float(NAV),
        )
        create_position_snapshot(
            db, port, "CASH", "", snapshot_date=EX, platform_code=plat,
            cash_amount=0, market_value=0,
        )
        db.commit()

    def test_confirm_as_cash(self, test_db):
        port, plat, fund = _env(test_db, "CASH")
        event = _pending_event(test_db, port, fund, plat)
        self._ex_position(test_db, port, fund, plat)

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="cash_dividend", cash_pay_date=PAY),
        )
        refreshed = test_db.get(ShareChangeEvent, event.id)
        assert refreshed.event_type == "cash_dividend"
        assert refreshed.cash_pay_date == PAY

    def test_confirm_as_reinvest(self, test_db):
        port, plat, fund = _env(test_db, "REINV")
        event = _pending_event(test_db, port, fund, plat)
        self._ex_position(test_db, port, fund, plat)

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="reinvest_dividend", share_confirm_date=PAY),
        )
        refreshed = test_db.get(ShareChangeEvent, event.id)
        assert refreshed.event_type == "reinvest_dividend"
        assert refreshed.share_confirm_date == PAY
