# ============================================================================
# 单元测试：现金流水聚合读侧 (test_cash_flow_service.py)
# ============================================================================
# issue #725 PR-1。覆盖不依赖 HTTP 层的口径：
#   - 四类来源的 cash_date / flow_type / 符号 / settled 派生规则表（契约调整 C1/C2/C4）
#   - group_total_count 解除类型筛选口径（C5，验收断言 7 的后端面）
#   - 流入/流出的两级排除规则与 recon 公式（C6，断言 5/15 的后端面）
#   - 余额按 (平台, 现金日期) 去重取价、且恒为平台级（C7，断言 1 的后端面）
#   - 响应契约不含组合级余额与「预计到账后余额」投影（断言 1/10 的后端面）
# 端到端行为（HTTP + 快照 + 余额逐行对照）在 tests/integration/test_cash_flows_*.py。
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.schemas.cash_flow import CashFlowRow, PaginatedCashFlowResponse
from app.services import cash_flow_service
from app.services.cash_flow_service import FLOW_TYPES, list_cash_flows
from app.services.exceptions import BusinessError
from app.services.trading_utils import get_next_trading_day
from tests.factories import (
    create_manual_market_value,
    create_portfolio,
    create_share_change_event,
    create_subscription,
    create_trade,
)

TODAY = date.today()
PAST = date(2025, 3, 3)          # 周一，种子日历内（2025-01-01 起工作日为交易日）
PAST_NEXT = date(2025, 3, 4)
FUTURE = TODAY + timedelta(days=7)


@pytest.fixture
def flows_portfolio(test_db) -> str:
    """造齐四类来源的组合 CF_UNIT（固定日期 = 已生效；today+7 = 在途）。"""
    pc = "CF_UNIT"
    create_portfolio(test_db, code=pc, name="现金流水单测组合", status="active")

    # ① 调仓买入：基金腿 pending + CASH sell 腿（#493 创建即 confirmed，trade_date=T）
    create_trade(
        test_db, portfolio_code=pc, product_code="510300.SH", market="CN_EXCHANGE",
        trade_type="buy", amount=8200, platform_code="MYCF", trade_date=PAST,
        confirm_date=PAST_NEXT, transfer_group="rebal_unit0001", status="pending",
    )
    create_trade(
        test_db, portfolio_code=pc, product_code="CASH", market="",
        trade_type="sell", amount=8200, platform_code="MYCF", trade_date=PAST,
        confirm_date=PAST, transfer_group="rebal_unit0001", status="confirmed",
    )

    # ② 调仓卖出：基金腿已确认（C=PAST）+ CASH buy 腿到账日在未来 → 在途
    create_trade(
        test_db, portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
        trade_type="sell", amount=23800, platform_code="HBZQ", trade_date=PAST,
        confirm_date=PAST, transfer_group="rebal_unit0002", status="confirmed",
    )
    create_trade(
        test_db, portfolio_code=pc, product_code="CASH", market="",
        trade_type="buy", amount=23800, platform_code="HBZQ", trade_date=PAST,
        confirm_date=FUTURE, transfer_group="rebal_unit0002", status="confirmed",
    )

    # ③ 申购（CASH buy = 注资入账）与 ④ 赎回（CASH sell = 支付）
    sub = create_subscription(
        test_db, portfolio_code=pc, investor_code="ADMIN", sub_type="subscribe",
        amount=20000, apply_date=PAST, confirm_date=PAST_NEXT, status="confirmed",
        platform_code="HBZQ",
    )
    create_trade(
        test_db, portfolio_code=pc, product_code="CASH", market="",
        trade_type="buy", amount=20000, platform_code="HBZQ", trade_date=PAST,
        confirm_date=PAST_NEXT, transfer_group=f"sub_{sub.id}", status="confirmed",
    )
    redeem = create_subscription(
        test_db, portfolio_code=pc, investor_code="VIEWER", sub_type="redeem",
        amount=62400, apply_date=PAST, confirm_date=PAST_NEXT, status="confirmed",
        platform_code="MYCF",
    )
    create_trade(
        test_db, portfolio_code=pc, product_code="CASH", market="",
        trade_type="sell", amount=62400, platform_code="MYCF", trade_date=PAST,
        confirm_date=PAST_NEXT, transfer_group=f"sub_{redeem.id}", status="confirmed",
    )

    # ⑤ 平台间转移（跨天）：转出腿 confirmed、转入腿 pending（confirm_date=未来）
    create_trade(
        test_db, portfolio_code=pc, product_code="CASH", market="",
        trade_type="sell", amount=15000, platform_code="MYCF", trade_date=PAST,
        confirm_date=PAST, transfer_group="0123456789ab", status="confirmed",
    )
    create_trade(
        test_db, portfolio_code=pc, product_code="CASH", market="",
        trade_type="buy", amount=15000, platform_code="HBZQ", trade_date=PAST,
        confirm_date=FUTURE, transfer_group="0123456789ab", status="pending",
    )
    # 已取消的腿不出行（回退不是流水）
    create_trade(
        test_db, portfolio_code=pc, product_code="CASH", market="",
        trade_type="buy", amount=999, platform_code="MYCF", trade_date=PAST,
        confirm_date=PAST, transfer_group="cancelled0001", status="cancelled",
    )

    # ⑥ 分红：平台级子记录出行，基金级父记录不出行（否则双计）
    create_share_change_event(
        test_db, portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
        event_type="cash_dividend", ex_date=PAST, entitlement_date=PAST,
        status="confirmed", platform_code=None, cash_change=Decimal("640"),
    )
    create_share_change_event(
        test_db, portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
        event_type="cash_dividend", ex_date=PAST, entitlement_date=PAST,
        status="confirmed", platform_code="MYCF", cash_change=Decimal("320"),
        cash_pay_date=PAST,
    )
    # 在途分红：cash_pay_date 在未来（与快照分红在途窗口同判据）
    create_share_change_event(
        test_db, portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
        event_type="cash_dividend", ex_date=PAST, entitlement_date=PAST,
        status="confirmed", platform_code="HBZQ", cash_change=Decimal("860"),
        cash_pay_date=FUTURE,
    )
    # forced_adjustment 归 flow_type=dividend、label 按 event_type（取舍点 T5）
    create_share_change_event(
        test_db, portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
        event_type="forced_adjustment", ex_date=PAST, entitlement_date=PAST,
        status="confirmed", platform_code="MYCF", cash_change=Decimal("-50"),
    )
    # pending 事件不出行（既不进可用现金、也不进快照在途）
    create_share_change_event(
        test_db, portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
        event_type="cash_dividend", ex_date=PAST, entitlement_date=PAST,
        status="pending", platform_code="MYCF", cash_change=Decimal("111"),
    )

    # ⑦ 手动重估（绝对值，非增量）
    create_manual_market_value(
        test_db, portfolio_code=pc, platform_code="HBZQ", product_code="CASH",
        record_date=PAST, market_value=100000, computed_value=92400,
    )
    return pc


def _rows_by_type(result: dict) -> dict:
    out = {}
    for row in result["items"]:
        out.setdefault(row["flow_type"], []).append(row)
    return out


class TestDerivationRules:
    """契约调整 C1/C2/C4：逐来源的 cash_date 锚点、符号、已生效判据。"""

    def test_rebal_buy_leg_anchors_on_trade_date_and_is_negative(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, flow_type="rebal_buy", page_size=50)
        (row,) = result["items"]
        assert row["cash_date"] == PAST                 # 扣款日 T，不是基金腿确认日
        assert row["cash_date_label"] == "扣款日 T"
        assert row["amount"] == Decimal("-8200")        # CASH sell 腿 → 流出
        assert row["settled"] is True
        assert row["status"]["label"] == "已扣款"
        assert row["source_kind"] == "trade_leg"
        # 基金腿 pending：现金已扣、份额未生效，由 note 点明（不与在途混淆）
        assert row["status"]["note"] == "基金腿待确认（现金已扣款）"
        assert row["pair_ref"]["kind"] == "fund_leg"
        assert row["pair_ref"]["settled"] is False

    def test_rebal_sell_leg_anchors_on_arrival_date_and_is_in_transit(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, flow_type="rebal_sell", page_size=50)
        (row,) = result["items"]
        assert row["cash_date"] == FUTURE               # 到账日 A，不是基金确认日 C
        assert row["cash_date_label"] == "到账日 A · 预计"
        assert row["cash_date_sub"] == f"基金确认日 C {PAST}"
        assert row["amount"] == Decimal("23800")
        assert row["settled"] is False                  # confirmed + 未来到账日 = 在途
        assert row["status"]["variant"] == "warning"
        assert row["pair_ref"]["settled"] is True

    def test_subscription_leg_is_inflow_and_redeem_leg_is_outflow(self, test_db, flows_portfolio):
        """取舍点 T1：按后端账本口径，申购 = 流入(+)、赎回 = 流出(−)。"""
        rows = _rows_by_type(list_cash_flows(test_db, flows_portfolio, page_size=50))
        (sub_row,) = rows["sub_buy"]
        assert sub_row["amount"] == Decimal("20000")
        assert sub_row["cash_date"] == PAST_NEXT        # 确认日 C
        assert sub_row["cash_date_label"] == "确认日 C"
        assert sub_row["flow_type_label"] == "申购 · 注资入账"
        assert sub_row["settled"] is True
        assert sub_row["pair_ref"]["kind"] == "subscription"

        (redeem_row,) = rows["redeem"]
        assert redeem_row["amount"] == Decimal("-62400")
        assert redeem_row["cash_date"] == PAST          # 申请日：流出锚定下单日（#70/#78）
        assert redeem_row["cash_date_label"] == "申请日"
        assert redeem_row["cash_date_sub"] == f"到账日 {PAST_NEXT}"
        assert redeem_row["flow_type_label"] == "赎回 · 支付"

    def test_transfer_splits_into_two_rows_pointing_at_each_other(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, flow_type="transfer", page_size=50)
        assert result["total"] == 2                     # 一笔转移 = 两笔现金变动
        out_row = next(r for r in result["items"] if r["amount"] < 0)
        in_row = next(r for r in result["items"] if r["amount"] > 0)

        assert out_row["platform_code"] == "MYCF"
        assert out_row["cash_date_label"] == "转移日"
        assert out_row["settled"] is True
        assert out_row["source_main"] == "华宝证券"      # 来源/去向 = 对方平台名
        assert out_row["pair_ref"]["platform_code"] == "HBZQ"
        assert out_row["pair_ref"]["amount"] == Decimal("15000")

        assert in_row["platform_code"] == "HBZQ"
        assert in_row["cash_date"] == FUTURE
        assert in_row["cash_date_label"] == "到账日 · 预计"
        assert in_row["settled"] is False
        assert in_row["action"] == {"label": "确认到账", "kind": "confirm"}
        assert in_row["pair_ref"]["settled"] is True

    def test_dividend_rows_use_cash_effective_date_and_raw_sign(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, flow_type="dividend", page_size=50)
        assert result["total"] == 3                     # 基金级父记录与 pending 事件不出行
        # MYCF 同日另有 forced_adjustment（同 flow_type），故按平台 + label 取行
        settled = next(
            r for r in result["items"]
            if r["platform_code"] == "MYCF" and r["flow_type_label"] == "现金分红"
        )
        assert settled["amount"] == Decimal("320")      # 事件金额原符号，不翻向
        assert settled["cash_date"] == PAST
        assert settled["cash_date_label"] == "到账日 cash_pay_date"
        assert settled["settled"] is True

        in_transit = next(r for r in result["items"] if r["platform_code"] == "HBZQ")
        assert in_transit["cash_date"] == FUTURE        # cash_pay_date 未来 → 在途
        assert in_transit["settled"] is False
        assert in_transit["status"]["label"] == "在途"
        assert in_transit["flow_type_label"] == "现金分红"

    def test_forced_adjustment_shares_flow_type_but_keeps_own_label(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, flow_type="dividend", page_size=50)
        (row,) = [r for r in result["items"] if r["amount"] < 0]
        assert row["flow_type"] == "dividend"           # 取舍点 T5：不另立值域
        assert row["flow_type_label"] == "现金调整"
        assert row["amount"] == Decimal("-50")
        assert row["cash_date_label"] == "除息日 ex_date"

    def test_revalue_row_is_absolute_and_marked_override(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, flow_type="revalue", page_size=50)
        (row,) = result["items"]
        assert row["amount"] == Decimal("100000")       # 绝对值，非增量
        assert row["is_override"] is True
        assert row["cash_date"] == PAST
        assert row["cash_date_label"] == "覆盖日"
        assert row["source_main"] == "手工更新现金市值"
        # computed_value 是 Numeric(15,4) 审计值，原值直出（T3：读侧不量化）
        assert row["source_sub"] == "覆盖前计算值 92400.0000"
        assert row["action"] == {"label": "撤销", "kind": "revoke"}
        # 无快照 → 覆盖尚未烘焙进快照：不得称「已生效」（余额列与可用现金都不含它）
        assert row["settled"] is False
        assert row["status"] == {
            "variant": "warning",
            "label": "待重算",
            "note": "覆盖尚未烘焙进快照，需重算快照后生效",
        }
        assert row["origin_ref"]["kind"] == "manual_market_value"

    def test_unbaked_revalue_leaves_available_unchanged_and_out_of_settled(
        self, test_db, flows_portfolio
    ):
        """PR #726 评审：未烘焙的覆盖 settled=False、可用现金不含它、也不进在途金额。"""
        before = list_cash_flows(test_db, flows_portfolio, page_size=100)["overview"]

        # 再加一条同日覆盖（MYCF），可用现金与在途金额都不动
        create_manual_market_value(
            test_db, portfolio_code=flows_portfolio, platform_code="MYCF",
            product_code="CASH", record_date=PAST, market_value=1,
        )
        after = list_cash_flows(test_db, flows_portfolio, page_size=100)["overview"]
        assert after["available"] == before["available"]
        assert after["in_transit"] == before["in_transit"]

        # status 筛选：settled 档不含未烘焙覆盖，in_transit 档含（尚未生效）
        assert list_cash_flows(
            test_db, flows_portfolio, status="settled", flow_type="revalue", page_size=50
        )["total"] == 0
        in_transit = list_cash_flows(
            test_db, flows_portfolio, status="in_transit", flow_type="revalue", page_size=50
        )
        assert in_transit["total"] == 2
        assert all(row["settled"] is False for row in in_transit["items"])

    def test_confirmed_sub_buy_with_future_confirm_date_is_not_pending_label(self, test_db):
        """PR #726 评审：T+1 确认的申购注资腿标「已确认 · 待入账」，不是「待确认」。"""
        pc = "CF_SUBBUY"
        create_portfolio(test_db, code=pc, name="申购标签组合", status="active")
        confirm_day = get_next_trading_day(test_db, TODAY)

        # 今天确认：apply_date=今天、confirm_date=下一交易日（确认路径写入的形态：
        # CASH buy 腿 trade_date=apply_date、confirm_date=确认日、直接 confirmed）
        sub = create_subscription(
            test_db, portfolio_code=pc, investor_code="ADMIN", sub_type="subscribe",
            amount=20000, apply_date=TODAY, confirm_date=confirm_day,
            status="confirmed", platform_code="HBZQ",
        )
        create_trade(
            test_db, portfolio_code=pc, product_code="CASH", market="",
            trade_type="buy", amount=20000, platform_code="HBZQ", trade_date=TODAY,
            confirm_date=confirm_day, transfer_group=f"sub_{sub.id}", status="confirmed",
        )

        (row,) = list_cash_flows(test_db, pc, flow_type="sub_buy", page_size=50)["items"]
        assert row["cash_date"] == confirm_day          # 买入锚 confirm_date
        assert row["settled"] is False
        assert row["status"]["label"] == "已确认 · 待入账"
        assert row["status"]["variant"] == "warning"
        # pair_ref 与行同一锚点口径：确认日未到 → 同样未生效
        assert row["pair_ref"]["kind"] == "subscription"
        assert row["pair_ref"]["ref_date"] == confirm_day
        assert row["pair_ref"]["settled"] is False

    def test_cancelled_legs_are_excluded(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, page_size=100)
        assert all(row["amount"] != Decimal("999") for row in result["items"])

    def test_every_flow_type_value_is_in_the_filter_domain(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, page_size=100)
        assert {row["flow_type"] for row in result["items"]} == set(FLOW_TYPES)


class TestGroupCount:
    """C5 / 断言 7：group_total_count = 解除类型筛选后的同 (平台, 现金日期) 总笔数。"""

    def test_group_count_ignores_type_filter(self, test_db, flows_portfolio):
        unfiltered = list_cash_flows(test_db, flows_portfolio, page_size=100)
        expected = {}
        for row in unfiltered["items"]:
            key = (row["platform_code"], row["cash_date"])
            expected[key] = expected.get(key, 0) + 1
        # MYCF 在 PAST 有：调仓买入扣款、赎回支付、转移转出、分红、现金调整 = 5 笔
        assert expected[("MYCF", PAST)] == 5

        for flow_type in ("rebal_buy", "dividend", "transfer"):
            filtered = list_cash_flows(
                test_db, flows_portfolio, flow_type=flow_type, page_size=100
            )
            for row in filtered["items"]:
                key = (row["platform_code"], row["cash_date"])
                if key in expected:
                    assert row["group_total_count"] == expected[key], flow_type

    def test_group_count_follows_status_filter(self, test_db, flows_portfolio):
        """状态筛选保留（与「当日弹窗只解除类型筛选」同口径）。"""
        settled = list_cash_flows(
            test_db, flows_portfolio, status="settled", platform_code="MYCF",
            from_date=PAST, to_date=PAST, page_size=100,
        )
        assert settled["total"] == 5
        assert {row["group_total_count"] for row in settled["items"]} == {5}


class TestOverviewScope:
    """C6 / 断言 5、11、15：流入流出的两级排除规则与 recon 公式。"""

    def test_portfolio_level_excludes_transfer_and_revalue(self, test_db, flows_portfolio):
        overview = list_cash_flows(test_db, flows_portfolio, page_size=100)["overview"]
        # 已生效行：+20000（申购注资）、−8200（调仓买入）、−62400（赎回）、
        #          +320（分红）、−50（现金调整）
        # 排除：转移两腿（内部搬运）、重估（绝对值）、全部在途行
        assert overview["inflow"] == Decimal("20320")
        assert overview["outflow"] == Decimal("-70650")

    def test_platform_level_includes_transfer_legs(self, test_db, flows_portfolio):
        overview = list_cash_flows(
            test_db, flows_portfolio, platform_code="MYCF", page_size=100
        )["overview"]
        # MYCF 已生效：−8200、−62400、−15000（转出腿）、+320、−50
        assert overview["inflow"] == Decimal("320")
        assert overview["outflow"] == Decimal("-85650")

    def test_revalue_never_enters_in_transit(self, test_db, flows_portfolio):
        """未生效的重估是「覆盖日在未来」，不是钱在路上——不得混进在途。"""
        base = list_cash_flows(test_db, flows_portfolio, page_size=100)["overview"]
        # 在途行：+23800（调仓卖出到账）、+15000（转移转入）、+860（分红）
        assert base["in_transit"] == Decimal("39660")

        create_manual_market_value(
            test_db, portfolio_code=flows_portfolio, platform_code="HBZQ",
            product_code="CASH", record_date=FUTURE, market_value=1,
        )
        overview = list_cash_flows(test_db, flows_portfolio, page_size=100)["overview"]
        assert overview["in_transit"] == base["in_transit"]

    def test_in_transit_and_available_ignore_type_and_status_filters(self, test_db, flows_portfolio):
        base = list_cash_flows(test_db, flows_portfolio, page_size=100)["overview"]
        for kwargs in (
            {"flow_type": "dividend"},
            {"status": "settled"},
            {"from_date": FUTURE},
        ):
            other = list_cash_flows(test_db, flows_portfolio, page_size=100, **kwargs)[
                "overview"
            ]
            assert other["in_transit"] == base["in_transit"], kwargs
            assert other["available"] == base["available"], kwargs

    def test_breakdown_recon_equals_settled_transfer_legs(self, test_db, flows_portfolio):
        """断言 15：行和 − 合计 的差 = 本期已生效的转移腿净额。"""
        breakdown = list_cash_flows(test_db, flows_portfolio, page_size=100)[
            "platform_breakdown"
        ]
        rows_in = sum((r["inflow"] for r in breakdown["rows"]), Decimal("0"))
        rows_out = sum((r["outflow"] for r in breakdown["rows"]), Decimal("0"))
        assert breakdown["recon"]["inflow"] == rows_in - breakdown["total"]["inflow"]
        assert breakdown["recon"]["outflow"] == rows_out - breakdown["total"]["outflow"]
        # 已生效转移腿只有 MYCF 的转出 −15000（转入腿在途，不进流量）
        assert breakdown["recon"]["inflow"] == Decimal("0")
        assert breakdown["recon"]["outflow"] == Decimal("-15000")

    def test_breakdown_rows_ignore_platform_filter(self, test_db, flows_portfolio):
        """断言 13：构成弹窗恒为全部平台 + 组合口径合计。"""
        scoped = list_cash_flows(test_db, flows_portfolio, platform_code="MYCF", page_size=1)
        unscoped = list_cash_flows(test_db, flows_portfolio, page_size=1)
        assert scoped["platform_breakdown"] == unscoped["platform_breakdown"]
        assert {r["platform_code"] for r in unscoped["platform_breakdown"]["rows"]} == {
            "MYCF", "HBZQ",
        }


class TestBalancePricing:
    """C7 / 断言 1：余额按 (平台, 现金日期) 去重、恒为平台级单次调用。"""

    def test_balance_lookup_deduped_and_always_platform_scoped(
        self, test_db, flows_portfolio, monkeypatch
    ):
        calls: list = []
        real = cash_flow_service.calculate_available_cash

        def spy(db, portfolio_code, platform_code=None, as_of_date=None):
            calls.append((platform_code, as_of_date))
            return real(db, portfolio_code, platform_code, as_of_date)

        monkeypatch.setattr(cash_flow_service, "calculate_available_cash", spy)
        result = list_cash_flows(
            test_db, flows_portfolio, platform_code="MYCF",
            from_date=PAST, to_date=PAST, page_size=100,
        )
        # MYCF/PAST 组内 5 行共用一格余额：日终与前一交易日各只查一次
        assert calls.count(("MYCF", PAST)) == 1
        assert len(result["items"]) == 5
        assert len({row["platform_end_of_day_balance"] for row in result["items"]}) == 1
        assert len({row["prev_day_balance"] for row in result["items"]}) == 1
        # 余额列永远是平台级：没有一次以 platform_code=None 取行余额
        row_dates = {row["cash_date"] for row in result["items"]}
        assert all(
            plat is not None for plat, as_of in calls if as_of in row_dates
        )

    def test_same_group_rows_share_both_balances(self, test_db, flows_portfolio):
        result = list_cash_flows(
            test_db, flows_portfolio, platform_code="MYCF",
            from_date=PAST, to_date=PAST, page_size=100,
        )
        first = result["items"][0]
        for row in result["items"][1:]:
            assert row["platform_end_of_day_balance"] == first[
                "platform_end_of_day_balance"
            ]
            assert row["prev_day_balance"] == first["prev_day_balance"]

    def test_rows_are_sorted_by_date_desc_then_platform_then_source(self, test_db, flows_portfolio):
        result = list_cash_flows(test_db, flows_portfolio, page_size=100)
        keys = [
            (row["cash_date"], row["platform_code"], row["source_kind"])
            for row in result["items"]
        ]
        assert keys == sorted(
            keys,
            key=lambda k: (
                -k[0].toordinal(), k[1],
                {"trade_leg": 0, "transfer": 1, "dividend": 2, "revalue": 3}[k[2]],
            ),
        )
        # 同 (平台, 日) 必然连续：前端 rowSpan 合并段不会被切断
        seen: set = set()
        previous_group = None
        for key in keys:
            group = key[:2]
            if group != previous_group:
                assert group not in seen, f"分组 {group} 被切断：{keys}"
                seen.add(group)
            previous_group = group


class TestResponseContract:
    """断言 1/10 的契约面：无组合级余额列、无「预计到账后余额」投影字段。"""

    def test_row_schema_has_no_portfolio_level_or_projection_field(self):
        fields = set(CashFlowRow.model_fields)
        assert "platform_end_of_day_balance" in fields
        assert not any("portfolio" in name for name in fields)
        assert not any(
            token in name for name in fields for token in ("projected", "projection")
        )

    def test_response_envelope_fields(self):
        assert set(PaginatedCashFlowResponse.model_fields) == {
            "items", "total", "page", "page_size", "overview", "platform_breakdown",
        }


class TestParamValidation:
    def test_invalid_status_rejected(self, test_db, flows_portfolio):
        with pytest.raises(BusinessError) as exc:
            list_cash_flows(test_db, flows_portfolio, status="confirmed")
        assert exc.value.code == "INVALID_PARAM"
        assert "settled / in_transit" in exc.value.message

    def test_invalid_flow_type_rejected(self, test_db, flows_portfolio):
        with pytest.raises(BusinessError) as exc:
            list_cash_flows(test_db, flows_portfolio, flow_type="cash_adjust")
        assert exc.value.code == "INVALID_PARAM"
        assert "rebal_buy" in exc.value.message

    def test_reversed_date_range_rejected(self, test_db, flows_portfolio):
        with pytest.raises(BusinessError) as exc:
            list_cash_flows(test_db, flows_portfolio, from_date=PAST_NEXT, to_date=PAST)
        assert exc.value.code == "INVALID_DATE_RANGE"

    def test_page_bounds_rejected(self, test_db, flows_portfolio):
        with pytest.raises(BusinessError):
            list_cash_flows(test_db, flows_portfolio, page=0)
        with pytest.raises(BusinessError):
            list_cash_flows(test_db, flows_portfolio, page_size=101)

    def test_unknown_portfolio_raises_not_found(self, test_db):
        from app.services.exceptions import NotFoundError

        with pytest.raises(NotFoundError) as exc:
            list_cash_flows(test_db, "CF_MISSING")
        assert exc.value.message == "组合 CF_MISSING 不存在"


class TestPagination:
    def test_total_counts_cash_moves_and_pages_slice(self, test_db, flows_portfolio):
        full = list_cash_flows(test_db, flows_portfolio, page_size=100)
        assert full["total"] == len(full["items"]) == 10

        first = list_cash_flows(test_db, flows_portfolio, page=1, page_size=4)
        second = list_cash_flows(test_db, flows_portfolio, page=2, page_size=4)
        assert first["total"] == second["total"] == 10
        assert len(first["items"]) == 4 and len(second["items"]) == 4
        assert {r["origin_ref"]["id"] for r in first["items"]}.isdisjoint(
            {r["origin_ref"]["id"] for r in second["items"]}
        )
        # 排序稳定：第一页末尾日期 >= 第二页首行日期
        assert first["items"][-1]["cash_date"] >= second["items"][0]["cash_date"]

    def test_empty_portfolio_returns_empty_page_with_zero_overview(self, test_db):
        create_portfolio(test_db, code="CF_EMPTY", name="空组合", status="draft")
        result = list_cash_flows(test_db, "CF_EMPTY")
        assert result["items"] == [] and result["total"] == 0
        assert result["overview"] == {
            "available": Decimal("0"), "in_transit": Decimal("0"),
            "inflow": Decimal("0"), "outflow": Decimal("0"),
        }
        assert result["platform_breakdown"]["rows"] == []
