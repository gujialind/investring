"""#766：同日 dividend_pending 的闸门放行、派生在途与确认链路。

检测器对同日公告（`ex_date == record_date`）建 `dividend_pending`（方式未定）。
为破除「闸门阻断除息日快照 ↔ 确认需除息日基数快照」的死锁：
- 闸门对同日 `dividend_pending` 放行除息日快照，从 D+1 起阻断；
- 除息日起派生现金在途对冲净值缺口（金额与现金/再投同源），确认切换类型后退出。

#837 追加读侧覆盖：pending 窗口产品维度日/持有/累计收益经
`aggregate_cash_dividend_flows` 加回，确认前后同一快照日读数不跳变。

本文件钉住这些行为，不重复覆盖非同日 pending（见 test_share_events_cash_same_day.py
与 test_share_events_reinvest_receivable.py）。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.share_change_event import ShareChangeEvent
from app.services.reinvest_receivable import (
    aggregate_cash_dividend_flows,
    list_cash_dividend_in_transit,
)
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
    create_share_change_event as create_raw_event,
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
    """基线环境；`ex_nav` 模拟除息日起（含之后交易日）的除权价（默认全程与基线同价）。"""
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
        day_nav = ex_nav if day >= EX and ex_nav is not None else NAV
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


def _generate(client, headers, port, day):
    response = client.post(
        "/api/snapshots/generate", headers=headers,
        json={"portfolio_code": port, "target_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True, response.text


def _holdings(client, headers, port, day):
    response = client.get(
        f"/api/positions/portfolio/{port}/holdings/by-product", headers=headers,
        params={"snapshot_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _positions_api(client, headers, port, day):
    response = client.get(
        "/api/positions", headers=headers,
        params={"portfolio_code": port, "snapshot_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    return {p["product_code"]: p for p in response.json()["items"]}


class TestSameDayPendingReadSide:
    """#837：pending 窗口产品维度三字段不留除权缺口，确认前后同日读数不跳变。

    除息日起净值恒 1.45（ex_nav 覆盖 EX 及之后），除权缺口与应收 R=50 精确
    相等；EX/MID 与现金路径按 0 等值断言，再投到账日按一分容差（份额两步
    量化的固有残差，参照 #743 TestReadSideContinuity 的 CENT 口径）。
    """

    EX_NAV = Decimal("1.45")
    CENT = Decimal("0.01")

    def _fund(self, body, fund):
        return {p["product_code"]: p for p in body["products"]}[fund]

    def _assert_zero_profits(self, row, *, exact=True):
        for field in ("daily_profit", "holding_profit", "cumulative_profit"):
            value = Decimal(str(row[field] or 0))
            if exact:
                assert value == 0, (field, row)
            else:
                assert abs(value) <= self.CENT, (field, row)

    def test_pending_ex_profits_continuous(self, client, admin_headers, test_db):
        """A1/A2：pending 除息日三字段为 0，总额=产品市值+在途，CASH 不动。"""
        port, plat, fund = _env(test_db, "RDA", ex_nav=self.EX_NAV)
        _pending_event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)

        body = _holdings(client, admin_headers, port, EX)
        row = self._fund(body, fund)
        assert Decimal(str(row["market_value"])) == Decimal("1450")
        self._assert_zero_profits(row)
        assert Decimal(str(body["total_market_value"])) == Decimal("1500")
        assert Decimal(str(body["in_transit_market_value"])) == Decimal("50")
        assert Decimal(str(body["total_market_value"])) == sum(
            (Decimal(str(p["market_value"])) for p in body["products"]), Decimal("0")
        ) + Decimal(str(body["in_transit_market_value"]))

        items = _positions_api(client, admin_headers, port, EX)
        assert items[fund]["daily_profit"] == 0
        assert items[fund]["profit_loss"] == 0
        assert items["IN_TRANSIT_DIVIDEND"]["cash_amount"] == 50
        assert items["IN_TRANSIT_DIVIDEND"]["daily_profit"] is None
        assert items["CASH"]["cash_amount"] == 0
        assert items["CASH"]["daily_profit"] == 0

    def test_confirm_cash_no_jump_and_pay_day(self, client, admin_headers, test_db):
        """A3/A4：确认现金后同日重查不跳变；到账日在途转 CASH、收益口径不变。"""
        port, plat, fund = _env(test_db, "RDC", ex_nav=self.EX_NAV)
        event = _pending_event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="cash_dividend", cash_pay_date=PAY),
        )
        test_db.commit()

        body = _holdings(client, admin_headers, port, EX)
        self._assert_zero_profits(self._fund(body, fund))
        assert Decimal(str(body["total_market_value"])) == Decimal("1500")
        assert Decimal(str(body["in_transit_market_value"])) == Decimal("50")

        _generate(client, admin_headers, port, MID)
        _generate(client, admin_headers, port, PAY)
        body = _holdings(client, admin_headers, port, PAY)
        assert Decimal(str(body["in_transit_market_value"])) == Decimal("0")
        assert Decimal(str(body["total_market_value"])) == Decimal("1500")
        self._assert_zero_profits(self._fund(body, fund))
        items = _positions_api(client, admin_headers, port, PAY)
        assert "IN_TRANSIT_DIVIDEND" not in items
        assert items["CASH"]["cash_amount"] == 50
        assert items["CASH"]["daily_profit"] == 0
        assert items["CASH"]["profit_loss"] == 0
        assert items[fund]["daily_profit"] == 0

    def test_confirm_reinvest_no_jump_and_share_arrival(self, client, admin_headers, test_db):
        """A5：同日 pending 确认再投，EX/MID 连续；到账日份额落地、残差 ≤ 一分。"""
        port, plat, fund = _env(test_db, "RDR", ex_nav=self.EX_NAV)
        event = _pending_event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)

        confirm_share_change_event(
            test_db, event,
            selection=DividendSelection(event_type="reinvest_dividend", share_confirm_date=PAY),
        )
        test_db.commit()

        body = _holdings(client, admin_headers, port, EX)
        self._assert_zero_profits(self._fund(body, fund))
        assert Decimal(str(body["total_market_value"])) == Decimal("1500")

        _generate(client, admin_headers, port, MID)
        body = _holdings(client, admin_headers, port, MID)
        assert Decimal(str(body["in_transit_market_value"])) == Decimal("50")
        self._assert_zero_profits(self._fund(body, fund))
        mid_row = test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port, product_code=fund, snapshot_date=MID,
        ).one()
        assert Decimal(str(mid_row.shares)) == Decimal("1000")

        _generate(client, admin_headers, port, PAY)
        body = _holdings(client, admin_headers, port, PAY)
        assert Decimal(str(body["in_transit_market_value"])) == Decimal("0")
        assert abs(Decimal(str(body["total_market_value"])) - Decimal("1500")) <= self.CENT
        self._assert_zero_profits(self._fund(body, fund), exact=False)
        pay_row = test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port, product_code=fund, snapshot_date=PAY,
        ).one()
        assert Decimal(str(pay_row.shares)) == Decimal("1034.48")
        assert test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port, product_code="IN_TRANSIT_DIVIDEND", snapshot_date=PAY,
        ).first() is None


class TestAggregateCashDividendFlows:
    """#837 派生流水单点：pending-only、成对规则、非正/缺基数跳过、一次批量。"""

    def test_same_day_pending_single_leg(self, test_db):
        port, plat, fund = _env(test_db, "FLS1")
        _pending_event(test_db, port, fund, plat)
        TestSameDayPendingConfirmation()._ex_snapshot(test_db, port, fund, plat)
        flows = aggregate_cash_dividend_flows(test_db, [port], PAY)
        assert flows == {(port, fund, MARKET, plat): [(EX, 50.0)]}

    def test_non_same_day_pending_paired(self, test_db):
        port, plat, fund = _env(test_db, "FLS2")
        create_raw_event(
            test_db, portfolio_code=port, product_code=fund, market=MARKET,
            platform_code=plat, event_type="cash_dividend", status="pending",
            ex_date=EX, entitlement_date=BASE, cash_pay_date=PAY, div_cash=DIV_CASH,
        )
        flows = aggregate_cash_dividend_flows(test_db, [port], PAY)
        assert flows == {(port, fund, MARKET, plat): [(EX, 50.0), (PAY, -50.0)]}

    @pytest.mark.parametrize("status", ["confirmed", "cancelled"])
    def test_non_pending_excluded(self, test_db, status):
        """confirmed 由既有事件现金腿同额接管，cancelled 从未生效——都不得重复产出。"""
        port, plat, fund = _env(test_db, f"FLS3{status[:4]}")
        create_raw_event(
            test_db, portfolio_code=port, product_code=fund, market=MARKET,
            platform_code=plat, event_type="cash_dividend", status=status,
            ex_date=EX, entitlement_date=BASE, cash_pay_date=PAY,
            div_cash=DIV_CASH, cash_change=CASH_AMOUNT,
        )
        assert aggregate_cash_dividend_flows(test_db, [port], PAY) == {}

    def test_reinvest_type_not_in_cash_flows(self, test_db):
        """确认再投后类型切换退出本查询，由 aggregate_receivable_flows 接管。"""
        port, plat, fund = _env(test_db, "FLS4")
        create_raw_event(
            test_db, portfolio_code=port, product_code=fund, market=MARKET,
            platform_code=plat, event_type="reinvest_dividend", status="pending",
            ex_date=EX, entitlement_date=EX, share_confirm_date=PAY, div_cash=DIV_CASH,
        )
        assert aggregate_cash_dividend_flows(test_db, [port], PAY) == {}

    @pytest.mark.parametrize("div_cash", [Decimal("0"), Decimal("-0.05")])
    def test_non_positive_div_cash_skipped(self, test_db, div_cash):
        """#771 同判据：非正金额两端同消失，不造收益。"""
        port, plat, fund = _env(test_db, f"FLS5{'Z' if div_cash == 0 else 'N'}")
        create_raw_event(
            test_db, portfolio_code=port, product_code=fund, market=MARKET,
            platform_code=plat, event_type="cash_dividend", status="pending",
            ex_date=EX, entitlement_date=BASE, cash_pay_date=PAY, div_cash=div_cash,
        )
        assert aggregate_cash_dividend_flows(test_db, [port], PAY) == {}

    def test_missing_entitlement_base_skipped(self, test_db):
        """权益登记日无持仓快照（基数 0）→ 金额 0 → 跳过，不抛错。"""
        port, plat, fund = _env(test_db, "FLS6")
        create_raw_event(
            test_db, portfolio_code=port, product_code=fund, market=MARKET,
            platform_code=plat, event_type="cash_dividend", status="pending",
            ex_date=EX, entitlement_date=date(2026, 10, 15), cash_pay_date=PAY,
            div_cash=DIV_CASH,
        )
        assert aggregate_cash_dividend_flows(test_db, [port], PAY) == {}

    def test_multi_portfolio_and_platform_batch(self, test_db):
        """多组合多平台一次调用全返回（键不串行），金额按各自平台基数。"""
        port1, plat1, fund1 = _env(test_db, "FLS7A")
        port2, plat2, fund2 = _env(test_db, "FLS7B")
        plat3 = "SP_FLS7C"
        create_platform(test_db, code=plat3)
        create_position_snapshot(
            test_db, port1, fund1, MARKET, snapshot_date=BASE, platform_code=plat3,
            shares=500, market_value=750, unit_price=float(NAV), cost_price=float(NAV),
        )
        for port, fund, plat in ((port1, fund1, plat1), (port2, fund2, plat2), (port1, fund1, plat3)):
            create_raw_event(
                test_db, portfolio_code=port, product_code=fund, market=MARKET,
                platform_code=plat, event_type="cash_dividend", status="pending",
                ex_date=EX, entitlement_date=BASE, cash_pay_date=PAY, div_cash=DIV_CASH,
            )
        flows = aggregate_cash_dividend_flows(test_db, [port1, port2], PAY)
        assert flows == {
            (port1, fund1, MARKET, plat1): [(EX, 50.0), (PAY, -50.0)],
            (port2, fund2, MARKET, plat2): [(EX, 50.0), (PAY, -50.0)],
            (port1, fund1, MARKET, plat3): [(EX, 25.0), (PAY, -25.0)],
        }

    def test_empty_portfolios_short_circuits(self, test_db):
        assert aggregate_cash_dividend_flows(test_db, [], PAY) == {}


class TestSameDayPendingDirtyDivCash:
    """A9（#771 同判据）：非正 div_cash 的 pending 行不产生流水与在途，读数同改动前。"""

    @pytest.mark.parametrize("div_cash", [Decimal("0"), Decimal("-0.05")])
    def test_no_flows_no_transit_no_addback(self, client, admin_headers, test_db, div_cash):
        port, plat, fund = _env(
            test_db, f"DIRTY{'Z' if div_cash == 0 else 'N'}", ex_nav=Decimal("1.45"),
        )
        test_db.add(ShareChangeEvent(
            portfolio_code=port, product_code=fund, market=MARKET, platform_code=plat,
            event_type="dividend_pending", status="pending", event_source="tushare",
            ex_date=EX, entitlement_date=EX, div_cash=div_cash,
        ))
        test_db.commit()
        _generate(client, admin_headers, port, EX)

        assert aggregate_cash_dividend_flows(test_db, [port], EX) == {}
        items = _positions_api(client, admin_headers, port, EX)
        assert "IN_TRANSIT_DIVIDEND" not in items
        # 无加回：除权下跌如实体现（与改动前一致），不凭空造收益
        assert items[fund]["daily_profit"] == -50

