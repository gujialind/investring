"""同日权益登记/除息 + 派生再投资应收（#743）。

场外分红普遍「权益登记日 = 除息日」：红利按除息日净值折算份额，到红利发放日
（`share_confirm_date`）才到账。本文件钉住四段口径：

- **录入**：设了 `share_confirm_date` 才允许 `entitlement_date == ex_date`；确认日
  仅限再投资、须为交易日、须晚于除息日。
- **应收派生**：金额 = 权益登记日基数份额 × `div_cash`（两步量化 #425），窗口
  `ex_date <= D < share_confirm_date`，pending 与 confirmed 都计、cancelled 不计、
  `share_confirm_date` 为 NULL 的存量事件不计（行为不变）。
- **快照**：除息日快照把应收记入 `IN_TRANSIT_DIVIDEND`（计市值与在途合计，不进
  CASH、不计可用现金、不动份额）；pending 闸门与 auto_confirm 判键都按
  `share_effective_date`；确认日份额落地、成本基数守恒（#673）、应收窗口关闭。
- **读侧**：日收益、持有收益、累计收益在窗口两端各加回一次 → 总市值只随净值波动。

数值刻意选「每份分红 == 净值跌幅」（0.0037 = 1.0136 − 1.0099），使除息日的市值
跌幅与应收金额精确相等，连续性可按 0.00 等值断言而非容差。
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot, ShareChangeEvent
from app.services.position_service import calculate_available_cash
from app.services.reinvest_receivable import list_reinvest_receivables
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
    create_subscription,
    create_trade,
    create_value_snapshot,
    ensure_trading_day,
)

MARKET = "CN_OTC"
BASE = date(2026, 10, 16)    # 基线快照日（周五）
EX = date(2026, 10, 19)      # 除息日（周一）；同日场景下也是权益登记日
MID = date(2026, 10, 20)     # 窗口中的第二个交易日（周二）
APPLY = date(2026, 10, 21)   # 份额到账日（周三）

NAV_BASE = Decimal("1.0136")
NAV_EX = Decimal("1.0099")
SHARES = Decimal("8797")
DIV_CASH = Decimal("0.0037")          # == NAV_BASE − NAV_EX，令连续性精确
RECEIVABLE = Decimal("32.55")         # quantize_amount(8797 × 0.0037)
NEW_SHARES = Decimal("32.23")         # quantize_shares(32.55 / 1.0099)
CAPITAL = quantize_amount(SHARES * NAV_BASE)   # 8916.64（申赎/调仓的 2 位现金流）
BASE_MV = quantize_nav(SHARES * NAV_BASE)      # 8916.6392（快照市值按 4 位落库）
# 应收按分量化（32.55）与市值的精确跌幅（32.5489）差半分级，故收益连续性按
# 「不超过一分」断言，不按 0 断言——这是量化口径的固有残差，不是记账漂移。
CENT = Decimal("0.01")
SCALE_BOUND_PER_SHARE = Decimal("0.00005")      # cost_price 列标度 4 位


def _env(db, suffix, *, shares=SHARES, nav_ex=NAV_EX, sell_on_ex=None):
    """建一个 BASE 日已就绪的场外组合：基金买入（配对 CASH 腿）+ 等额申购入金。

    现金净流为 0（申购入金 == 买入出金），避免负现金阻断（#203）；买入腿真实落库，
    累计收益的全历史净流量口径才有分母。

    `sell_on_ex` 在除息日追加一组已确认调仓卖出（基金腿 + CASH 腿），令除息日份额
    少于权益登记日份额——用来钉住应收基数取**权益登记日**持仓而非除息日持仓。
    """
    port, plat, fund = f"R743_{suffix}", f"RP_{suffix}", f"RF_{suffix}"
    capital = quantize_amount(shares * NAV_BASE)

    create_portfolio(db, code=port, status="active")
    create_platform(db, code=plat)
    create_product(db, code=fund, market=MARKET, product_type="OEF", confirm_days=0)
    for day, is_open in (
        (BASE, True), (date(2026, 10, 17), False), (date(2026, 10, 18), False),
        (EX, True), (MID, True), (APPLY, True),
    ):
        ensure_trading_day(db, day, is_open=is_open)
    create_position_snapshot(
        db, port, fund, MARKET, snapshot_date=BASE, platform_code=plat,
        shares=float(shares), market_value=float(quantize_nav(shares * NAV_BASE)),
        unit_price=float(NAV_BASE), cost_price=float(NAV_BASE),
    )
    create_position_snapshot(
        db, port, "CASH", "", snapshot_date=BASE, platform_code=plat,
        cash_amount=0, market_value=0,
    )
    create_value_snapshot(
        db, port, BASE,
        total_value=float(quantize_nav(shares * NAV_BASE)),
        total_shares=float(capital), unit_price=1,
    )
    create_investor_holding(db, port, "VIEWER", BASE, shares=float(capital))
    create_subscription(
        db, port, "VIEWER", amount=float(capital), shares=float(capital), unit_price=1,
        platform_code=plat, apply_date=date(2026, 10, 15), confirm_date=BASE, status="confirmed",
    )
    for day in (BASE, EX, MID, APPLY):
        create_price_record(db, fund, MARKET, day, float(NAV_BASE if day == BASE else nav_ex))
    create_trade(
        db, port, "CASH", "", trade_type="buy", platform_code=plat,
        trade_date=date(2026, 10, 15), confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital), price=1,
        transfer_group=f"sub_r743_{suffix}",
    )
    group = f"rebal_r743_{suffix}"
    create_trade(
        db, port, "CASH", "", trade_type="sell", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital), price=1, transfer_group=group,
    )
    create_trade(
        db, port, fund, MARKET, trade_type="buy", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital),
        price=float(NAV_BASE), shares=float(shares), transfer_group=group,
    )
    if sell_on_ex is not None:
        proceeds = quantize_amount(Decimal(str(sell_on_ex)) * nav_ex)
        sell_group = f"rebal_r743_{suffix}_sell"
        create_trade(
            db, port, fund, MARKET, trade_type="sell", platform_code=plat,
            trade_date=EX, confirm_date=EX, status="confirmed",
            amount=float(proceeds), actual_amount=float(proceeds),
            price=float(nav_ex), shares=float(sell_on_ex), transfer_group=sell_group,
        )
        create_trade(
            db, port, "CASH", "", trade_type="buy", platform_code=plat,
            trade_date=EX, confirm_date=EX, status="confirmed",
            amount=float(proceeds), actual_amount=float(proceeds), price=1,
            transfer_group=sell_group,
        )
    db.commit()
    return port, plat, fund


def _event(db, port, fund, plat, *, entitlement=EX, ex_date=EX, confirm_date=APPLY,
           div_cash=DIV_CASH, event_type="reinvest_dividend", **kwargs):
    event = create_share_change_event(
        db, portfolio_code=port, product_code=fund, market=MARKET, platform_code=plat,
        event_type=event_type, ex_date=ex_date, entitlement_date=entitlement,
        share_confirm_date=confirm_date, div_cash=div_cash, force_cover=True, **kwargs,
    )
    db.commit()
    return event


def _auto_confirm(db, port, day):
    """/snapshots/generate 不跑后置钩子（只有 catch-up / generate-next / 重算跑），
    故按 #744 的测试惯例直接调服务函数，验证判键为 `share_effective_date`。"""
    results = auto_confirm_after_snapshot(db, port, day)
    db.commit()
    return results


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


def _by_product(client, headers, port, day):
    response = client.get(
        f"/api/positions/portfolio/{port}/holdings/by-product", headers=headers,
        params={"snapshot_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    return {p["product_code"]: p for p in response.json()["products"]}


def _basis(row):
    return Decimal(str(row.shares)) * Decimal(str(row.cost_price))


def _error(resp):
    assert resp.status_code == 422, resp.json()
    return resp.json()["detail"]


def _body(port, fund, plat, **overrides):
    body = {
        "portfolio_code": port, "event_type": "reinvest_dividend",
        "product_code": fund, "market": MARKET, "platform_code": plat,
        "ex_date": EX.isoformat(), "entitlement_date": EX.isoformat(),
        "share_confirm_date": APPLY.isoformat(), "div_cash": float(DIV_CASH),
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 录入校验
# ---------------------------------------------------------------------------


class TestSameDayInput:
    def test_same_day_rejected_without_confirm_date(self, client, admin_headers, test_db):
        """未设确认日时同日登记/除息照旧拒绝：份额与现金都按 ex_date 生效会互相打架。"""
        port, plat, fund = _env(test_db, "IN1")
        detail = _error(client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, share_confirm_date=None),
        ))
        assert detail["error"] == "INVALID_DATE_ORDER"
        assert "严格大于" in detail["message"]
        assert test_db.query(ShareChangeEvent).filter_by(portfolio_code=port).count() == 0

    def test_same_day_accepted_with_confirm_date(self, client, admin_headers, test_db):
        port, plat, fund = _env(test_db, "IN2")
        resp = client.post(
            "/api/share-change-events", headers=admin_headers, json=_body(port, fund, plat)
        )
        assert resp.status_code in (200, 201), resp.json()
        row = test_db.query(ShareChangeEvent).filter_by(portfolio_code=port).one()
        assert row.entitlement_date == row.ex_date == EX
        assert row.share_confirm_date == APPLY
        assert row.share_effective_date == APPLY
        assert resp.json()["share_confirm_date"] == APPLY.isoformat()

    def test_ex_before_entitlement_rejected_even_with_confirm_date(
        self, client, admin_headers, test_db
    ):
        port, plat, fund = _env(test_db, "IN3")
        detail = _error(client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, entitlement_date=APPLY.isoformat()),
        ))
        assert detail["error"] == "INVALID_DATE_ORDER"
        assert "不能早于权益登记日" in detail["message"]

    @pytest.mark.parametrize(
        "overrides,expected,message",
        [
            ({"event_type": "cash_dividend"}, "INVALID_PARAM", "仅红利再投资"),
            ({"share_confirm_date": date(2026, 10, 24).isoformat()},
             "INVALID_SHARE_CONFIRM_DATE", "不是交易日"),
            ({"share_confirm_date": EX.isoformat()}, "INVALID_DATE_ORDER", "必须晚于除息日"),
            ({"share_confirm_date": BASE.isoformat()}, "INVALID_DATE_ORDER", "必须晚于除息日"),
        ],
    )
    def test_confirm_date_rejections(
        self, client, admin_headers, test_db, overrides, expected, message
    ):
        port, plat, fund = _env(test_db, "IN4")
        detail = _error(client.post(
            "/api/share-change-events", headers=admin_headers,
            json=_body(port, fund, plat, **overrides),
        ))
        assert detail["error"] == expected
        assert message in detail["message"]

    def test_update_sets_and_clears_confirm_date(self, client, admin_headers, test_db):
        port, plat, fund = _env(test_db, "IN5")
        event = _event(test_db, port, fund, plat, confirm_date=None, entitlement=BASE)
        assert event.share_effective_date == EX

        resp = client.put(
            f"/api/share-change-events/{event.id}", headers=admin_headers,
            json={"entitlement_date": EX.isoformat(), "share_confirm_date": APPLY.isoformat()},
        )
        assert resp.status_code == 200, resp.json()
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).share_confirm_date == APPLY

        resp = client.put(
            f"/api/share-change-events/{event.id}", headers=admin_headers,
            json={"share_confirm_date": None, "entitlement_date": BASE.isoformat()},
        )
        assert resp.status_code == 200, resp.json()
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).share_confirm_date is None

    def test_legacy_types_share_effective_date_is_ex_date(self, test_db):
        """存量口径不变：非再投资事件（或未设确认日的再投资）份额生效日就是除息日。"""
        port, plat, fund = _env(test_db, "IN6")
        reinvest = _event(test_db, port, fund, plat, entitlement=BASE, confirm_date=None)
        assert reinvest.share_effective_date == EX
        split = create_share_change_event(
            test_db, portfolio_code=port, product_code=fund, market=MARKET,
            event_type="share_split", ex_date=EX, entitlement_date=BASE,
            ratio=Decimal("2"), force_cover=True,
        )
        test_db.commit()
        assert split.share_confirm_date is None
        assert split.share_effective_date == EX


# ---------------------------------------------------------------------------
# 应收派生
# ---------------------------------------------------------------------------


class TestReceivableDerivation:
    def test_base_is_entitlement_date_shares(self, test_db):
        """合成基数差异：权益登记日 8797 份、除息日卖到 8710 份 → 应收按 8797 计。

        div_cash 取现场值 0.0039：8797 × 0.0039 = 34.31；若误用除息日基数
        （8710）则为 33.97，正好是 007169 现场的金额——两个值必须能区分。
        """
        port, plat, fund = _env(test_db, "RC1", sell_on_ex=87)
        event = _event(test_db, port, fund, plat, entitlement=BASE, ex_date=EX,
                       div_cash=Decimal("0.0039"))
        assert event.entitlement_shares is None      # pending 不落库基数

        receivables = list_reinvest_receivables(test_db, port, EX)
        assert [r.amount for r in receivables] == [Decimal("34.31")]
        assert receivables[0].event_id == event.id

    def test_window_status_and_null_confirm_date(self, test_db):
        """窗口 [ex_date, share_confirm_date)、cancelled 不计、S 为 NULL 不计。

        登记日取 BASE（非同日）：读侧不传 live_positions，基数来自已落库的
        权益登记日持仓行——同日场景的读侧基数是当日快照行，由
        `TestReadSideContinuity` 覆盖。
        """
        port, plat, fund = _env(test_db, "RC2")
        _event(test_db, port, fund, plat, entitlement=BASE)
        assert list_reinvest_receivables(test_db, port, BASE) == []
        assert [r.amount for r in list_reinvest_receivables(test_db, port, EX)] == [RECEIVABLE]
        assert [r.amount for r in list_reinvest_receivables(test_db, port, MID)] == [RECEIVABLE]
        assert [r.amount for r in list_reinvest_receivables(test_db, port, APPLY)] == []

        event = test_db.query(ShareChangeEvent).filter_by(portfolio_code=port).one()
        cancel_share_change_event(test_db, event)
        test_db.commit()
        assert list_reinvest_receivables(test_db, port, EX) == []

    def test_confirmed_event_uses_stored_entitlement_shares(self, test_db):
        """确认后基数改取落库的 entitlement_shares：删掉权益登记日快照行也不动应收。"""
        port, plat, fund = _env(test_db, "RC3")
        event = _event(test_db, port, fund, plat, entitlement=BASE)
        confirm_share_change_event(test_db, event)
        test_db.commit()
        test_db.expire_all()
        event = test_db.get(ShareChangeEvent, event.id)
        assert Decimal(str(event.entitlement_shares)) == SHARES
        assert Decimal(str(event.shares_change)) == NEW_SHARES
        assert [r.amount for r in list_reinvest_receivables(test_db, port, EX)] == [RECEIVABLE]

        # 锁「来源」而不只是「值」：本夹具下 confirmed 分支（落库 entitlement_shares
        # 8797）与 pending 分支（权益登记日快照 8797）同值，删掉快照行后两者才可区分——
        # confirmed 分支若被误改成读快照 join，基数会变 0、应收消失，下面的断言即红。
        # （快照行禁止 ORM 直改，见 models/portfolio_position.py 的更新守卫。）
        test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port, product_code=fund, snapshot_date=BASE
        ).delete()
        test_db.commit()
        assert [r.amount for r in list_reinvest_receivables(test_db, port, EX)] == [RECEIVABLE]
        assert [r.amount for r in list_reinvest_receivables(test_db, port, MID)] == [RECEIVABLE]

    def test_zero_base_yields_no_receivable(self, test_db):
        """基数为 0（权益登记日无持仓行）不落 0 值在途行，与既有口径一致。"""
        port, plat, fund = _env(test_db, "RC4")
        _event(test_db, port, fund, plat, entitlement=BASE)
        test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port, product_code=fund, snapshot_date=BASE
        ).delete()
        test_db.commit()
        assert list_reinvest_receivables(test_db, port, EX) == []


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


class TestSnapshotFlow:
    def test_ex_date_snapshot_books_receivable(self, client, admin_headers, test_db):
        """除息日：应收进 IN_TRANSIT_DIVIDEND 与在途合计，CASH/可用现金/份额不动。"""
        port, plat, fund = _env(test_db, "SN1")
        event = _event(test_db, port, fund, plat)
        assert event.status == "pending"

        _generate(client, admin_headers, port, EX)
        rows = _rows(test_db, port, EX)

        transit = rows[("IN_TRANSIT_DIVIDEND", plat)]
        assert Decimal(str(transit.cash_amount)) == RECEIVABLE
        assert transit.shares is None
        assert transit.frozen_amount == transit.frozen_shares == 0

        fund_row = rows[(fund, plat)]
        assert Decimal(str(fund_row.shares)) == SHARES          # 份额尚未到账
        assert Decimal(str(fund_row.market_value)) == quantize_nav(SHARES * NAV_EX)
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == Decimal("0")
        assert calculate_available_cash(test_db, port, plat, EX) == Decimal("0")

        snapshot = _value(test_db, port, EX)
        assert Decimal(str(snapshot.in_transit_total)) == RECEIVABLE
        # 8884.0903 + 32.55：与 BASE 总市值只差应收的分位量化残差
        assert abs(Decimal(str(snapshot.total_value)) - BASE_MV) <= CENT

    def test_pending_gate_and_auto_confirm_key_on_share_effective_date(
        self, client, admin_headers, test_db
    ):
        """闸门放行 [ex_date, S) 的 pending 事件；auto_confirm 判键为 S 而非 ex_date。"""
        port, plat, fund = _env(test_db, "SN2")
        event = _event(test_db, port, fund, plat, confirm_date=MID)

        # 除息日快照生成时事件仍 pending（份额生效日是 MID）→ 不被闸门阻断
        _generate(client, admin_headers, port, EX)
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"
        assert [r.amount for r in list_reinvest_receivables(test_db, port, EX)] == [RECEIVABLE]

        # S == EX 的次一交易日 → 后置钩子按 share_effective_date 命中并确认
        results = _auto_confirm(test_db, port, EX)
        assert [r["id"] for r in results if r.get("type") == "event"] == [event.id]
        assert all(r["action"] == "auto_confirmed" for r in results if r.get("type") == "event")
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "confirmed"
        _generate(client, admin_headers, port, MID)
        rows = _rows(test_db, port, MID)
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows       # 窗口关闭
        assert Decimal(str(rows[(fund, plat)].shares)) == SHARES + NEW_SHARES

    def test_same_day_event_not_hit_by_prior_day_auto_confirm(
        self, client, admin_headers, test_db
    ):
        """验收第 8 条：EX 快照生成前跑 `auto_confirm(BASE)` → 同日事件不被命中。

        BASE 的次一交易日正是 EX，故判键若回落 `ex_date`，此刻就会去确认一个基数快照
        （= EX 快照）尚不存在的事件——它只能以 MISSING_POSITION_SNAPSHOT 失败并留在
        pending，现网表象与正确实现完全一致，所以必须直接钉住「结果里没有这条事件」。
        """
        port, plat, fund = _env(test_db, "SN8")
        event = _event(test_db, port, fund, plat)          # 同日登记/除息，S = APPLY

        results = _auto_confirm(test_db, port, BASE)
        assert [r for r in results if r.get("type") == "event"] == []
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"

        # 基数快照落地后才可确认（验收同条的后半句：生成 EX 快照后手动确认）
        _generate(client, admin_headers, port, EX)
        assert [r.amount for r in list_reinvest_receivables(test_db, port, EX)] == [RECEIVABLE]
        confirm_share_change_event(test_db, test_db.get(ShareChangeEvent, event.id))
        test_db.commit()
        test_db.expire_all()
        confirmed = test_db.get(ShareChangeEvent, event.id)
        assert confirmed.status == "confirmed"
        assert Decimal(str(confirmed.entitlement_shares)) == SHARES
        assert Decimal(str(confirmed.shares_change)) == NEW_SHARES

    def test_pending_event_blocks_snapshot_on_share_effective_date(
        self, client, admin_headers, test_db
    ):
        port, plat, fund = _env(test_db, "SN3")
        event = _event(test_db, port, fund, plat)      # S = APPLY
        _generate(client, admin_headers, port, EX)
        _generate(client, admin_headers, port, MID)    # 窗口内 pending 照旧放行
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"

        assert _check_share_change_events(test_db, port, MID)["status"] == "passed"
        blocked = _check_share_change_events(test_db, port, APPLY)
        assert blocked["status"] == "failed"
        assert "未确认的份额变动事件" in blocked["message"]

        resp = _generate(client, admin_headers, port, APPLY, ok=False)
        assert resp.status_code == 422, resp.text
        assert "未确认" in resp.text

        confirm_share_change_event(test_db, test_db.get(ShareChangeEvent, event.id))
        test_db.commit()
        _generate(client, admin_headers, port, APPLY)
        assert Decimal(str(_rows(test_db, port, APPLY)[(fund, plat)].shares)) == SHARES + NEW_SHARES

    def test_total_value_moves_only_with_nav(self, client, admin_headers, test_db):
        """净值自除息日起不变 → 四天总市值恒等；应收与份额到账只是形态转换。"""
        port, plat, fund = _env(test_db, "SN4")
        _event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)
        _generate(client, admin_headers, port, MID)
        _auto_confirm(test_db, port, MID)              # 次一交易日 == APPLY == 份额生效日
        _generate(client, admin_headers, port, APPLY)
        totals = [
            Decimal(str(_value(test_db, port, day).total_value)) for day in (BASE, EX, MID, APPLY)
        ]
        assert all(abs(total - BASE_MV) <= CENT for total in totals), totals

    def test_shares_arrival_conserves_cost_basis(self, client, admin_headers, test_db):
        """到账日份额增加、成本基数守恒（#673）：免成本份额不按原成本价计本金。"""
        port, plat, fund = _env(test_db, "SN5")
        _event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)
        _generate(client, admin_headers, port, MID)
        _auto_confirm(test_db, port, MID)
        _generate(client, admin_headers, port, APPLY)
        row = _rows(test_db, port, APPLY)[(fund, plat)]
        assert Decimal(str(row.shares)) == SHARES + NEW_SHARES
        assert abs(_basis(row) - BASE_MV) <= Decimal(str(row.shares)) * SCALE_BOUND_PER_SHARE

    def test_recalculate_is_idempotent(self, client, admin_headers, test_db):
        """重算逐日删除重建：派生应收不重复计入、份额不重复到账。"""
        port, plat, fund = _env(test_db, "SN6")
        _event(test_db, port, fund, plat, confirm_date=MID)
        _generate(client, admin_headers, port, EX)
        _auto_confirm(test_db, port, EX)
        _generate(client, admin_headers, port, MID)
        before = {
            day: (
                Decimal(str(_rows(test_db, port, day)[(fund, plat)].shares)),
                Decimal(str(_value(test_db, port, day).total_value)),
            )
            for day in (EX, MID)
        }

        resp = client.post(
            "/api/snapshots/recalculate", headers=admin_headers,
            json={"portfolio_code": port, "start_date": EX.isoformat(), "end_date": MID.isoformat()},
        )
        assert resp.status_code == 200, resp.text
        assert not resp.json().get("errors"), resp.text
        test_db.expire_all()
        after = {
            day: (
                Decimal(str(_rows(test_db, port, day)[(fund, plat)].shares)),
                Decimal(str(_value(test_db, port, day).total_value)),
            )
            for day in (EX, MID)
        }
        assert after == before

    def test_rebuild_after_bulk_delete_does_not_double_count(
        self, client, admin_headers, test_db
    ):
        """删除快照级联回退事件为 pending，重建后应收仍只有一笔。"""
        port, plat, fund = _env(test_db, "SN7")
        event = _event(test_db, port, fund, plat, confirm_date=MID)
        _generate(client, admin_headers, port, EX)
        _auto_confirm(test_db, port, EX)
        _generate(client, admin_headers, port, MID)
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "confirmed"

        resp = client.delete(
            f"/api/snapshots/{port}/bulk/{EX.isoformat()}",
            headers=admin_headers, params={"confirm": "true"},
        )
        assert resp.status_code == 200, resp.text
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"

        _generate(client, admin_headers, port, EX)
        _auto_confirm(test_db, port, EX)
        _generate(client, admin_headers, port, MID)
        rows = _rows(test_db, port, MID)
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert Decimal(str(rows[(fund, plat)].shares)) == SHARES + NEW_SHARES
        assert abs(Decimal(str(_value(test_db, port, MID).total_value)) - BASE_MV) <= CENT

    def test_same_day_plus_ex_date_sell_full_chain(
        self, client, admin_headers, test_db
    ):
        """issue #743 合成回归：同日登记 + 当日卖出 87 份 → 33.97 元 / 33.64 份。

        基数必须取 EX 当日的 8710 份（同日形态下走 `live_positions`／当日快照），而非
        BASE 的 8797 份——后者算出 34.31 / 33.97，正是 RC1 钉住的反面取值。preview 与
        确认落库同值，份额在 S 日到账、应收窗口关闭。
        """
        port, plat, fund = _env(test_db, "SN9", sell_on_ex=87)
        event = _event(test_db, port, fund, plat, entitlement=EX, ex_date=EX,
                       div_cash=Decimal("0.0039"))

        _generate(client, admin_headers, port, EX)
        rows = _rows(test_db, port, EX)
        assert Decimal(str(rows[(fund, plat)].shares)) == Decimal("8710")
        assert Decimal(str(rows[("IN_TRANSIT_DIVIDEND", plat)].cash_amount)) == Decimal("33.97")

        preview = client.get(
            f"/api/share-change-events/{event.id}/preview", headers=admin_headers
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()["preview"]
        assert Decimal(str(body["entitlement_shares"])) == Decimal("8710")
        assert Decimal(str(body["dividend_amount"])) == Decimal("33.97")
        assert Decimal(str(body["shares_change"])) == Decimal("33.64")

        confirm_share_change_event(test_db, test_db.get(ShareChangeEvent, event.id))
        test_db.commit()
        test_db.expire_all()
        confirmed = test_db.get(ShareChangeEvent, event.id)
        assert Decimal(str(confirmed.entitlement_shares)) == Decimal("8710")
        assert Decimal(str(confirmed.shares_change)) == Decimal("33.64")
        assert Decimal(str(confirmed.shares_after)) == Decimal("8743.64")

        for day in (MID, APPLY):
            _generate(client, admin_headers, port, day)
        rows = _rows(test_db, port, APPLY)
        assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
        assert Decimal(str(rows[(fund, plat)].shares)) == Decimal("8743.64")
        assert [r.amount for r in list_reinvest_receivables(test_db, port, MID)] == [
            Decimal("33.97")
        ]
        assert list_reinvest_receivables(test_db, port, APPLY) == []


# ---------------------------------------------------------------------------
# 读侧收益连续
# ---------------------------------------------------------------------------


class TestReadSideContinuity:
    def test_profits_continuous_across_receivable_window(
        self, client, admin_headers, test_db
    ):
        """日收益/持有收益/累计收益在窗口两端各加回一次 → 全程停在零附近。

        残差上界为一分：应收按分量化（32.55），而净值跌幅是 32.5489。
        """
        port, plat, fund = _env(test_db, "RD1")
        _event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)
        _generate(client, admin_headers, port, MID)
        _auto_confirm(test_db, port, MID)
        _generate(client, admin_headers, port, APPLY)

        expected_mv = {
            BASE: BASE_MV,
            EX: quantize_nav(SHARES * NAV_EX),
            MID: quantize_nav(SHARES * NAV_EX),
            APPLY: quantize_nav((SHARES + NEW_SHARES) * NAV_EX),
        }
        for day in (BASE, EX, MID, APPLY):
            row = _by_product(client, admin_headers, port, day)[fund]
            assert abs(Decimal(str(row["market_value"])) - expected_mv[day]) <= CENT, (day, row)
            assert abs(Decimal(str(row["daily_profit"] or 0))) <= CENT, (day, row)
            assert abs(Decimal(str(row["cumulative_profit"] or 0))) <= CENT, (day, row)
            assert abs(Decimal(str(row["holding_profit"] or 0))) <= CENT, (day, row)
            # 现金腿全程不动：应收只进产品维度，不进 CASH
            rows = _rows(test_db, port, day)
            assert Decimal(str(rows[("CASH", plat)].cash_amount)) == Decimal("0"), day

    def test_receivable_not_counted_as_cash_profit(self, client, admin_headers, test_db):
        """应收只进产品维度加回，不进现金腿：可用现金与 CASH 行都不因再投资抬升。"""
        port, plat, fund = _env(test_db, "RD2")
        _event(test_db, port, fund, plat)
        _generate(client, admin_headers, port, EX)
        rows = _rows(test_db, port, EX)
        assert Decimal(str(rows[("CASH", plat)].cash_amount)) == Decimal("0")
        assert calculate_available_cash(test_db, port, plat, EX) == Decimal("0")
        fund_row = _by_product(client, admin_headers, port, EX)[fund]
        assert abs(Decimal(str(fund_row["cumulative_profit"] or 0))) <= CENT


# ---------------------------------------------------------------------------
# 守卫
# ---------------------------------------------------------------------------


class TestPendingGuards:
    def _generate_ex(self, client, admin_headers, test_db, suffix, **kwargs):
        port, plat, fund = _env(test_db, suffix)
        event = _event(test_db, port, fund, plat, **kwargs)
        _generate(client, admin_headers, port, EX)
        test_db.expire_all()
        assert test_db.get(ShareChangeEvent, event.id).status == "pending"
        return port, plat, fund, event.id

    def test_update_cancel_delete_blocked_after_receivable_snapshotted(
        self, client, admin_headers, test_db
    ):
        port, plat, fund, event_id = self._generate_ex(client, admin_headers, test_db, "GD1")

        resp = client.put(
            f"/api/share-change-events/{event_id}", headers=admin_headers,
            json={"div_cash": 0.005},
        )
        detail = _error(resp)
        assert detail["error"] == "SNAPSHOT_DEPENDENCY"
        assert detail["details"]["from_date"] == EX.isoformat()

        with pytest.raises(BusinessError) as cancel_exc:
            cancel_share_change_event(test_db, test_db.get(ShareChangeEvent, event_id))
        assert cancel_exc.value.code == "SNAPSHOT_DEPENDENCY"

        with pytest.raises(BusinessError) as delete_exc:
            delete_share_change_event(test_db, test_db.get(ShareChangeEvent, event_id))
        assert delete_exc.value.code == "SNAPSHOT_DEPENDENCY"

        test_db.expire_all()
        row = test_db.get(ShareChangeEvent, event_id)
        assert row.status == "pending"
        assert Decimal(str(row.div_cash)) == DIV_CASH

    def test_notes_only_update_still_allowed(self, client, admin_headers, test_db):
        port, plat, fund, event_id = self._generate_ex(client, admin_headers, test_db, "GD2")
        resp = client.put(
            f"/api/share-change-events/{event_id}", headers=admin_headers,
            json={"notes": "只改备注不影响口径"},
        )
        assert resp.status_code == 200, resp.json()

    def test_edit_dialog_shaped_notes_update_still_allowed(
        self, client, admin_headers, test_db
    ):
        """编辑弹窗形态：恒带日期/金额字段但值未变 + 改备注 → 200。

        弹窗刻意恒发 `ex_date` / `entitlement_date` / `share_confirm_date`（后者要支持
        清空），故豁免必须按**实际变更的字段**判、不能按请求键集合判，否则「仅改 notes
        豁免」在任何 UI 上都不可达（只有 CLI 单发 `--notes` 走得到）。同一 payload 只把
        `div_cash` 改成新值即须重新冻结——证明放行的确是「值未变」而非「守卫失效」。
        """
        port, plat, fund, event_id = self._generate_ex(client, admin_headers, test_db, "GD5")
        row = test_db.get(ShareChangeEvent, event_id)
        payload = {
            "ex_date": row.ex_date.isoformat(),
            "entitlement_date": row.entitlement_date.isoformat(),
            "share_confirm_date": row.share_confirm_date.isoformat(),
            "div_cash": float(row.div_cash),
            "notes": "补一句备注",
        }

        resp = client.put(
            f"/api/share-change-events/{event_id}", headers=admin_headers, json=payload
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["notes"] == "补一句备注"
        test_db.expire_all()
        assert Decimal(str(test_db.get(ShareChangeEvent, event_id).div_cash)) == DIV_CASH

        detail = _error(client.put(
            f"/api/share-change-events/{event_id}", headers=admin_headers,
            json={**payload, "div_cash": 0.005},
        ))
        assert detail["error"] == "SNAPSHOT_DEPENDENCY"
        assert detail["details"]["from_date"] == EX.isoformat()

    def test_update_allowed_before_ex_date_snapshot(self, client, admin_headers, test_db):
        """最新快照日仍早于除息日 → 应收未进任何快照，改期/改额照常放行。"""
        port, plat, fund = _env(test_db, "GD3")
        event = _event(test_db, port, fund, plat, entitlement=BASE)
        updated = update_share_change_event(
            test_db, event, {"div_cash": Decimal("0.005"), "share_confirm_date": MID}
        )
        test_db.commit()
        assert Decimal(str(updated.div_cash)) == Decimal("0.005")
        assert updated.share_confirm_date == MID
        assert [r.amount for r in list_reinvest_receivables(test_db, port, EX)] == [
            quantize_amount(SHARES * Decimal("0.005"))
        ]

    def test_unconfirm_blocked_by_snapshot_dependency(self, client, admin_headers, test_db):
        port, plat, fund = _env(test_db, "GD4")
        event = _event(test_db, port, fund, plat, confirm_date=MID)
        _generate(client, admin_headers, port, EX)
        _auto_confirm(test_db, port, EX)               # 判键 = share_effective_date = MID
        test_db.expire_all()
        event = test_db.get(ShareChangeEvent, event.id)
        assert event.status == "confirmed"

        resp = client.post(
            f"/api/share-change-events/{event.id}/unconfirm", headers=admin_headers
        )
        assert _error(resp)["error"] == "SNAPSHOT_DEPENDENCY"


# ---------------------------------------------------------------------------
# 现场案例（007169）
# ---------------------------------------------------------------------------


def test_007169_field_case(client, admin_headers, test_db):
    """007169 / 易方达：8709.19 份 × 0.0039 = 33.97 元，按 1.0099 折 33.64 份。

    登记日 10-16、除息日 10-19（非同日）、份额到账日 10-21：除息日只记应收，
    到账日份额 8742.83。
    """
    port, plat, fund = _env(test_db, "FLD", shares=Decimal("8709.19"))
    event = _event(test_db, port, fund, plat, entitlement=BASE, ex_date=EX,
                   div_cash=Decimal("0.0039"))
    _generate(client, admin_headers, port, EX)
    rows = _rows(test_db, port, EX)
    assert Decimal(str(rows[("IN_TRANSIT_DIVIDEND", plat)].cash_amount)) == Decimal("33.97")
    assert Decimal(str(rows[(fund, plat)].shares)) == Decimal("8709.19")

    confirm_share_change_event(test_db, test_db.get(ShareChangeEvent, event.id))
    test_db.commit()
    test_db.expire_all()
    confirmed = test_db.get(ShareChangeEvent, event.id)
    assert Decimal(str(confirmed.entitlement_shares)) == Decimal("8709.19")
    assert Decimal(str(confirmed.shares_change)) == Decimal("33.64")
    assert Decimal(str(confirmed.shares_after)) == Decimal("8742.83")

    for day in (MID, APPLY):
        _generate(client, admin_headers, port, day)
    rows = _rows(test_db, port, APPLY)
    assert ("IN_TRANSIT_DIVIDEND", plat) not in rows
    assert Decimal(str(rows[(fund, plat)].shares)) == Decimal("8742.83")
