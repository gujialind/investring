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


# 六个核心财务模块全域禁止直接 .quantize()；round() 默认禁止（#590：收紧 PR #589
# 守卫——新增未登记函数直接 round() 即失败，不再依赖「登记产生点」圈定禁止范围）。
_FINANCIAL_GUARD_MODULES = (
    "app/services/subscription_service.py",
    "app/services/trade_service.py",
    "app/services/share_change_event_service.py",
    "app/services/cash_transfer_service.py",
    "app/services/position_service.py",
    "app/services/snapshot_service.py",
)

# round() 读侧例外：统计/展示用途（float 序列化），非财务量化产生点。
# 只按「文件 + 限定函数」登记，豁免登记函数的函数体及其嵌套函数（装饰器与默认值在外层
# 作用求值，不在豁免内），不扩大为整文件豁免；.quantize() 全域禁令不受例外影响。
# 例外函数更名/迁移，或其子树内不再有任何直接 round() 调用时，守卫失败（过期例外）。
_FINANCIAL_ROUND_EXCEPTIONS = {
    "app/services/position_service.py": {
        "compute_daily_profits",            # 每日收益统计（float，读侧展示）
        "compute_cash_cumulative_profits",  # 现金累计收益统计（float，读侧展示）
        "compute_event_cash_addbacks",      # 事件现金加回统计（float，读侧展示）
    },
}


def _collect_quantization_violations(sources, round_exceptions):
    """扫描文件全域禁直接 .quantize() 与 round()；round 例外限登记函数的函数体及其嵌套函数。"""
    assert sources, "empty source scan"
    unscanned = set(round_exceptions) - set(sources)
    assert not unscanned, f"exception files not scanned: {sorted(unscanned)}"
    violations = []
    for filename, source in sorted(sources.items()):
        exceptions = round_exceptions.get(filename, set())
        functions = set()
        used_exceptions = set()

        def visit(node, path=(), exception_origin=None):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                path = (*path, node.name)
                if isinstance(node, ast.ClassDef):
                    for child in ast.iter_child_nodes(node):
                        visit(child, path, exception_origin)
                    return
                qualified_name = ".".join(path)
                functions.add(qualified_name)
                body_origin = exception_origin
                if body_origin is None and qualified_name in exceptions:
                    body_origin = qualified_name
                # 装饰器、默认值、注解与形参在**定义处的外层作用**求值，不属于函数体，
                # 故按外层例外状态扫描——否则例外函数上的 `@deco(round(X, 2))` /
                # `def f(v=round(1.5, 2))` 既被豁免、又能喂饱 used_exceptions
                # 使过期例外检查失效。
                outer_scope = (
                    *node.decorator_list,
                    node.args,
                    *getattr(node, "type_params", ()),
                    *((node.returns,) if node.returns is not None else ()),
                )
                for child in outer_scope:
                    visit(child, path, exception_origin)
                for child in node.body:
                    visit(child, path, body_origin)
                return
            if isinstance(node, ast.Call):
                func = node.func
                operation = None
                if isinstance(func, ast.Attribute) and func.attr == "quantize":
                    operation = "quantize"
                elif (isinstance(func, ast.Name) and func.id == "round") or (
                    # 属性形式一律拦截，不白名单接收者：builtins.round()、别名
                    # `import builtins as b; b.round()`、np.round()、Series.round()
                    isinstance(func, ast.Attribute) and func.attr == "round"
                ):
                    operation = "round"
                if operation == "round" and exception_origin is not None:
                    used_exceptions.add(exception_origin)
                elif operation:
                    violations.append((filename, ".".join(path), node.lineno, operation))
            for child in ast.iter_child_nodes(node):
                visit(child, path, exception_origin)

        visit(ast.parse(source, filename=filename))
        missing_exceptions = exceptions - functions
        assert not missing_exceptions, (
            f"missing round-exception functions: {filename}: {sorted(missing_exceptions)}"
        )
        stale_exceptions = exceptions - used_exceptions
        assert not stale_exceptions, (
            f"stale round exceptions without direct round(): {filename}: {sorted(stale_exceptions)}"
        )
    return sorted(violations)


class TestFinancialQuantizationGuard:
    def test_financial_services_use_quantization_helpers(self):
        assert set(_FINANCIAL_GUARD_MODULES) == {
            "app/services/subscription_service.py", "app/services/trade_service.py",
            "app/services/share_change_event_service.py", "app/services/cash_transfer_service.py",
            "app/services/position_service.py", "app/services/snapshot_service.py",
        }
        # 例外台账钉死：扩大/迁移豁免必须是评审可见的显式改动（#590）
        assert _FINANCIAL_ROUND_EXCEPTIONS == {
            "app/services/position_service.py": {
                "compute_daily_profits", "compute_cash_cumulative_profits",
                "compute_event_cash_addbacks",
            },
        }
        backend = Path(__file__).resolve().parents[2]
        sources = {
            filename: (backend / filename).read_text(encoding="utf-8")
            for filename in _FINANCIAL_GUARD_MODULES
        }
        assert _collect_quantization_violations(sources, _FINANCIAL_ROUND_EXCEPTIONS) == []

    @pytest.mark.parametrize("expression, operation", [
        ('value.quantize(Decimal("0.01"))', "quantize"),
        ('value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)', "quantize"),
        ("round(value, 2)", "round"),
        ("builtins.round(value, 4)", "round"),
        # 属性形式不白名单接收者：别名、numpy、pandas 同属误舍入路径
        ("aliased_builtins.round(value, 4)", "round"),
        ("np.round(value, 4)", "round"),
        ("series.round(4)", "round"),
    ])
    def test_rejects_direct_rounding_in_unregistered_functions_by_default(
        self, expression, operation
    ):
        """#590：未登记的新函数直接量化/舍入默认失败，报告文件、函数与行号"""
        source = f"def produce(value):\n    return {expression}\n"
        assert _collect_quantization_violations(
            {"service.py": source}, {},
        ) == [("service.py", "produce", 2, operation)]

    def test_new_unregistered_function_beside_exception_is_rejected(self):
        """例外函数旁新增的未登记函数不豁免——例外按函数登记，不扩大为整文件"""
        source = dedent('''\
            def compute_daily_profits(value):
                return round(float(value), 4)
            def newly_added_producer(value):
                return round(value, 2)
        ''')
        assert _collect_quantization_violations(
            {"service.py": source}, {"service.py": {"compute_daily_profits"}},
        ) == [("service.py", "newly_added_producer", 4, "round")]

    def test_exception_does_not_leak_to_decorators_and_defaults(self):
        """装饰器/默认值在定义处的外层作用求值：例外函数不豁免它们"""
        source = dedent('''\
            def deco(x): return x
            @deco(round(1.5, 2))
            def compute_daily_profits(value=round(0.5, 2)):
                return round(float(value), 4)
        ''')
        assert _collect_quantization_violations(
            {"service.py": source}, {"service.py": {"compute_daily_profits"}},
        ) == [
            ("service.py", "compute_daily_profits", 2, "round"),
            ("service.py", "compute_daily_profits", 3, "round"),
        ]

    def test_decorator_round_does_not_satisfy_exception_ledger(self):
        """体内已无直接 round()、只剩装饰器/默认值里的 round() → 仍按过期例外失败"""
        source = dedent('''\
            def deco(x): return x
            @deco(round(1.5, 2))
            def compute_daily_profits(value):
                return quantize_amount(value)
        ''')
        with pytest.raises(AssertionError, match="stale round exceptions"):
            _collect_quantization_violations(
                {"service.py": source}, {"service.py": {"compute_daily_profits"}},
            )

    def test_module_level_calls_are_rejected(self):
        """模块级（函数外）直接 round/quantize 同样默认失败"""
        source = dedent('''\
            LIMIT = round(THRESHOLD, 2)
            STEP_LIMIT = VALUE.quantize(STEP)
            def compute_daily_profits(value):
                return round(float(value), 4)
        ''')
        assert _collect_quantization_violations(
            {"service.py": source}, {"service.py": {"compute_daily_profits"}},
        ) == [
            ("service.py", "", 1, "round"),
            ("service.py", "", 2, "quantize"),
        ]

    def test_nested_and_async_functions_follow_default_deny(self):
        """例外传播到嵌套函数；quantize 在例外内不豁免；未登记 async/嵌套默认拒绝"""
        source = dedent('''\
            class Writer:
                def compute_stats(self, value):
                    def inner():
                        return round(value, 2)
                    async def nested():
                        return value.quantize(STEP, rounding=ROUND_HALF_UP)
                    return round(value, 4)
                async def produce(self, value):
                    return round(value, 4)
            def helper(value):
                async def inner_async():
                    return round(value, 4)
                return inner_async
        ''')
        assert _collect_quantization_violations(
            {"service.py": source}, {"service.py": {"Writer.compute_stats"}},
        ) == [
            ("service.py", "Writer.compute_stats.nested", 6, "quantize"),
            ("service.py", "Writer.produce", 9, "round"),
            ("service.py", "helper.inner_async", 12, "round"),
        ]

    def test_round_exceptions_are_file_specific(self):
        """同名函数在其他文件不豁免（例外按「文件 + 函数」登记）"""
        sources = {
            "app/services/position_service.py": (
                "def compute_daily_profits(value):\n    return round(float(value), 4)\n"
            ),
            "app/services/trade_service.py": (
                "def compute_daily_profits(value):\n    return round(float(value), 4)\n"
            ),
        }
        assert _collect_quantization_violations(sources, {
            "app/services/position_service.py": {"compute_daily_profits"},
        }) == [("app/services/trade_service.py", "compute_daily_profits", 2, "round")]

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
        assert _collect_quantization_violations(
            {"app/services/trade_service.py": source}, {},
        ) == []

    @pytest.mark.parametrize("sources, exceptions, message", [
        ({}, {}, "empty source scan"),
        ({"service.py": "def produce(): pass"},
         {"other.py": {"produce"}}, "exception files not scanned"),
        # 例外函数删除/更名/限定名不符 → missing（例外失效反例）
        ({"service.py": "def renamed(value):\n    return round(value, 4)\n"},
         {"service.py": {"compute_daily_profits"}}, "missing round-exception functions"),
        ({"service.py": "def produce(value):\n    return round(value, 4)\n"},
         {"service.py": {"Writer.produce"}}, "missing round-exception functions"),
        ({"service.py": ""},
         {"service.py": {"compute_daily_profits"}}, "missing round-exception functions"),
        # 例外函数尚存、但最后一个需豁免的直接 round() 已移除 → stale（过期例外）
        ({"service.py": "def compute_daily_profits(value):\n    return quantize_amount(value)\n"},
         {"service.py": {"compute_daily_profits"}}, "stale round exceptions"),
    ], ids=[
        "no-sources", "exception-file-not-scanned", "renamed-exception",
        "wrong-qualname-exception", "empty-file", "last-exempt-call-removed",
    ])
    def test_empty_or_stale_exceptions_fail(self, sources, exceptions, message):
        with pytest.raises(AssertionError, match=message):
            _collect_quantization_violations(sources, exceptions)
