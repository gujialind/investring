"""持仓行 4 位口径的产生点量化（#691）。

`portfolio_position.cost_price` 与 `market_value` 的列标度都是 4 位，此前买入加权
平均（`shares × cost / Σshares`）与市值（`shares × nav`）**不在产生点量化**，把收口
外包给方言：MySQL 在 INSERT 时按 `DECIMAL(10,4)` half-away-from-zero 舍入（pymysql
以 `repr()` 送字面量，服务端因此看到精确半数），SQLite 不强制列标度、存成二进制
float，读回按 `"%.4f"` 收口——十进制半数存不进双精度，偏下的一侧就少 0.0001。
分叉方向逐值而定：等份额两笔买入的 60 组构造实测 27 组分叉，2 位份额 × 4 位净值的
12 万组实测 2.5% 分叉。

断言一律取「第 5 位恰为 5」的边界值，按 `quantize_nav`（HALF_UP）的期望值精确等值：
量化收在产生点后，SQLite（CI `backend-test`）与 MySQL（CI `backend-test-mysql`）
必须落同一个值，两侧跑同一批用例。
"""

from datetime import date
from decimal import Decimal

from app.models import PortfolioPosition, PortfolioValueSnapshot
from app.utils.quantize import quantize_amount, quantize_nav
from tests.factories import (
    create_investor_holding,
    create_platform,
    create_portfolio,
    create_position_snapshot,
    create_price_record,
    create_product,
    create_trade,
    create_value_snapshot,
)

BASE = date(2025, 6, 5)
TARGET = date(2025, 6, 6)
MARKET = "CN_EXCHANGE"


def _env(db, suffix, *, legs, cash="0"):
    """BASE 日基线：若干净值型持仓行 + 一条 CASH 行 + 当日市值/投资人份额快照。

    `legs` 是 `(shares, cost_price, nav)` 三元组列表；`nav` 同时用于 BASE 与 TARGET
    两日的 PriceRecord，使 TARGET 市值可预算为 `份额 × nav`。
    CASH 行的余额就是可用现金，买入用例需预先注资到不少于出金额（#203 负现金阻断）。
    """
    port = f"QP_{suffix}"
    plat = f"QPP_{suffix}"
    create_portfolio(db, code=port, status="active")
    create_platform(db, code=plat)

    funds = []
    total = Decimal(cash)
    for i, (shares, cost_price, nav) in enumerate(legs):
        fund = f"QPF_{suffix}_{i}"
        create_product(db, code=fund, market=MARKET, product_type="ETF", confirm_days=0)
        create_price_record(db, fund, MARKET, BASE, float(nav))
        create_price_record(db, fund, MARKET, TARGET, float(nav))
        create_position_snapshot(
            db, port, fund, MARKET, snapshot_date=BASE, platform_code=plat,
            shares=float(shares), cost_price=float(cost_price), unit_price=float(nav),
            market_value=float(quantize_nav(Decimal(shares) * Decimal(nav))),
        )
        total += Decimal(shares) * Decimal(nav)
        funds.append(fund)

    create_position_snapshot(
        db, port, "CASH", "", snapshot_date=BASE, platform_code=plat,
        cash_amount=float(Decimal(cash)), unit_price=None, cost_price=None,
        market_value=float(Decimal(cash)),
    )
    capital = quantize_nav(total)
    create_value_snapshot(
        db, port, BASE, total_value=float(capital), total_shares=float(capital), unit_price=1,
    )
    create_investor_holding(db, port, "VIEWER", BASE, shares=float(capital))
    return {"port": port, "plat": plat, "funds": funds, "capital": capital}


def _buy(db, env, fund_index, *, shares, price):
    """TARGET 日一笔已确认的场内买入（含等额配对 CASH 出金腿），出金由 BASE 现金承担。"""
    env_port, plat = env["port"], env["plat"]
    fund = env["funds"][fund_index]
    amount = quantize_amount(Decimal(shares) * Decimal(price))
    group = f"rebal_qp_{env_port}_{fund_index}"
    create_trade(
        db, env_port, fund, MARKET, trade_type="buy", platform_code=plat,
        trade_date=TARGET, confirm_date=TARGET, status="confirmed",
        amount=float(amount), actual_amount=float(amount),
        price=float(price), shares=float(shares), transfer_group=group,
    )
    create_trade(
        db, env_port, "CASH", "", trade_type="sell", platform_code=plat,
        trade_date=TARGET, confirm_date=TARGET, status="confirmed",
        amount=float(amount), actual_amount=float(amount), price=1,
        transfer_group=group,
    )
    return amount


def _generate(client, admin_headers, port):
    response = client.post(
        "/api/snapshots/generate", headers=admin_headers,
        json={"portfolio_code": port, "target_date": TARGET.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True, response.text


def _row(db, port, product_code):
    return db.query(PortfolioPosition).filter_by(
        portfolio_code=port, product_code=product_code, snapshot_date=TARGET,
    ).one()


def _snapshot(db, port):
    return db.query(PortfolioValueSnapshot).filter_by(
        portfolio_code=port, snapshot_date=TARGET,
    ).one()


# ---------------------------------------------------------------------------
# 买入加权平均成本价
# ---------------------------------------------------------------------------


def test_second_buy_weighted_average_is_half_up_boundary(client, admin_headers, test_db):
    """100 份 @2.0000 再买 100 份 @2.0001 → 加权平均 2.00005，落库必须进位到 2.0001

    未量化时 SQLite 把 2.00005 存成 `2.00004999…` 的二进制 float，读回收口到 2.0000，
    比 MySQL 少 0.0001（issue 复现例第 1 行）。
    """
    env = _env(test_db, "BUY1", legs=[("100", "2.0000", "2.0000")], cash="200.01")
    _buy(test_db, env, 0, shares="100", price="2.0001")

    _generate(client, admin_headers, env["port"])

    row = _row(test_db, env["port"], env["funds"][0])
    assert Decimal(str(row.shares)) == Decimal("200.00")
    assert Decimal(str(row.cost_price)) == Decimal("2.0001"), (
        f"买入加权平均应经 quantize_nav 得 2.0001，实际 {row.cost_price}"
        "（2.0000 说明收口被外包给了方言）"
    )


def test_unequal_shares_buy_average_boundary(client, admin_headers, test_db):
    """300 份 @0.2344 再买 100 份 @0.2346 → 0.23445，期望 0.2345

    份额不等的配比同样命中第 5 位为 5，不是「两笔等量」才有的罕见构造。
    """
    env = _env(test_db, "BUY2", legs=[("300", "0.2344", "0.2344")], cash="23.46")
    _buy(test_db, env, 0, shares="100", price="0.2346")

    _generate(client, admin_headers, env["port"])

    row = _row(test_db, env["port"], env["funds"][0])
    assert Decimal(str(row.shares)) == Decimal("400.00")
    assert Decimal(str(row.cost_price)) == Decimal("0.2345"), (
        f"期望 0.2345，实际 {row.cost_price}（0.2344 = SQLite 侧 float 偏下后被收口）"
    )


def test_buy_average_already_exact_side_is_not_regressed(client, admin_headers, test_db):
    """100 份 @1.0000 再买 100 份 @1.0001 → 1.00005 的双精度值恰好偏上，两侧本已一致

    修复前后同为 1.0001，作防回归用：量化只应改回分叉的那些值，不动本已一致的值。
    """
    env = _env(test_db, "BUY3", legs=[("100", "1.0000", "1.0000")], cash="200.01")
    _buy(test_db, env, 0, shares="100", price="1.0001")

    _generate(client, admin_headers, env["port"])

    row = _row(test_db, env["port"], env["funds"][0])
    assert Decimal(str(row.cost_price)) == Decimal("1.0001")


def test_first_buy_takes_price_unchanged(client, admin_headers, test_db):
    """首笔买入（窗口内无基数）直接取成交价，不产生长小数，也不被量化改动

    `else` 分支的 `Trade.price` 来自 `Numeric(10,4)`，恒 ≤4 位；本用例钉住「修复没有
    顺手动它」——用 4 位满标的价格，任何多余处理都会显形。
    """
    env = _env(test_db, "BUY4", legs=[("0", "0", "1.2345")], cash="123.45")
    # BASE 行为 0 份额占位行，窗口内这笔是组合里该产品的第一笔有效买入
    _buy(test_db, env, 0, shares="100", price="1.2345")

    _generate(client, admin_headers, env["port"])

    row = _row(test_db, env["port"], env["funds"][0])
    assert Decimal(str(row.shares)) == Decimal("100.00")
    assert Decimal(str(row.cost_price)) == Decimal("1.2345")


# ---------------------------------------------------------------------------
# 市值：份额 × 净值
# ---------------------------------------------------------------------------


def test_market_value_boundary_is_half_up(client, admin_headers, test_db):
    """0.50 份 × 净值 1.0001 = 0.50005 → market_value 必须落 0.5001

    2 位份额 × 4 位净值最长按 6 位小数落 `Numeric(15,4)`；末两位恰为 `50` 时即命中
    与 cost_price 同类的方言分叉（实测占普通配比的 2.5%）。
    """
    env = _env(test_db, "MV1", legs=[("0.50", "1.0001", "1.0001")])

    _generate(client, admin_headers, env["port"])

    row = _row(test_db, env["port"], env["funds"][0])
    assert Decimal(str(row.market_value)) == Decimal("0.5001"), (
        f"市值应在产生点量化到 0.5001，实际 {row.market_value}"
    )


def test_total_value_is_the_sum_of_quantized_position_rows(client, admin_headers, test_db):
    """逐行收口后 `Σ 持仓行市值 == total_value`（#691 引入的聚合一致性）

    两个边界持仓：0.50×1.0001=0.50005、200.10×1.0825=216.60825。修复前 total_value
    累加的是**未收口**的乘积（217.10830 → 217.1083），与按列标度落库的行市值之和
    （0.5001+216.6083=217.1084）在 MySQL 上就已对不上；修复后两侧同为 217.1084。
    """
    env = _env(test_db, "MV2", legs=[
        ("0.50", "1.0001", "1.0001"),
        ("200.10", "1.0825", "1.0825"),
    ])

    _generate(client, admin_headers, env["port"])

    rows = [_row(test_db, env["port"], fund) for fund in env["funds"]]
    assert [Decimal(str(r.market_value)) for r in rows] == [
        Decimal("0.5001"), Decimal("216.6083"),
    ]
    cash_row = _row(test_db, env["port"], "CASH")
    row_sum = sum(Decimal(str(r.market_value)) for r in rows + [cash_row])
    snap = _snapshot(test_db, env["port"])
    assert Decimal(str(snap.total_value)) == row_sum, (
        f"组合市值 {snap.total_value} 应等于逐行市值之和 {row_sum}"
    )
