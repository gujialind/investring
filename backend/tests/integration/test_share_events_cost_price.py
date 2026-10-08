"""无本金额事件的成本价摊薄（#673）。

再投资 / 拆分 / 合并 / 送股改变份额时**没有外部资金进出**，持仓的成本基数
（`shares × cost_price`）必须守恒，`cost_price` 随份额反向摊薄或浓缩。修复前
事件应用段只改份额不动成本价，免成本份额被按原成本价计入本金，`holding_profit`
系统性低估（issue 复现例：¥64.25；2:1 拆分下 1000 份 @1.0 凭空产生 −1000 假亏损）。

断言口径两条：
- **成本基数守恒**是主断言，与列标度无关，双方言稳定；
- `cost_price` 落库受 `Numeric(10,4)` 限制，凡由它反推的成本基数带
  `份额 × 5e-5` 的固有量化误差——`holding_profit` 与 `cumulative_profit` 的残差
  按该上界断言，不按 0.01 断言（0.01 在 4 位成本价口径下对任何方案都不可达）。

刻意选 4 位下精确的值（拆分 0.5 / 合并 2.0）做等值断言：SQLite 不强制列标度、
MySQL 按标度舍入，非精确值会让 CI 双跑一侧绿一侧红。
"""

from datetime import date
from decimal import Decimal

import pytest

from app.constants.share_change_events import (
    CAPITAL_FREE_SHARE_EVENT_TYPES,
    CASH_EFFECT_EVENT_TYPES,
)
from app.models import PortfolioPosition, ShareChangeEvent
from app.services.share_change_event_service import (
    confirm_share_change_event,
    create_share_change_event,
)
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
)

BASE = date(2025, 6, 5)
EX = date(2025, 6, 6)
MONDAY = date(2025, 6, 9)
MARKET = "CN_EXCHANGE"

# cost_price 列标度 4 位 → 反推成本基数的误差上界 = 份额 × 半个标度单位
SCALE_BOUND_PER_SHARE = Decimal("0.00005")

# _env 的 position_cost_price 哨兵：区分「未传（跟随 cost_price）」与「显式传 None」
_SAME = object()


def _env(db, suffix, *, shares, cost_price, nav, nav_ex=None, position_cost_price=_SAME):
    """建一个 BASE 日已就绪的组合：一笔基金买入（含配对 CASH 腿）+ 等额申购入金。

    现金净流为 0（申购入金 == 买入出金），避免快照生成的负现金阻断（#203）；
    买入腿必须真实落库，`cumulative_profit` 的全历史净流量口径才有分母。

    `position_cost_price` 只改 BASE 快照行的成本价、不改买入价，用于构造
    「成本价维护上线前落库的历史行」（cost_price 为 NULL）；快照行不可 ORM 更新
    （`models/portfolio_position.py` 的 before_update 守卫），只能建时就指定。
    """
    nav_ex = nav if nav_ex is None else nav_ex
    snapshot_cost = cost_price if position_cost_price is _SAME else position_cost_price
    port, plat, fund = f"CD_{suffix}", f"CDP_{suffix}", f"CDF_{suffix}"
    # 本金量化到分：真实系统的交易金额走 quantize_amount，cumulative_profit 的
    # 全历史净流量口径吃的就是这个 2 位值，不量化会与 issue 复现例差 0.0014
    capital = quantize_amount(Decimal(str(shares)) * Decimal(str(cost_price)))

    create_portfolio(db, code=port, status="active")
    create_platform(db, code=plat)
    create_product(db, code=fund, market=MARKET, product_type="ETF", confirm_days=0)
    create_position_snapshot(
        db, port, fund, MARKET, snapshot_date=BASE, platform_code=plat,
        shares=float(shares), market_value=float(Decimal(str(shares)) * Decimal(str(nav))),
        unit_price=float(nav),
        cost_price=None if snapshot_cost is None else float(snapshot_cost),
    )
    create_position_snapshot(
        db, port, "CASH", "", snapshot_date=BASE,
        platform_code=plat, cash_amount=0, market_value=0,
    )
    create_value_snapshot(
        db, port, BASE, total_value=float(capital), total_shares=float(capital), unit_price=1,
    )
    create_investor_holding(db, port, "VIEWER", BASE, shares=float(capital))
    create_subscription(
        db, port, "VIEWER", amount=float(capital), shares=float(capital), unit_price=1,
        platform_code=plat, apply_date=date(2025, 6, 4), confirm_date=BASE, status="confirmed",
    )
    create_price_record(db, fund, MARKET, BASE, float(nav))
    for day in (EX, MONDAY):
        create_price_record(db, fund, MARKET, day, float(nav_ex))

    group = f"rebal_cd_{suffix}"
    create_trade(
        db, port, "CASH", "", trade_type="buy", platform_code=plat,
        trade_date=date(2025, 6, 4), confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital), price=1,
        transfer_group=f"sub_cd_{suffix}",
    )
    create_trade(
        db, port, "CASH", "", trade_type="sell", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital), price=1,
        transfer_group=group,
    )
    create_trade(
        db, port, fund, MARKET, trade_type="buy", platform_code=plat,
        trade_date=BASE, confirm_date=BASE, status="confirmed",
        amount=float(capital), actual_amount=float(capital),
        price=float(cost_price), shares=float(shares), transfer_group=group,
    )
    return port, plat, fund


def _event(db, port, fund, *, event_type, platform=None, **kwargs):
    """建并确认一条事件（平台级传 platform，基金级不传，确认时自动拆子记录）。"""
    event = create_share_change_event(
        db, portfolio_code=port, product_code=fund, market=MARKET,
        event_type=event_type, ex_date=kwargs.pop("ex_date", EX),
        entitlement_date=BASE, platform_code=platform, force_cover=True, **kwargs,
    )
    confirm_share_change_event(db, event)
    db.commit()
    return event


def _generate(client, headers, port, day):
    response = client.post(
        "/api/snapshots/generate", headers=headers,
        json={"portfolio_code": port, "target_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True, response.text


def _fund_row(db, port, fund, day):
    return db.query(PortfolioPosition).filter_by(
        portfolio_code=port, product_code=fund, market=MARKET, snapshot_date=day,
    ).one()


def _derived(client, headers, port, day):
    response = client.get(
        "/api/positions", headers=headers,
        params={"portfolio_code": port, "snapshot_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    return {p["product_code"]: p for p in response.json()["items"]}


def _by_product(client, headers, port, day):
    response = client.get(
        f"/api/positions/portfolio/{port}/holdings/by-product", headers=headers,
        params={"snapshot_date": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    return {p["product_code"]: p for p in response.json()["products"]}


def _basis(row):
    return Decimal(str(row.shares)) * Decimal(str(row.cost_price))


# ---------------------------------------------------------------------------
# 摊薄：成本基数守恒
# ---------------------------------------------------------------------------


def test_reinvest_dividend_dilutes_cost_price_preserving_basis(
    client, admin_headers, test_db,
):
    """issue #673 复现例：5377.61 份 @1.0943，再投 58.71 份。

    修复前 cost_price 停在 1.0943，免成本的 58.71 份被按原成本价计入本金。
    守恒后 cost_price = 5377.61 × 1.0943 / 5436.32 = 1.0825（4 位）。
    """
    port, plat, fund = _env(
        test_db, "REINV", shares="5377.61", cost_price="1.0943",
        nav="1.0943", nav_ex="1.0796",
    )
    basis_before = Decimal("5377.61") * Decimal("1.0943")

    event = _event(
        test_db, port, fund, event_type="reinvest_dividend", platform=plat,
        div_cash=Decimal("0.0119"), reinvest_nav=Decimal("1.0899"),
    )
    # 两步量化（#425）：quantize_amount(5377.61 × 0.0119) = 63.99，63.99 / 1.0899 = 58.71
    assert Decimal(str(event.shares_change)) == Decimal("58.71")

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("5436.32")
    assert Decimal(str(row.cost_price)) == Decimal("1.0825")
    # 成本基数守恒：只受 4 位列标度影响，不随份额数放大
    assert abs(_basis(row) - basis_before) <= Decimal(str(row.shares)) * SCALE_BOUND_PER_SHARE


def test_share_split_dilutes_cost_price(client, admin_headers, test_db):
    """2:1 拆分：份额翻倍、净值减半，成本价必须减半（4 位下精确）。

    修复前成本价停在 1.0，成本基数翻倍 → 1000 份 @1.0 的持仓凭空产生 −1000 假亏损。
    """
    port, plat, fund = _env(
        test_db, "SPLIT", shares=1000, cost_price=1.0, nav=1.0, nav_ex=0.5,
    )
    _event(test_db, port, fund, event_type="share_split", ratio=Decimal("2"))

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("2000")
    assert Decimal(str(row.cost_price)) == Decimal("0.5")
    assert _basis(row) == Decimal("1000")
    # 市值不变（份额翻倍 × 净值减半），故持有收益仍为 0
    assert Decimal(str(row.market_value)) == Decimal("1000")


def test_share_merge_concentrates_cost_price(client, admin_headers, test_db):
    """1:2 合并是摊薄的反向：份额减半、成本价翻倍，基数同样守恒。"""
    port, plat, fund = _env(
        test_db, "MERGE", shares=1000, cost_price=1.0, nav=1.0, nav_ex=2.0,
    )
    _event(test_db, port, fund, event_type="share_merge", ratio=Decimal("2"))

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("500")
    assert Decimal(str(row.cost_price)) == Decimal("2.0")
    assert _basis(row) == Decimal("1000")


def test_bonus_share_dilutes_cost_price(client, admin_headers, test_db):
    """送股 10%：1000 → 1100 份，成本价摊为 1000/1100 = 0.9091（4 位）。"""
    port, plat, fund = _env(
        test_db, "BONUS", shares=1000, cost_price=1.0, nav=1.0,
    )
    _event(test_db, port, fund, event_type="bonus_share", ratio=Decimal("0.1"))

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("1100")
    assert Decimal(str(row.cost_price)) == Decimal("0.9091")
    assert abs(_basis(row) - Decimal("1000")) <= Decimal(str(row.shares)) * SCALE_BOUND_PER_SHARE


def test_buy_and_capital_free_event_compose_to_same_basis(
    client, admin_headers, test_db,
):
    """同一窗口内「买入 + 事件」的成本基数与顺序无关。

    实现里买入按批先应用、事件后应用，本用例钉的是**这个顺序**：加权平均是基数可加
    （+ n×p），摊薄是基数守恒（× s_old/s_new），故两序在未舍入的代数意义等价。#691 把
    两段都收口到 4 位后，换序的**成本价**不再逐位复现（误差被份额比放大，上界约
    `5e-5 × 旧份额/新份额`，见 business-constraints.md 的份额变动事件节）——可复现的是
    成本基数，因此下面的守恒断言按 `_basis` 而非成本价差判定。
    100 份 @1.0（基数 100）→ EX 买 100 份 @2.0（基数 300、200 份、成本 1.5）
    → 送股 10%（210 份）→ 成本 300/210 = 1.4286。
    """
    port, plat, fund = _env(
        test_db, "COMPOSE", shares=100, cost_price=1.0, nav=1.0, nav_ex=2.0,
    )
    group = "rebal_cd_COMPOSE2"
    # EX 日的追加买入需要现金。入金腿必须落在**本次生成窗口内**（confirm_date >= BASE+1）：
    # 现金基线取自 BASE 快照行的 cash_amount=0，窗口外的流水不会被重新应用。
    create_trade(
        test_db, port, "CASH", "", trade_type="buy", platform_code=plat,
        trade_date=EX, confirm_date=EX, status="confirmed",
        amount=200, actual_amount=200, price=1, transfer_group="sub_cd_COMPOSE2",
    )
    create_trade(
        test_db, port, "CASH", "", trade_type="sell", platform_code=plat,
        trade_date=EX, confirm_date=EX, status="confirmed",
        amount=200, actual_amount=200, price=1, transfer_group=group,
    )
    create_trade(
        test_db, port, fund, MARKET, trade_type="buy", platform_code=plat,
        trade_date=EX, confirm_date=EX, status="confirmed",
        amount=200, actual_amount=200, price=2.0, shares=100, transfer_group=group,
    )
    _event(test_db, port, fund, event_type="bonus_share", ratio=Decimal("0.1"))

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("210")
    assert Decimal(str(row.cost_price)) == Decimal("1.4286")
    assert abs(_basis(row) - Decimal("300")) <= Decimal(str(row.shares)) * SCALE_BOUND_PER_SHARE


def test_capital_free_shares_after_same_window_full_sell_get_zero_basis(
    client, admin_headers, test_db,
):
    """同窗口「先清仓、再白得份额」：基数为 0，成本价摊成 0 而不是沿用卖出前的旧单价。

    白得份额按**权益登记日**持仓派发，与登记日之后是否清仓无关，故这条路径真实可达
    （生成窗口跨多日时更容易命中）。修复前 `old_shares > 0` 守卫挡住摊薄，10 份白得
    份额被贴上 1.0 的旧单价，凭空多出 10 元本金、持有收益少算 10 元——正是 #673 的症状。
    """
    port, plat, fund = _env(
        test_db, "SELLZERO", shares=1000, cost_price=1.0, nav=1.0, nav_ex=1.0,
    )
    group = "rebal_cd_SELLZERO"
    # 清仓腿落在本次生成窗口内（confirm_date >= BASE+1），现金腿是卖出到账
    create_trade(
        test_db, port, "CASH", "", trade_type="buy", platform_code=plat,
        trade_date=EX, confirm_date=EX, status="confirmed",
        amount=1000, actual_amount=1000, price=1, transfer_group=group,
    )
    create_trade(
        test_db, port, fund, MARKET, trade_type="sell", platform_code=plat,
        trade_date=EX, confirm_date=EX, status="confirmed",
        amount=1000, actual_amount=1000, price=1.0, shares=1000,
        transfer_group=group,
    )
    # 送股 1%：登记日基数 1000 份 → 白得 10 份
    _event(test_db, port, fund, event_type="bonus_share", ratio=Decimal("0.01"))

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("10")
    # 事件前基数已是 0（1000 份全部卖出），守恒要求成本价为 0 而不是 NULL
    assert row.cost_price is not None, "0 成本价被折叠成 NULL，基数信息丢失"
    assert Decimal(str(row.cost_price)) == Decimal("0")
    assert _basis(row) == Decimal("0")
    # 读侧：无本金 → 持有收益等于市值（修复前这里是 0.0）
    assert _derived(client, admin_headers, port, EX)[fund]["profit_loss"] == 10.0

    # 次日逐日重建要读回前一日持仓行：0 不得在继承时退化成 NULL，否则收益又变未知
    _generate(client, admin_headers, port, MONDAY)
    next_row = _fund_row(test_db, port, fund, MONDAY)
    assert next_row.cost_price is not None, "0 成本价在次日读回时被折叠成 NULL"
    assert _basis(next_row) == Decimal("0")
    assert _derived(client, admin_headers, port, MONDAY)[fund]["profit_loss"] == 10.0


# ---------------------------------------------------------------------------
# 不摊薄：现金分红与强制调整
# ---------------------------------------------------------------------------


def test_cash_dividend_leaves_cost_price_unchanged(client, admin_headers, test_db):
    """现金分红份额不变（shares_change=0），成本价不得被动，加回项仍由读侧负责。

    再投资与现金分红是同一笔红利的两种形态：前者靠「基数不涨」达成复权，
    后者靠「+Σcash_change」达成复权，两者不得互相串味或重复计入。
    """
    port, plat, fund = _env(
        test_db, "CASHDIV", shares=1000, cost_price=1.0, nav=1.0,
    )
    event = _event(
        test_db, port, fund, event_type="cash_dividend", platform=plat,
        div_cash=Decimal("0.1"), cash_pay_date=EX,
    )
    assert Decimal(str(event.cash_change)) == Decimal("100")

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("1000")
    assert Decimal(str(row.cost_price)) == Decimal("1.0")
    # 复权口径：市值未涨、现金进了 CASH 腿，故 +100 由加回项体现
    assert _derived(client, admin_headers, port, EX)[fund]["profit_loss"] == pytest.approx(
        100.0, abs=1e-4,
    )


def test_forced_adjustment_leaves_cost_price_unchanged(client, admin_headers, test_db):
    """强制调整刻意排除在摊薄之外：shares_change 由用户直填，无客观本金口径。

    可能是数据纠错（不该动基数）也可能是外部转入（该动但无成本价可用），
    维持既有行为，见 CAPITAL_FREE_SHARE_EVENT_TYPES 的注释。
    """
    port, plat, fund = _env(
        test_db, "FORCED", shares=1000, cost_price=1.0, nav=1.0,
    )
    _event(
        test_db, port, fund, event_type="forced_adjustment", platform=plat,
        shares_change=Decimal("100"),
    )

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("1100")
    assert Decimal(str(row.cost_price)) == Decimal("1.0")


def test_capital_free_types_exclude_cash_effect_types():
    """两个白名单不得重叠：动现金的事件与摊成本的事件是互斥的两类口径。

    cash_dividend 的 shares_change 恒为 0（摊薄是空操作），forced_adjustment
    同时出现在现金白名单里——若它也被摊薄，用户直填的现金调整会连带改成本价。
    """
    assert set(CAPITAL_FREE_SHARE_EVENT_TYPES) == {
        "reinvest_dividend", "share_split", "share_merge", "bonus_share",
    }
    assert not set(CAPITAL_FREE_SHARE_EVENT_TYPES) & set(CASH_EFFECT_EVENT_TYPES)


# ---------------------------------------------------------------------------
# 读侧口径：holding_profit 与 cumulative_profit 对齐
# ---------------------------------------------------------------------------


def test_holding_profit_aligns_with_cumulative_within_column_scale(
    client, admin_headers, test_db,
):
    """issue 的核心诉求：两个收益口径本应吻合，残差只来自 cost_price 的 4 位列标度。

    issue 原文断言 ≤0.01，但 quantize.py 明文承诺成本价 4 位、列是 Numeric(10,4)，
    凡从成本价反推基数都带 份额×5e-5 的固有误差（本例上界 ¥0.27、实测 ¥0.096），
    故按列标度上界断言。修复前差值为 ¥64.25，比上界大两个数量级。
    """
    port, plat, fund = _env(
        test_db, "ALIGN", shares="5377.61", cost_price="1.0943",
        nav="1.0943", nav_ex="1.0796",
    )
    _event(
        test_db, port, fund, event_type="reinvest_dividend", platform=plat,
        div_cash=Decimal("0.0119"), reinvest_nav=Decimal("1.0899"),
    )
    _generate(client, admin_headers, port, EX)

    holding = _derived(client, admin_headers, port, EX)[fund]["profit_loss"]
    cumulative = _by_product(client, admin_headers, port, EX)[fund]["cumulative_profit"]
    row = _fund_row(test_db, port, fund, EX)
    bound = float(Decimal(str(row.shares)) * SCALE_BOUND_PER_SHARE)

    # cumulative_profit 走全历史净流量、完全不读 cost_price：5869.0511 − 5884.72
    assert cumulative == pytest.approx(-15.6689, abs=1e-4)
    assert holding == pytest.approx(cumulative, abs=bound)
    # 修复前 holding_profit 为 −79.91，与 cumulative 差 64.25（远超列标度上界）
    assert abs(holding - cumulative) < 1.0


def test_aggregated_holding_profit_uses_diluted_cost(
    client, admin_headers, test_db,
):
    """按产品聚合的 holding_profit 同样吃摊薄后的成本价（读侧无需改动即自洽）。"""
    port, plat, fund = _env(
        test_db, "AGG", shares=1000, cost_price=1.0, nav=1.0, nav_ex=0.5,
    )
    _event(test_db, port, fund, event_type="share_split", ratio=Decimal("2"))
    _generate(client, admin_headers, port, EX)

    product = _by_product(client, admin_headers, port, EX)[fund]
    # 拆分不改经济实质：市值 1000、本金 1000 → 持有收益 0（修复前为 −1000）
    assert product["holding_profit"] == pytest.approx(0.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 回退与重算：无残留、幂等
# ---------------------------------------------------------------------------


def test_unconfirm_and_reconfirm_leaves_no_residue(client, admin_headers, test_db):
    """取消确认后成本价与份额一起回到事件前状态，重确认不重复摊薄。"""
    port, plat, fund = _env(
        test_db, "UNCONF", shares=1000, cost_price=1.0, nav=1.0, nav_ex=0.5,
    )
    event = _event(test_db, port, fund, event_type="share_split", ratio=Decimal("2"))

    _generate(client, admin_headers, port, EX)
    assert Decimal(str(_fund_row(test_db, port, fund, EX).cost_price)) == Decimal("0.5")

    # 删 EX 快照后才能 unconfirm（回退保护 ex_date 及之后的快照）
    assert client.delete(
        f"/api/snapshots/{port}/{EX.isoformat()}", headers=admin_headers,
    ).status_code == 200
    response = client.post(
        f"/api/share-change-events/{event.id}/unconfirm", headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    test_db.refresh(event)
    assert event.status == "pending"
    assert event.shares_change is None

    # 事件前状态未被污染：BASE 行仍是 1000 份 @1.0
    base_row = _fund_row(test_db, port, fund, BASE)
    assert Decimal(str(base_row.shares)) == Decimal("1000")
    assert Decimal(str(base_row.cost_price)) == Decimal("1.0")

    # 重确认 + 重新生成：摊薄只发生一次，不叠加
    confirm_share_change_event(test_db, event)
    test_db.commit()
    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)
    assert Decimal(str(row.shares)) == Decimal("2000")
    assert Decimal(str(row.cost_price)) == Decimal("0.5")


def test_recalculate_is_idempotent(client, admin_headers, test_db):
    """重算逐日重建，成本价摊薄不得因重复重算而累积漂移。"""
    port, plat, fund = _env(
        test_db, "IDEM", shares=1000, cost_price=1.0, nav=1.0, nav_ex=0.5,
    )
    _event(test_db, port, fund, event_type="share_split", ratio=Decimal("2"))
    _generate(client, admin_headers, port, EX)

    for _ in range(2):
        response = client.post("/api/snapshots/recalculate", headers=admin_headers, json={
            "portfolio_code": port,
            "start_date": BASE.isoformat(),
            "end_date": EX.isoformat(),
        })
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True, response.text
        assert all(not r["errors"] for r in response.json()["results"]), response.text
        row = _fund_row(test_db, port, fund, EX)
        assert Decimal(str(row.shares)) == Decimal("2000")
        assert Decimal(str(row.cost_price)) == Decimal("0.5")


def test_dilution_propagates_to_later_snapshots(client, admin_headers, test_db):
    """摊薄后的成本价被后续快照继承（逐日重建读前一日持仓行），不会隔天回弹。"""
    port, plat, fund = _env(
        test_db, "PROP", shares=1000, cost_price=1.0, nav=1.0, nav_ex=0.5,
    )
    _event(test_db, port, fund, event_type="share_split", ratio=Decimal("2"))

    _generate(client, admin_headers, port, EX)
    _generate(client, admin_headers, port, MONDAY)

    assert Decimal(str(_fund_row(test_db, port, fund, MONDAY).cost_price)) == Decimal("0.5")
    assert Decimal(str(_fund_row(test_db, port, fund, MONDAY).shares)) == Decimal("2000")


# ---------------------------------------------------------------------------
# 守卫：无基数可守恒 / 打空持仓时不摊薄
# ---------------------------------------------------------------------------


def test_null_cost_price_row_is_left_untouched(client, admin_headers, test_db):
    """cost_price 为 NULL 的历史行无基数可守恒：份额照改、成本价保持 NULL。

    钉住 `old_cost is not None`——删掉它会在 `old_shares * None` 上抛 TypeError，
    整个快照生成变 500。
    """
    port, plat, fund = _env(
        test_db, "NULLCOST", shares=1000, cost_price=1.0, nav=1.0,
        position_cost_price=None,
    )
    assert _fund_row(test_db, port, fund, BASE).cost_price is None

    _event(test_db, port, fund, event_type="share_split", ratio=Decimal("2"))

    _generate(client, admin_headers, port, EX)
    row = _fund_row(test_db, port, fund, EX)

    assert Decimal(str(row.shares)) == Decimal("2000")
    assert row.cost_price is None
    # 读侧对缺成本价的行不猜收益
    assert _derived(client, admin_headers, port, EX)[fund]["profit_loss"] is None


def test_whitelisted_event_zeroing_position_skips_dilution(
    client, admin_headers, test_db,
):
    """白名单内事件把份额打到 0 时，`new_shares > 0` 守卫拦住零除，仍走既有打空告警。

    可达路径靠份额 2 位量化：期初 1.00 份 + share_merge ratio=1000 →
    `shares_after = quantize_shares(1.00/1000) = 0.00`、`shares_change = -1.00`。
    **不要用 forced_adjustment 构造打空来测这条守卫**——它不在摊薄白名单内，整个分支
    被跳过，删掉守卫也照样绿（该形态的告警由
    `test_snapshot_forced_adjustment.py::test_negative_adjustment_zeroed_position_warning` 钉住）。
    """
    port, plat, fund = _env(
        test_db, "ZERO", shares=1, cost_price=1.0, nav=1.0,
    )
    event = _event(
        test_db, port, fund, event_type="share_merge", ratio=Decimal("1000"),
    )
    assert Decimal(str(event.shares_change)) == Decimal("-1.00")

    response = client.post(
        "/api/snapshots/generate", headers=admin_headers,
        json={"portfolio_code": port, "target_date": EX.isoformat()},
    )
    assert response.status_code == 200, response.text
    # 告警带的是基金级事件确认时拆出的**子记录** id（快照只读 platform_code 非空的行），
    # 故按产品/平台/份额归零三项定位，不与父记录 id 比较
    zeroed = [
        w for w in (response.json()["warnings"] or [])
        if w["type"] == "event_zeroed_position"
    ]
    assert len(zeroed) == 1
    assert zeroed[0]["product_code"] == fund
    assert zeroed[0]["platform_code"] == plat
    assert Decimal(str(zeroed[0]["shares_after"])) == Decimal("0")
    # 份额归零的行被跳过，不落库
    assert test_db.query(PortfolioPosition).filter_by(
        portfolio_code=port, product_code=fund, market=MARKET, snapshot_date=EX,
    ).first() is None


def test_events_still_reject_missing_position(client, admin_headers, test_db):
    """摊薄逻辑不得削弱 #278 的持仓存在性硬拒绝。"""
    port, plat, fund = _env(
        test_db, "MISS", shares=1000, cost_price=1.0, nav=1.0,
    )
    other_platform = "CDP_MISS_OTHER"
    create_platform(test_db, code=other_platform)
    event = create_share_change_event(
        test_db, portfolio_code=port, product_code=fund, market=MARKET,
        event_type="reinvest_dividend", ex_date=EX, entitlement_date=BASE,
        platform_code=other_platform, div_cash=Decimal("0.1"),
        reinvest_nav=Decimal("1.0"), force_cover=True,
    )
    confirm_share_change_event(test_db, event)
    test_db.commit()

    response = client.post(
        "/api/snapshots/generate", headers=admin_headers,
        json={"portfolio_code": port, "target_date": EX.isoformat()},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "POSITION_NOT_FOUND"
    assert test_db.query(ShareChangeEvent).filter_by(id=event.id).one().status == "confirmed"
