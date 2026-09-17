"""
调仓单腿确认（#493）CLI 参数与关键行为测试

覆盖：
- `--cash-confirm-date` 从 create 移除、补到 confirm/preview/update
- `--cash-platform-code` 创建时仅供买入：卖出创建走 CLI 前置拒绝，
  错误码与后端一致（CASH_PLATFORM_NOT_ALLOWED）且携带改在 confirm 传入的 hints
- confirm/preview 把到账平台/到账日作为 query 参数下发；update 作为请求体下发
- `create --confirm` 卖出的 A=C 缺省由 confirm 请求缺省承载（CLI 不臆造 A）
- 链式 create --confirm 确认失败仍返回 created_trade_id（勿重建：买入创建即扣款、卖出会重复占用份额）
- 成功 hints 反映新语义（买入创建即扣款 / 确认后快照 / 调仓不自动确认）

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/ -q
"""
import json
import re

import pytest

from typer.testing import CliRunner

from ir_cli.client import APIClient, ApiError
from ir_cli.main import app
from ir_cli.output import error


def _runner() -> CliRunner:
    try:
        # click <8.2 默认混流，需显式分离 stderr；click >=8.2 无此参数且默认分离
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class _RecordingClient:
    """路由式假客户端：handler 返回响应体 dict（APIClient 契约），并记录请求参数/请求体

    handler 返回 {"detail": {"error": ...}} 形状时按 APIClient 的语义处理：
    raise_errors=True 抛 ApiError（链式 create --confirm 的失败分支），否则直接退出。
    """

    def __init__(self, handler):
        self._handler = handler
        self.calls: list = []

    def _record(self, method: str, path: str, params=None, json_data=None, raise_errors=False):
        self.calls.append(
            {"method": method, "path": path, "params": params or {}, "json": json_data}
        )
        body = self._handler(method, path, params, json_data)
        if isinstance(body, dict) and isinstance(body.get("detail"), dict):
            detail = body["detail"]
            if raise_errors:
                # APIClient 会并入 http_status（get_hint 插值依赖）
                raise ApiError(
                    detail.get("error", "HTTP_ERROR"),
                    detail.get("message", ""),
                    {**(detail.get("details") or {}), "http_status": 422},
                )
            error(detail.get("error", "HTTP_ERROR"), detail.get("message", ""))
        return body

    def get(self, path, params=None):
        return self._record("GET", path, params=params)

    def post(self, path, json_data=None, params=None, raise_errors=False):
        return self._record("POST", path, params=params, json_data=json_data, raise_errors=raise_errors)

    def put(self, path, json_data=None):
        return self._record("PUT", path, json_data=json_data)

    def delete(self, path):
        return self._record("DELETE", path)


@pytest.fixture()
def cli():
    return app


def _patch_client(monkeypatch, handler) -> _RecordingClient:
    client = _RecordingClient(handler)
    monkeypatch.setattr(
        APIClient, "from_config", classmethod(lambda cls, require_auth=True: client)
    )
    return client


def _run(cli, args):
    """执行命令并返回 (退出码, stdout JSON 文档)"""
    result = _runner().invoke(cli, args)
    if not result.stdout.strip():
        raise AssertionError(f"stdout 为空: exit={result.exit_code} exc={result.exception!r}")
    return result.exit_code, json.loads(result.stdout)


SELL_CREATE_ARGS = [
    "trade", "create",
    "--portfolio-code", "P1", "--product-code", "F001", "--market", "CN_OTC",
    "--type", "sell", "--trade-date", "2026-07-28",
    "--shares", "1000", "--platform-code", "PF1",
]


class TestCreateDirectionGate:
    """create 的方向闸门与选项面（#493）"""

    def test_sell_with_cash_platform_code_rejected_before_request(self, monkeypatch, cli):
        client = _patch_client(monkeypatch, lambda *a: {"detail": "unreachable"})
        code, doc = _run(cli, SELL_CREATE_ARGS + ["--cash-platform-code", "PF2"])
        assert code == 1
        assert doc["ok"] is False
        assert doc["error"]["code"] == "CASH_PLATFORM_NOT_ALLOWED"
        hint = doc["error"]["hints"][0]
        assert "ir trade confirm <id> --cash-platform-code PF2" in hint
        # 前置拒绝：不发任何请求
        assert client.calls == []

    def test_sell_with_cash_platform_code_via_json_rejected(self, monkeypatch, cli):
        client = _patch_client(monkeypatch, lambda *a: {"detail": "unreachable"})
        body = {
            "portfolio_code": "P1", "product_code": "F001", "market": "CN_OTC",
            "trade_type": "sell", "trade_date": "2026-07-28",
            "shares": 1000, "platform_code": "PF1", "cash_platform_code": "PF2",
        }
        code, doc = _run(cli, ["trade", "create", "--json", json.dumps(body)])
        assert code == 1
        assert doc["error"]["code"] == "CASH_PLATFORM_NOT_ALLOWED"
        assert client.calls == []

    def test_create_help_drops_cash_confirm_date(self, cli):
        result = _runner().invoke(cli, ["trade", "create", "--help"])
        assert result.exit_code == 0
        # 选项面不再有 --cash-confirm-date（说明文字里仍指引它改到 confirm 传）
        options = result.stdout.split("Options:", 1)[1]
        assert "--cash-confirm-date" not in options
        assert "--cash-platform-code" in options

    def test_buy_cash_platform_code_still_sent(self, monkeypatch, cli):
        def handler(method, path, params, body):
            return {"data": {"id": 7, "status": "pending", "trade_type": "buy"}}

        client = _patch_client(monkeypatch, handler)
        code, _ = _run(cli, [
            "trade", "create",
            "--portfolio-code", "P1", "--product-code", "F001", "--market", "CN_OTC",
            "--type", "buy", "--trade-date", "2026-07-28", "--actual-amount", "1000",
            "--platform-code", "PF1", "--cash-platform-code", "PF2",
        ])
        assert code == 0
        assert client.calls[0]["json"]["cash_platform_code"] == "PF2"


class TestConfirmPreviewCashInputs:
    """confirm/preview 的到账平台与到账日（卖出在确认期录入，#493）"""

    def test_confirm_sends_cash_platform_and_date_as_query(self, monkeypatch, cli):
        def handler(method, path, params, body):
            assert path == "/api/trades/42/confirm"
            return {"data": {"id": 42, "status": "confirmed"}}

        client = _patch_client(monkeypatch, handler)
        code, doc = _run(cli, [
            "trade", "confirm", "42",
            "--cash-platform-code", "PF2", "--cash-confirm-date", "2026-08-03",
        ])
        assert code == 0
        call = client.calls[0]
        assert call["method"] == "POST"
        assert call["params"] == {"cash_platform_code": "PF2", "cash_confirm_date": "2026-08-03"}
        assert call["json"] is None
        # 确认成功 hint 反映「快照 + confirmed ≠ 现金当天可用」
        assert "ir snapshot generate" in doc["hints"][0]
        assert "不等于现金当天可用" in doc["hints"][0]

    def test_confirm_omits_cash_inputs_when_absent(self, monkeypatch, cli):
        client = _patch_client(
            monkeypatch,
            lambda *a: {"data": {"id": 42, "status": "confirmed"}},
        )
        code, _ = _run(cli, ["trade", "confirm", "42"])
        assert code == 0
        assert client.calls[0]["params"] == {}

    def test_confirm_help_exposes_cash_options(self, cli):
        result = _runner().invoke(cli, ["trade", "confirm", "--help"])
        assert result.exit_code == 0
        assert "--cash-platform-code" in result.stdout
        assert "--cash-confirm-date" in result.stdout
        # 帮助文案按终端宽度折行（click 用 textwrap 吃掉断点空格并对续行重缩进），
        # 故按空白归一化后再匹配，避免窄终端把该断言打红
        assert "缺省A=C" in re.sub(r"\s+", "", result.stdout)

    def test_preview_sends_cash_platform_and_date_as_query(self, monkeypatch, cli):
        client = _patch_client(
            monkeypatch,
            lambda *a: {"data": {"trade": {}, "preview": {}, "paired_cash_amount": 1}},
        )
        code, _ = _run(cli, [
            "trade", "preview", "42",
            "--cash-platform-code", "PF2", "--cash-confirm-date", "2026-08-03",
        ])
        assert code == 0
        call = client.calls[0]
        assert call["method"] == "GET"
        assert call["path"] == "/api/trades/42/preview"
        assert call["params"] == {"cash_platform_code": "PF2", "cash_confirm_date": "2026-08-03"}

    def test_preview_help_exposes_cash_options(self, cli):
        result = _runner().invoke(cli, ["trade", "preview", "--help"])
        assert result.exit_code == 0
        assert "--cash-platform-code" in result.stdout
        assert "--cash-confirm-date" in result.stdout


class TestUpdateCashConfirmDate:
    """update 的到账日窄修正（已确认卖出，#493 决策 3）"""

    def test_update_sends_cash_confirm_date_in_body(self, monkeypatch, cli):
        client = _patch_client(
            monkeypatch,
            lambda *a: {"data": {"id": 42, "status": "confirmed"}},
        )
        code, _ = _run(cli, ["trade", "update", "42", "--cash-confirm-date", "2026-08-05"])
        assert code == 0
        call = client.calls[0]
        assert call["method"] == "PUT"
        assert call["path"] == "/api/trades/42"
        assert call["json"] == {"cash_confirm_date": "2026-08-05"}

    def test_update_combines_cash_confirm_date_with_notes(self, monkeypatch, cli):
        client = _patch_client(
            monkeypatch,
            lambda *a: {"data": {"id": 42, "status": "confirmed"}},
        )
        code, _ = _run(cli, [
            "trade", "update", "42", "--cash-confirm-date", "2026-08-05", "--notes", "以对账单为准",
        ])
        assert code == 0
        assert client.calls[0]["json"] == {
            "cash_confirm_date": "2026-08-05", "notes": "以对账单为准",
        }

    def test_update_help_exposes_cash_confirm_date(self, cli):
        result = _runner().invoke(cli, ["trade", "update", "--help"])
        assert result.exit_code == 0
        assert "--cash-confirm-date" in result.stdout
        assert "已确认卖出" in result.stdout


class TestChainedSellConfirm:
    """create --confirm 卖出：A=C 缺省由后端承载，CLI 不臆造到账日"""

    def test_chain_confirm_leaves_arrival_default_to_backend(self, monkeypatch, cli):
        def handler(method, path, params, body):
            if method == "POST" and path == "/api/trades":
                return {"data": {"id": 51, "status": "pending", "trade_type": "sell"}}
            if path == "/api/trades/51/confirm":
                return {"data": {"id": 51, "status": "confirmed"}}
            return {"detail": "not found"}

        client = _patch_client(monkeypatch, handler)
        code, doc = _run(cli, SELL_CREATE_ARGS + ["--confirm"])
        assert code == 0
        assert doc["data"]["status"] == "confirmed"
        create_call, confirm_call = client.calls
        assert "cash_confirm_date" not in create_call["json"]
        assert "cash_platform_code" not in create_call["json"]
        # 缺省 A=C、平台=基金腿平台由后端语义决定，CLI 不传即不覆盖
        assert confirm_call["params"] == {}

    def test_two_step_sell_carries_arrival_inputs_on_confirm(self, monkeypatch, cli):
        """自定义到账信息走 create + confirm 两步：create 不下发到账字段"""

        def handler(method, path, params, body):
            if method == "POST" and path == "/api/trades":
                assert "cash_platform_code" not in body and "cash_confirm_date" not in body
                return {"data": {"id": 61, "status": "pending", "trade_type": "sell"}}
            if path == "/api/trades/61/confirm":
                return {"data": {"id": 61, "status": "confirmed"}}
            return {"detail": "not found"}

        client = _patch_client(monkeypatch, handler)
        code, _ = _run(cli, SELL_CREATE_ARGS)
        assert code == 0
        code, _ = _run(cli, [
            "trade", "confirm", "61",
            "--cash-platform-code", "PF2", "--cash-confirm-date", "2026-08-03",
        ])
        assert code == 0
        assert client.calls[1]["params"] == {
            "cash_platform_code": "PF2", "cash_confirm_date": "2026-08-03",
        }

    def test_confirm_failure_still_returns_created_trade_id(self, monkeypatch, cli):
        """确认失败必须回 created_trade_id：卖出误重建会重复占用份额，买入误重建会重复扣款"""

        def handler(method, path, params, body):
            if method == "POST" and path == "/api/trades":
                return {"data": {"id": 77, "status": "pending", "trade_type": "sell"}}
            if path == "/api/trades/77/confirm":
                return {"detail": {
                    "error": "SNAPSHOT_DEPENDENCY",
                    "message": "基金确认日必须晚于最新快照日（2026-07-28）",
                }}
            return {"detail": "not found"}

        _patch_client(monkeypatch, handler)
        code, doc = _run(cli, SELL_CREATE_ARGS + ["--confirm"])
        assert code == 1
        assert doc["ok"] is False
        assert doc["error"]["code"] == "SNAPSHOT_DEPENDENCY"
        assert doc["error"]["details"]["created_trade_id"] == 77
        assert doc["error"]["details"]["http_status"] == 422
        assert any("ir trade confirm 77" in h for h in doc["error"]["hints"])


class TestCreateHintsReflectNewSemantics:
    """成功 hints 文案与 #493 语义一致"""

    def test_pending_buy_hint_says_cash_already_deducted(self, monkeypatch, cli):
        _patch_client(
            monkeypatch,
            lambda *a: {"data": {"id": 8, "status": "pending", "trade_type": "buy"}},
        )
        code, doc = _run(cli, [
            "trade", "create",
            "--portfolio-code", "P1", "--product-code", "F001", "--market", "CN_OTC",
            "--type", "buy", "--trade-date", "2026-07-28", "--actual-amount", "1000",
            "--platform-code", "PF1",
        ])
        assert code == 0
        joined = " ".join(doc["hints"])
        assert "ir trade confirm 8" in joined
        assert "创建即记扣款" in joined
        assert "不参与快照自动确认" in joined

    def test_pending_sell_hint_points_to_confirm_arrival_inputs(self, monkeypatch, cli):
        _patch_client(
            monkeypatch,
            lambda *a: {"data": {"id": 9, "status": "pending", "trade_type": "sell"}},
        )
        code, doc = _run(cli, SELL_CREATE_ARGS)
        assert code == 0
        joined = " ".join(doc["hints"])
        assert "ir trade confirm 9" in joined
        assert "确认时用 ir trade confirm <id> --cash-platform-code" in joined
        assert "IN_TRANSIT_SELL" in joined
