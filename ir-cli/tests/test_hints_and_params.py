"""
错误就近提示与参数风格统一测试（issue #86/#81）

覆盖：
- get_hint 按 details 动态插值（MARKET_AMBIGUOUS/PRODUCT_NOT_FOUND/INSUFFICIENT_SHARES）
- 无动态规则时 fallback 静态表；未知错误码返回 None
- output.error 缺省 hints 时按 code+details 自动生成
- available-cash/available-shares 双通道参数：--portfolio-code option 优先、
  位置参数弃用告警走 stderr（stdout 仍为纯 JSON）、两者皆缺报 VALIDATION_ERROR
- available-cash --platform-code 透传为 query；缺省不传键（组合合计口径）

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/ -q
"""
import json

import pytest
from typer.testing import CliRunner

from ir_cli.client import APIClient
from ir_cli.hints import ERROR_HINTS, get_hint
from ir_cli.main import app
from ir_cli.output import error


class TestGetHint:
    """get_hint 动态插值与静态 fallback"""

    def test_market_ambiguous_interpolates_available_markets(self):
        hint = get_hint("MARKET_AMBIGUOUS", {"product_code": "161005", "available_markets": ["CN_OTC", "CN_SH"]})
        assert "--market" in hint
        assert "CN_OTC, CN_SH" in hint

    def test_market_ambiguous_without_details_falls_back_static(self):
        assert get_hint("MARKET_AMBIGUOUS") == ERROR_HINTS["MARKET_AMBIGUOUS"]
        assert get_hint("MARKET_AMBIGUOUS", {"http_status": 409}) == ERROR_HINTS["MARKET_AMBIGUOUS"]

    def test_product_not_found_with_available_markets(self):
        hint = get_hint("PRODUCT_NOT_FOUND", {"product_code": "161005.OF", "available_markets": ["CN_SZ"]})
        assert "161005.OF" in hint
        assert "CN_SZ" in hint

    def test_product_not_found_without_markets_falls_back_static(self):
        hint = get_hint("PRODUCT_NOT_FOUND", {"product_code": "999999.OF"})
        assert hint == ERROR_HINTS["PRODUCT_NOT_FOUND"]
        assert "ir product list" in hint

    def test_not_found_with_available_markets(self):
        hint = get_hint("NOT_FOUND", {"product_code": "161005.OF", "available_markets": ["CN_SH", "CN_SZ"]})
        assert "CN_SH, CN_SZ" in hint

    def test_not_found_without_markets_falls_back_static(self):
        assert get_hint("NOT_FOUND", {"http_status": 404}) == ERROR_HINTS["NOT_FOUND"]

    def test_insufficient_shares_interpolates_available_shares(self):
        hint = get_hint("INSUFFICIENT_SHARES", {"available_shares": 1234.56})
        assert "1234.56" in hint

    def test_insufficient_shares_without_details_falls_back_static(self):
        assert get_hint("INSUFFICIENT_SHARES") == ERROR_HINTS["INSUFFICIENT_SHARES"]

    def test_unknown_code_returns_none(self):
        assert get_hint("SOME_UNKNOWN_CODE") is None
        assert get_hint("SOME_UNKNOWN_CODE", {"available_markets": ["CN_SH"]}) is None

    def test_static_table_new_entries_present(self):
        for code in ("NOT_FOUND", "PRODUCT_NOT_FOUND", "MARKET_AMBIGUOUS",
                     "CONFIRM_REQUIRED", "NO_SNAPSHOT_BASELINE", "CALENDAR_NOT_SYNCED",
                     "INVALID_STATUS"):
            assert ERROR_HINTS.get(code), f"静态表缺少 {code}"

    def test_static_table_cash_lifecycle_entries(self):
        """#493 新增现金生命周期错误码的就近指引"""
        assert "--cash-platform-code" in ERROR_HINTS["CASH_PLATFORM_NOT_ALLOWED"]
        assert "--cash-confirm-date" in ERROR_HINTS["CASH_CONFIRM_DATE_NOT_ALLOWED"]
        assert "unconfirm" in ERROR_HINTS["CASH_LEG_MISSING"]
        assert get_hint("CASH_LEG_MISSING") == ERROR_HINTS["CASH_LEG_MISSING"]

    def test_static_entries_recommend_new_commands(self):
        assert "catch-up" in ERROR_HINTS["SNAPSHOT_NOT_CONTINUOUS"]
        assert "catch-up" in ERROR_HINTS["NAV_NOT_AVAILABLE"]
        assert "calendar-sync" in ERROR_HINTS["CALENDAR_NOT_SYNCED"]
        assert "--dry-run" in ERROR_HINTS["CONFIRM_REQUIRED"]
        assert "--portfolio-code" in ERROR_HINTS["INSUFFICIENT_CASH"]
        assert "--portfolio-code" in ERROR_HINTS["INSUFFICIENT_SHARES"]


class TestErrorOutputDynamicHints:
    """output.error 缺省 hints 时按 code+details 自动生成"""

    def test_error_emits_interpolated_hint(self, capsys):
        with pytest.raises(SystemExit) as ei:
            error("MARKET_AMBIGUOUS", "产品 161005 存在多个市场",
                  details={"product_code": "161005", "available_markets": ["CN_OTC", "CN_SH"]})
        assert ei.value.code == 1
        doc = json.loads(capsys.readouterr().out)
        assert doc["ok"] is False
        assert any("CN_OTC, CN_SH" in h for h in doc["error"]["hints"])

    def test_explicit_hints_take_precedence(self, capsys):
        with pytest.raises(SystemExit):
            error("MARKET_AMBIGUOUS", "msg",
                  details={"available_markets": ["CN_SH"]}, hints=["自定义提示"])
        doc = json.loads(capsys.readouterr().out)
        assert doc["error"]["hints"] == ["自定义提示"]


class _StubClient:
    """记录请求路径并返回固定响应的假客户端"""

    def __init__(self):
        self.calls = []
        self.writes = []
        self.response_data = {"available_cash": 100.0}

    def get(self, path, params=None):
        self.calls.append((path, params))
        return {"data": self.response_data}

    def post(self, path, json_data=None, params=None):
        self.writes.append(("POST", path, json_data))
        return {"data": json_data or {}}

    def put(self, path, json_data=None):
        self.writes.append(("PUT", path, json_data))
        return {"data": json_data or {}}


@pytest.fixture
def stub_client(monkeypatch):
    stub = _StubClient()
    monkeypatch.setattr(APIClient, "from_config", classmethod(lambda cls, require_auth=True: stub))
    return stub


def _runner() -> CliRunner:
    try:
        # click <8.2 默认混流，需显式分离 stderr；click >=8.2 无此参数且默认分离
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class TestAvailableCashDualChannel:
    """available-cash 双通道参数（issue #81）"""

    def test_option_style(self, stub_client):
        result = _runner().invoke(app, ["position", "available-cash", "--portfolio-code", "PORT001"])
        assert result.exit_code == 0
        doc = json.loads(result.stdout)
        assert doc["ok"] is True
        assert stub_client.calls[0][0] == "/api/positions/portfolio/PORT001/available-cash"
        assert "弃用" not in result.stderr

    def test_positional_style_warns_on_stderr(self, stub_client):
        result = _runner().invoke(app, ["position", "available-cash", "PORT001"])
        assert result.exit_code == 0
        # stdout 保持纯 JSON，弃用告警只出现在 stderr
        doc = json.loads(result.stdout)
        assert doc["ok"] is True
        assert "弃用" in result.stderr
        assert "--portfolio-code" in result.stderr
        assert stub_client.calls[0][0] == "/api/positions/portfolio/PORT001/available-cash"

    def test_option_takes_precedence_over_positional(self, stub_client):
        result = _runner().invoke(
            app, ["position", "available-cash", "OLD001", "--portfolio-code", "NEW001"]
        )
        assert result.exit_code == 0
        assert stub_client.calls[0][0] == "/api/positions/portfolio/NEW001/available-cash"

    def test_missing_both_reports_validation_error(self, stub_client):
        result = _runner().invoke(app, ["position", "available-cash"])
        assert result.exit_code == 1
        doc = json.loads(result.stdout)
        assert doc["error"]["code"] == "VALIDATION_ERROR"
        assert "--portfolio-code" in doc["error"]["message"]
        assert stub_client.calls == []


class TestAvailableCashPlatformScope:
    """available-cash 的 --platform-code 透传（#527-② 选定的补参数口径）"""

    def test_platform_code_sent_as_query(self, stub_client):
        result = _runner().invoke(
            app,
            ["position", "available-cash", "--portfolio-code", "PORT001",
             "--platform-code", "HBZQ"],
        )
        assert result.exit_code == 0
        assert stub_client.calls[0] == (
            "/api/positions/portfolio/PORT001/available-cash",
            {"platform_code": "HBZQ"},
        )

    def test_omitted_platform_code_sends_no_query_key(self, stub_client):
        """缺省必须是「不传」而不是传空串：后端按 platform_code is None 判定组合合计"""
        result = _runner().invoke(
            app, ["position", "available-cash", "--portfolio-code", "PORT001"]
        )
        assert result.exit_code == 0
        assert stub_client.calls[0][1] == {}

    def test_help_exposes_platform_code(self):
        result = _runner().invoke(app, ["position", "available-cash", "--help"])
        assert result.exit_code == 0
        assert "--platform-code" in result.stdout.split("Options:", 1)[1]


class TestAvailableSharesDualChannel:
    """available-shares 双通道参数（issue #81）"""

    def test_option_style(self, stub_client):
        result = _runner().invoke(app, [
            "position", "available-shares",
            "--portfolio-code", "PORT001", "--product-code", "022959.OF",
        ])
        assert result.exit_code == 0
        assert stub_client.calls[0][0] == (
            "/api/positions/portfolio/PORT001/product/022959.OF/available-shares"
        )

    def test_positional_style_warns_on_stderr(self, stub_client):
        result = _runner().invoke(app, ["position", "available-shares", "PORT001", "022959.OF"])
        assert result.exit_code == 0
        assert "弃用" in result.stderr
        assert stub_client.calls[0][0] == (
            "/api/positions/portfolio/PORT001/product/022959.OF/available-shares"
        )

    def test_missing_product_code_reports_validation_error(self, stub_client):
        result = _runner().invoke(app, ["position", "available-shares", "--portfolio-code", "PORT001"])
        assert result.exit_code == 1
        doc = json.loads(result.stdout)
        assert doc["error"]["code"] == "VALIDATION_ERROR"
        assert "--product-code" in doc["error"]["message"]


class TestPortfolioDisplayConfigParam:
    """portfolio create/update 的 --display-config 参数（issue #144）"""

    def test_update_invalid_json_reports_validation_error(self, stub_client):
        result = _runner().invoke(
            app, ["portfolio", "update", "PORT001", "--display-config", "{bad"]
        )
        assert result.exit_code == 1
        doc = json.loads(result.stdout)
        assert doc["error"]["code"] == "VALIDATION_ERROR"
        assert "--display-config" in doc["error"]["message"]
        assert stub_client.writes == []

    def test_update_non_object_json_reports_validation_error(self, stub_client):
        result = _runner().invoke(
            app, ["portfolio", "update", "PORT001", "--display-config", '["style"]']
        )
        assert result.exit_code == 1
        doc = json.loads(result.stdout)
        assert doc["error"]["code"] == "VALIDATION_ERROR"
        assert stub_client.writes == []

    def test_update_valid_json_passthrough(self, stub_client):
        result = _runner().invoke(
            app,
            ["portfolio", "update", "PORT001", "--display-config", '{"ASSET_STOCK": "style"}'],
        )
        assert result.exit_code == 0
        method, path, body = stub_client.writes[0]
        assert method == "PUT"
        assert path == "/api/portfolios/PORT001"
        assert body == {"display_config": {"ASSET_STOCK": "style"}}

    def test_create_valid_json_passthrough(self, stub_client):
        result = _runner().invoke(
            app,
            [
                "portfolio", "create",
                "--code", "P_NEW", "--name", "新组合",
                "--display-config", '{"ASSET_BOND": "region"}',
            ],
        )
        assert result.exit_code == 0
        method, path, body = stub_client.writes[0]
        assert method == "POST"
        assert path == "/api/portfolios"
        assert body["display_config"] == {"ASSET_BOND": "region"}

    def test_update_clear_via_json_body(self, stub_client):
        """清空配置走 --json 显式 null（resolve_body 过滤 None，逐项参数无法表达）"""
        result = _runner().invoke(
            app,
            ["portfolio", "update", "PORT001", "--json", '{"display_config": null}'],
        )
        assert result.exit_code == 0
        _, _, body = stub_client.writes[0]
        assert body == {"display_config": None}


class TestProductCreateConfirmDays:
    """product create --confirm-days（issue #241）：缺省不下发（后端按市场+QDII 推导），
    显式传入如实下发（#231/#236/#241 显式优先）"""

    def test_omitted_not_sent(self, stub_client):
        result = _runner().invoke(
            app,
            ["product", "create", "--code", "T1.OF", "--market", "CN_OTC",
             "--name", "测试", "--product-type", "OEF"],
        )
        assert result.exit_code == 0, result.stdout
        _, _, body = stub_client.writes[0]
        assert "confirm_days" not in body

    def test_explicit_sent(self, stub_client):
        result = _runner().invoke(
            app,
            ["product", "create", "--code", "T2.OF", "--market", "CN_OTC",
             "--name", "测试", "--product-type", "OEF", "--confirm-days", "2"],
        )
        assert result.exit_code == 0, result.stdout
        _, _, body = stub_client.writes[0]
        assert body["confirm_days"] == 2


SHARE_EVENT_CREATE_BODY = {
    "portfolio_code": "P1", "product_code": "F001", "market": "CN_OTC",
    "platform_code": "PF1", "event_type": "cash_dividend",
    "ex_date": "2026-06-05", "entitlement_date": "2026-06-04", "div_cash": 0.05,
}
SHARE_EVENT_CREATE_ARGS = [
    "share-event", "create", "--portfolio-code", "P1", "--product-code", "F001",
    "--market", "CN_OTC", "--platform-code", "PF1", "--event-type", "cash_dividend",
    "--ex-date", "2026-06-05", "--entitlement-date", "2026-06-04", "--div-cash", "0.05",
]


class TestShareEventCashPayDate:
    """#522：只透传到账日，不在 CLI 推算默认值或复制后端业务日期校验。"""

    @pytest.mark.parametrize("command", ["create", "update"])
    @pytest.mark.parametrize("via_json", [False, True])
    @pytest.mark.parametrize("pay_date", ["2026-06-05", "2026-06-06", "2026-06-08"])
    def test_cash_pay_date_in_request_body(self, stub_client, command, via_json, pay_date):
        body = dict(SHARE_EVENT_CREATE_BODY) if command == "create" else {}
        body["cash_pay_date"] = pay_date
        args = list(SHARE_EVENT_CREATE_ARGS) if command == "create" else ["share-event", "update", "42"]
        if via_json:
            args += ["--json", json.dumps(body)]
        else:
            args += ["--cash-pay-date", pay_date]
        result = _runner().invoke(app, args)
        assert result.exit_code == 0, result.stdout
        path = "/api/share-change-events" + ("/42" if command == "update" else "")
        assert stub_client.writes == [("POST" if command == "create" else "PUT", path, body)]
        assert json.loads(result.stdout)["data"]["cash_pay_date"] == pay_date

    @pytest.mark.parametrize("command", ["create", "update"])
    @pytest.mark.parametrize("via_json", [False, True])
    @pytest.mark.parametrize("bad_date", [
        "not-a-date", "2026/06/06", "2026-02-30", "20260606", "2026-W23-6",
    ])
    def test_invalid_date_rejected_before_request(self, stub_client, command, via_json, bad_date):
        args = list(SHARE_EVENT_CREATE_ARGS) if command == "create" else ["share-event", "update", "42"]
        if via_json:
            body = dict(SHARE_EVENT_CREATE_BODY) if command == "create" else {}
            args += ["--json", json.dumps({**body, "cash_pay_date": bad_date})]
        else:
            args += ["--cash-pay-date", bad_date]
        result = _runner().invoke(app, args)
        assert result.exit_code == 1
        doc = json.loads(result.stdout)
        assert doc["error"]["code"] == "VALIDATION_ERROR"
        assert "cash_pay_date" in doc["error"]["message"]
        assert "YYYY-MM-DD" in doc["error"]["message"]
        assert stub_client.writes == []

    @pytest.mark.parametrize("command", ["create", "update"])
    def test_json_null_preserved_and_overrides_option(self, stub_client, command):
        body = dict(SHARE_EVENT_CREATE_BODY) if command == "create" else {}
        body["cash_pay_date"] = None
        args = ["share-event", command] + (["42"] if command == "update" else [])
        result = _runner().invoke(app, args + [
            "--cash-pay-date", "2026-06-08", "--json", json.dumps(body),
        ])
        assert result.exit_code == 0, result.stdout
        assert stub_client.writes[0][2] == body
        assert "cash_pay_date" in stub_client.writes[0][2]
        assert json.loads(result.stdout)["data"]["cash_pay_date"] is None

    @pytest.mark.parametrize("command", ["create", "update"])
    @pytest.mark.parametrize("via_json", [False, True])
    def test_omission_does_not_fill_date(self, stub_client, command, via_json):
        body = dict(SHARE_EVENT_CREATE_BODY) if command == "create" else {"notes": "仅改备注"}
        args = list(SHARE_EVENT_CREATE_ARGS) if command == "create" else [
            "share-event", "update", "42", "--notes", "仅改备注",
        ]
        if via_json:
            args += ["--json", json.dumps(body)]
        result = _runner().invoke(app, args)
        assert result.exit_code == 0, result.stdout
        assert stub_client.writes[0][2] == body
        assert "cash_pay_date" not in stub_client.writes[0][2]

    @pytest.mark.parametrize("command", ["list", "get"])
    @pytest.mark.parametrize("date_fields", [{}, {"cash_pay_date": None}, {"cash_pay_date": "2026-06-06"}])
    def test_read_returns_date_unchanged(self, stub_client, command, date_fields):
        event = {"id": 42, "ex_date": "2026-06-05", "status": "confirmed", **date_fields}
        stub_client.response_data = [event] if command == "list" else event
        args = ["share-event", command] + (["42"] if command == "get" else [])
        result = _runner().invoke(app, args)
        assert result.exit_code == 0, result.stdout
        assert json.loads(result.stdout)["data"] == stub_client.response_data
        path = "/api/share-change-events" + ("/42" if command == "get" else "")
        assert stub_client.calls[0][0] == path

    def test_list_can_select_cash_pay_date(self, stub_client):
        stub_client.response_data = [{"id": 42, "cash_pay_date": "2026-06-06", "notes": "备注"}]
        result = _runner().invoke(app, ["share-event", "list", "--fields", "id,cash_pay_date"])
        assert result.exit_code == 0, result.stdout
        assert json.loads(result.stdout)["data"] == [{"id": 42, "cash_pay_date": "2026-06-06"}]

    def test_confirm_hint_distinguishes_cash_arrival(self, stub_client):
        result = _runner().invoke(app, ["share-event", "confirm", "42"])
        assert result.exit_code == 0, result.stdout
        hint = json.loads(result.stdout)["hints"][0]
        assert "cash_pay_date" in hint
        assert "不计可用现金" in hint
        assert "下一交易日快照消费" in hint

    @pytest.mark.parametrize("field", ["ex_date", "entitlement_date", "cash_pay_date"])
    @pytest.mark.parametrize("bad_date", ["20260604", "2026-W23-4"])
    def test_shared_date_validation_requires_calendar_date(self, stub_client, field, bad_date):
        body = {**SHARE_EVENT_CREATE_BODY, field: bad_date}
        result = _runner().invoke(app, ["share-event", "create", "--json", json.dumps(body)])
        assert result.exit_code == 1
        doc = json.loads(result.stdout)
        assert doc["error"]["code"] == "VALIDATION_ERROR"
        assert field in doc["error"]["message"]
        assert stub_client.writes == []
