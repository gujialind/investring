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

import pytest
from decimal import Decimal

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
