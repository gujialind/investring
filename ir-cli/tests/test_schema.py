"""
ir schema 响应字段契约与索引模式测试（issue #64）

通过 typer.main.get_command 构建真实命令树，验证 build_schema 三种视图：
- 全量视图：向后兼容 + 命中命令携带 output 契约
- 按组视图：output 契约同样携带
- index_only 视图：极简索引（<1KB），不含参数/输出细节

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests/ -q
"""
import json

import pytest
import typer

from ir_cli.main import app
from ir_cli.schema import build_schema

# 首期覆盖的 5 个契约命令
CONTRACT_COMMANDS = [
    ("position", "list"),
    ("portfolio", "nav-history"),
    ("snapshot", "status"),
    ("trade", "list"),
    ("sub", "list"),
]


@pytest.fixture(scope="module")
def root():
    return typer.main.get_command(app)


@pytest.fixture(scope="module")
def full_schema(root):
    return build_schema(root)


class TestOutputContract:
    """命令条目 output 契约"""

    @pytest.mark.parametrize("group,sub", CONTRACT_COMMANDS)
    def test_contract_commands_have_output_fields(self, full_schema, group, sub):
        entry = full_schema["commands"][group][sub]
        assert "output" in entry, f"{group}.{sub} 缺少 output 契约"
        output = entry["output"]
        assert output["shape"] in ("list", "object")
        assert isinstance(output["fields"], str) and output["fields"]

    def test_position_list_fields_detail(self, full_schema):
        output = full_schema["commands"]["position"]["list"]["output"]
        fields = output["fields"].split(",")
        # market_value 在契约中且为摘要字段
        assert "*market_value:num?" in fields
        # cash_amount 可空（带 ? 后缀）
        assert any(f.lstrip("*").startswith("cash_amount:") and f.endswith("?") for f in fields)
        # notes 含 cash_amount 警示（基金行恒为 null）
        assert "恒为null" in output["notes"]["cash_amount"]

    def test_nav_history_command_level_note(self, full_schema):
        notes = full_schema["commands"]["portfolio"]["nav-history"]["output"]["notes"]
        assert "_" in notes  # 命令级注释
        assert "snapshot_date升序" in notes["_"]

    def test_group_view_carries_output(self, root):
        """按 group 过滤视图同样携带 output 契约"""
        result = build_schema(root, "position")
        assert "output" in result["commands"]["position"]["list"]

    def test_non_contract_command_has_no_output(self, full_schema):
        assert "output" not in full_schema["commands"]["position"]["get"]


class TestIndexOnly:
    """index_only 极简索引"""

    def test_index_structure(self, root):
        index = build_schema(root, index_only=True)
        assert set(index.keys()) == {"protocol", "groups"}
        assert "exit_codes" in index["protocol"]
        # groups 为紧凑编码 "组名:子命令1 子命令2;..."，所有契约命令均可解析到
        parsed = {
            part.split(":", 1)[0]: part.split(":", 1)[1].split(" ")
            for part in index["groups"].split(";")
        }
        for group, sub in CONTRACT_COMMANDS:
            assert sub in parsed[group]

    def test_index_has_no_details_and_under_budget(self, root):
        index = build_schema(root, index_only=True)
        raw = json.dumps(index, ensure_ascii=False)
        assert "params" not in raw
        assert '"output"' not in raw
        # 索引预算随命令面增长适度上调（#88 新增 position 子命令后约 1KB），
        # 目标仍是保持极简可低成本加载
        assert len(raw.encode("utf-8")) < 1280, f"索引超过预算: {len(raw.encode('utf-8'))} bytes"


class TestBackwardCompat:
    """默认全量输出向后兼容"""

    def test_full_schema_top_level_keys(self, full_schema):
        for key in ("protocol", "conventions", "enums", "error_hints", "workflows", "commands"):
            assert key in full_schema, f"全量输出缺少键: {key}"

    def test_entry_keeps_help_and_params(self, full_schema):
        entry = full_schema["commands"]["trade"]["list"]
        assert "help" in entry
        assert "params" in entry

    def test_full_schema_is_json_serializable(self, full_schema):
        assert json.loads(json.dumps(full_schema, ensure_ascii=False)) == full_schema


class TestShareEventCashPayDate:
    @pytest.mark.parametrize("group_name", [None, "share-event"])
    @pytest.mark.parametrize("command", ["create", "update"])
    def test_optional_cash_pay_date_discovered_from_command(self, root, group_name, command):
        schema = build_schema(root, group_name)
        params = schema["commands"]["share-event"][command]["params"]
        matches = [p for p in params if p.get("opt") == "--cash-pay-date"]
        assert len(matches) == 1
        option = matches[0]
        assert option["type"] == "TEXT"
        assert not option.get("required", False)
        assert "default" not in option
        assert "YYYY-MM-DD" in option["help"]
        assert "非交易日" in option["help"]
        assert not any("clear-cash-pay-date" in p.get("opt", "") for p in params)

    def test_workflow_describes_cash_pay_date_default_and_clear(self, full_schema):
        workflow = full_schema["workflows"]["份额变动事件"]
        assert "--cash-pay-date" in workflow["steps"][0]
        assert "默认除息日" in workflow["notes"]
        assert "update 省略不改" in workflow["notes"]
        assert '--json \'{"cash_pay_date":null}\'' in workflow["notes"]
        assert "允许非交易日" in workflow["notes"]
