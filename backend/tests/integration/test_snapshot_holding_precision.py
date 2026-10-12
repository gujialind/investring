"""投资人份额快照三列（market_value/total_cost/profit）的产生点量化（#696）。

`_generate_investor_holding` 的这三列此前不在产生点量化，按 `float()` 直写
`Numeric(15,4)`：MySQL 服务端按 DECIMAL 标度 half-away-from-zero 舍入、SQLite 存
二进制 float 按标度读回——改代码前实测（真实列类型 + 生产同形绑定路径，双方言直插
直读，见 issue #696「实测回填」）：`market_value` 12 万组 1586 组分叉（1.32%）、
`total_cost` 现状形态 0.63%（含 206 组长小数商经 float 往返 repr 塌缩恰成半数、
MySQL 落值偏离精确真值 HALF_UP）、`profit` 现状形态 0.53%。

修复口径（issue #696「评估结论」）：`total_cost` 由**落库收口值** `cost_per_share`
派生（行内自洽 `total_cost == quantize_nav(shares × cost_per_share)`，即模型注释
语义）；`profit` 为两个精确 4 位值之差，按构造恒精确 4 位、无需再量化（同形态 2 万
组实测 0 分叉）。断言取「第 5 位恰为 5」的边界值，修复后 SQLite（CI `backend-test`）
与 MySQL（CI `backend-test-mysql`）必须落同一个值。
"""

from datetime import date
from decimal import Decimal

from app.models import InvestorHolding
from app.services.snapshot_service import _generate_investor_holding
from app.utils.quantize import quantize_nav
from tests.factories import (
    create_investor,
    create_investor_holding,
    create_portfolio,
    create_subscription,
    create_value_snapshot,
)

BASE = date(2025, 6, 5)
TARGET = date(2025, 6, 6)


def _env(db, suffix, *, base_shares=None, base_cost=None):
    """前序（BASE 日）投资人份额快照；不传 base_* 则组合无历史快照（首申购窗口）。"""
    port = f"HM_{suffix}"
    inv = f"HMI_{suffix}"
    create_portfolio(db, code=port, status="active")
    create_investor(db, code=inv)
    if base_shares is not None:
        create_investor_holding(
            db, port, inv, BASE, shares=base_shares, cost_per_share=base_cost,
        )
    return port, inv


def _subscribe(db, port, inv, *, shares, unit_price, confirm_date=TARGET):
    create_subscription(
        db, port, inv, "subscribe",
        amount=float(Decimal(str(shares)) * Decimal(str(unit_price))),
        shares=shares, unit_price=unit_price,
        apply_date=confirm_date, confirm_date=confirm_date, status="confirmed",
    )


def _generate(db, port, unit_price):
    """TARGET 日生成投资人份额快照并落库，读回该投资人的行（真实方言往返）。"""
    snap = create_value_snapshot(
        db, port, TARGET,
        total_value=float(unit_price), total_shares=1, unit_price=float(unit_price),
    )
    holdings = _generate_investor_holding(db, port, TARGET, snap)
    db.add_all(holdings)
    db.commit()
    db.expire_all()
    return db.query(InvestorHolding).filter_by(
        portfolio_code=port, snapshot_date=TARGET,
    ).one()


def test_market_value_boundary_is_half_up(test_db):
    """前持 0.50 份、当日净值 1.0001 → 0.50×1.0001=0.50005 必须落 0.5001

    修复前 SQLite 把 0.50005 的双精度值（偏下）读回收口到 0.5000，MySQL 落 0.5001
    （issue #696 实测该形态 12 万组中 1586 组分叉）。
    """
    port, _ = _env(test_db, "MV1", base_shares=0.50, base_cost=1.0000)

    row = _generate(test_db, port, "1.0001")

    assert Decimal(str(row.market_value)) == Decimal("0.5001"), (
        f"市值应在产生点量化到 0.5001，实际 {row.market_value}"
    )


def test_total_cost_carry_forward_is_half_up(test_db):
    """零申购窗口（递推形态 2×4）：0.50 份 @ 成本价 1.0001 → total_cost 落 0.5001

    净值取 2.0000 使 market_value 恰为精确 4 位，本用例只钉 total_cost 的边界。
    """
    port, _ = _env(test_db, "TC1", base_shares=0.50, base_cost=1.0001)

    row = _generate(test_db, port, "2.0000")

    assert Decimal(str(row.market_value)) == Decimal("1.0000")
    assert Decimal(str(row.total_cost)) == Decimal("0.5001")


def test_multi_subscribe_window_pins_derived_basis(test_db):
    """多笔申购窗口钉口径：total_cost 由落库收口值 cost_per_share 派生（#696 评估结论）

    前 0.50@1.0000 + 申购 0.25@1.0010 → 加权平均商 1.00033…3（28 位），落库
    `cost_per_share = 1.0003` 不变；`total_cost = quantize_nav(0.75 × 1.0003) =
    0.7502`。修复前现状形态 `0.75 × 未收口商` 的真值为 0.7502499…98，经 float()
    后 repr 塌缩成 0.75025 精确半数——SQLite 读回 0.7502、MySQL 服务端进位 0.7503
    （issue #696 实测登记的四位塌缩形态在本构造上逐字复现）；修复后两侧同落 0.7502。
    """
    port, inv = _env(test_db, "MS1", base_shares=0.50, base_cost=1.0000)
    _subscribe(test_db, port, inv, shares="0.25", unit_price="1.0010")

    row = _generate(test_db, port, "1.0000")

    assert Decimal(str(row.shares)) == Decimal("0.75")
    assert Decimal(str(row.cost_per_share)) == Decimal("1.0003")
    assert Decimal(str(row.total_cost)) == Decimal("0.7502"), (
        f"total_cost 应等于 quantize_nav(份额×落库成本价) = 0.7502，实际 {row.total_cost}"
    )
    assert Decimal(str(row.market_value)) == Decimal("0.7500")
    assert Decimal(str(row.profit)) == Decimal("-0.0002")


def test_first_subscribe_window_boundary(test_db):
    """首申购窗口（无前序快照）：0.50 份 @ 1.0001、当日净值 1.0001 → 两值各 0.50005 → 0.5001

    `prev_cost = new_price` 分支（Numeric(15,4)/申购传入价口径，恒 ≤4 位）不产生
    长小数，但派生乘积仍是 2×4 边界形态。
    """
    port, inv = _env(test_db, "FS1")
    _subscribe(test_db, port, inv, shares="0.50", unit_price="1.0001",
               confirm_date=TARGET)

    row = _generate(test_db, port, "1.0001")

    assert Decimal(str(row.cost_per_share)) == Decimal("1.0001")
    assert Decimal(str(row.market_value)) == Decimal("0.5001")
    assert Decimal(str(row.total_cost)) == Decimal("0.5001")
    assert Decimal(str(row.profit)) == Decimal("0.0000")


def test_zero_cost_path_is_not_broken(test_db):
    """prev_cost=0 路径：三列落 0（净值同为 0），不崩，cost_per_share 不为 None

    量化 0 无效果；本用例挡「收口改动把 0/None 分支弄崩或写成 NULL」。
    """
    port, _ = _env(test_db, "ZC1", base_shares=0.50, base_cost=None)

    row = _generate(test_db, port, "0")

    assert row.cost_per_share is not None
    assert Decimal(str(row.cost_per_share)) == Decimal("0")
    assert Decimal(str(row.market_value)) == Decimal("0")
    assert Decimal(str(row.total_cost)) == Decimal("0")
    assert Decimal(str(row.profit)) == Decimal("0")


def test_row_internal_consistency_of_stored_values(test_db):
    """落库值自洽：profit == market_value − total_cost 且
    total_cost == quantize_nav(shares × cost_per_share)（口径 A 的钉桩）

    取多笔申购窗口（三列形态都非平凡）逐条核对。
    """
    port, inv = _env(test_db, "IC1", base_shares=0.50, base_cost=1.0000)
    _subscribe(test_db, port, inv, shares="0.25", unit_price="1.0010")

    row = _generate(test_db, port, "1.0001")

    mv = Decimal(str(row.market_value))
    tc = Decimal(str(row.total_cost))
    pf = Decimal(str(row.profit))
    assert pf == mv - tc, f"落库 profit {pf} 应等于 mv {mv} − tc {tc}"
    assert tc == quantize_nav(Decimal(str(row.shares)) * Decimal(str(row.cost_per_share)))
