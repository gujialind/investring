"""ir-cli ledger 命令组契约测试（issue #905 PR3，验收断言 11）。

台账型创建/激活在 portfolio 命令组（PR1 已交付）；本份覆盖 ledger 命令组
（entry/flow CRUD + totals）与 REST 同口径：命令打正确的 REST 路径与请求体、
响应 data 原样透传、必填/枚举/确认门槛本地拦截。用 RecordingClient 截获调用，
不依赖运行中的后端（REST 端点行为由 backend/tests/integration/test_ledger_*.py 覆盖）。

运行：PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/test_ledgers.py -q
"""
import json

import pytest
from typer.testing import CliRunner

from ir_cli.client import APIClient
from ir_cli.main import app


class RecordingClient:
    """截获 (method, path, json_data, params)，返回 canned data 信封。"""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, None, params))
        return {"data": self.response}

    def post(self, path, json_data=None, params=None, raise_errors=False):
        self.calls.append(("POST", path, json_data, params))
        return {"data": self.response}

    def put(self, path, json_data=None, params=None):
        self.calls.append(("PUT", path, json_data, params))
        return {"data": self.response}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, None, params))
        return {"data": self.response}


def _invoke(args, client):
    try:
        runner = CliRunner(mix_stderr=False)
    except TypeError:
        runner = CliRunner()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(APIClient, "from_config", classmethod(lambda cls, require_auth=True: client))
        return runner.invoke(app, args)


def _doc(result):
    return json.loads(result.stdout)


ENTRY = {"id": 1, "portfolio_code": "C", "platform_code": "PF", "value_date": "2026-01-05",
         "market_value": 100.0, "asset_kind": "non_cash", "note": None}
FLOW = {"id": 2, "portfolio_code": "C", "platform_code": "PF", "flow_date": "2026-01-05",
        "direction": "inflow", "amount": 100.0, "note": None, "warnings": []}


class TestLedgerEntryCommands:
    def test_entry_add_hits_rest_with_body_and_passes_through(self):
        client = RecordingClient(ENTRY)
        result = _invoke(["ledger", "entry-add", "C", "--platform-code", "PF",
                          "--value-date", "2026-01-05", "--market-value", "100"], client)
        assert result.exit_code == 0, result.stdout
        assert client.calls == [("POST", "/api/portfolios/C/ledger/entries",
                                 {"platform_code": "PF", "value_date": "2026-01-05", "market_value": 100.0}, None)]
        assert _doc(result)["data"] == ENTRY

    def test_entry_add_missing_required_is_validation_error(self):
        client = RecordingClient(ENTRY)
        result = _invoke(["ledger", "entry-add", "C", "--value-date", "2026-01-05",
                          "--market-value", "100"], client)
        assert result.exit_code == 1
        assert _doc(result)["error"]["code"] == "VALIDATION_ERROR"
        assert client.calls == []  # 本地拦截，未发请求

    def test_entry_add_bad_asset_kind_is_validation_error(self):
        client = RecordingClient(ENTRY)
        result = _invoke(["ledger", "entry-add", "C", "--platform-code", "PF",
                          "--value-date", "2026-01-05", "--market-value", "100",
                          "--asset-kind", "bogus"], client)
        assert result.exit_code == 1
        assert _doc(result)["error"]["code"] == "VALIDATION_ERROR"
        assert client.calls == []

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
    def test_entry_add_non_finite_market_value_is_validation_error(self, bad):
        # S4（#905 PR3 L2）：nan/inf 本地拦截为输入类错误，不发请求、不逃 INTERNAL_ERROR。
        client = RecordingClient(ENTRY)
        result = _invoke(["ledger", "entry-add", "C", "--platform-code", "PF",
                          "--value-date", "2026-01-05", "--market-value", bad], client)
        assert result.exit_code == 1
        assert _doc(result)["error"]["code"] == "VALIDATION_ERROR"
        assert client.calls == []

    def test_entry_list_hits_rest(self):
        client = RecordingClient([ENTRY])
        result = _invoke(["ledger", "entry-list", "C", "--platform-code", "PF"], client)
        assert result.exit_code == 0, result.stdout
        assert client.calls[0][0] == "GET"
        assert client.calls[0][1] == "/api/portfolios/C/ledger/entries"
        assert client.calls[0][3] == {"platform_code": "PF"}

    def test_entry_update_empty_body_is_validation_error(self):
        client = RecordingClient(ENTRY)
        result = _invoke(["ledger", "entry-update", "C", "1"], client)
        assert result.exit_code == 1
        assert _doc(result)["error"]["code"] == "VALIDATION_ERROR"
        assert client.calls == []

    def test_entry_delete_requires_yes(self):
        client = RecordingClient({"message": "台账市值记录删除成功"})
        result = _invoke(["ledger", "entry-delete", "C", "1"], client)
        assert result.exit_code == 1
        assert _doc(result)["error"]["code"] == "CONFIRM_REQUIRED"
        assert client.calls == []

    def test_entry_delete_with_yes_hits_rest(self):
        client = RecordingClient({"message": "台账市值记录删除成功"})
        result = _invoke(["ledger", "entry-delete", "C", "1", "--yes"], client)
        assert result.exit_code == 0, result.stdout
        assert client.calls == [("DELETE", "/api/portfolios/C/ledger/entries/1", None, None)]


class TestLedgerFlowCommands:
    def test_flow_add_hits_rest_with_body(self):
        client = RecordingClient(FLOW)
        result = _invoke(["ledger", "flow-add", "C", "--platform-code", "PF",
                          "--flow-date", "2026-01-05", "--direction", "inflow",
                          "--amount", "100"], client)
        assert result.exit_code == 0, result.stdout
        assert client.calls == [("POST", "/api/portfolios/C/ledger/flows",
                                 {"platform_code": "PF", "flow_date": "2026-01-05",
                                  "direction": "inflow", "amount": 100.0}, None)]

    def test_flow_add_bad_direction_is_validation_error(self):
        client = RecordingClient(FLOW)
        result = _invoke(["ledger", "flow-add", "C", "--platform-code", "PF",
                          "--flow-date", "2026-01-05", "--direction", "sideways",
                          "--amount", "100"], client)
        assert result.exit_code == 1
        assert _doc(result)["error"]["code"] == "VALIDATION_ERROR"
        assert client.calls == []

    def test_flow_add_surfaces_negative_net_inflow_warning(self):
        warned = dict(FLOW, warnings=["累计撤资已超过累计注资，收益基数异常，请核对进出记录"])
        client = RecordingClient(warned)
        result = _invoke(["ledger", "flow-add", "C", "--platform-code", "PF",
                          "--flow-date", "2026-01-05", "--direction", "outflow",
                          "--amount", "200"], client)
        assert result.exit_code == 0, result.stdout
        doc = _doc(result)
        assert doc["data"]["warnings"] == warned["warnings"]
        assert any("累计撤资" in h for h in doc["hints"])  # S4 不静默

    def test_flow_delete_requires_yes(self):
        client = RecordingClient({"message": "台账进出记录删除成功"})
        result = _invoke(["ledger", "flow-delete", "C", "2"], client)
        assert result.exit_code == 1
        assert _doc(result)["error"]["code"] == "CONFIRM_REQUIRED"
        assert client.calls == []


class TestLedgerTotals:
    def test_totals_hits_rest_with_as_of_date(self):
        totals = {"portfolio_code": "C", "as_of_date": "2026-01-06", "total_value": 110.0,
                  "net_inflow": 100.0, "total_profit": 10.0, "warnings": []}
        client = RecordingClient(totals)
        result = _invoke(["ledger", "totals", "C", "--as-of-date", "2026-01-06"], client)
        assert result.exit_code == 0, result.stdout
        assert client.calls == [("GET", "/api/portfolios/C/ledger/totals", None, {"as_of_date": "2026-01-06"})]
        assert _doc(result)["data"] == totals

    def test_totals_default_no_as_of_date(self):
        client = RecordingClient({"portfolio_code": "C", "total_value": 0.0, "net_inflow": 0.0,
                                  "total_profit": 0.0, "warnings": []})
        result = _invoke(["ledger", "totals", "C"], client)
        assert result.exit_code == 0, result.stdout
        assert client.calls[0][3] == {}  # build_body 过滤 None