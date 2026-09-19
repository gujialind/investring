# ============================================================================
# 单元测试：买入份额纯派生 helper（#565）
# ============================================================================
# 覆盖 app/services/trade_service.py::_derive_buy_shares：
# - (含费支出 − fee) / price，份额 2 位 HALF_UP（与 quantize_shares 同口径）
# - 无价格（场外未传价 / 价格为 0）返回占位 0，确认时按净值重算自愈
# 创建（create_trade）与编辑（update_trade）共用此实现，口径单一。
# ============================================================================

from decimal import Decimal

from app.services.trade_service import _derive_buy_shares


class TestDeriveBuyShares:
    def test_normal_derivation(self):
        # (5000 − 10) / 1.6 = 3118.75
        assert _derive_buy_shares(
            Decimal("5000"), Decimal("10"), Decimal("1.6"),
        ) == Decimal("3118.75")

    def test_half_up_quantization(self):
        # 4990 / 1.5 = 3326.666... -> 3326.67（HALF_UP）
        assert _derive_buy_shares(
            Decimal("4990"), Decimal("0"), Decimal("1.5"),
        ) == Decimal("3326.67")

    def test_zero_fee(self):
        # 5000 / 1.6 = 3125.00
        assert _derive_buy_shares(
            Decimal("5000"), Decimal("0"), Decimal("1.6"),
        ) == Decimal("3125.00")

    def test_none_price_returns_zero(self):
        assert _derive_buy_shares(Decimal("5000"), Decimal("10"), None) == Decimal("0")

    def test_zero_price_returns_zero(self):
        assert _derive_buy_shares(
            Decimal("5000"), Decimal("10"), Decimal("0"),
        ) == Decimal("0")
