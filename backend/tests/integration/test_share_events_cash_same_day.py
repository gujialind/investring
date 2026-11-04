"""同日登记/除息的现金分红 + 现金分红 pending 时序统一（#765）。

统一形态（v2 决策，issue #765 范围口径「现金分红 pending 时序统一 + 支持同日」）：

- **录入**：`cash_dividend` 允许 `entitlement_date == ex_date`，且同日形态必须携带
  **严格晚于除息日**的 `cash_pay_date`（缺失/同日 422——否则「确认要基数快照、
  快照要闸门放行」互等死锁）。`dividend_pending` 同日**刻意不放宽**（方式未定
  无适用日期可作闸门/派生键，同型死锁，留 #766 一并设计）。
- **闸门**：`cash_dividend` 判键 `cash_effective_date`（= `cash_pay_date or ex_date`），
  pending 事件在 `ex_date <= D < cash_pay_date` 区间放行快照生成；不带到账日的
  形态回落 `ex_date`，阻断点与统一前逐字一致。
- **派生在途**：pending 与 confirmed 同口径计入 `IN_TRANSIT_DIVIDEND`
  （`reinvest_receivable.list_cash_dividend_in_transit`，pending 按基数 × div_cash
  现算、confirmed 用落库 cash_change，两者逐分相同 → 确认时点不影响已生成快照）。
- **自动确认**：现金分红改范围谓词（除息日快照生成后的下一次快照日 + 到账兜底），
  不带到账日的存量形态仍在除息日前一快照日触发（与统一前逐字一致）。
- **守卫**：pending 现金分红的派生在途进入快照后，改/取消/删除冻结
  （`SNAPSHOT_DEPENDENCY`，键 `ex_date`）。

数值口径：现金分红不改变净值（#rule-product 净值稳定性），NAV 全程 1.5；
红利金额 = 1000 × 0.05 = 50.00，除息日起总市值上台阶（在途计入市值）。
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot, ShareChangeEvent
from app.services.position_service import calculate_available_cash
from app.services.reinvest_receivable import list_cash_dividend_in_transit
from app.services.share_change_event_service import (
    cancel_share_change_event,
    confirm_share_change_event,
    create_share_change_event,
    delete_share_change_event,
    update_share_change_event,
)
from app.services.snapshot_service import _check_share_change_events, auto_confirm_after_snapshot
from app.services.exceptions import BusinessError
from app.utils.quantize import quantize_amount, quantize_nav
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
EX = date(2026, 10, 19)      # 除息日（周一）；同日场景下也是权益登记日
MID = date(2026, 10, 20)     # 除息日后的第一个快照日（周二）
PAY = date(2026, 10, 21)     # 现金到账日（周三）

NAV = Decimal("1.5")
SHARES = Decimal("1000")
DIV_CASH = Decimal("0.05")
CASH_AMOUNT = Decimal("50.00")   # quantize_amount(1000 × 0.05)


def _env(db, suffix, *, shares=SHARES):
    """BASE 日已就绪的场外组合：申购入金 + 基金买入（现金净流 0，避免 #203 阻断）。"""
    port, plat, fund = f"C765_{suffix}", f"CP_{suffix}", f"CF_{suffix}"
    capital = quantize_amount(shares * NAV)

    create_portfolio(db, code=port, status="active")
    create_platform(db, code=plat)
    create_product(db, code=fund, market=MARKET, product_type="OEF", confirm_days=0)
    for day in (BASE, EX, MID, PAY):
        ensure_trading_day(db, day, is_open=True)
    create_position_snapshot(
        db, port, fund, MARKET, snapshot_date=BASE, platform_code=plat,
        shares=float(shares), market_value=float(quantize_nav(shares * NAV)),
        unit_price=float(NAV), cost_price=float(NAV),
    )
    create_position_snapshot(
        db, port, "CASH", "", snapshot_date=BASE, platform_code=plat,
        cash_amount=0, market_value=0,
    )
    create_value_snapshot(
        db, port, BASE,
        total_value=float(quantize_nav(shares * NAV)),
        total_shares=float(capital), unit_price=1,
    )
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
        transfer_group=f"sub_c765_{suffix}",
    )
    group = f"rebal_c765_{suffix}"
    create_trade(
        db, port, "CASH", "", trade_type="sell", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital), price=1, transfer_group=group,
    )
    create_trade(
        db, port, fund, MARKET, trade_type="buy", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital),
        price=float(NAV), shares=float(shares), transfer_group=group,
    )
    db.commit()
    return port, plat, fund


def _event(db, port, fund, plat, *, entitlement=EX, ex_date=EX, pay_date=PAY,
           div_cash=DIV_CASH, event_type="cash_dividend", **kwargs):
    event = create_share_change_event(
        db, portfolio_code=port, product_code=fund, market=MARKET, platform_code=plat,
        event_type=event_type, ex_date=ex_date, entitlement_date=entitlement,
        cash_pay_date=pay_date, div_cash=div_cash, force_cover=True, **kwargs,
    )
    db.commit()
    return event


def _auto_confirm(db, port, day):
    """/snapshots/generate 不跑后置钩子，按测试惯例直接调服务函数。"""
    results = auto_confirm_after_snapshot(db, port, day)
    db.commit()
    return results


def _event_entries(results):
    return [r for r in results if r.get("type") == "event"]


def _generate(client, headers, port, day, *, ok=True):
    response = client.post(
        "/api/snapshots/generate", headers=headers,
        json={"portfolio_code": port, "target_date": day.isoformat()},
    )
    if ok:
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True, response.text
    return response


def _rows(db, port, day):
    return {
        (p.product_code, p.platform_code): p
        for p in db.query(PortfolioPosition).filter_by(portfolio_code=port, snapshot_date=day).all()
    }


def _value(db, port, day):
    return db.query(PortfolioValueSnapshot).filter_by(portfolio_code=port, snapshot_date=day).one()


def _error(resp):
    assert resp.status_code == 422, resp.json()
    return resp.json()["detail"]


def _body(port, fund, plat, **overrides):
    body = {
        "portfolio_code": port, "event_type": "cash_dividend",
        "product_code": fund, "market": MARKET, "platform_code": plat,
        "ex_date": EX.isoformat(), "entitlement_date": EX.isoformat(),
        "cash_pay_date": PAY.isoformat(), "div_cash": float(DIV_CASH),
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 录入校验（同日形态）
# ---------------------------------------------------------------------------


class TestSameDayCashInput:
    def test_same_day_accepted_with_later_pay_date(self, client, admin_headers, test_db):
        port, plat, fund = _env(test_db, "IN1")
        resp = client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat),
        )
        assert resp.status_code in (200, 201), resp.json()
        row = test_db.query(ShareChangeEvent).filter_by(portfolio_code=port).one()
        assert row.entitlement_date == row.ex_date == EX
        assert row.cash_pay_date == PAY
        assert row.cash_effective_date == PAY

    @pytest.mark.parametrize(
        "overrides,expected,message",
        [
            # 同日缺到账日：死锁形态，拒绝并说明原因
            ({"cash_pay_date": None}, "INVALID_PARAM", "现金到账日"),
            # 同日到账 == 除息日：现金当日生效同样死锁，须严格晚于
            ({"cash_pay_date": EX.isoformat()}, "INVALID_DATE_ORDER", "严格晚于除息日"),
            # 到账早于除息日：既有约束不变
            ({"cash_pay_date": BASE.isoformat()}, "INVALID_DATE_ORDER", "不能早于除息日"),
            # 现金分红不得携带份额确认日（校验顺序：确认日字段错先报）
            ({"share_confirm_date": PAY.isoformat()}, "INVALID_PARAM", "仅红利再投资"),
            # 除息日早于登记日仍然拒绝
            ({"entitlement_date": PAY.isoformat()}, "INVALID_DATE_ORDER", "不能早于权益登记日"),
        ],
    )
    def test_same_day_rejections(
        self, client, admin_headers, test_db, overrides, expected, message
    ):
        port, plat, fund = _env(test_db, "IN2")
        detail = _error(client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, **overrides),
        ))
        assert detail["error"] == expected
        assert message in detail["message"]
        assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=port).count() == 0

    def test_non_same_day_semantics_unchanged(self, client, admin_headers, test_db):
        """非同日回归：无到账日合法（按除息日到账）、到账日 == 除息日合法（#522）。"""
        port, plat, fund = _env(test_db, "IN3")
        resp = client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, entitlement_date=BASE.isoformat(),
                       cash_pay_date=None),
        )
        assert resp.status_code in (200, 201), resp.json()
        resp = client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, entitlement_date=BASE.isoformat(),
                       ex_date=MID.isoformat(), cash_pay_date=MID.isoformat()),
        )
        assert resp.status_code in (200, 201), resp.json()
        rows = test_db.query(ShareChangeEvent).filter_by(portfolio_code=port).all()
        assert {r.cash_pay_date for r in rows} == {None, MID}

    def test_same_day_pending_dividend_still_rejected(self, client, admin_headers, test_db):
        """同日 dividend_pending 仍拒绝（#765 收窄）：方式未定则无适用日期可作
        闸门/派生键，同日形态在「闸门阻断 D 日快照 ↔ 确认需要 D 日基数快照」
        之间结构性死锁；同日待定分红留给 #766 一并设计。"""
        port, plat, fund = _env(test_db, "IN4")
        detail = _error(client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, event_type="dividend_pending",
                       cash_pay_date=None),
        ))
        assert detail["error"] == "INVALID_DATE_ORDER"
        assert "严格大于" in detail["message"]
        assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=port).count() == 0

    def test_update_pending_dividend_to_same_day_rejected(self, client, admin_headers, test_db):
        """非同日待定分红把登记日改到除息日（同日形态）→ 重校验拒绝。"""
        port, plat, fund = _env(test_db, "IN5")
        resp = client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, event_type="dividend_pending",
                       entitlement_date=BASE.isoformat(), cash_pay_date=None),
        )
        assert resp.status_code in (200, 201), resp.json()
        event_id = resp.json()["id"]
        resp = client.put(
            f"/api/share-change-events/{event_id}",
            json={"entitlement_date": EX.isoformat()},
            headers=admin_headers,
        )
        detail = _error(resp)
        assert detail["error"] == "INVALID_DATE_ORDER"


# ---------------------------------------------------------------------------
# 派生在途 + 快照流（同日形态）
# ---------------------------------------------------------------------------


class TestSameDaySnapshotFlow:
    def test_ex_date_snapshot_books_derived_in_transit_while_pending(
        self, client, admin_headers, test_db
    ):
        """除息日快照在事件 pending 时即可生成：派生在途进 IN_TRANSIT_DIVIDEND，
        CASH/可用现金/份额不动（§2.1 同日行）。"""
        port, plat, fund = _env(test_db, "SN1")
        event = _event(test_db, port, fund, plat)
        assert event.status == "pending"

        _generate(client, admin_headers, port, EX)
        rows = _rows(test_db, port, EX)

        transit = rows[("IN_TRANSIT_DIVIDEND", plat)]
        assert Decimal(str(transit.cash_amount)) == CASH_AMOUNT
        assert transit.shares is None

        fund_row = rows[(fund, plat)]
        assert Decimal(str(fund_row.shares)) == SHARES
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == Decimal("0")
        assert calculate_available_cash(test_db, port, plat, EX) == Decimal("0")

        snapshot = _value(test_db, port, EX)
        assert Decimal(str(snapshot.in_transit_total)) == CASH_AMOUNT
        # 现金分红不改变净值：总市值 = 基金市值 + 派生在途
        assert Decimal(str(snapshot.total_value)) == quantize_nav(SHARES * NAV) + CASH_AMOUNT

        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"

    def test_in_transit_persists_through_window_then_cash_lands(
        self, client, admin_headers, test_db
    ):
        """窗口内逐日在途；确认后到账日现金入账、在途行消失（验收断言 2）。"""
        port, plat, fund = _env(test_db, "SN2")
        event = _event(test_db, port, fund, plat)

        _generate(client, admin_headers, port, EX)
        # 除息日快照生成时事件仍 pending；MID 前不触发（主句 ex < D 未满足、
        # 兜底句 PAY <= next(EX)=MID 未满足）
        results = _auto_confirm(test_db, port, EX)
        assert _event_entries(results) == []
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"

        _generate(client, admin_headers, port, MID)
        rows = _rows(test_db, port, MID)
        assert Decimal(str(rows[("IN_TRANSIT_DIVIDEND", plat)].cash_amount)) == CASH_AMOUNT
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == Decimal("0")

        # 除息日快照后的下一次快照日（MID）：主句命中，自动确认
        results = _auto_confirm(test_db, port, MID)
        entries = _event_entries(results)
        assert [r["id"] for r in entries] == [event.id]
        assert all(r["action"] == "auto_confirmed" for r in entries)
        test_db.expire_all()
        confirmed = test_db.get(ShareChangeEvent, event.id)
        assert confirmed.status == "confirmed"
        # 确认落库金额与 pending 期派生值逐分相同（幂等，快照零重算）
        assert Decimal(str(confirmed.cash_change)) == CASH_AMOUNT
        assert Decimal(str(confirmed.entitlement_shares)) == SHARES

        _generate(client, admin_headers, port, PAY)
        rows = _rows(test_db, port, PAY)
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == CASH_AMOUNT
        assert calculate_available_cash(test_db, port, plat, PAY) == CASH_AMOUNT
        assert Decimal(str(rows[(fund, plat)].shares)) == SHARES

    def test_pay_equal_next_day_confirmed_at_ex_date_run(
        self, client, admin_headers, test_db
    ):
        """兜底句：到账日 == 除息日的次一交易日时，在除息日快照当日运行即确认，
        否则次日闸门（cash_effective <= target）会在确认前阻断快照。"""
        port, plat, fund = _env(test_db, "SN3")
        event = _event(test_db, port, fund, plat, pay_date=MID)

        _generate(client, admin_headers, port, EX)
        results = _auto_confirm(test_db, port, EX)
        entries = _event_entries(results)
        assert [r["id"] for r in entries] == [event.id]
        assert all(r["action"] == "auto_confirmed" for r in entries)

        _generate(client, admin_headers, port, MID)
        rows = _rows(test_db, port, MID)
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == CASH_AMOUNT

    def test_not_hit_by_prior_day_auto_confirm(self, client, admin_headers, test_db):
        """BASE 运行（除息日快照生成前）不得命中同日事件——基数快照尚不存在。"""
        port, plat, fund = _env(test_db, "SN4")
        event = _event(test_db, port, fund, plat)

        results = _auto_confirm(test_db, port, BASE)
        assert _event_entries(results) == []
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"

    def test_gate_blocks_at_pay_date_while_pending(self, client, admin_headers, test_db):
        """到账日仍未确认（自动确认被绕过）时，闸门按 cash_effective_date 兜底阻断。"""
        port, plat, fund = _env(test_db, "SN5")
        _event(test_db, port, fund, plat)

        assert _check_share_change_events(test_db, port, EX)["status"] == "passed"
        assert _check_share_change_events(test_db, port, MID)["status"] == "passed"
        blocked = _check_share_change_events(test_db, port, PAY)
        assert blocked["status"] == "failed"
        assert "未确认的份额变动事件" in blocked["message"]

        _generate(client, admin_headers, port, EX)
        _generate(client, admin_headers, port, MID)
        resp = _generate(client, admin_headers, port, PAY, ok=False)
        assert resp.status_code == 422, resp.text
        assert "未确认" in resp.text

    def test_confirmed_window_values_identical_to_pending_derivation(
        self, client, admin_headers, test_db
    ):
        """同一窗口先确认后生成 vs 先生成（pending 派生）后确认：快照数值一致。"""
        port, plat, fund = _env(test_db, "SN6")
        event = _event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)
        pending_transit = Decimal(str(
            _rows(test_db, port, EX)[("IN_TRANSIT_DIVIDEND", plat)].cash_amount
        ))
        pending_total = Decimal(str(_value(test_db, port, EX).total_value))

        confirm_share_change_event(test_db, test_db.get(ShareChangeEvent, event.id))
        test_db.commit()
        # 重新生成同日快照（先删后插），confirmed 口径落库
        _generate(client, admin_headers, port, EX)
        rows = _rows(test_db, port, EX)
        assert Decimal(str(rows[("IN_TRANSIT_DIVIDEND", plat)].cash_amount)) == pending_transit
        assert Decimal(str(_value(test_db, port, EX).total_value)) == pending_total

    def test_derived_in_transit_listing_window(self, test_db):
        """模块窗口口径：ex <= D < pay 计入；NULL 到账日不计；金额 <= 0 跳过。

        读侧（无 live_positions）基数取权益登记日**已落库**快照行，故先生成
        EX 日快照再断言（与 reinvest 应收读侧同一约定）。
        """
        from app.services.snapshot_service import generate_daily_snapshots
        port, plat, fund = _env(test_db, "SN7")
        _event(test_db, port, fund, plat)
        generate_daily_snapshots(test_db, port, EX)
        test_db.commit()
        assert [r.amount for r in list_cash_dividend_in_transit(test_db, port, BASE)] == []
        assert [r.amount for r in list_cash_dividend_in_transit(test_db, port, EX)] == [CASH_AMOUNT]
        assert [r.amount for r in list_cash_dividend_in_transit(test_db, port, MID)] == [CASH_AMOUNT]
        assert [r.amount for r in list_cash_dividend_in_transit(test_db, port, PAY)] == []
        # NULL 到账日（非同日形态）没有在途区间：MID 日在途仍只有同日事件一笔
        event2 = _event(test_db, port, fund, plat, entitlement=BASE, ex_date=MID,
                        pay_date=None)
        rows = list_cash_dividend_in_transit(test_db, port, MID)
        assert [r.amount for r in rows] == [CASH_AMOUNT]
        assert event2.id not in {r.event_id for r in rows}


# ---------------------------------------------------------------------------
# 守卫（派生在途进快照后冻结 pending 事件）
# ---------------------------------------------------------------------------


class TestReceivableGuardForCashDividend:
    def _snapshot_ex(self, client, admin_headers, test_db, suffix):
        port, plat, fund = _env(test_db, suffix)
        event = _event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)
        return port, plat, fund, event

    def test_update_cancel_delete_frozen_after_snapshot(
        self, client, admin_headers, test_db
    ):
        port, plat, fund, event = self._snapshot_ex(client, admin_headers, test_db, "GD1")
        for mutate in (
            lambda: update_share_change_event(
                test_db, test_db.get(ShareChangeEvent, event.id),
                {"div_cash": Decimal("0.06")},
            ),
            lambda: cancel_share_change_event(test_db, test_db.get(ShareChangeEvent, event.id)),
            lambda: delete_share_change_event(test_db, test_db.get(ShareChangeEvent, event.id)),
        ):
            with pytest.raises(BusinessError) as exc:
                mutate()
            assert exc.value.code == "SNAPSHOT_DEPENDENCY"
            assert exc.value.details == {"from_date": EX.isoformat()}

    def test_notes_only_update_exempt(self, client, admin_headers, test_db):
        port, plat, fund, event = self._snapshot_ex(client, admin_headers, test_db, "GD2")
        updated = update_share_change_event(
            test_db, test_db.get(ShareChangeEvent, event.id), {"notes": "仅备注"}
        )
        test_db.commit()
        assert updated.notes == "仅备注"

    def test_guard_not_reachable_without_snapshot(self, test_db):
        """未生成除息日快照前，pending 同日现金分红可正常改/取消（守卫不误伤）。"""
        port, plat, fund = _env(test_db, "GD3")
        event = _event(test_db, port, fund, plat)
        update_share_change_event(
            test_db, event, {"cash_pay_date": date(2026, 10, 22)}
        )
        test_db.commit()
        cancel_share_change_event(test_db, event)
        test_db.commit()
        assert event.status == "cancelled"


# ---------------------------------------------------------------------------
# 非同日形态（§2.1 矩阵第一、二行）
# ---------------------------------------------------------------------------


class TestNonSameDayUnified:
    def test_no_pay_date_behavior_identical_to_legacy(self, client, admin_headers, test_db):
        """不带到账日（存量主流）：确认触发点仍在除息日前一快照日（兜底句），
        闸门仍从除息日阻断 pending，现金按除息日入账、无在途区间。"""
        port, plat, fund = _env(test_db, "NS1")
        event = _event(test_db, port, fund, plat,
                       entitlement=BASE, ex_date=EX, pay_date=None)

        # pending 闸门从除息日起阻断（与统一前一致）
        assert _check_share_change_events(test_db, port, BASE)["status"] == "passed"
        assert _check_share_change_events(test_db, port, EX)["status"] == "failed"

        # BASE 运行（EX 的前一快照日）：兜底句 cash_effective(EX) <= next(BASE)=EX 命中
        results = _auto_confirm(test_db, port, BASE)
        entries = _event_entries(results)
        assert [r["id"] for r in entries] == [event.id]
        assert all(r["action"] == "auto_confirmed" for r in entries)

        _generate(client, admin_headers, port, EX)
        rows = _rows(test_db, port, EX)
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == CASH_AMOUNT
        assert calculate_available_cash(test_db, port, plat, EX) == CASH_AMOUNT

    def test_with_pay_date_pending_window_generates_snapshots(
        self, client, admin_headers, test_db
    ):
        """带到账日（行为变更区）：pending 期 D..P-1 快照放行且含派生在途；
        确认时点为除息日后的下一快照日（主句），快照数值与确认后逐分一致。"""
        port, plat, fund = _env(test_db, "NS2")
        event = _event(test_db, port, fund, plat,
                       entitlement=BASE, ex_date=EX, pay_date=PAY)

        # BASE 运行不再触发确认（统一前是等值判键在 BASE 触发——已按 v2 决策后移）
        results = _auto_confirm(test_db, port, BASE)
        assert _event_entries(results) == []

        _generate(client, admin_headers, port, EX)
        rows = _rows(test_db, port, EX)
        assert Decimal(str(rows[("IN_TRANSIT_DIVIDEND", plat)].cash_amount)) == CASH_AMOUNT
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == Decimal("0")

        results = _auto_confirm(test_db, port, EX)   # 主句 ex < D 未满足（D=EX），兜底 PAY<=MID 否
        assert _event_entries(results) == []

        _generate(client, admin_headers, port, MID)
        results = _auto_confirm(test_db, port, MID)  # 主句 EX < MID 命中
        entries = _event_entries(results)
        assert [r["id"] for r in entries] == [event.id]
        assert all(r["action"] == "auto_confirmed" for r in entries)
        test_db.expire_all()
        assert Decimal(str(test_db.get(ShareChangeEvent, event.id).cash_change)) == CASH_AMOUNT

        _generate(client, admin_headers, port, PAY)
        rows = _rows(test_db, port, PAY)
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == CASH_AMOUNT

    def test_missing_base_snapshot_skipped_not_failed(self, client, admin_headers, test_db):
        """基数快照缺失（HK 半开同型：登记日无持仓快照）→ auto_confirm_skipped，
        不计 auto_confirm_failed（不进 records_failed）。"""
        port, plat, fund = _env(test_db, "NS3")
        event = _event(test_db, port, fund, plat,
                       entitlement=BASE, ex_date=EX, pay_date=None)
        test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port, snapshot_date=BASE
        ).delete()
        test_db.commit()

        results = _auto_confirm(test_db, port, BASE)
        entries = _event_entries(results)
        assert [r["id"] for r in entries] == [event.id]
        assert entries[0]["action"] == "auto_confirm_skipped"
        assert entries[0]["code"] == "MISSING_POSITION_SNAPSHOT"
        assert not any(r.get("action") == "auto_confirm_failed" for r in results)
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"


# ---------------------------------------------------------------------------
# 脏行（#771 同族）：派生在途对非正金额整体跳过
# ---------------------------------------------------------------------------


def test_dirty_non_positive_rows_skipped_in_transit(test_db):
    port, plat, fund = _env(test_db, "DT1")
    create_raw_event(
        test_db, port, fund, MARKET, event_type="cash_dividend", ex_date=EX,
        entitlement_date=EX, platform_code=plat, div_cash=Decimal("-0.05"),
        cash_pay_date=PAY, event_source="tushare",
    )
    test_db.commit()
    assert list_cash_dividend_in_transit(test_db, port, EX) == []
    # 不阻断快照生成
    from app.services.snapshot_service import generate_daily_snapshots
    generate_daily_snapshots(test_db, port, EX)
    test_db.commit()
    assert ("IN_TRANSIT_DIVIDEND", plat) not in _rows(test_db, port, EX)
