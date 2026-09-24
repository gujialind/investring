import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def verify(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("local_verify_test", ROOT / "scripts/verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid",
    )

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, env=environment, text=True).strip()

    git("init", "-q")
    files = {
        ".gitignore": ".cache/\n",
        "scripts/ci_policy.json": (ROOT / "scripts/ci_policy.json").read_text(),
        "backend/check_openapi.py": "print('checked current schema')\n",
        "ir-cli/scripts/gen_response_fields.py": "import sys\nassert sys.argv[1:] == ['--check']\n",
        "tracked.txt": "original\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git("add", *files)
    tree = git("write-tree")
    commit = git("commit-tree", tree, "-m", "fixture")
    git("update-ref", "HEAD", commit)
    return tmp_path, git


def test_plan_includes_untracked_and_does_not_create_artifacts(verify, repo):
    root, _ = repo
    router = root / "backend/app/routers/new.py"
    router.parent.mkdir(parents=True)
    router.write_text("x = 1\n")
    result = verify.plan(root, "HEAD")
    assert result["source"]["changed_paths"] == ["backend/app/routers/new.py"]
    assert result["scopes"]["backend"]
    assert result["scopes"]["frontend"]
    assert "frontend-e2e" in result["required_ci_jobs"]
    assert not (root / ".cache").exists()
    assert "not verification" in result["limitations"]


def test_snapshot_preserves_staged_changes_hidden_by_working_copy(verify, repo):
    root, git = repo
    path = root / "tracked.txt"
    path.write_text("staged\n")
    git("add", "tracked.txt")
    path.write_text("original\n")
    result = verify.snapshot(root, "HEAD")
    assert "tracked.txt" in result["working_paths"]


@pytest.mark.parametrize("override", [
    "git-dir", "work-tree", "index-file", "common-dir", "object-directory",
    "config-count", "config-parameters", "all",
])
def test_snapshot_ignores_parent_git_redirection(verify, repo, monkeypatch, override):
    root, git = repo
    other = root / ".cache/other-repo"
    other.mkdir(parents=True)
    git("-C", str(other), "init", "-q")
    (other / "decoy.txt").write_text("other worktree\n")
    git("-C", str(other), "add", "decoy.txt")
    tree = git("-C", str(other), "write-tree")
    other_head = git("-C", str(other), "commit-tree", tree, "-m", "other fixture")
    git("-C", str(other), "update-ref", "HEAD", other_head)
    assert other_head != git("rev-parse", "HEAD")

    (root / "tracked.txt").write_text("staged local change\n")
    git("add", "tracked.txt")
    (root / "tracked.txt").write_text("unstaged local change\n")
    (root / "untracked.txt").write_text("local untracked content\n")
    expected = verify.snapshot(root, "HEAD")
    assert expected["working_paths"] == expected["changed_paths"] == ["tracked.txt", "untracked.txt"]
    indexes = {path: path.read_bytes() for path in (root / ".git/index", other / ".git/index")}

    overrides = {
        "git-dir": {"GIT_DIR": str(other / ".git")},
        "work-tree": {"GIT_WORK_TREE": str(other)},
        "index-file": {"GIT_INDEX_FILE": str(other / ".git/index")},
        "common-dir": {"GIT_COMMON_DIR": str(other / ".git")},
        "object-directory": {"GIT_OBJECT_DIRECTORY": str(other / ".git/objects")},
        "config-count": {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
                         "GIT_CONFIG_VALUE_0": str(other)},
        "config-parameters": {"GIT_CONFIG_PARAMETERS": f"'core.worktree={other}'"},
    }
    poison = overrides[override] if override != "all" else {
        key: value for values in overrides.values() for key, value in values.items()
    }
    for key, value in {**poison, "GIT_OPTIONAL_LOCKS": "1", "GIT_FIXTURE_PARENT": "must-not-leak"}.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    actual_git = verify.ci_gate._git
    environments = []

    def recording_git(root, *args, env=None):
        environments.append(env)
        return actual_git(root, *args, env=env)

    monkeypatch.setattr(verify.ci_gate, "_git", recording_git)
    assert verify.snapshot(root, "HEAD") == expected
    assert verify.plan(root, "HEAD")["source"] == expected
    assert environments
    for environment in environments:
        assert {key: value for key, value in environment.items() if key.startswith("GIT_")} == {
            "GIT_OPTIONAL_LOCKS": "0",
        }
        assert environment == {**{key: value for key, value in before.items() if not key.startswith("GIT_")},
                               "GIT_OPTIONAL_LOCKS": "0"}
    assert dict(os.environ) == before
    assert all(path.read_bytes() == content for path, content in indexes.items())


@pytest.mark.parametrize("command", [["plan"], ["run", "contract"]])
def test_snapshot_and_cli_reject_a_subdirectory_as_worktree_root(verify, repo, monkeypatch, capsys, command):
    root, git = repo
    wrong_root = root / "backend"
    assert Path(git("-C", str(wrong_root), "rev-parse", "--show-toplevel")) == root
    with pytest.raises(verify.ci_gate.GateError, match="Verification root does not match the actual Git worktree"):
        verify.snapshot(wrong_root, "HEAD")
    monkeypatch.setattr(verify, "ROOT", wrong_root)
    assert verify.main([*command, "--base", "HEAD", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert result["reason"] == "Verification root does not match the actual Git worktree"
    assert not (root / ".cache").exists()
    assert not (wrong_root / ".cache").exists()


def test_snapshot_does_not_read_untracked_symlink_target(verify, repo, tmp_path):
    root, _ = repo
    target = tmp_path.parent / f"{tmp_path.name}-outside"
    target.write_text("first")
    (root / "external-link").symlink_to(target)
    before = verify.snapshot(root, "HEAD")["fingerprint"]
    target.write_text("changed private content")
    assert verify.snapshot(root, "HEAD")["fingerprint"] == before


def test_missing_base_is_an_error_without_running_checks(verify, repo, monkeypatch, capsys):
    root, _ = repo
    monkeypatch.setattr(verify, "ROOT", root)
    assert verify.main(["run", "contract", "--base", "missing-base", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert not (root / ".cache").exists()


@pytest.mark.parametrize("source,status,code", [
    ("print('consistent')\n", "pass", 0),
    ("raise SystemExit(1)\n", "fail", 1),
    ("raise SystemExit(2)\n", "error", 2),
    ("raise RuntimeError('checker unavailable')\n", "error", 2),
    ("this is invalid python !!!\n", "error", 2),
])
def test_checker_outcomes_are_not_conflated(verify, repo, source, status, code):
    root, _ = repo
    (root / "backend/check_openapi.py").write_text(source)
    result = verify.run_check(root, "contract", [], "HEAD")
    assert result["status"] == status
    assert result["commands"][0]["exit_code"] == code
    assert len(result["commands"]) == (2 if status == "pass" else 1)
    assert json.loads(Path(result["result_file"]).read_text()) == result
    assert all(Path(command["log"]).is_file() for command in result["commands"])


def test_contract_worker_does_not_inherit_application_environment(verify, repo, monkeypatch):
    root, _ = repo
    monkeypatch.setenv("DATABASE_URL", "mysql://private.invalid/live")
    monkeypatch.setenv("APP_VERSION", "outside")
    (root / "backend/check_openapi.py").write_text(
        "import os\nassert 'DATABASE_URL' not in os.environ\nassert 'APP_VERSION' not in os.environ\n"
    )
    assert verify.run_check(root, "contract", [], "HEAD")["status"] == "pass"


def test_fields_execution_error_is_not_reported_as_drift(verify, repo):
    root, _ = repo
    (root / "ir-cli/scripts/gen_response_fields.py").write_text("raise RuntimeError('cannot check')\n")
    result = verify.run_check(root, "contract", [], "HEAD")
    assert result["status"] == "error"
    assert result["commands"][1]["exit_code"] == 2


def test_edit_during_execution_does_not_report_current_state_passed(verify, repo):
    root, _ = repo
    (root / "backend/check_openapi.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('changed')\n")
    result = verify.run_check(root, "contract", [], "HEAD")
    assert result["status"] == "error"
    assert "changed during execution" in result["reason"]
    assert result["source"]["fingerprint"] != result["source_after"]["fingerprint"]


def test_contract_cannot_generate_or_accept_shell_commands(verify, repo):
    root, _ = repo
    for arguments in (["--generate"], ["; touch /tmp/should-not-exist"]):
        with pytest.raises(ValueError, match="no forwarded arguments"):
            verify.commands_for(root, "contract", arguments)
    with pytest.raises(ValueError, match="Unsupported check"):
        verify.commands_for(root, "shell", ["true"])
    assert not (root / ".cache").exists()


def test_check_arguments_are_forwarded_as_argv(verify, repo):
    root, _ = repo
    arguments = ["--backend-port", "18001", "--", "--grep", "literal; text"]
    command = verify.commands_for(root, "e2e", arguments)[0]
    assert command[-len(arguments):] == arguments
    assert command[2] == "e2e"


def test_each_run_executes_instead_of_reusing_results(verify, repo):
    root, _ = repo
    first = verify.run_check(root, "contract", [], "HEAD")
    second = verify.run_check(root, "contract", [], "HEAD")
    assert first["status"] == second["status"] == "pass"
    assert first["result_file"] != second["result_file"]
    assert first["commands"][0]["log"] != second["commands"][0]["log"]


@pytest.mark.parametrize("check,shutdown_timeout", [("contract", 10), ("e2e", 40), ("visual", 40)])
def test_run_check_wires_shutdown_timeout(verify, repo, monkeypatch, check, shutdown_timeout):
    root, _ = repo
    budgets = []
    real_owned = verify.owned_process

    def recording_owned(command, **kwargs):
        budgets.append(kwargs["shutdown_timeout"])
        return real_owned(command, **kwargs)

    monkeypatch.setattr(verify, "owned_process", recording_owned)
    monkeypatch.setattr(verify, "commands_for", lambda *args: [[sys.executable, "-I", "-B", "-c", "pass"]])
    assert verify.run_check(root, check, [], "HEAD")["status"] == "pass"
    assert budgets == [shutdown_timeout]


def test_verify_sigterm_reaps_three_slow_groups_without_stopping_other_stack(repo):
    root, _ = repo
    # Scale both layers equally: three real 0.5s cleanups fit in 2s, not the old 0.5s budget.
    bootstrap = '''
import json, os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from local_stack import owned_process as real_owned_process

def owned_process(command, *, shutdown_timeout=10, **kwargs):
    return real_owned_process(command, shutdown_timeout=shutdown_timeout / 20, **kwargs)
'''
    runner_code = bootstrap + '''
from contextlib import ExitStack

directory = Path(sys.argv[2])
(directory / "runner.pid").write_text(str(os.getpid()))
def interrupted(signum, frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, interrupted)
child_code = """
import os, signal, sys
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
ready = Path(sys.argv[1])
ready.with_suffix(".tmp").write_text(str(os.getpid()))
ready.with_suffix(".tmp").replace(ready)
while True:
    signal.pause()
"""
processes = []
try:
    with ExitStack() as children:
        for role in ("backend", "frontend", "e2e"):
            child = children.enter_context(owned_process(
                [sys.executable, "-I", "-B", "-c", child_code, str(directory / f"{role}.ready")],
                cwd=directory, environment={"PATH": os.defpath}, log=directory / f"{role}.log",
            ))
            processes.append(child)
            (directory / f"{role}.pid").write_text(str(child.pid))
        (directory / "runner.ready").touch()
        while True:
            signal.pause()
except KeyboardInterrupt:
    (directory / "reaped.json").write_text(json.dumps([child.returncode for child in processes]))
    raise SystemExit(130)
'''
    verify_code = bootstrap + '''
import verify
verify.ROOT = Path(sys.argv[2])
verify.owned_process = owned_process
verify.commands_for = lambda *args: [[
    sys.executable, "-I", "-B", "-c", sys.argv[4], sys.argv[1], sys.argv[3],
]]
raise SystemExit(verify.main(["run", "e2e", "--base", "HEAD", "--json"]))
'''
    directories = [root / ".cache" / name for name in ("first-stack", "second-stack")]
    processes = []

    def start(code, *args):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", code, *map(str, args)], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
        )
        processes.append(process)
        return process

    def wait_ready(path, owner):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if path.exists():
                return
            assert owner.poll() is None, f"verify exited before {path.name}: {owner.stdout.read()}"
            time.sleep(0.01)
        pytest.fail(f"fixture did not create {path}")

    try:
        unrelated = start("import signal; signal.pause()")
        owners, groups = [], []
        for directory in directories:
            directory.mkdir(parents=True)
            owner = start(verify_code, ROOT / "scripts", root, directory, runner_code)
            owners.append(owner)
            wait_ready(directory / "runner.ready", owner)
            group = {"runner": int((directory / "runner.pid").read_text())}
            for role in ("backend", "frontend", "e2e"):
                wait_ready(directory / f"{role}.ready", owner)
                group[role] = int((directory / f"{role}.ready").read_text())
            groups.append(group)
        pids = [unrelated.pid, *(owner.pid for owner in owners), *(pid for group in groups for pid in group.values())]
        assert len(set(pids)) == 11
        assert all(os.getpgid(pid) == pid for pid in pids)

        owners[0].send_signal(signal.SIGTERM)
        output, _ = owners[0].communicate(timeout=5)
        assert owners[0].returncode == 2, output
        result = json.loads(output)
        assert result["status"] == "error"
        assert result["reason"] == "Verification interrupted; owned processes stopped"
        assert json.loads(Path(result["result_file"]).read_text()) == result
        for pid in groups[0].values():
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert json.loads((directories[0] / "reaped.json").read_text()) == [-signal.SIGKILL] * 3

        assert unrelated.poll() is None
        assert owners[1].poll() is None
        assert not (directories[1] / "reaped.json").exists()
        assert all(os.getpgid(pid) == pid for pid in groups[1].values())
        owners[1].send_signal(signal.SIGTERM)
        output, _ = owners[1].communicate(timeout=5)
        assert owners[1].returncode == 2, output
        for pid in groups[1].values():
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert json.loads((directories[1] / "reaped.json").read_text()) == [-signal.SIGKILL] * 3
        assert unrelated.poll() is None
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            process.stdout.close()
        # Include groups whose readiness handshake failed, but never signal any external PID.
        for directory in directories:
            for path in directory.glob("*.pid"):
                try:
                    os.killpg(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
