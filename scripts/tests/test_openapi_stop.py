import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("stop_adapter_test", ROOT / "scripts/check_openapi_stop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("QODER_PROJECT_DIR", raising=False)
    monkeypatch.setattr(module.verify, "git", lambda *args: str(tmp_path).encode())
    monkeypatch.setattr(module.verify, "plan", Mock(return_value={
        "source": {"changed_paths": ["backend/app/routers/new.py"]},
        "required_ci_jobs": ["cli-contract-check"],
    }))
    monkeypatch.setattr(module.verify, "run_check", Mock(return_value={
        "status": "pass", "result_file": str(tmp_path / "result.json"),
    }))
    return module, tmp_path


def event(root, **overrides):
    return {"hook_event_name": "Stop", "cwd": str(root), "session_id": "fixture", "stop_hook_active": False, **overrides}


@pytest.mark.parametrize("continuation", [False, True])
@pytest.mark.parametrize("status", ["pass", "not_applicable", "fail", "error"])
def test_host_exit_status_does_not_turn_failure_into_success(adapter, capsys, continuation, status):
    module, _ = adapter
    code = module.respond({"status": status, "reason": "fixture"}, continuation)
    output = capsys.readouterr()
    if status in {"pass", "not_applicable"}:
        assert code == 0
        record = json.loads(output.out)
        assert record["decision"] == "allow"
        assert record["hookSpecificOutput"]["hookEventName"] == "Stop"
        assert status in record["reason"]
    else:
        assert code == (1 if continuation else 2)
        assert not output.out
        record = json.loads(output.err)
        assert record["status"] == status
        assert record["task_state"] == "unverified"
        if status == "error":
            assert "not evidence of contract drift" in record["next_step"]


@pytest.mark.parametrize("paths,jobs", [([], ["cli-contract-check"]), (["docs/guide.md"], ["cli-contract-check"]), (["frontend/src/a.ts"], [])])
def test_inapplicable_changes_do_not_run_contracts(adapter, paths, jobs):
    module, root = adapter
    module.verify.plan.return_value = {"source": {"changed_paths": paths}, "required_ci_jobs": jobs}
    result = module.evaluate(event(root), "HEAD", root=root)
    assert result["status"] == "not_applicable"
    assert result["reason"]
    module.verify.run_check.assert_not_called()


def test_continued_stop_rechecks_instead_of_bypassing(adapter):
    module, root = adapter
    module.verify.run_check.side_effect = [{"status": "fail"}, {"status": "pass"}]
    assert module.evaluate(event(root), "HEAD", root=root)["status"] == "fail"
    assert module.evaluate(event(root, stop_hook_active=True), "HEAD", root=root)["status"] == "pass"
    assert module.verify.run_check.call_count == 2


def test_base_error_is_not_inapplicable(adapter):
    module, root = adapter
    module.verify.plan.side_effect = module.ci_gate.GateError("missing base")
    with pytest.raises(module.ci_gate.GateError, match="missing base"):
        module.evaluate(event(root), "unavailable", root=root)
    module.verify.run_check.assert_not_called()


@pytest.mark.parametrize("override", [{"hook_event_name": "PreToolUse"}, {"cwd": None}, {"cwd": ""}, {"stop_hook_active": "true"}])
def test_invalid_event_is_rejected(adapter, override):
    module, root = adapter
    with pytest.raises(ValueError):
        module.evaluate(event(root, **override), "HEAD", root=root)
    module.verify.run_check.assert_not_called()


def test_other_worktree_is_rejected(adapter):
    module, root = adapter
    with pytest.raises(ValueError, match="different worktrees"):
        module.evaluate(event(root), "HEAD", root=root / "other")
    module.verify.run_check.assert_not_called()


def test_configured_project_mismatch_is_rejected(adapter, monkeypatch):
    module, root = adapter
    monkeypatch.setenv("QODER_PROJECT_DIR", str(root / "other"))
    with pytest.raises(ValueError, match="QODER_PROJECT_DIR"):
        module.evaluate(event(root), "HEAD", root=root)
    module.verify.run_check.assert_not_called()


def test_malformed_stdin_is_a_visible_execution_error(adapter, monkeypatch, capsys):
    module, _ = adapter
    monkeypatch.setattr(module.sys, "stdin", io.StringIO("{"))
    assert module.main([]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["status"] == "error"
    module.verify.run_check.assert_not_called()
