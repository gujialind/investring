"""
分页 meta 归一化测试（#752 按组分页的读侧口径）

覆盖：
- 后端返回的组口径字段 `total_groups` / `total_pages` 透传进 CLI 的 `meta`（手册
  CLI_MANUAL.md「响应包络」与 `ir trade list` 两处都声称有），未返回时不凭空补键
- `has_more` 在有 `total_pages` 时按 `page < total_pages` 判；缺失时回落行口径
  `page * page_size < total`（非按组分页的列表端点行为不变）
- `ir trade list` 端到端：stdout JSON 的 `meta` 带两字段，且末页 `has_more` 为 false

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/ -q
"""
import json

import httpx
import pytest
from typer.testing import CliRunner

from ir_cli.client import APIClient
from ir_cli.main import app


def _client_returning(body: dict) -> APIClient:
    """构造 MockTransport 客户端：任何请求都回 200 + 给定响应体"""
    client = APIClient("http://test")
    client._client = httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    )
    return client


def _meta(body: dict) -> dict:
    return _client_returning(body).get("/api/trades")["meta"]


class TestGroupMetaPassthrough:
    """组口径字段按「后端返回才透传」进 meta"""

    def test_group_fields_passed_through(self):
        meta = _meta(
            {"items": [], "total": 25, "page": 1, "page_size": 20,
             "total_groups": 13, "total_pages": 2}
        )
        assert meta["total_groups"] == 13
        assert meta["total_pages"] == 2
        assert meta["total"] == 25
        assert meta["page"] == 1
        assert meta["page_size"] == 20

    def test_group_fields_absent_for_row_paged_endpoints(self):
        """未返回组字段的列表（申赎/日志等）meta 不出现这两个键"""
        meta = _meta({"items": [], "total": 3, "page": 1, "page_size": 20})
        assert "total_groups" not in meta
        assert "total_pages" not in meta

    def test_zero_group_fields_passed_through(self):
        """空结果：total_groups / total_pages 都是 0，照实透传（不折成 null）"""
        meta = _meta(
            {"items": [], "total": 0, "page": 1, "page_size": 20,
             "total_groups": 0, "total_pages": 0}
        )
        assert meta["total_groups"] == 0
        assert meta["total_pages"] == 0


class TestHasMoreUnderGroupPaging:
    """`has_more` 判据：有 total_pages 用它，否则回落行口径公式"""

    def test_last_page_is_not_more_even_when_rows_exceed_page_size(self):
        """3 个两腿组、page_size=5：第 1 页带满 6 行且 total_pages=1 ⇒ 没有下一页

        行口径公式 `1 * 5 < 6` 会误报 True，让翻页方多发一次空页请求。
        """
        meta = _meta(
            {"items": [{}] * 6, "total": 6, "page": 1, "page_size": 5,
             "total_groups": 3, "total_pages": 1}
        )
        assert meta["has_more"] is False

    def test_more_group_pages_remain(self):
        meta = _meta(
            {"items": [{}] * 20, "total": 25, "page": 1, "page_size": 20,
             "total_groups": 13, "total_pages": 2}
        )
        assert meta["has_more"] is True

    def test_short_last_page_is_not_more(self):
        """组大小 [2,2,2,2]、page_size=3：第 2 页带 4 行且已是末页"""
        meta = _meta(
            {"items": [{}] * 4, "total": 8, "page": 2, "page_size": 3,
             "total_groups": 4, "total_pages": 2}
        )
        assert meta["has_more"] is False

    def test_empty_result_total_pages_zero(self):
        meta = _meta(
            {"items": [], "total": 0, "page": 1, "page_size": 20,
             "total_groups": 0, "total_pages": 0}
        )
        assert meta["has_more"] is False

    def test_row_formula_fallback_without_total_pages(self):
        assert _meta({"items": [{}] * 20, "total": 25, "page": 1, "page_size": 20})["has_more"] is True
        assert _meta({"items": [{}] * 5, "total": 25, "page": 2, "page_size": 20})["has_more"] is False

    def test_has_more_none_without_page(self):
        assert _meta({"items": [], "total": 0})["has_more"] is None


class TestTradeListCommandMeta:
    """`ir trade list` 端到端：手册承诺的两个字段确实出现在 stdout JSON 的 meta 里"""

    @staticmethod
    def _runner() -> CliRunner:
        try:
            # click <8.2 默认混流，需显式分离 stderr；click >=8.2 无此参数且默认分离
            return CliRunner(mix_stderr=False)
        except TypeError:
            return CliRunner()

    def _run(self, monkeypatch, body: dict, args: list) -> dict:
        client = _client_returning(body)
        monkeypatch.setattr(
            APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
        )
        result = self._runner().invoke(app, ["trade", "list"] + args)
        assert result.stdout.strip(), f"stdout 为空: exit={result.exit_code} exc={result.exception!r}"
        return json.loads(result.stdout)

    def test_meta_carries_group_fields_and_last_page_flag(self, monkeypatch):
        body = {
            "items": [{"id": i, "product_code": "F001", "trade_type": "sell"} for i in (2, 1)],
            "total": 6, "page": 1, "page_size": 5, "total_groups": 3, "total_pages": 1,
        }
        doc = self._run(monkeypatch, body, ["--page-size", "5"])
        assert doc["ok"] is True
        assert doc["meta"]["total_groups"] == 3
        assert doc["meta"]["total_pages"] == 1
        assert doc["meta"]["has_more"] is False
        assert len(doc["data"]) == 2

    def test_all_pages_ignores_group_meta(self, monkeypatch):
        """`--all` 的收尾仍按行口径 total（get_all 自建 meta），组字段不参与"""
        body = {
            "items": [{"id": 1}], "total": 1, "page": 1, "page_size": 100,
            "total_groups": 1, "total_pages": 1,
        }
        doc = self._run(monkeypatch, body, ["--all"])
        assert doc["ok"] is True
        assert doc["meta"] == {"total": 1, "has_more": False}


@pytest.mark.parametrize("page,total_pages,expected", [(1, 3, True), (3, 3, False), (4, 3, False)])
def test_has_more_matches_total_pages(page, total_pages, expected):
    meta = _meta(
        {"items": [], "total": 6, "page": page, "page_size": 2,
         "total_groups": 3, "total_pages": total_pages}
    )
    assert meta["has_more"] is expected
