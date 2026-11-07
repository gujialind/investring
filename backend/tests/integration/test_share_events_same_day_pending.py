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

from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
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


def _env(db, suffix, ex_nav=None):
    """基线环境；`ex_nav` 用于模拟除息日起净值维持的除权后水平（默认与基线同价）。

    `ex_nav` 同时应用于 EX/MID/PAY：除息后净值不回升，MID/PAY 的市值缺口才是
    分红本身造成的，才能验证在途/应收的对冲连续性（#836 回归）。
    """
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
        day_nav = ex_nav if day != BASE and ex_nav is not None else NAV
        create_price_record(db, fund, MARKET, day, float(day_nav))
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
    def _ex_snapshot(self, db, port, fund, plat):
        """模拟闸门放行后的除息日快照完整态：持仓行（确认取基数）+ 净值行。

        净值行是关键——真实快照生成必写 PortfolioValueSnapshot，使
        get_latest_snapshot_date == EX；缺了它确认路径撞不到「ex_date <=
        最新快照日」判据，#766 评审 Blocker 正是这样漏网的。
        """
        create_position_snapshot(
            db, port, fund, MARKET, snapshot_date=EX, platform_code=plat,
            shares=float(SHARES), market_value=float(SHARES * NAV),
            unit_price=float(NAV), cost_price=float(NAV),
        )
        create_position_snapshot(
            db, port, "CASH", "", snapshot_date=EX, platform_code=plat,
            cash_amount=0, market_value=0,
        )
        create_value_snapshot(db, port, EX, total_value=float(SHARES * NAV),
                              unit_price=float(NAV), total_shares=float(SHARES))
        db.commit()

    def test_confirm_as_cash(self, test_db):
        port, plat, fund = _env(test_db, "CASH")
        event = _pending_event(test_db, port, fund, plat)
        self._ex_snapshot(test_db, port, fund, plat)

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
        self._ex_snapshot(test_db, port, fund, plat)

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="reinvest_dividend", share_confirm_date=PAY),
        )
        refreshed = test_db.get(ShareChangeEvent, event.id)
        assert refreshed.event_type == "reinvest_dividend"
        assert refreshed.share_confirm_date == PAY

    def test_confirm_without_ex_position_snapshot_rejected(self, test_db):
        """锁死「确认只能在快照后」：无除息日基数快照时报 MISSING_POSITION_SNAPSHOT。"""
        from app.services.exceptions import BusinessError
        port, plat, fund = _env(test_db, "NOBASE")
        event = _pending_event(test_db, port, fund, plat)

        with pytest.raises(BusinessError) as exc_info:
            confirm_share_change_event(
                test_db, event,
                selection=DividendSelection(event_type="cash_dividend", cash_pay_date=PAY),
            )
        assert exc_info.value.code == "MISSING_POSITION_SNAPSHOT"


class TestSameDayPendingFreeze:
    """除息日快照生成后，同日 pending 的改/取消被派生在途守卫冻结（#766 评审）。"""

    def _ex_snapshot(self, db, port, fund, plat):
        TestSameDayPendingConfirmation()._ex_snapshot(db, port, fund, plat)

    def test_update_div_cash_after_ex_snapshot_blocked(self, test_db):
        from app.services.exceptions import BusinessError
        from app.services.share_change_event_service import update_share_change_event
        port, plat, fund = _env(test_db, "FRZU")
        event = _pending_event(test_db, port, fund, plat)
        self._ex_snapshot(test_db, port, fund, plat)

        with pytest.raises(BusinessError) as exc_info:
            update_share_change_event(test_db, event, {"div_cash": Decimal("0.08")})
        assert exc_info.value.code == "SNAPSHOT_DEPENDENCY"

    def test_cancel_after_ex_snapshot_blocked(self, test_db):
        from app.services.exceptions import BusinessError
        from app.services.share_change_event_service import cancel_share_change_event
        port, plat, fund = _env(test_db, "FRZC")
        event = _pending_event(test_db, port, fund, plat)
        self._ex_snapshot(test_db, port, fund, plat)

        with pytest.raises(BusinessError) as exc_info:
            cancel_share_change_event(test_db, event)
        assert exc_info.value.code == "SNAPSHOT_DEPENDENCY"

    def test_update_before_ex_snapshot_allowed(self, test_db):
        """对照组：除息日快照未生成时（守卫天然不可达）改 div_cash 仍放行。"""
        from app.services.share_change_event_service import update_share_change_event
        port, plat, fund = _env(test_db, "FRZB")
        event = _pending_event(test_db, port, fund, plat)

        update_share_change_event(test_db, event, {"div_cash": Decimal("0.08")})
        assert test_db.get(ShareChangeEvent, event.id).div_cash == Decimal("0.08")


class TestSameDayPendingSnapshotE2E:
    """真实快照生成端到端：#766 复审 Suggestion 1 的反例。

    原有用例都在助手函数层、净值全程 1.5，无法验证「派生在途恰好补齐除息日
    除权缺口、确认切换类型后 total_value 不跳变」。本类走真实
    `generate_daily_snapshots`：EX 日净值跌到 1.45，持仓市值 1450，派生在途
    50.00 补回 1500；确认现金/再投后重建 EX，total_value 仍为 1500。
    派生金额与确认写库金额同源（`quantize_amount(基数 × div_cash)`）由此获得
    可复核的运行证据，而非仅靠代码分析。
    """

    EX_NAV = Decimal("1.45")
    EX_MARKET_VALUE = quantize_amount(SHARES * EX_NAV)      # 1450.00（除权缺口 50.00）
    EXPECTED_TOTAL = EX_MARKET_VALUE + CASH_AMOUNT          # 1500.00

    def _generate_ex(self, db, port):
        from app.services.snapshot_service import generate_daily_snapshots
        result = generate_daily_snapshots(db, port, EX)
        assert result["success"], result
        db.commit()
        return (
            db.query(PortfolioValueSnapshot)
            .filter(
                PortfolioValueSnapshot.portfolio_code == port,
                PortfolioValueSnapshot.snapshot_date == EX,
            )
            .one()
        )

    def _recalc_ex(self, db, port):
        from app.services.snapshot_service import recalculate_snapshots
        result = recalculate_snapshots(db, port, EX, EX)
        assert not result.get("errors"), result
        db.commit()

    def _assert_no_gap(self, db, port):
        snap = (
            db.query(PortfolioValueSnapshot)
            .filter(
                PortfolioValueSnapshot.portfolio_code == port,
                PortfolioValueSnapshot.snapshot_date == EX,
            )
            .one()
        )
        assert quantize_amount(snap.total_value) == self.EXPECTED_TOTAL
        assert quantize_amount(snap.in_transit_total) == CASH_AMOUNT

    def test_ex_dividend_gap_offset_and_cash_confirm_keeps_total(self, test_db):
        port, plat, fund = _env(test_db, "E2ECASH", ex_nav=self.EX_NAV)
        event = _pending_event(test_db, port, fund, plat)

        snap = self._generate_ex(test_db, port)
        # 除权后持仓市值 1450 + 派生在途 50 = 1500，与除息前净值持平
        assert quantize_amount(snap.total_value) == self.EXPECTED_TOTAL
        assert quantize_amount(snap.in_transit_total) == CASH_AMOUNT

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="cash_dividend", cash_pay_date=PAY),
        )
        test_db.commit()
        refreshed = test_db.get(ShareChangeEvent, event.id)
        # 派生金额 == 确认写库金额（同源的单点证据）
        assert refreshed.cash_change == CASH_AMOUNT

        self._recalc_ex(test_db, port)
        self._assert_no_gap(test_db, port)

    def test_ex_dividend_gap_offset_and_reinvest_confirm_keeps_total(self, test_db):
        port, plat, fund = _env(test_db, "E2EREINV", ex_nav=self.EX_NAV)
        event = _pending_event(test_db, port, fund, plat)

        snap = self._generate_ex(test_db, port)
        assert quantize_amount(snap.total_value) == self.EXPECTED_TOTAL

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="reinvest_dividend", share_confirm_date=PAY),
        )
        test_db.commit()

        self._recalc_ex(test_db, port)
        self._assert_no_gap(test_db, port)


class TestSameDayPendingConfirmThenGenerateMidPay:
    """#836：确认后按 MID/PAY 逐日走真实生成入口（原缺陷：MID/PAY 只做常量/单元式调用）。

    判别力：若移除确认路径的 `allow_snapshot_overlap`（除息日快照已存在时
    `DATE_BEFORE_SNAPSHOT`），`confirm_share_change_event` 直接失败；若移除
    snapshot_service 的在途派生，MID 的 `in_transit_total` 为 0。两者都会让本类
    断言失败。覆盖现金与再投资两条路径：分红应收在 MID 的净值行可见、份额未提前
    入账；PAY 现金到账/份额入账后在途退出。
    """

    EX_NAV = TestSameDayPendingSnapshotE2E.EX_NAV
    EX_MARKET_VALUE = TestSameDayPendingSnapshotE2E.EX_MARKET_VALUE
    EXPECTED_TOTAL = TestSameDayPendingSnapshotE2E.EXPECTED_TOTAL

    def _generate(self, db, port, day):
        from app.services.snapshot_service import generate_daily_snapshots
        result = generate_daily_snapshots(db, port, day)
        assert result["success"], result
        db.commit()
        return (
            db.query(PortfolioValueSnapshot)
            .filter(
                PortfolioValueSnapshot.portfolio_code == port,
                PortfolioValueSnapshot.snapshot_date == day,
            )
            .one()
        )

    def _rows(self, db, port, day):
        return {
            (p.product_code, p.platform_code): p
            for p in db.query(PortfolioPosition).filter(
                PortfolioPosition.portfolio_code == port,
                PortfolioPosition.snapshot_date == day,
            )
        }

    def test_cash_confirm_mid_in_transit_then_pay_cash_arrival(self, test_db):
        port, plat, fund = _env(test_db, "E2EMIDC", ex_nav=self.EX_NAV)
        event = _pending_event(test_db, port, fund, plat)
        self._generate(test_db, port, EX)

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="cash_dividend", cash_pay_date=PAY),
        )
        test_db.commit()

        mid = self._generate(test_db, port, MID)
        rows = self._rows(test_db, port, MID)
        # 份额未提前入账，除权后市值仍未恢复，缺口由派生在途补齐
        assert quantize_amount(rows[(fund, plat)].shares) == SHARES
        assert quantize_amount(rows[(fund, plat)].market_value) == self.EX_MARKET_VALUE
        assert quantize_amount(rows[("IN_TRANSIT_DIVIDEND", plat)].cash_amount) == CASH_AMOUNT
        assert quantize_amount(mid.total_value) == self.EXPECTED_TOTAL
        assert quantize_amount(mid.in_transit_total) == CASH_AMOUNT

        pay = self._generate(test_db, port, PAY)
        rows = self._rows(test_db, port, PAY)
        # 到账日在途退出，现金入账，份额不变
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert quantize_amount(rows[(fund, plat)].shares) == SHARES
        assert quantize_amount(rows[("CASH", plat)].cash_amount) == CASH_AMOUNT
        assert quantize_amount(pay.total_value) == self.EXPECTED_TOTAL
        assert quantize_amount(pay.in_transit_total) == Decimal("0")

    def test_reinvest_confirm_mid_receivable_then_pay_shares(self, test_db):
        port, plat, fund = _env(test_db, "E2EMIDR", ex_nav=self.EX_NAV)
        event = _pending_event(test_db, port, fund, plat)
        self._generate(test_db, port, EX)

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="reinvest_dividend", share_confirm_date=PAY),
        )
        test_db.commit()
        expected_shares = quantize_amount(test_db.get(ShareChangeEvent, event.id).shares_after)

        mid = self._generate(test_db, port, MID)
        rows = self._rows(test_db, port, MID)
        # 确认日未到，份额未提前入账，应收在途仍挂在净值行
        assert quantize_amount(rows[(fund, plat)].shares) == SHARES
        assert quantize_amount(rows[("IN_TRANSIT_DIVIDEND", plat)].cash_amount) == CASH_AMOUNT
        assert quantize_amount(mid.total_value) == self.EXPECTED_TOTAL
        assert quantize_amount(mid.in_transit_total) == CASH_AMOUNT

        pay = self._generate(test_db, port, PAY)
        rows = self._rows(test_db, port, PAY)
        # 确认日份额入账，应收退出，总额保持
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert quantize_amount(rows[(fund, plat)].shares) == expected_shares
        assert quantize_amount(pay.total_value) == self.EXPECTED_TOTAL
        assert quantize_amount(pay.in_transit_total) == Decimal("0")

