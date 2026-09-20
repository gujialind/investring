# ============================================================================
# 单元测试：显式 null 收口（null_guard，issue #573）
# ============================================================================
# 部分更新语义是「不传 = 不动」；显式 null 在多数字段上没有定义语义，放行会沿
# exclude_unset → setattr → 可空列落库（响应模型字段不接受 None 时该行此后 GET
# 恒 500，如 #573 的 investor.role / product.is_qdii）。收口统一为 reject_explicit_nulls，
# 调用点用 allow 声明「null 有明确语义」的字段（清除、或另有专用校验器）。
#
# 端点级行为（422 形状、三态：显式 null / 缺省 / 正常值）在集成测试覆盖：
#   tests/integration/test_investors.py、test_products_validation.py、
#   test_asset_classification.py
# ============================================================================

import pytest

from app.services.exceptions import BusinessError
from app.services.null_guard import reject_explicit_nulls


class TestRejectExplicitNulls:
    def test_rejects_null_field(self):
        """显式 null 抛 INVALID_PARAM，消息列出字段名（与 #493 申赎侧同形）"""
        with pytest.raises(BusinessError) as exc:
            reject_explicit_nulls({"role": None, "name": "x"})
        assert exc.value.code == "INVALID_PARAM"
        assert "role" in exc.value.message
        assert "name" not in exc.value.message

    def test_rejects_all_null_fields_sorted(self):
        """多字段 null 一次报全（排序稳定，便于调用方/测试断言）"""
        with pytest.raises(BusinessError) as exc:
            reject_explicit_nulls({"name": None, "role": None})
        assert exc.value.message.endswith("name, role")

    def test_allow_listed_field_passes(self):
        """allow 里的字段放行（null = 清除，或由专用校验器收口）"""
        reject_explicit_nulls({"notes": None, "amount": 1.0}, allow={"notes"})

    def test_no_allow_means_no_null_tolerated(self):
        """缺省 allow 为空：不传该参数即任何 null 都拒"""
        with pytest.raises(BusinessError):
            reject_explicit_nulls({"notes": None})

    def test_allow_str_rejected(self):
        """allow 传 str 会退化为子串匹配（"note"/"e"/"s" 全放行）——静默放行必须炸"""
        with pytest.raises(TypeError):
            reject_explicit_nulls({"notes": None}, allow="notes")

    def test_non_null_values_pass(self):
        """正常值与空字典都是 no-op（缺省不传 = 不动）"""
        reject_explicit_nulls({})
        reject_explicit_nulls({"name": "x", "sort_order": 0, "is_active": False})
