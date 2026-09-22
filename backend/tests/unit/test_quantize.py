# ============================================================================
# 单元测试：份额/金额/净值精度量化工具 (test_quantize.py)
# ============================================================================
# 覆盖 app/utils/quantize.py::quantize_shares / quantize_amount / quantize_nav：
# - ROUND_HALF_UP 行为（四舍五入，末位后一位 >= 5 进位；份额 issue #87、金额 issue #94、
#   4 位估值口径 issue #428）
# - 负数远离零进位（与正数按绝对值对称）
# - None 透传
# - Decimal / float / int / str 输入兼容
# ============================================================================

import ast
from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from app.utils.quantize import (
    AMOUNT_QUANT,
    NAV_QUANT,
    SHARES_QUANT,
    quantize_amount,
    quantize_nav,
    quantize_shares,
)


class TestQuantizeShares:
    """quantize_shares 基本行为"""

    def test_half_up_rounds_third_decimal(self):
        """第 3 位 >= 5 进位，< 5 舍去"""
        assert quantize_shares(Decimal("6837.2967")) == Decimal("6837.30")
        assert quantize_shares(Decimal("6837.299")) == Decimal("6837.30")
        assert quantize_shares(Decimal("6837.295")) == Decimal("6837.30")
        assert quantize_shares(Decimal("6837.294")) == Decimal("6837.29")
        assert quantize_shares(Decimal("0.019")) == Decimal("0.02")
        assert quantize_shares(Decimal("0.014")) == Decimal("0.01")

    def test_half_up_boundary_005(self):
        """边界值：恰为 0.005 时进位"""
        assert quantize_shares(Decimal("0.005")) == Decimal("0.01")
        assert quantize_shares(Decimal("0.0049")) == Decimal("0.00")
        assert quantize_shares(Decimal("1.005")) == Decimal("1.01")

    def test_negative_away_from_zero(self):
        """负数与正数按绝对值对称：远离零进位"""
        assert quantize_shares(Decimal("-1.235")) == Decimal("-1.24")
        assert quantize_shares(Decimal("-1.234")) == Decimal("-1.23")
        assert quantize_shares(Decimal("-0.005")) == Decimal("-0.01")
        assert quantize_shares(Decimal("-6837.295")) == Decimal("-6837.30")

    def test_already_two_decimals_unchanged(self):
        """已是 2 位小数的值保持不变"""
        assert quantize_shares(Decimal("6837.30")) == Decimal("6837.30")
        assert quantize_shares(Decimal("6837.29")) == Decimal("6837.29")
        assert quantize_shares(Decimal("0")) == Decimal("0.00")

    def test_result_exponent_is_two_decimals(self):
        """结果统一量化到 0.01（exponent = -2）"""
        assert quantize_shares(Decimal("100")).as_tuple().exponent == -2
        assert quantize_shares(Decimal("6837.2967")).as_tuple().exponent == -2

    def test_none_passthrough(self):
        """None 原样返回，方便可空字段透传"""
        assert quantize_shares(None) is None

    def test_float_input(self):
        """float 输入经 str 转换后量化，无二进制浮点误差"""
        assert quantize_shares(6837.2967) == Decimal("6837.30")
        assert quantize_shares(6837.29) == Decimal("6837.29")

    def test_str_input(self):
        """str 输入直接量化"""
        assert quantize_shares("6837.2967") == Decimal("6837.30")
        assert quantize_shares("6837.30") == Decimal("6837.30")

    def test_int_input(self):
        """int 输入量化为 2 位小数"""
        assert quantize_shares(6837) == Decimal("6837.00")

    def test_division_result_half_up(self):
        """申购/买入确认典型场景：amount / nav 除不尽时四舍五入"""
        # issue #87 实例：3000 / 0.9757 = 3074.715588...，HALF_UP → 3074.72
        shares = quantize_shares(Decimal("3000") / Decimal("0.9757"))
        assert shares == Decimal("3074.72")
        # 10000 / 1.4623 = 6838.5426...，HALF_UP → 6838.54
        shares = quantize_shares(Decimal("10000") / Decimal("1.4623"))
        assert shares == Decimal("6838.54")

    def test_shares_quant_constant(self):
        """SHARES_QUANT 为 0.01（2 位小数口径）"""
        assert SHARES_QUANT == Decimal("0.01")


class TestQuantizeAmount:
    """quantize_amount 基本行为（issue #94，语义与 quantize_shares 对称）"""

    def test_issue_94_sell_amount(self):
        """issue #94 实例：卖出 6837.30 份 × 净值 1.1024 = 7537.43952 → 7537.44"""
        amount = quantize_amount(Decimal("6837.30") * Decimal("1.1024"))
        assert amount == Decimal("7537.44")

    def test_half_up_rounds_third_decimal(self):
        """第 3 位 >= 5 进位，< 5 舍去"""
        assert quantize_amount(Decimal("7537.4395")) == Decimal("7537.44")
        assert quantize_amount(Decimal("7537.435")) == Decimal("7537.44")
        assert quantize_amount(Decimal("7537.434")) == Decimal("7537.43")
        assert quantize_amount(Decimal("0.019")) == Decimal("0.02")
        assert quantize_amount(Decimal("0.014")) == Decimal("0.01")

    def test_half_up_boundary_005(self):
        """边界值：恰为 0.005 时进位"""
        assert quantize_amount(Decimal("0.005")) == Decimal("0.01")
        assert quantize_amount(Decimal("0.0049")) == Decimal("0.00")
        assert quantize_amount(Decimal("1.005")) == Decimal("1.01")

    def test_negative_away_from_zero(self):
        """负数与正数按绝对值对称：远离零进位"""
        assert quantize_amount(Decimal("-1.235")) == Decimal("-1.24")
        assert quantize_amount(Decimal("-1.234")) == Decimal("-1.23")
        assert quantize_amount(Decimal("-0.005")) == Decimal("-0.01")
        assert quantize_amount(Decimal("-7537.435")) == Decimal("-7537.44")

    def test_already_two_decimals_unchanged(self):
        """已是 2 位小数的值保持不变（幂等）"""
        assert quantize_amount(Decimal("7537.44")) == Decimal("7537.44")
        assert quantize_amount(Decimal("0")) == Decimal("0.00")

    def test_result_exponent_is_two_decimals(self):
        """结果统一量化到 0.01（exponent = -2）"""
        assert quantize_amount(Decimal("100")).as_tuple().exponent == -2
        assert quantize_amount(Decimal("7537.4395")).as_tuple().exponent == -2

    def test_none_passthrough(self):
        """None 原样返回，方便可空字段透传"""
        assert quantize_amount(None) is None

    def test_float_input(self):
        """float 输入经 str 转换后量化，无二进制浮点误差"""
        assert quantize_amount(7537.4395) == Decimal("7537.44")
        assert quantize_amount(7537.44) == Decimal("7537.44")

    def test_str_input(self):
        """str 输入直接量化"""
        assert quantize_amount("7537.4395") == Decimal("7537.44")
        assert quantize_amount("7537.44") == Decimal("7537.44")

    def test_int_input(self):
        """int 输入量化为 2 位小数"""
        assert quantize_amount(7537) == Decimal("7537.00")

    def test_subtraction_of_quantized_stays_two_decimals(self):
        """两个 2 位金额相减仍精确为 2 位（买入 amount = actual_amount - fee 口径）"""
        net = quantize_amount(Decimal("7537.44")) - quantize_amount(Decimal("1.50"))
        assert net == Decimal("7535.94")
        assert net.as_tuple().exponent == -2

    def test_amount_quant_constant(self):
        """AMOUNT_QUANT 为 0.01（2 位小数口径）"""
        assert AMOUNT_QUANT == Decimal("0.01")


class TestQuantizeNav:
    """quantize_nav 基本行为（issue #428：4 位估值口径显式 ROUND_HALF_UP）

    本条的核心是**判别式**：4 位口径此前各调用点写 `Decimal("0.0001")` 且不传
    `rounding=`，走 Decimal 上下文缺省的 ROUND_HALF_EVEN（银行家舍入）。HALF_UP 与
    HALF_EVEN 只在「恰好第 5 位为 5 且其后为 0」的边界值上分叉——下面前两个断言就是
    这两者分叉的现场：1.23445 → HALF_UP 1.2345 / HALF_EVEN 1.2344。
    """

    def test_half_up_boundary_differs_from_banker_rounding(self):
        """边界值：末位后一位恰为 5 → 进位（HALF_EVEN 会得 1.2344，本断言即判别式）"""
        assert quantize_nav(Decimal("1.23445")) == Decimal("1.2345")
        assert Decimal("1.23445").quantize(Decimal("0.0001")) == Decimal(
            "1.2344"
        ), "Decimal 缺省是 HALF_EVEN——本行绿即证明 quantize_nav 显式覆盖了它"

    def test_half_up_rounds_fifth_decimal(self):
        """第 5 位 >= 5 进位，< 5 舍去"""
        assert quantize_nav(Decimal("1.23446")) == Decimal("1.2345")
        assert quantize_nav(Decimal("1.23444")) == Decimal("1.2344")
        assert quantize_nav(Decimal("1.00005")) == Decimal("1.0001")
        assert quantize_nav(Decimal("1.00004")) == Decimal("1.0000")
        assert quantize_nav(Decimal("0.00005")) == Decimal("0.0001")
        assert quantize_nav(Decimal("0.00004")) == Decimal("0.0000")

    def test_negative_away_from_zero(self):
        """负数与正数按绝对值对称：远离零进位（与 2 位 helper 同语义）"""
        assert quantize_nav(Decimal("-1.23445")) == Decimal("-1.2345")
        assert quantize_nav(Decimal("-1.23444")) == Decimal("-1.2344")
        assert quantize_nav(Decimal("-0.00005")) == Decimal("-0.0001")

    def test_already_four_decimals_unchanged(self):
        """已是 4 位小数的值保持不变（幂等）——nav_price 来自 Numeric(10,4) 的常态"""
        assert quantize_nav(Decimal("1.2345")) == Decimal("1.2345")
        assert quantize_nav(Decimal("4.2000")) == Decimal("4.2000")
        assert quantize_nav(Decimal("0")) == Decimal("0.0000")

    def test_result_exponent_is_four_decimals(self):
        """结果统一量化到 0.0001（exponent = -4）"""
        assert quantize_nav(Decimal("100")).as_tuple().exponent == -4
        assert quantize_nav(Decimal("1.23445")).as_tuple().exponent == -4

    def test_none_passthrough(self):
        """None 原样返回，方便可空字段透传"""
        assert quantize_nav(None) is None

    def test_float_input(self):
        """float 输入经 str 转换后量化（无二进制浮点误差）"""
        assert quantize_nav(1.2345) == Decimal("1.2345")
        assert quantize_nav(4.2) == Decimal("4.2000")

    def test_str_input(self):
        """str 输入直接量化——`1.23445` 这类边界值必须能按字面量表达"""
        assert quantize_nav("1.23445") == Decimal("1.2345")

    def test_int_input(self):
        """int 输入量化为 4 位小数"""
        assert quantize_nav(1) == Decimal("1.0000")

    def test_nav_quant_constant(self):
        """NAV_QUANT 为 0.0001（4 位小数口径）"""
        assert NAV_QUANT == Decimal("0.0001")


class TestAmountToSharesTwoStepQuantization:
    """#425：金额 → 份额的转换必须先量化金额到分，再折算份额。

    `reinvest_dividend` 是全系统唯一「金额 → 份额」的事件类型；跳过中间金额量化会把
    金额量化损失（< 0.005 元）带进份额，跨四舍五入边界时差 0.01 份。
    """

    ES = Decimal("5377.61")
    DIV_CASH = Decimal("0.0119")
    REINVEST_NAV = Decimal("1.0899")

    def test_two_step_matches_fund_company(self):
        """两步量化：5377.61 × 0.0119 = 63.993559 → 63.99；63.99 / 1.0899 → 58.71"""
        dividend_amount = quantize_amount(self.ES * self.DIV_CASH)
        assert dividend_amount == Decimal("63.99")
        assert quantize_shares(dividend_amount / self.REINVEST_NAV) == Decimal("58.71")

    def test_one_step_crosses_rounding_boundary(self):
        """反例：跳过金额量化得 58.72（58.715073… 被 ROUND_HALF_UP 进位），与基金公司差 0.01 份"""
        one_step = quantize_shares(self.ES * self.DIV_CASH / self.REINVEST_NAV)
        assert one_step == Decimal("58.72")
        assert one_step != quantize_shares(
            quantize_amount(self.ES * self.DIV_CASH) / self.REINVEST_NAV
        )


_FINANCIAL_ROUND_SCOPE = {
    "app/services/subscription_service.py": {
        "calculate_subscription_confirm_preview", "confirm_single_subscription",
        "create_subscription", "update_subscription",
    },
    "app/services/trade_service.py": {
        "validate_buy_cash_with_addback", "validate_sell_shares_with_addback",
        "attach_paired_cash_leg", "sync_transfer_group", "calculate_confirm_preview",
        "resolve_cash_leg_plan", "compute_confirm_plan", "validate_confirm_cash_leg",
        "_apply_confirm_cash_leg", "confirm_single_trade", "_derive_sell_amounts",
        "_derive_buy_shares", "create_trade", "update_trade",
    },
    "app/services/share_change_event_service.py": {
        "compute_event_fields", "apply_event_fields", "compute_share_change_event_preview",
        "_confirm_fund_level_event", "create_share_change_event",
        "update_share_change_event", "confirm_share_change_event",
    },
    "app/services/cash_transfer_service.py": {
        "create_cash_transfer", "confirm_cash_transfer",
    },
    "app/services/position_service.py": {"update_cash_position"},
    "app/services/snapshot_service.py": {
        "_compute_in_transit_amounts", "_generate_portfolio_position",
        "_generate_portfolio_value_snapshot", "_generate_investor_holding",
    },
}


def _collect_quantization_violations(sources, round_scope):
    """六模块禁直接 quantize；round 仅限登记产生点及其嵌套函数。"""
    assert sources, "empty source scan"
    assert round_scope, "empty financial scope"
    missing_files = set(round_scope) - set(sources)
    assert not missing_files, f"missing financial files: {sorted(missing_files)}"
    violations = []
    for filename, producers in sorted(round_scope.items()):
        assert producers, f"empty function scope: {filename}"
        functions = set()

        def visit(node, path=(), in_producer=False):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                path = (*path, node.name)
                if not isinstance(node, ast.ClassDef):
                    qualified_name = ".".join(path)
                    functions.add(qualified_name)
                    in_producer = in_producer or qualified_name in producers
            if isinstance(node, ast.Call):
                func = node.func
                operation = None
                if isinstance(func, ast.Attribute) and func.attr == "quantize":
                    operation = "quantize"
                elif in_producer and (
                    isinstance(func, ast.Name) and func.id == "round"
                    or isinstance(func, ast.Attribute) and func.attr == "round"
                    and isinstance(func.value, ast.Name) and func.value.id == "builtins"
                ):
                    operation = "round"
                if operation:
                    violations.append((filename, ".".join(path), node.lineno, operation))
            for child in ast.iter_child_nodes(node):
                visit(child, path, in_producer)

        visit(ast.parse(sources[filename], filename=filename))
        missing_functions = producers - functions
        assert not missing_functions, (
            f"missing financial functions: {filename}: {sorted(missing_functions)}"
        )
    return sorted(violations)


class TestFinancialQuantizationGuard:
    def test_financial_services_use_quantization_helpers(self):
        assert set(_FINANCIAL_ROUND_SCOPE) == {
            "app/services/subscription_service.py", "app/services/trade_service.py",
            "app/services/share_change_event_service.py", "app/services/cash_transfer_service.py",
            "app/services/position_service.py", "app/services/snapshot_service.py",
        }
        backend = Path(__file__).resolve().parents[2]
        sources = {
            filename: (backend / filename).read_text(encoding="utf-8")
            for filename in _FINANCIAL_ROUND_SCOPE
        }
        assert _collect_quantization_violations(sources, _FINANCIAL_ROUND_SCOPE) == []

    @pytest.mark.parametrize("expression, operation", [
        ('value.quantize(Decimal("0.01"))', "quantize"),
        ('value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)', "quantize"),
        ("round(value, 2)", "round"),
        ("builtins.round(value, 4)", "round"),
    ])
    def test_rejects_direct_rounding(self, expression, operation):
        source = f"def produce(value):\n    return {expression}\n"
        assert _collect_quantization_violations(
            {"service.py": source}, {"service.py": {"produce"}},
        ) == [("service.py", "produce", 2, operation)]

    def test_nested_and_async_producers_keep_qualified_names(self):
        source = dedent('''\
            class Writer:
                async def produce(self, value):
                    def inner():
                        return round(value, 2)
                    async def nested():
                        return value.quantize(STEP, rounding=ROUND_HALF_UP)
                    return round(value, 4)
                def statistics(self, value):
                    return round(value, 4)
            def produce(value):
                return round(value, 4)
        ''')
        assert _collect_quantization_violations(
            {"service.py": source}, {"service.py": {"Writer.produce"}},
        ) == [
            ("service.py", "Writer.produce", 7, "round"),
            ("service.py", "Writer.produce.inner", 4, "round"),
            ("service.py", "Writer.produce.nested", 6, "quantize"),
        ]

    def test_quantize_is_forbidden_outside_producers_too(self):
        source = dedent('''\
            value.quantize(STEP)
            def produce(value):
                return quantize_amount(value)
            async def statistics(value):
                return value.quantize(STEP, rounding=ROUND_HALF_UP)
        ''')
        assert _collect_quantization_violations(
            {"service.py": source}, {"service.py": {"produce"}},
        ) == [
            ("service.py", "", 1, "quantize"),
            ("service.py", "statistics", 5, "quantize"),
        ]

    def test_helpers_float_tolerance_comments_and_strings_are_allowed(self):
        source = dedent('''\
            from app.utils.quantize import quantize_amount, quantize_nav, quantize_shares
            def produce(value):
                # value.quantize(STEP); round(value, 2)
                note = "value.quantize(STEP); round(value, 2)"
                tolerance = Decimal("0.01")
                return (quantize_amount(value), quantize_nav(value),
                        quantize_shares(value), float(value), abs(value) <= tolerance)
        ''')
        assert _collect_quantization_violations({
            "app/services/trade_service.py": source,
            "app/utils/quantize.py": (
                "def quantize_amount(value):\n"
                "    return value.quantize(STEP, rounding=ROUND_HALF_UP)\n"
            ),
        }, {"app/services/trade_service.py": {"produce"}}) == []

    def test_round_scope_is_file_specific_and_preserves_read_statistics(self):
        position_source = dedent('''\
            def update_cash_position(value):
                return quantize_amount(value)
            def compute_daily_profits(value):
                return round(float(value), 4)
            def compute_cash_cumulative_profits(value):
                return round(float(value), 4)
            def compute_event_cash_addbacks(value):
                return round(float(value), 4)
            def create_trade(value):
                return round(value, 2)
        ''')
        assert _collect_quantization_violations({
            "app/services/position_service.py": position_source,
            "app/services/trade_service.py": "def create_trade(value):\n    return round(value, 2)\n",
            "app/services/performance_service.py": "def stats(value):\n    return round(value, 4)\n",
        }, {
            "app/services/position_service.py": {"update_cash_position"},
            "app/services/trade_service.py": {"create_trade"},
        }) == [("app/services/trade_service.py", "create_trade", 2, "round")]

    @pytest.mark.parametrize("sources, scope, message", [
        ({}, {"service.py": {"produce"}}, "empty source scan"),
        ({"service.py": "def produce(): pass"}, {}, "empty financial scope"),
        ({"other.py": "def produce(): pass"}, {"service.py": {"produce"}}, "missing financial files"),
        ({"service.py": "def produce(): pass"}, {"service.py": set()}, "empty function scope"),
        ({"service.py": ""}, {"service.py": {"produce"}}, "missing financial functions"),
        ({"service.py": "def renamed(): pass"}, {"service.py": {"produce"}}, "missing financial functions"),
        ({"service.py": "def produce(): pass"}, {"service.py": {"Writer.produce"}}, "missing financial functions"),
    ], ids=["no-sources", "no-scope", "missing-file", "no-functions", "empty-file", "renamed", "wrong-qualname"])
    def test_empty_or_stale_scope_fails(self, sources, scope, message):
        with pytest.raises(AssertionError, match=message):
            _collect_quantization_violations(sources, scope)
