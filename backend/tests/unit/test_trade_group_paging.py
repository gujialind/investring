# ============ 单元测试：按组分页的页边界折算（issue #752） ============
# `trade_service._walk_group_pages` 是纯函数：输入「排序序下每组的行数」与 page/page_size，
# 输出行级 (offset, limit, total_pages)。这里钉住三条口径——组不拆（一页可多带同组剩余腿）、
# 页间不重叠不漏行、total_pages 与请求的是第几页无关。集成侧的端到端形态见
# tests/integration/test_trades_list.py::TestListGroupPagination。

import pytest

from app.services.trade_service import _walk_group_pages


class TestWalkGroupPages:
    @pytest.mark.parametrize(
        "sizes,page_size,expected",
        [
            # 每组都不跨边界：与行分页逐字一致
            ([1, 1, 1, 1], 2, [(0, 2), (2, 2)]),
            # 两腿组压在边界上：整组带上 ⇒ 第 1 页 3 行（page_size + 组大小 - 1），
            # 下一页从该组之后开始
            ([1, 2, 1], 2, [(0, 3), (3, 1)]),
            # 组比 page_size 还大：一页装不下也得整组给（不拆是硬约束，行数上限让位）
            ([4], 2, [(0, 4)]),
            # 连续多组：装到 >= page_size 才停，故 p1 = 2+2 = 4 行
            ([2, 2, 2, 1, 1], 3, [(0, 4), (4, 3), (7, 1)]),
        ],
    )
    def test_windows(self, sizes, page_size, expected):
        for page, (offset, limit) in enumerate(expected, start=1):
            got_offset, got_limit, total_pages = _walk_group_pages(
                sizes, page, page_size
            )
            assert (got_offset, got_limit) == (offset, limit)
            assert total_pages == len(expected)

    def test_windows_tile_the_row_range_exactly(self):
        """页窗口首尾相接、覆盖 [0, sum(sizes))，既不重叠也不留缝"""
        sizes = [2, 1, 3, 1, 1, 2, 5, 1]
        total_pages = _walk_group_pages(sizes, 1, 4)[2]
        rows: list[int] = []
        for page in range(1, total_pages + 1):
            offset, limit, _ = _walk_group_pages(sizes, page, 4)
            rows.extend(range(offset, offset + limit))
        assert rows == list(range(sum(sizes)))

    def test_total_pages_independent_of_requested_page(self):
        sizes = [2, 2, 1]
        assert {_walk_group_pages(sizes, p, 2)[2] for p in (1, 2, 3, 9)} == {3}

    def test_page_beyond_last_is_empty(self):
        assert _walk_group_pages([1, 1], 5, 1) == (0, 0, 2)

    def test_no_rows(self):
        assert _walk_group_pages([], 1, 20) == (0, 0, 0)

    @pytest.mark.parametrize("page_size", [0, -1])
    def test_non_positive_page_size_yields_empty_page(self, page_size):
        """装满 0 行的页永不结束 ⇒ 无法推进，按既有退化形态给空页而不是死循环"""
        assert _walk_group_pages([2, 2], 1, page_size) == (0, 0, 0)

    @pytest.mark.parametrize("page", [0, -1])
    def test_non_positive_page_yields_empty_page(self, page):
        assert _walk_group_pages([2, 2], page, 2) == (0, 0, 2)
