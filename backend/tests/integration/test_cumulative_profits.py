"""#598 累计收益服务：真实数据库上的历史流水、日期边界及三粒度守恒。"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import event as sql_event

from app.models import PortfolioPosition, Trade
from app.services import cumulative_profit_service
from app.services.cumulative_profit_service import compute_cumulative_profits
from app.services.exceptions import BusinessError, NotFoundError
from app.utils.quantize import quantize_nav
from tests.factories import (
    create_manual_market_value,
    create_platform,
    create_portfolio,
    create_position_snapshot,
    create_product,
    create_share_change_event,
    create_trade,
    create_value_snapshot,
)


FUND = "CUM_FUND"
MARKET = "CN_OTC"
A, B, C = "CUM_A", "CUM_B", "CUM_C"
START = date(2025, 1, 6)
ENTITLEMENT = date(2025, 1, 9)
EX = date(2025, 1, 10)
MONDAY = date(2025, 1, 13)
AFTER = date(2025, 1, 14)
TRANSIT_CODES = ("IN_TRANSIT_BUY", "IN_TRANSIT_SELL", "IN_TRANSIT_DIVIDEND")
ZERO = Decimal("0")


@pytest.fixture
def portfolio(test_db):
    create_portfolio(test_db, code="CUM_PORT", status="active")
    create_product(test_db, code=FUND, market=MARKET)
    for platform in (A, B, C):
        create_platform(test_db, code=platform)
    return "CUM_PORT"


def _trade(db, portfolio, trade_type, actual_amount, *, product=FUND,
           market=MARKET, platform=A, confirm_date=START, trade_date=START,
           status="confirmed", amount=None, fee="0", transfer_group=None):
    return create_trade(
        db, portfolio_code=portfolio, product_code=product, market=market,
        platform_code=platform, trade_type=trade_type,
        actual_amount=actual_amount,
        amount=actual_amount if amount is None else amount,
        fee=fee, shares=Decimal("100") if product != "CASH" else None,
        price=Decimal("1"), trade_date=trade_date, confirm_date=confirm_date,
        status=status, transfer_group=transfer_group,
    )


def _position(db, portfolio, day, value, *, product=FUND, market=MARKET,
              platform=A, shares="100", cost_price="77"):
    cash = product == "CASH" or product in TRANSIT_CODES
    return create_position_snapshot(
        db, portfolio_code=portfolio, product_code=product, market=market,
        platform_code=platform, snapshot_date=day,
        market_value=Decimal(value), cash_amount=Decimal(value) if cash else None,
        shares=None if cash else Decimal(shares),
        unit_price=Decimal("1"), cost_price=Decimal(cost_price),
    )


def _snapshot(db, portfolio, day):
    total = sum((row.market_value for row in db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == portfolio,
        PortfolioPosition.snapshot_date == day,
    ).all()), ZERO)
    # unit_price 恒 1：大额精度夹具的 total/份额 会超出 MySQL 列范围（SQLite 不校验），
    # 服务只校验快照存在、不读 total_shares/unit_price。
    return create_value_snapshot(
        db, portfolio_code=portfolio, snapshot_date=day,
        total_value=total, total_shares=total, unit_price=Decimal("1"),
    )


def _event(db, portfolio, cash="0", *, product=FUND, market=MARKET,
           platform=A, ex_date=EX, status="confirmed", event_type="cash_dividend",
           **kwargs):
    return create_share_change_event(
        db, portfolio_code=portfolio, product_code=product, market=market,
        platform_code=platform, ex_date=ex_date, entitlement_date=ENTITLEMENT,
        status=status, event_type=event_type,
        cash_change=Decimal(cash) if cash is not None else None, **kwargs,
    )


def _assert_rollups(result):
    """返回字段、Decimal 四位与三粒度严格相加；不借用被测公式算期望收益。"""
    assert set(result) == {"snapshot_date", "platform_products", "products", "platforms"}
    assert type(result["snapshot_date"]) is date
    dimensions = {
        "platform_products": ("product_code", "market", "platform_code"),
        "products": ("product_code", "market"),
        "platforms": ("platform_code",),
    }
    for name, keys in dimensions.items():
        rows = result[name]
        actual_order = [tuple((r[k] is not None, r[k] or "") for k in keys) for r in rows]
        assert actual_order == sorted(actual_order)
        assert len(actual_order) == len(set(actual_order))
        for row in rows:
            assert set(row) == {*keys, "market_value", "cumulative_profit"}
            for field in ("market_value", "cumulative_profit"):
                assert isinstance(row[field], Decimal)
                assert row[field].as_tuple().exponent == -4
    for name in ("products", "platforms"):
        for row in result[name]:
            children = [r for r in result["platform_products"]
                        if all(r[k] == row[k] for k in dimensions[name])]
            for field in ("market_value", "cumulative_profit"):
                assert row[field] == sum((r[field] for r in children), ZERO)
    for field in ("market_value", "cumulative_profit"):
        totals = [sum((r[field] for r in result[name]), ZERO) for name in dimensions]
        assert totals[0] == totals[1] == totals[2]


def _compute(db, portfolio, day):
    result = compute_cumulative_profits(db, portfolio, day)
    assert result["snapshot_date"] == day
    _assert_rollups(result)
    return result


def _rows(result):
    return {
        (r["product_code"], r["market"], r["platform_code"]):
        (r["market_value"], r["cumulative_profit"])
        for r in result["platform_products"]
    }


def test_requires_exact_portfolio_value_snapshot_and_explicit_date(test_db, portfolio):
    _snapshot(test_db, portfolio, START)
    _position(test_db, portfolio, EX, "100")  # 仅有持仓行不证明组合快照完整。
    other = create_portfolio(test_db, code="CUM_OTHER").code
    _snapshot(test_db, other, EX)
    with pytest.raises(NotFoundError) as caught:
        compute_cumulative_profits(test_db, portfolio, EX)
    assert caught.value.code == "NOT_FOUND"
    assert caught.value.http_status == 404
    assert portfolio in caught.value.message
    assert str(EX) in caught.value.message
    with pytest.raises(TypeError):
        compute_cumulative_profits(test_db, portfolio)


@pytest.mark.parametrize("transit_only", [False, True])
def test_empty_or_transit_only_snapshot_has_no_profit_rows(test_db, portfolio, transit_only):
    if transit_only:
        for product in TRANSIT_CODES:
            _position(test_db, portfolio, EX, "99", product=product, market="")
            # 读侧也排除意外落库的在途交易和事件，不能仅过滤快照。
            _trade(test_db, portfolio, "buy", "99", product=product, market="")
            _event(test_db, portfolio, "7", product=product, market="",
                   event_type="forced_adjustment")
    _snapshot(test_db, portfolio, EX)
    assert _compute(test_db, portfolio, EX) == {
        "snapshot_date": EX, "platform_products": [], "products": [], "platforms": [],
    }


@pytest.mark.parametrize("explicit_zero_position", [False, True])
def test_full_history_buy_101_sell_119_closed_profit_is_18(
    test_db, portfolio, explicit_zero_position,
):
    _trade(test_db, portfolio, "buy", "101", amount="100", fee="1")
    _position(test_db, portfolio, START, "100", cost_price="1.01")
    _snapshot(test_db, portfolio, START)
    _trade(test_db, portfolio, "sell", "119", amount="120", fee="1", confirm_date=EX)
    if explicit_zero_position:
        _position(test_db, portfolio, EX, "0", shares="0")
    _snapshot(test_db, portfolio, EX)

    result = _compute(test_db, portfolio, EX)
    assert _rows(result) == {(FUND, MARKET, A): (ZERO, Decimal("18"))}
    assert result["platforms"] == [{
        "platform_code": A, "market_value": ZERO, "cumulative_profit": Decimal("18"),
    }]
    assert result["products"][0]["cumulative_profit"] == Decimal("18")
    assert _rows(_compute(test_db, portfolio, START))[(FUND, MARKET, A)] == (
        Decimal("100"), Decimal("-1"),
    )


def test_partial_sell_then_close_and_rebuy_keeps_all_history(test_db, portfolio):
    partial_day = date(2025, 1, 8)
    _trade(test_db, portfolio, "buy", "101", amount="100", fee="1")
    _trade(test_db, portfolio, "sell", "59", amount="60", fee="1", confirm_date=partial_day)
    _trade(test_db, portfolio, "sell", "59", amount="60", fee="1", confirm_date=EX)
    _trade(test_db, portfolio, "buy", "51", amount="50", fee="1", confirm_date=MONDAY)
    _position(test_db, portfolio, partial_day, "60", shares="50")
    _position(test_db, portfolio, MONDAY, "55", shares="50", cost_price="1.02")
    for day, expected_value, expected_profit in (
        (partial_day, "60", "18"), (EX, "0", "17"), (MONDAY, "55", "21"),
    ):
        _snapshot(test_db, portfolio, day)
        assert _rows(_compute(test_db, portfolio, day))[(FUND, MARKET, A)] == (
            Decimal(expected_value), Decimal(expected_profit),
        )


def test_cross_platform_cash_legs_do_not_move_fund_profit(test_db, portfolio):
    # 入金 B，基金归 A，扣款/卖出到账均在 B；再赎回及转移至 C。
    _trade(test_db, portfolio, "buy", "200", product="CASH", market="", platform=B,
           transfer_group="sub_deposit")
    _trade(test_db, portfolio, "buy", "101", amount="100", fee="1",
           transfer_group="rebal_buy")
    _trade(test_db, portfolio, "sell", "101", product="CASH", market="", platform=B,
           transfer_group="rebal_buy")
    _trade(test_db, portfolio, "sell", "119", amount="120", fee="1", confirm_date=EX,
           transfer_group="rebal_sell")
    _trade(test_db, portfolio, "buy", "119", product="CASH", market="", platform=B,
           confirm_date=EX, transfer_group="rebal_sell")
    _trade(test_db, portfolio, "sell", "18", product="CASH", market="", platform=B,
           confirm_date=EX, transfer_group="sub_redeem")
    _trade(test_db, portfolio, "sell", "200", product="CASH", market="", platform=B,
           confirm_date=EX, transfer_group="cash_transfer")
    _trade(test_db, portfolio, "buy", "200", product="CASH", market="", platform=C,
           confirm_date=EX, transfer_group="cash_transfer")
    _position(test_db, portfolio, EX, "200", product="CASH", market="", platform=C)
    _snapshot(test_db, portfolio, EX)

    result = _compute(test_db, portfolio, EX)
    assert _rows(result) == {
        (FUND, MARKET, A): (ZERO, Decimal("18")),
        ("CASH", "", B): (ZERO, ZERO),
        ("CASH", "", C): (Decimal("200"), ZERO),
    }
    assert {r["platform_code"]: r["cumulative_profit"] for r in result["platforms"]} == {
        A: Decimal("18"), B: ZERO, C: ZERO,
    }


def test_confirmed_sell_profit_precedes_cash_arrival_without_transit_profit(test_db, portfolio):
    _trade(test_db, portfolio, "buy", "101", product="CASH", market="", platform=B)
    _trade(test_db, portfolio, "buy", "101", amount="100", fee="1",
           transfer_group="rebal_buy")
    _trade(test_db, portfolio, "sell", "101", product="CASH", market="", platform=B,
           transfer_group="rebal_buy")
    _trade(test_db, portfolio, "sell", "119", amount="120", fee="1",
           trade_date=ENTITLEMENT, confirm_date=EX, transfer_group="rebal_sell")
    _trade(test_db, portfolio, "buy", "119", product="CASH", market="", platform=B,
           trade_date=EX, confirm_date=MONDAY, transfer_group="rebal_sell")
    _position(test_db, portfolio, EX, "119", product="IN_TRANSIT_SELL", market="", platform=B)
    for day, cash in ((EX, "0"), (MONDAY, "119")):
        _position(test_db, portfolio, day, cash, product="CASH", market="", platform=B)
        _snapshot(test_db, portfolio, day)
        assert _rows(_compute(test_db, portfolio, day)) == {
            (FUND, MARKET, A): (ZERO, Decimal("18")),
            ("CASH", "", B): (Decimal(cash), ZERO),
        }


@pytest.mark.parametrize("cash_pay_date", [None, MONDAY, date(2025, 1, 11)])
def test_dividend_confirmation_ex_and_pay_dates_count_once_including_weekend(
    test_db, portfolio, cash_pay_date,
):
    _trade(test_db, portfolio, "buy", "100")
    _event(test_db, portfolio, "10", cash_pay_date=cash_pay_date,
           confirmed_at=datetime(2025, 1, 9, 16))
    cash_date = cash_pay_date or EX
    for day in (ENTITLEMENT, EX, MONDAY, AFTER):
        fund_value = "100" if day < EX else "90"
        cash = "10" if cash_date <= day else "0"
        _position(test_db, portfolio, day, fund_value)
        _position(test_db, portfolio, day, cash, product="CASH", market="")
        if EX <= day < cash_date:
            _position(test_db, portfolio, day, "10", product="IN_TRANSIT_DIVIDEND", market="")
        _snapshot(test_db, portfolio, day)
        assert _rows(_compute(test_db, portfolio, day)) == {
            (FUND, MARKET, A): (Decimal(fund_value), ZERO),
            ("CASH", "", A): (Decimal(cash), ZERO),
        }
    # 到账周末无快照就拒绝，不能把周五快照冒充周末值，也不前移到账日。
    with pytest.raises(NotFoundError):
        compute_cumulative_profits(test_db, portfolio, date(2025, 1, 11))


@pytest.mark.parametrize("shares_change,cash_change,value,profit", [
    ("10", None, "110", "10"),
    ("-10", None, "90", "-10"),
    ("-100", None, "0", "-100"),
    ("-10", "10", "90", "0"),
    ("10", "-10", "110", "0"),
])
def test_forced_shares_are_not_fictitious_principal(
    test_db, portfolio, shares_change, cash_change, value, profit,
):
    _trade(test_db, portfolio, "buy", "100")
    _trade(test_db, portfolio, "buy", "100", product="CASH", market="")
    _event(test_db, portfolio, cash_change, event_type="forced_adjustment",
           shares_change=Decimal(shares_change))
    if value != "0":
        _position(test_db, portfolio, EX, value, shares=str(100 + Decimal(shares_change)))
    cash = Decimal("100") + Decimal(cash_change or "0")
    _position(test_db, portfolio, EX, str(cash), product="CASH", market="")
    _snapshot(test_db, portfolio, EX)
    assert _rows(_compute(test_db, portfolio, EX)) == {
        (FUND, MARKET, A): (Decimal(value), Decimal(profit)),
        ("CASH", "", A): (cash, ZERO),
    }


@pytest.mark.parametrize("event_type,shares_change,value,profit,fields", [
    ("share_split", "100", "100", "0", {"ratio": Decimal("2")}),
    ("share_merge", "-50", "100", "0", {"ratio": Decimal("0.5")}),
    ("bonus_share", "10", "110", "10", {"ratio": Decimal("0.1")}),
    ("reinvest_dividend", "11.11", "99.9999", "-0.0001",
     {"div_cash": Decimal("0.1"), "reinvest_nav": Decimal("0.9")}),
])
def test_split_merge_bonus_and_reinvestment_do_not_add_investment(
    test_db, portfolio, event_type, shares_change, value, profit, fields,
):
    _trade(test_db, portfolio, "buy", "100")
    _event(test_db, portfolio, event_type=event_type,
           shares_change=Decimal(shares_change), **fields)
    _position(test_db, portfolio, EX, value, shares=str(100 + Decimal(shares_change)))
    _snapshot(test_db, portfolio, EX)
    assert _rows(_compute(test_db, portfolio, EX)) == {
        (FUND, MARKET, A): (Decimal(value), Decimal(profit)),
    }


def test_lof_markets_and_platform_children_are_independent(test_db, portfolio):
    code = "CUM_LOF"
    for market in ("CN_EXCHANGE", MARKET):
        create_product(test_db, code=code, market=market, product_type="LOF")
    for market, platform, value in (("CN_EXCHANGE", B, "80"),
                                    (MARKET, A, "110"), ("CN_EXCHANGE", A, "90")):
        _trade(test_db, portfolio, "buy", "100", product=code, market=market, platform=platform)
        _position(test_db, portfolio, EX, value, product=code, market=market, platform=platform)
    # 父记录的汇总值故意非零，确保按平台/子行统计而非 parent_event_id IS NULL。
    parent = _event(test_db, portfolio, "30", product=code, market="CN_EXCHANGE", platform=None)
    for platform, cash in ((A, "10"), (B, "20")):
        _event(test_db, portfolio, cash, product=code, market="CN_EXCHANGE", platform=platform,
               parent_event_id=parent.id)
        _position(test_db, portfolio, EX, cash, product="CASH", market="", platform=platform)
    _snapshot(test_db, portfolio, EX)
    result = _compute(test_db, portfolio, EX)
    assert _rows(result) == {
        (code, "CN_EXCHANGE", A): (Decimal("90"), ZERO),
        (code, "CN_EXCHANGE", B): (Decimal("80"), ZERO),
        (code, MARKET, A): (Decimal("110"), Decimal("10")),
        ("CASH", "", A): (Decimal("10"), ZERO),
        ("CASH", "", B): (Decimal("20"), ZERO),
    }
    assert [(r["product_code"], r["market"]) for r in result["products"]] == [
        ("CASH", ""), (code, "CN_EXCHANGE"), (code, MARKET),
    ]


def test_union_includes_event_only_and_position_only_keys_but_not_stale_positions(test_db, portfolio):
    create_product(test_db, code="CUM_STALE", market=MARKET)
    create_product(test_db, code="CUM_HELD", market=MARKET)
    _position(test_db, portfolio, START, "900", product="CUM_STALE")
    _snapshot(test_db, portfolio, START)
    _event(test_db, portfolio, "7")
    _position(test_db, portfolio, EX, "7", product="CASH", market="")
    _position(test_db, portfolio, EX, "12", product="CUM_HELD")
    _snapshot(test_db, portfolio, EX)
    assert _rows(_compute(test_db, portfolio, EX)) == {
        (FUND, MARKET, A): (ZERO, Decimal("7")),
        ("CUM_HELD", MARKET, A): (Decimal("12"), Decimal("12")),
        ("CASH", "", A): (Decimal("7"), ZERO),
    }


@pytest.mark.parametrize("status,confirm_date,event_date", [
    ("pending", START, EX), ("cancelled", START, EX),
    ("confirmed", MONDAY, MONDAY), ("confirmed", None, MONDAY),
])
def test_future_pending_cancelled_and_undated_trades_are_excluded(
    test_db, portfolio, status, confirm_date, event_date,
):
    create_product(test_db, code="CUM_EXCLUDED", market=MARKET)
    _trade(test_db, portfolio, "buy", "100")
    _position(test_db, portfolio, EX, "100")
    for trade_type in ("buy", "sell"):
        for product, market in (("CUM_EXCLUDED", MARKET), ("CASH", "")):
            _trade(test_db, portfolio, trade_type, "900", product=product, market=market,
                   platform=B, status=status, confirm_date=confirm_date)
    _event(test_db, portfolio, "999", product="CUM_EXCLUDED", platform=B,
           status=status, ex_date=event_date)
    _snapshot(test_db, portfolio, EX)
    assert _rows(_compute(test_db, portfolio, EX)) == {
        (FUND, MARKET, A): (Decimal("100"), ZERO),
    }


def test_other_portfolio_flows_events_and_positions_cannot_leak(test_db, portfolio):
    other = create_portfolio(test_db, code="CUM_OTHER").code
    _trade(test_db, portfolio, "buy", "100")
    _position(test_db, portfolio, EX, "110")
    _snapshot(test_db, portfolio, EX)
    _trade(test_db, other, "sell", "900")
    _trade(test_db, other, "buy", "800", product="CASH", market="")
    _event(test_db, other, "700")
    _position(test_db, other, EX, "900")
    _snapshot(test_db, other, EX)
    assert _rows(_compute(test_db, portfolio, EX)) == {
        (FUND, MARKET, A): (Decimal("110"), Decimal("10")),
    }


@pytest.mark.parametrize("adjustment,expected_profit", [("5", "8.25"), ("-5", "-1.75")])
def test_cash_own_adjustment_and_manual_revaluation_remain_profit(
    test_db, portfolio, adjustment, expected_profit,
):
    _trade(test_db, portfolio, "buy", "100", product="CASH", market="")
    _trade(test_db, portfolio, "sell", "20", product="CASH", market="")
    _event(test_db, portfolio, adjustment, product="CASH", market="",
           event_type="forced_adjustment", shares_change=None)
    cash = Decimal("80") + Decimal(expected_profit)
    create_manual_market_value(
        test_db, portfolio_code=portfolio, platform_code=A, product_code="CASH",
        record_date=EX, market_value=cash, computed_value=Decimal("80") + Decimal(adjustment),
    )
    for day in (EX, MONDAY):
        # 手动覆盖已经固化到 D 日及后续快照，不再读取/重复应用覆盖表。
        _position(test_db, portfolio, day, str(cash), product="CASH", market="")
        _snapshot(test_db, portfolio, day)
        assert _rows(_compute(test_db, portfolio, day)) == {
            ("CASH", "", A): (cash, Decimal(expected_profit)),
        }


def test_decimal_precision_and_rollups_never_independently_round(test_db, portfolio, monkeypatch):
    for platform in (C, A, B):
        _trade(test_db, portfolio, "buy", "10000000000", platform=platform)
        for _ in range(11):
            _trade(test_db, portfolio, "sell", "0.0001", platform=platform)
        for _ in range(7):
            _trade(test_db, portfolio, "buy", "0.0001", platform=platform)
        _event(test_db, portfolio, "0.0001", platform=platform)
        _position(test_db, portfolio, EX, "10000000000.0001", platform=platform)
        _position(test_db, portfolio, EX, "0.0001", product="CASH", market="", platform=platform)
    _snapshot(test_db, portfolio, EX)
    quantize = Mock(wraps=quantize_nav)
    monkeypatch.setattr(cumulative_profit_service, "quantize_nav", quantize)
    result = _compute(test_db, portfolio, EX)
    for platform in (A, B, C):
        assert _rows(result)[(FUND, MARKET, platform)] == (
            Decimal("10000000000.0001"), Decimal("0.0006"),
        )
    assert result["products"][1]["market_value"] == Decimal("30000000000.0003")
    assert result["products"][1]["cumulative_profit"] == Decimal("0.0018")
    assert quantize.call_count == 2 * len(result["platform_products"])
    assert all(isinstance(call.args[0], Decimal) for call in quantize.call_args_list)


def test_historical_nullable_platforms_sort_stably(test_db, portfolio):
    for platform in (B, None, A):
        _trade(test_db, portfolio, "buy", "1", platform=platform)
        _position(test_db, portfolio, EX, "2", platform=platform)
    _snapshot(test_db, portfolio, EX)
    result = _compute(test_db, portfolio, EX)
    assert [r["platform_code"] for r in result["platform_products"]] == [None, A, B]
    assert [r["platform_code"] for r in result["platforms"]] == [None, A, B]
    assert result == _compute(test_db, portfolio, EX)


@pytest.mark.parametrize("count", [1, 101])
def test_queries_are_fixed_read_only_and_independent_of_list_pagination(
    test_db, portfolio, count, monkeypatch,
):
    for index in reversed(range(count)):
        product = f"CUM_{index:03d}"
        create_product(test_db, code=product, market=MARKET)
        _trade(test_db, portfolio, "buy", "100.01", product=product)
        _event(test_db, portfolio, "0.0001", product=product)
        _position(test_db, portfolio, EX, "101.0101", product=product)
    _position(test_db, portfolio, EX, str(Decimal("0.0001") * count), product="CASH", market="")
    _snapshot(test_db, portfolio, EX)
    page = test_db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == portfolio,
        PortfolioPosition.snapshot_date == EX,
    ).order_by(PortfolioPosition.id).offset(1).limit(1).all()
    assert len(page) == 1

    # 调用方尚未 flush 的改动必须保持原状；服务也不能触发隐式 autoflush。
    trade = test_db.query(Trade).filter(Trade.portfolio_code == portfolio).first()
    trade.notes = "caller-owned unsaved change"
    pending = Trade()
    test_db.add(pending)
    dirty_before, new_before = set(test_db.dirty), set(test_db.new)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def forbidden(*args, **kwargs):
        pytest.fail("cumulative profit service must not write or own the transaction")

    connection = test_db.connection()
    sql_event.listen(connection, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patch:
            for method in ("commit", "rollback", "flush", "add", "add_all", "delete"):
                patch.setattr(test_db, method, forbidden)
            result = _compute(test_db, portfolio, EX)
    finally:
        sql_event.remove(connection, "before_cursor_execute", capture)
    assert set(test_db.dirty) == dirty_before
    assert set(test_db.new) == new_before
    assert trade.notes == "caller-owned unsaved change"
    assert len(statements) == 4
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert all("LIMIT" not in statement.upper() and "OFFSET" not in statement.upper()
               for statement in statements[1:])
    assert len(result["platform_products"]) == count + 1
    assert sum((r["cumulative_profit"] for r in result["platforms"]), ZERO) == (
        Decimal("1.0002") * count
    )


@pytest.mark.parametrize("product,market", [(FUND, MARKET), ("CASH", "")])
def test_missing_confirmed_actual_amount_never_falls_back_to_amount(test_db, portfolio, product, market):
    _trade(test_db, portfolio, "buy", None, amount="100", product=product, market=market)
    _snapshot(test_db, portfolio, EX)
    with pytest.raises(BusinessError) as caught:
        compute_cumulative_profits(test_db, portfolio, EX)
    assert caught.value.code == "INVALID_AMOUNT"
    assert "实际金额" in caught.value.message
