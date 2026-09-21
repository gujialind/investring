import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(Path(__file__).resolve().parent)]
import ci_gate as gate  # noqa: E402
from _ci_text import job_block, step_body, step_run_block  # noqa: E402

GATE = ROOT / "scripts/ci_gate.py"
CI = ROOT / ".github/workflows/ci.yml"
POLICY_TEXT = (ROOT / "scripts/ci_policy.json").read_text()
POLICY = gate.validate_policy(gate.read_json(POLICY_TEXT))
SCOPES = ("backend", "frontend", "cli", "scripts", "e2e_morph")
FULL = "backend frontend cli scripts"
JOBS = {
    "changes": [], "docker-build-smoke": [],
    "backend-test": ["backend"], "backend-test-mysql": ["backend"],
    "cli-contract-check": ["cli", "backend", "scripts"],
    "frontend-check": ["frontend"], "frontend-e2e": ["frontend"],
    "e2e-compare-capture": ["e2e_morph"], "e2e-compare": ["e2e_morph"],
}


def expected(scopes=""):
    return {scope: str(scope in scopes.split()).lower() for scope in SCOPES}


def needs_for(scopes=""):
    needs = {job: {"result": "success" if not deps or set(deps) & set(scopes.split()) else "skipped"}
             for job, deps in JOBS.items()}
    needs["changes"]["outputs"] = expected(scopes)
    return needs


def replace_at(value, path, replacement):
    value = copy.deepcopy(value)
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = replacement
    return value


def test_policy_invariants():
    assert gate.SCOPES == SCOPES
    assert POLICY["jobs"] == JOBS
    assert POLICY["version"] == 1
    assert gate.validate_policy(copy.deepcopy(POLICY)) == POLICY
    assert gate.read_json('{"a": [1, null, true]}') == {"a": [1, None, True]}


@pytest.mark.parametrize("text,error", [
    ("{", "invalid JSON"), ('{"x": 1, "x": 2}', "duplicate JSON key: x"),
    ('{"outer": {"result": "success", "result": "failure"}}', "duplicate JSON key: result"),
    ('{"x": NaN}', "non-standard JSON constant: NaN"),
    ('{"x": Infinity}', "non-standard JSON constant: Infinity"),
    ('{"x": -Infinity}', "non-standard JSON constant: -Infinity"),
])
def test_bad_json(text, error):
    with pytest.raises(gate.GateError, match=error):
        gate.read_json(text)


@pytest.mark.parametrize("path,value,error", [
    (("version",), True, "structure/version"), (("version",), 2, "structure/version"),
    (("extra",), 1, "structure/version"), (("rules",), [], "nonempty list"),
    (("rules",), {}, "nonempty list"), (("rules", 0), None, "invalid policy rule"),
    (("rules", 0, "kind"), "regex", "invalid policy rule"),
    (("rules", 0, "paths"), "backend/", "invalid policy rule"),
    (("rules", 0, "paths"), [], "invalid policy rule"),
    (("rules", 0, "paths"), [""], "invalid policy rule"),
    (("rules", 0, "paths"), ["x", "x"], "invalid policy rule"),
    (("rules", 0, "scopes"), ["unknown"], "invalid policy rule"),
    (("rules", 0, "scopes"), ["backend", "backend"], "invalid policy rule"),
    (("rules", 0, "scopes"), [None], "invalid policy rule"),
    (("rules", 0, "reason"), "  ", "invalid policy rule"),
    (("jobs",), {}, "always be required"),
    (("jobs", "changes"), ["backend"], "always be required"),
    (("jobs", "docker-build-smoke"), ["frontend"], "always be required"),
    (("jobs", "ci-ok"), [], "invalid job policy"),
    (("jobs", "Bad_ID"), [], "invalid job policy"),
    (("jobs", "frontend-check"), ["unknown"], "invalid job policy"),
])
def test_invalid_policy(path, value, error):
    with pytest.raises(gate.GateError, match=error):
        gate.validate_policy(replace_at(POLICY, path, value))


@pytest.mark.parametrize("path,scopes", [
    ("README.md", ""), ("backend/x.py", "backend"), ("ir-cli/x.py", "cli"),
    ("scripts/x.py", "scripts"), ("frontend/e2e/x.ts", "frontend e2e_morph"),
    ("unknown.conf", FULL),
])
@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize("result", ["success", "skipped", "failure", "cancelled"])
def test_aggregate_result_matrix(path, scopes, job, result, capsys):
    reasons = gate.plan_changes(POLICY, [path], "pull_request")
    needs = needs_for(scopes)
    needs[job]["result"] = result
    if job == "changes" and result != "success":
        with pytest.raises(gate.GateError, match=f"changes must succeed, got {result}"):
            gate.aggregate(POLICY, reasons, needs)
        return
    required = not JOBS[job] or bool(set(JOBS[job]) & set(scopes.split()))
    passed = result == "success" or (not required and result == "skipped")
    assert gate.aggregate(POLICY, reasons, needs) is passed
    table = capsys.readouterr().out
    assert table.startswith("job | required | actual | verdict | reason\n")
    assert len(table.splitlines()) == len(JOBS) + 1
    assert f"{job} | {required} | {result} | {'pass' if passed else 'fail'} |" in table
    assert "always required" in table
    if required and JOBS[job]:
        assert repr(path) in table


@pytest.mark.parametrize("path,value,error", [
    (("extra-job",), {"result": "success"}, "needs job IDs mismatch"),
    (("backend-test",), None, "invalid result for backend-test"),
    (("backend-test",), {}, "invalid result for backend-test"),
    (("backend-test", "result"), "timed_out", "invalid result for backend-test"),
    (("backend-test", "result"), True, "invalid result for backend-test"),
    (("backend-test", "result"), [], "invalid result for backend-test"),
    (("changes", "outputs"), None, "changes outputs"),
    (("changes", "outputs"), {}, "changes outputs"),
    (("changes", "outputs", "backend"), "true", "recomputed impact"),
    (("changes", "outputs", "cli"), False, "changes outputs"),
    (("changes", "outputs", "scripts"), "False", "changes outputs"),
    (("changes", "outputs", "extra"), "false", "changes outputs"),
])
def test_malformed_needs(path, value, error):
    with pytest.raises(gate.GateError, match=error):
        gate.aggregate(POLICY, gate.plan_changes(POLICY, ["README.md"], "pull_request"),
                       replace_at(needs_for(), path, value))


@pytest.fixture
def repo(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GITHUB_", "CI_NEEDS"))}
    env.update(HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path),
               GIT_AUTHOR_NAME="CI fixture", GIT_AUTHOR_EMAIL="ci@example.invalid",
               GIT_COMMITTER_NAME="CI fixture", GIT_COMMITTER_EMAIL="ci@example.invalid")

    def git(*args, data=None):
        proc = subprocess.run(["git", *args], cwd=tmp_path, env=env, input=data, capture_output=True)
        assert proc.returncode == 0, proc.stderr.decode()
        return proc.stdout.decode().strip()

    git("init", "-q", str(tmp_path))
    source = GATE.read_text()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ci_policy.json").write_text(POLICY_TEXT)
    (tmp_path / "scripts/ci_gate.py").write_text(source)

    def commit(files, parent=None):
        git("read-tree", "--empty")
        tracked = {"scripts/ci_policy.json": POLICY_TEXT, "scripts/ci_gate.py": source, **files}
        for path, content in tracked.items():
            blob = git("hash-object", "-w", "--stdin", data=content.encode())
            git("update-index", "--add", "--cacheinfo", "100644", blob, path)
        tree = git("write-tree")
        sha = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", "fixture")
        git("update-ref", "HEAD", sha)
        return sha

    return tmp_path, env, commit


def event_env(repo, base, head, event="pull_request"):
    root, env, _ = repo
    payload = root / "event.json"
    payload.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}))
    return {**env, "GITHUB_EVENT_NAME": event, "GITHUB_EVENT_PATH": str(payload),
            "GITHUB_SHA": head, "GITHUB_OUTPUT": str(root / "output")}


def run_gate(repo, env, command, *, workflow=False):
    args = [sys.executable, str(GATE), command]
    if workflow:
        job, step = ("changes", "detect") if command == "detect" else ("ci-ok", "Aggregate result")
        block = job_block(CI.read_text(), job=job, source=str(CI))
        args = ["bash", "-e", "-o", "pipefail", "-c", step_run_block(block, step=step, source=str(CI))]
    return subprocess.run(args, cwd=repo[0], env=env, capture_output=True, text=True)


def assert_exit(proc, code, message=""):
    assert proc.returncode == code, (proc.stdout, proc.stderr)
    assert message in proc.stdout + proc.stderr


@pytest.mark.parametrize("before,after,paths,scopes", [
    ({"frontend/old.ts": "same"}, {"ir-cli/new.py": "same"}, ["frontend/old.ts", "ir-cli/new.py"], "frontend cli"),
    ({"backend/deleted.py": "x"}, {}, ["backend/deleted.py"], "backend"),
    ({}, {"frontend/e2e/a\nunknown.py": "x"}, ["frontend/e2e/a\nunknown.py"], "frontend e2e_morph"),
    ({}, {"backend/x.py": "x", "unknown.conf": "x"}, ["backend/x.py", "unknown.conf"], FULL),
    ({}, {"README.md": "x"}, ["README.md"], ""),
    ({}, {}, [], FULL),
])
def test_real_git_diff_and_cli(repo, before, after, paths, scopes):
    base = repo[2](before)
    head = repo[2](after, base)
    env = event_env(repo, base, head)
    event, changed, control = gate.event_changes(repo[0], env)
    assert (event, sorted(changed), control) == ("pull_request", sorted(paths), False)
    assert_exit(run_gate(repo, env, "detect"), 0)
    lines = Path(env["GITHUB_OUTPUT"]).read_text().splitlines()
    assert len(lines) == 5
    assert dict(line.split("=", 1) for line in lines) == expected(scopes)
    env["CI_NEEDS"] = json.dumps(needs_for(scopes))
    assert_exit(run_gate(repo, env, "aggregate"), 0, "docker-build-smoke | True | success | pass")


@pytest.mark.parametrize("path,control", [
    ("scripts/ci_policy.json", True), ("scripts/ci_gate.py", True), ("scripts/tests/_ci_text.py", True),
    ("scripts/tests/test_ci_future.py", True), (".github/workflows/ci.yml", True),
    ("backend/base-only.py", False),
])
def test_base_side_tree_comparison(repo, path, control):
    ancestor = repo[2]({})
    base = repo[2]({path: "base-only change"}, ancestor)
    head = repo[2]({"README.md": "head-only change"}, ancestor)
    env = event_env(repo, base, head)
    assert gate.event_changes(repo[0], env) == ("pull_request", ["README.md"], control)
    assert_exit(run_gate(repo, env, "detect"), 0)
    assert Path(env["GITHUB_OUTPUT"]).read_text() == "".join(f"{k}={v}\n" for k, v in expected(FULL if control else "").items())
    env["CI_NEEDS"] = json.dumps(needs_for())
    assert_exit(run_gate(repo, env, "aggregate"), 2 if control else 0,
                "disagree with recomputed impact" if control else "not applicable")


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_non_pr_real_event(repo, event):
    head = repo[2]({"frontend/e2e/x.ts": "x"})
    env = event_env(repo, None, head, event)
    assert gate.event_changes(repo[0], env) == (event, [], False)
    assert_exit(run_gate(repo, env, "detect"), 0)
    env["CI_NEEDS"] = json.dumps(needs_for(FULL))
    assert_exit(run_gate(repo, env, "aggregate"), 0, "e2e-compare | False | skipped | pass")


@pytest.mark.parametrize("case,error", [
    ("missing-base", "missing event head/base identity"), ("invalid-sha", "invalid event commit SHA"),
    ("wrong-head", "checkout HEAD does not match event head SHA"),
    ("unreachable-base", "git diff failed"), ("unrelated-base", "git diff failed"),
    ("payload-list", "event payload must be an object"), ("payload-broken", "invalid JSON"),
    ("payload-duplicate", "duplicate JSON key: pull_request"),
    ("unsupported-event", "unsupported event"), ("missing-event-file", "No such file"),
])
@pytest.mark.parametrize("command", ["detect", "aggregate"])
def test_cli_rejects_bad_event(repo, case, error, command):
    base = repo[2]({})
    head = repo[2]({"README.md": "x"}, None if case == "unrelated-base" else base)
    env = event_env(repo, base, head)
    payload = Path(env["GITHUB_EVENT_PATH"])
    data = json.loads(payload.read_text())
    if case == "missing-base":
        del data["pull_request"]["base"]
    elif case in {"invalid-sha", "unreachable-base"}:
        data["pull_request"]["base"]["sha"] = "HEAD;exit 0" if case == "invalid-sha" else "f" * 40
    elif case == "wrong-head":
        data["pull_request"]["head"]["sha"] = base
    elif case == "payload-list":
        data = []
    payload.write_text(json.dumps(data))
    if case == "payload-broken":
        payload.write_text("{")
    elif case == "payload-duplicate":
        payload.write_text('{"pull_request": {}, "pull_request": {}}')
    elif case == "unsupported-event":
        env["GITHUB_EVENT_NAME"] = "schedule"
    elif case == "missing-event-file":
        payload.unlink()
    env["CI_NEEDS"] = json.dumps(needs_for())
    assert_exit(run_gate(repo, env, command), 2, error)


@pytest.mark.parametrize("workflow", [False, True], ids=["cli", "workflow"])
def test_cli_exit_contract(repo, workflow):
    base = repo[2]({})
    head = repo[2]({"backend/x.py": "x"}, base)
    env = event_env(repo, base, head)
    assert_exit(run_gate(repo, env, "detect", workflow=workflow), 0)
    assert_exit(run_gate(repo, env, "detect", workflow=workflow), 2, "scope output already exists")
    needs = needs_for("backend")
    env["CI_NEEDS"] = json.dumps(needs)
    assert_exit(run_gate(repo, env, "aggregate", workflow=workflow), 0, "backend-test | True | success | pass")
    needs["backend-test"]["result"] = "skipped"
    env["CI_NEEDS"] = json.dumps(needs)
    assert_exit(run_gate(repo, env, "aggregate", workflow=workflow), 1, "backend-test | True | skipped | fail")
    for bad, error in [
        ("[]", "needs job IDs mismatch"), ("{}", "missing="),
        (json.dumps({k: v for k, v in needs.items() if k != "docker-build-smoke"}), "docker-build-smoke"),
        ('{"changes": {}, "changes": {}}', "duplicate JSON key: changes"),
        (json.dumps(replace_at(needs, ("changes", "result"), "skipped")), "changes must succeed"),
        (json.dumps(replace_at(needs, ("backend-test", "result"), "unknown")), "invalid result for backend-test"),
    ]:
        env["CI_NEEDS"] = bad
        assert_exit(run_gate(repo, env, "aggregate", workflow=workflow), 2, error)
    for command, variable in [("detect", "GITHUB_OUTPUT"), ("aggregate", "CI_NEEDS")]:
        missing = {k: v for k, v in env.items() if k != variable}
        assert_exit(run_gate(repo, missing, command, workflow=workflow), 2, variable)
    for raw, error in [("{}", "unsupported policy"), ("null", "unsupported policy"),
                       ('{"version": 1, "version": 1}', "duplicate JSON key: version")]:
        (repo[0] / "scripts/ci_policy.json").write_text(raw)
        for command in ("detect", "aggregate"):
            assert_exit(run_gate(repo, env, command, workflow=workflow), 2, error)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("workflow", [False, True], ids=["cli", "workflow"])
def test_cli_rejects_nonstandard_constants_in_unused_outputs(repo, constant, workflow):
    base = repo[2]({})
    head = repo[2]({"README.md": "x"}, base)
    env = event_env(repo, base, head)
    needs = replace_at(needs_for(), ("docker-build-smoke", "outputs"), {"probe": "CONSTANT"})
    env["CI_NEEDS"] = json.dumps(needs).replace('"CONSTANT"', constant)
    assert_exit(run_gate(repo, env, "aggregate", workflow=workflow), 2,
                f"non-standard JSON constant: {constant}")


def field(block, name, indent=4):
    lines = block.splitlines()
    indexes = [i for i, line in enumerate(lines) if line.startswith(" " * indent + name + ":")]
    assert len(indexes) == 1, name
    index = indexes[0]
    value = lines[index].split(":", 1)[1].strip()
    if value not in ("", "|", "|-", "|+", ">", ">-", ">+"):
        return value
    body = []
    for line in lines[index + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line.strip())
    return " ".join(body).strip()


def assert_workflow(text):
    jobs_text = text.split("jobs:\n", 1)[1]
    assert set(re.findall(r"^  ([\w-]+):$", jobs_text, re.M)) == set(JOBS) | {"ci-ok"}
    blocks = {job: job_block(text, job=job, source=str(CI)) for job in (*JOBS, "ci-ok")}
    headers = {job: block.split("    steps:\n", 1)[0] for job, block in blocks.items()}
    assert field(headers["ci-ok"], "name") == '"CI OK"'
    assert field(headers["ci-ok"], "if") == "always()"
    needs = re.findall(r"^      - ([\w-]+)$", headers["ci-ok"], re.M)
    assert len(needs) == len(JOBS) and set(needs) == set(JOBS)
    for job, header in headers.items():
        assert not re.search(r"^    continue-on-error:", header, re.M), job
        if job in ("changes", "docker-build-smoke", "e2e-compare"):
            assert not re.search(r"^    if:", header, re.M), job
        elif job != "ci-ok":
            assert field(header, "needs") == "changes"
            assert field(header, "if") == " || ".join(f"needs.changes.outputs.{s} == 'true'" for s in JOBS[job])
    assert field(headers["e2e-compare"], "needs") == "e2e-compare-capture"
    actual = re.findall(r"^      (\w+): \$\{\{ steps.detect.outputs\.(\w+) \}\}$", headers["changes"], re.M)
    assert actual == [(scope, scope) for scope in SCOPES]
    for job, step, command in [("changes", "detect", "detect"), ("ci-ok", "Aggregate result", "aggregate")]:
        block = blocks[job]
        checkout = step_body(block, step="Checkout", source=str(CI))
        assert "uses: actions/checkout@" in checkout
        assert field(checkout, "ref", 10) == "${{ github.event.pull_request.head.sha || github.sha }}"
        assert field(checkout, "fetch-depth", 10) == "0"
        body = step_body(block, step=step, source=str(CI))
        for protected in (checkout, body):
            assert not re.search(r"^        (?:if|continue-on-error):", protected, re.M)
        assert step_run_block(block, step=step, source=str(CI)).strip() == f"python3 scripts/ci_gate.py {command}"
    aggregate = step_body(blocks["ci-ok"], step="Aggregate result", source=str(CI))
    assert field(aggregate, "CI_NEEDS", 10) == "${{ toJSON(needs) }}"
    tests = step_body(blocks["cli-contract-check"], step="Run scripts tests", source=str(CI))
    assert not re.search(r"^        (?:if|continue-on-error):", tests, re.M)
    assert field(tests, "run", 8) == "python -m pytest scripts/tests -q"
    triggers = "\n".join(line for line in text.split("on:\n", 1)[1].split("\nconcurrency:", 1)[0].splitlines()
                         if not line.lstrip().startswith("#"))
    pr = triggers.split("  pull_request:\n", 1)[1].split("  workflow_dispatch:", 1)[0]
    assert not re.search(r"^    paths(?:-ignore)?:", pr, re.M)
    push = triggers.split("  push:\n", 1)[1].split("  pull_request:\n", 1)[0]
    assert field(push, "branches") == "[main]"
    assert field(push, "paths-ignore") == "- '**/*.md' - 'docs/**'"


def test_workflow_structure():
    assert_workflow(CI.read_text())


@pytest.mark.parametrize("old,new", [
    ('  ci-ok:\n    name: "CI OK"\n    if: always()', '  ci-ok:\n    name: "CI OK"\n    if: success()'),
    ("      - docker-build-smoke\n", ""),
    ("  ci-ok:\n", "  unlisted-job:\n    runs-on: ubuntu-latest\n  ci-ok:\n"),
    ("  ci-ok:\n", "  ci-ok:\n    continue-on-error: true\n"),
    ("if: needs.changes.outputs.backend == 'true'", "if: false"),
    ("backend: ${{ steps.detect.outputs.backend }}", "backend: ${{ steps.detect.outputs.frontend }}"),
    ("ref: ${{ github.event.pull_request.head.sha || github.sha }}", "ref: ${{ github.sha }}"),
    ("          python3 scripts/ci_gate.py detect", "          true"),
    ("          python3 scripts/ci_gate.py aggregate", "          python3 scripts/ci_gate.py aggregate || true"),
    ("CI_NEEDS: ${{ toJSON(needs) }}", "CI_NEEDS: '{}'"),
    ("      - name: Aggregate result\n", "      - name: Aggregate result\n        continue-on-error: true\n"),
    ("      - name: Run scripts tests\n", "      - name: Run scripts tests\n        if: false\n"),
    ("      - name: Run scripts tests\n", "      - name: Run scripts tests\n        continue-on-error: true\n"),
    ("  pull_request:\n", "  pull_request:\n    paths-ignore: ['**/*.md']\n"),
    ("      - 'docs/**'", "      - 'docs*'"),
])
def test_workflow_mutations_are_rejected(old, new):
    text = CI.read_text()
    assert old in text
    with pytest.raises(AssertionError):
        assert_workflow(text.replace(old, new, 1))
