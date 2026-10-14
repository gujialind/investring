"""#705：显示码单点口径单测（app.utils.product_code.display_code）。"""

from app.utils.product_code import display_code


class TestDisplayCode:
    def test_strips_tushare_suffix(self):
        assert display_code("510300.SH", "CN_EXCHANGE") == "510300"
        assert display_code("161017.SZ", "CN_EXCHANGE") == "161017"
        assert display_code("000300.OF", "CN_OTC") == "000300"

    def test_system_codes_pass_through(self):
        # CASH / IN_TRANSIT_* 无点：剥后缀是 no-op，不得出现空码或截断
        assert display_code("CASH", "") == "CASH"
        assert display_code("IN_TRANSIT_BUY", "") == "IN_TRANSIT_BUY"

    def test_hk_prefers_stored_sale_code(self):
        assert display_code("1001767344", "HK_MUTUAL", "968050") == "968050"

    def test_hk_falls_back_when_unmapped(self):
        # 映射不可得回退原码：不报错、不空码（#705 验收）
        assert display_code("1001767344", "HK_MUTUAL", None) == "1001767344"
        assert display_code("1001767344", "HK_MUTUAL", "") == "1001767344"

    def test_non_hk_ignores_sale_code(self):
        # hk_sale_code 仅港互认语义；其他市场误填不得改变显示码
        assert display_code("510300.SH", "CN_EXCHANGE", "968050") == "510300"

    def test_empty_code_passthrough(self):
        assert display_code("", "") == ""
