# ============================================================================
# 集成测试：现金流水余额列与快照吸收 (test_cash_flows_balance.py)
# ============================================================================
# issue #725 PR-1，验收断言 1/2/3/9/11 的后端面。余额列不是本模块自算的数：
# 它必须逐行等于 `calculate_available_cash(as_of=cash_date, platform)` 的**单次
# 调用**结果，否则详情弹窗的对账块（前日日终 + 本笔 = 日终）不可能闭合。
#
# 覆盖：
#   1. 每行 platform_end_of_day_balance / prev_day_balance 与直调引擎逐行相等；
#   2. 同 (平台, 现金日) 的多行共享同一对余额（前端 rowSpan 合并一格的前提）；
#   3. 类型 / 状态 / 区间筛选不改变在屏行的余额值；
#   9. 未到账行不进流入流出、只进在途（按到账平台归属），余额格照常计算；
#  11. 手动重估经快照生成被吸收后，日终余额 = 重估值（同日增量不再叠加）。
# HTTP 契约与筛选口径在 test_cash_flows_api.py。
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from app.services.position_service import calculate_available_cash, update_cash_position
from app.services.snapshot_service import generate_daily_snapshots
from app.services.trading_utils import try_get_prev_trading_day
from tests.factories import (
    create_investor_holding,
    create_manual_market_value,
    create_portfolio,
    create_position_snapshot,
    create_share_change_event,
    create_trade,
    create_value_snapshot,
    ensure_trading_day,
)

D0 = date(2025, 6, 6)   # 周五：基线快照日
D1 = date(2025, 6, 9)   # 周一：增量 + 重估日
D2 = date(2025, 6, 10)  # 周二：跨天转移的预计到账日
PORTFOLIO = "CF_BAL"
GROUP_PORTFOLIO = "CF_BAL_GRP"
BAKE_PORTFOLIO = "CF_BAL_BAKE"
GROUP_SAME_DAY = "d4e5f6a1b2c3"
GROUP_CROSS_DAY = "c3d4e5f6a1b2"
BASE_CASH = Decimal("50000")
OVERRIDE = Decimal("60000")


@pytest.fixture
def balanced_portfolio(test_db) -> str:
    """D0 基线快照 5 万；D1 两笔增量（分红 +3000、转出 −1500）+ 重估（已烘焙进快照）。"""
    for day in (D0, D1, D2):
        ensure_trading_day(test_db, day, is_open=True)
    create_portfolio(test_db, code=PORTFOLIO, name="现金流水余额组合", status="active")

    # D0 基线三表快照（只有现金，无基金持仓 → 生成 D1 快照不需要净值）
    create_position_snapshot(
        test_db, PORTFOLIO, "CASH", "", snapshot_date=D0,
        cash_amount=float(BASE_CASH), unit_price=None, cost_price=None,
        market_value=float(BASE_CASH), platform_code="MYCF",
    )
    create_value_snapshot(
        test_db, PORTFOLIO, D0, total_value=float(BASE_CASH),
        total_shares=float(BASE_CASH), unit_price=1.0,
    )
    create_investor_holding(test_db, PORTFOLIO, "VIEWER", D0, shares=float(BASE_CASH))

    # D1 ① 现金分红 +3000（entitlement_date 落在 D0：generate_daily_snapshots 会
    # 级联回退 entitlement_date == 目标日的 confirmed 事件、以及 apply_date == 目标日
    # 的 confirmed 申赎并删其 CASH 腿（#203），故 D1 增量用「权益日在前」的事件造，
    # 不用申赎腿——否则夹具的行会被快照生成自己吃掉）
    create_share_change_event(
        test_db, portfolio_code=PORTFOLIO, product_code="000300.OF", market="CN_OTC",
        event_type="cash_dividend", ex_date=D1, entitlement_date=D0,
        status="confirmed", platform_code="MYCF", cash_change=Decimal("3000"),
        cash_pay_date=D1,
    )

    # D1 ② 跨天转移 MYCF → HBZQ 1500（转入腿 D2 到账，仍 pending）
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="sell", amount=1500, platform_code="MYCF", trade_date=D1,
        confirm_date=D1, transfer_group=GROUP_CROSS_DAY, status="confirmed",
    )
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="buy", amount=1500, platform_code="HBZQ", trade_date=D1,
        confirm_date=D2, transfer_group=GROUP_CROSS_DAY, status="pending",
    )

    # D1 ③ 手动重估：绝对替换 MYCF 现金为 60000，随后生成快照把它烘焙进去
    update_cash_position(
        test_db, portfolio_code=PORTFOLIO, platform_code="MYCF",
        amount=OVERRIDE, update_date=D1, created_by="ADMIN",
    )
    test_db.flush()
    result = generate_daily_snapshots(test_db, PORTFOLIO, D1)
    assert result["success"] is True, result
    test_db.flush()
    return PORTFOLIO


def _rows(client, admin_headers, portfolio=PORTFOLIO, **params) -> list:
    params.setdefault("page_size", 100)
    response = client.get(
        f"/api/portfolios/{portfolio}/cash-flows", headers=admin_headers, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _overview(client, admin_headers, portfolio=PORTFOLIO, **params) -> dict:
    params.setdefault("page_size", 1)
    response = client.get(
        f"/api/portfolios/{portfolio}/cash-flows", headers=admin_headers, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()["overview"]


class TestBalanceEqualsEngine:
    def test_every_row_balance_is_a_single_engine_call(
        self, test_db, client, admin_headers, balanced_portfolio
    ):
        """断言 1：余额列 = calculate_available_cash(as_of=cash_date, platform)。"""
        rows = _rows(client, admin_headers)
        assert rows, "夹具未产生任何流水行"
        for row in rows:
            cash_date = date.fromisoformat(row["cash_date"])
            expected = calculate_available_cash(
                test_db, balanced_portfolio, row["platform_code"], as_of_date=cash_date
            )
            assert Decimal(row["platform_end_of_day_balance"]) == expected, row

            prev = try_get_prev_trading_day(test_db, cash_date)
            if prev is None:
                assert row["prev_day_balance"] is None, row
            else:
                assert Decimal(row["prev_day_balance"]) == calculate_available_cash(
                    test_db, balanced_portfolio, row["platform_code"], as_of_date=prev
                ), row

    def test_rows_in_same_group_share_both_balances(
        self, client, admin_headers, balanced_portfolio
    ):
        """断言 2：同 (平台, 现金日) 的余额是日终口径，组内每行同值（rowSpan 合并）。"""
        grouped = {}
        for row in _rows(client, admin_headers):
            grouped.setdefault((row["platform_code"], row["cash_date"]), []).append(row)
        multi = {key: rows for key, rows in grouped.items() if len(rows) > 1}
        assert multi, "夹具应至少有一组同日同平台多笔"
        for rows in multi.values():
            assert len({r["platform_end_of_day_balance"] for r in rows}) == 1
            assert len({r["prev_day_balance"] for r in rows}) == 1
            assert len({r["group_total_count"] for r in rows}) == 1
            assert rows[0]["group_total_count"] == len(rows)

    def test_filters_do_not_change_balance_values(
        self, client, admin_headers, balanced_portfolio
    ):
        """断言 3：余额是时点事实，不随类型/状态/区间筛选变化。"""
        baseline = {
            (r["origin_ref"]["kind"], r["origin_ref"]["id"]): (
                r["platform_end_of_day_balance"], r["prev_day_balance"]
            )
            for r in _rows(client, admin_headers)
        }
        for params in (
            {"flow_type": "transfer"},
            {"status": "settled"},
            {"status": "in_transit"},
            {"from_date": D1, "to_date": D2},
            {"platform_code": "MYCF", "flow_type": "sub_buy"},
        ):
            for row in _rows(client, admin_headers, **params):
                key = (row["origin_ref"]["kind"], row["origin_ref"]["id"])
                assert baseline[key] == (
                    row["platform_end_of_day_balance"], row["prev_day_balance"]
                ), params


class TestInTransitAndOverride:
    def test_unsettled_row_keeps_balance_but_stays_out_of_flows(
        self, test_db, client, admin_headers, balanced_portfolio
    ):
        """断言 9：在途行不进流入/流出、只进在途（按到账平台），余额格照常。"""
        (row,) = _rows(client, admin_headers, status="in_transit")
        assert row["platform_code"] == "HBZQ"          # 按到账平台归属
        assert row["cash_date"] == D2.isoformat()
        assert Decimal(row["amount"]) == Decimal("1500")
        assert row["platform_end_of_day_balance"] is not None
        assert Decimal(row["platform_end_of_day_balance"]) == calculate_available_cash(
            test_db, balanced_portfolio, "HBZQ", as_of_date=D2
        )

        portfolio = _overview(client, admin_headers)
        assert Decimal(portfolio["in_transit"]) == Decimal("1500")
        # 组合级不含转移腿（内部搬运）：只有分红 +3000 进流量
        assert Decimal(portfolio["inflow"]) == Decimal("3000")
        assert Decimal(portfolio["outflow"]) == Decimal("0")
        hbzq = _overview(client, admin_headers, platform_code="HBZQ")
        assert Decimal(hbzq["in_transit"]) == Decimal("1500")   # 按到账平台归属
        assert Decimal(hbzq["inflow"]) == Decimal("0")          # 转入腿在途不计
        mycf = _overview(client, admin_headers, platform_code="MYCF")
        assert Decimal(mycf["in_transit"]) == Decimal("0")
        # 平台级含对应腿：转出腿已生效 → 计入 MYCF 流出
        assert Decimal(mycf["inflow"]) == Decimal("3000")
        assert Decimal(mycf["outflow"]) == Decimal("-1500")

    def test_revalue_row_is_absorbed_by_snapshot(
        self, test_db, client, admin_headers, balanced_portfolio
    ):
        """断言 11：重估经快照烘焙后日终余额 = 重估值，同日增量被吸收不再叠加。"""
        (row,) = _rows(client, admin_headers, flow_type="revalue")
        assert row["is_override"] is True
        assert Decimal(row["amount"]) == OVERRIDE        # 绝对值，不是增量
        assert row["cash_date"] == D1.isoformat()
        assert Decimal(row["platform_end_of_day_balance"]) == OVERRIDE
        assert Decimal(row["platform_end_of_day_balance"]) == calculate_available_cash(
            test_db, balanced_portfolio, "MYCF", as_of_date=D1
        )
        # 重估行与同日增量行（分红 +3000、转出 −1500）共享同一日终余额
        same_day = [
            r for r in _rows(client, admin_headers, platform_code="MYCF")
            if r["cash_date"] == D1.isoformat()
        ]
        assert len(same_day) == 3
        assert {
            Decimal(r["platform_end_of_day_balance"]) for r in same_day
        } == {OVERRIDE}
        # 覆盖已烘焙进快照 → 不再挂「需重算快照」注记
        assert row["status"]["note"] is None

    def test_revalue_never_enters_flows(
        self, test_db, client, admin_headers, balanced_portfolio
    ):
        """断言 11 的另一半：重估是存量修正，两级流入/流出都不得计入。"""
        overview = _overview(client, admin_headers)
        # 组合级：分红 +3000 进流量、转移腿排除、重估（60000 绝对值）绝不进流量
        assert Decimal(overview["inflow"]) == Decimal("3000")
        assert Decimal(overview["outflow"]) == Decimal("0")
        # 可用现金卡是时点事实：等于引擎的组合级单次调用
        assert Decimal(overview["available"]) == calculate_available_cash(
            test_db, balanced_portfolio, None
        )
        # 平台级：转移腿计入，重估仍不计入
        mycf = _overview(client, admin_headers, platform_code="MYCF")
        assert Decimal(mycf["inflow"]) == Decimal("3000")
        assert Decimal(mycf["outflow"]) == Decimal("-1500")


@pytest.fixture
def group_sum_portfolio(test_db) -> str:
    """D0 基线 5 万（MYCF）；D1 该平台三笔已生效流量、**无重估**——供组级对账式和。"""
    for day in (D0, D1):
        ensure_trading_day(test_db, day, is_open=True)
    create_portfolio(
        test_db, code=GROUP_PORTFOLIO, name="现金流水组级对账组合", status="active"
    )
    create_position_snapshot(
        test_db, GROUP_PORTFOLIO, "CASH", "", snapshot_date=D0,
        cash_amount=float(BASE_CASH), unit_price=None, cost_price=None,
        market_value=float(BASE_CASH), platform_code="MYCF",
    )
    create_value_snapshot(
        test_db, GROUP_PORTFOLIO, D0, total_value=float(BASE_CASH),
        total_shares=float(BASE_CASH), unit_price=1.0,
    )
    create_investor_holding(
        test_db, GROUP_PORTFOLIO, "VIEWER", D0, shares=float(BASE_CASH)
    )

    # D1 ① 现金分红 +3000 ② 现金调整 −800（权益日均落 D0，避免被生成路径级联回退）
    create_share_change_event(
        test_db, portfolio_code=GROUP_PORTFOLIO, product_code="000300.OF",
        market="CN_OTC", event_type="cash_dividend", ex_date=D1, entitlement_date=D0,
        status="confirmed", platform_code="MYCF", cash_change=Decimal("3000"),
        cash_pay_date=D1,
    )
    create_share_change_event(
        test_db, portfolio_code=GROUP_PORTFOLIO, product_code="000300.OF",
        market="CN_OTC", event_type="forced_adjustment", ex_date=D1,
        entitlement_date=D0, status="confirmed", platform_code="MYCF",
        cash_change=Decimal("-800"),
    )
    # D1 ③ 同日到账的平台间转移 MYCF → HBZQ 1500（两腿皆 confirmed）
    create_trade(
        test_db, portfolio_code=GROUP_PORTFOLIO, product_code="CASH", market="",
        trade_type="sell", amount=1500, platform_code="MYCF", trade_date=D1,
        confirm_date=D1, transfer_group=GROUP_SAME_DAY, status="confirmed",
    )
    create_trade(
        test_db, portfolio_code=GROUP_PORTFOLIO, product_code="CASH", market="",
        trade_type="buy", amount=1500, platform_code="HBZQ", trade_date=D1,
        confirm_date=D1, transfer_group=GROUP_SAME_DAY, status="confirmed",
    )
    test_db.flush()
    result = generate_daily_snapshots(test_db, GROUP_PORTFOLIO, D1)
    assert result["success"] is True, result
    test_db.flush()
    return GROUP_PORTFOLIO


class TestGroupReconciliation:
    """rule-cash 的对账式是**组级**：前日 + 同日同平台全部已生效流量 = 日终。"""

    def test_prev_plus_all_settled_group_flows_equals_end_of_day(
        self, client, admin_headers, group_sum_portfolio
    ):
        rows = _rows(
            client, admin_headers, portfolio=GROUP_PORTFOLIO,
            platform_code="MYCF", from_date=D1, to_date=D1,
        )
        assert len(rows) == 3                    # 分红 +3000、现金调整 −800、转出 −1500
        assert all(row["settled"] for row in rows)
        assert not any(row["is_override"] for row in rows), "本组刻意不含重估日"

        # 组内共享同一对余额（rowSpan 合并一格的前提）
        end_of_day = {Decimal(row["platform_end_of_day_balance"]) for row in rows}
        prev_day = {Decimal(row["prev_day_balance"]) for row in rows}
        assert len(end_of_day) == 1 and len(prev_day) == 1
        (end,) = end_of_day
        (prev,) = prev_day

        assert prev == BASE_CASH
        flows = sum((Decimal(row["amount"]) for row in rows), Decimal("0"))
        assert flows == Decimal("700")           # 3000 − 800 − 1500
        assert prev + flows == end               # 组级式闭合

        # 单笔式在同日多笔时必然不闭合——文档与前端文案不得写成「前日 + 本笔 = 日终」
        assert all(prev + Decimal(row["amount"]) != end for row in rows)

    def test_revalue_day_replaces_instead_of_summing(
        self, test_db, client, admin_headers, balanced_portfolio
    ):
        """重估日不参与组级求和：日终 = 覆盖值，同日增量被吸收。"""
        rows = _rows(
            client, admin_headers, platform_code="MYCF", from_date=D1, to_date=D1
        )
        overrides = [row for row in rows if row["is_override"]]
        increments = [row for row in rows if not row["is_override"]]
        assert len(overrides) == 1 and len(increments) == 2
        (prev,) = {Decimal(row["prev_day_balance"]) for row in rows}
        (end,) = {Decimal(row["platform_end_of_day_balance"]) for row in rows}
        flows = sum((Decimal(row["amount"]) for row in increments), Decimal("0"))
        assert end == OVERRIDE
        assert prev + flows != end               # 求和式在重估日不成立
        assert prev + flows + Decimal(overrides[0]["amount"]) != end


@pytest.fixture
def unbaked_override_portfolio(test_db) -> str:
    """D0 基线快照 5 万；D1 覆盖 6 万但**尚未生成 D1 快照** → 覆盖未烘焙进快照。"""
    for day in (D0, D1):
        ensure_trading_day(test_db, day, is_open=True)
    create_portfolio(
        test_db, code=BAKE_PORTFOLIO, name="现金重估烘焙组合", status="active"
    )
    create_position_snapshot(
        test_db, BAKE_PORTFOLIO, "CASH", "", snapshot_date=D0,
        cash_amount=float(BASE_CASH), unit_price=None, cost_price=None,
        market_value=float(BASE_CASH), platform_code="MYCF",
    )
    create_value_snapshot(
        test_db, BAKE_PORTFOLIO, D0, total_value=float(BASE_CASH),
        total_shares=float(BASE_CASH), unit_price=1.0,
    )
    create_investor_holding(
        test_db, BAKE_PORTFOLIO, "VIEWER", D0, shares=float(BASE_CASH)
    )
    update_cash_position(
        test_db, portfolio_code=BAKE_PORTFOLIO, platform_code="MYCF",
        amount=OVERRIDE, update_date=D1, created_by="ADMIN",
    )
    test_db.flush()
    return BAKE_PORTFOLIO


class TestRevalueBaking:
    """PR #726 评审：重估行 settled = 覆盖已烘焙进快照，未烘焙标「待重算」。"""

    def test_unbaked_override_is_unsettled_and_leaves_available_alone(
        self, test_db, client, admin_headers, unbaked_override_portfolio
    ):
        (row,) = _rows(
            client, admin_headers, portfolio=BAKE_PORTFOLIO, flow_type="revalue"
        )
        assert row["cash_date"] == D1.isoformat()
        assert row["settled"] is False
        assert row["status"] == {
            "variant": "warning",
            "label": "待重算",
            "note": "覆盖尚未烘焙进快照，需重算快照后生效",
        }
        # 可用现金不含未烘焙的覆盖：仍是 D0 基线
        overview = _overview(client, admin_headers, portfolio=BAKE_PORTFOLIO)
        assert Decimal(overview["available"]) == BASE_CASH
        assert Decimal(overview["available"]) == calculate_available_cash(
            test_db, BAKE_PORTFOLIO, None
        )
        # 也不进在途金额（绝对值不是增量），但 status=in_transit 会选中它
        assert Decimal(overview["in_transit"]) == Decimal("0")
        (in_transit,) = _rows(
            client, admin_headers, portfolio=BAKE_PORTFOLIO, status="in_transit"
        )
        assert in_transit["flow_type"] == "revalue"
        assert _rows(
            client, admin_headers, portfolio=BAKE_PORTFOLIO, status="settled"
        ) == []

    def test_generating_the_snapshot_flips_settled_to_true(
        self, test_db, client, admin_headers, unbaked_override_portfolio
    ):
        """当天覆盖（无对应快照）同样 settled=False；烘焙按覆盖日逐条判定。"""
        today_override = create_manual_market_value(
            test_db, portfolio_code=BAKE_PORTFOLIO, platform_code="MYCF",
            product_code="CASH", record_date=date.today(), market_value=1,
        )
        today_row = next(
            row for row in _rows(client, admin_headers, portfolio=BAKE_PORTFOLIO)
            if row["origin_ref"]["id"] == today_override.id
        )
        assert today_row["settled"] is False
        assert today_row["status"]["label"] == "待重算"

        result = generate_daily_snapshots(test_db, BAKE_PORTFOLIO, D1)
        assert result["success"] is True, result
        test_db.flush()

        (row,) = _rows(
            client, admin_headers, portfolio=BAKE_PORTFOLIO,
            flow_type="revalue", from_date=D1, to_date=D1,
        )
        assert row["settled"] is True
        assert row["status"] == {"variant": "neutral", "label": "已生效", "note": None}
        overview = _overview(client, admin_headers, portfolio=BAKE_PORTFOLIO)
        assert Decimal(overview["available"]) == OVERRIDE
        assert Decimal(row["platform_end_of_day_balance"]) == OVERRIDE

        # 当天那条覆盖仍未烘焙（最新快照日 = D1 < 今天）→ 不跟着一起翻
        still_unbaked = next(
            r for r in _rows(client, admin_headers, portfolio=BAKE_PORTFOLIO)
            if r["origin_ref"]["id"] == today_override.id
        )
        assert still_unbaked["settled"] is False
        assert still_unbaked["status"]["label"] == "待重算"
