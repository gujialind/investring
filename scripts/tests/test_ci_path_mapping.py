import copy
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(Path(__file__).resolve().parent)]
import ci_gate as gate  # noqa: E402
from _ci_text import job_block, step_run_block  # noqa: E402

CI_YML = ROOT / ".github/workflows/ci.yml"
POLICY = gate.validate_policy(gate.read_json((ROOT / "scripts/ci_policy.json").read_text()))
OUTPUT_KEYS = ("backend", "frontend", "cli", "scripts", "e2e_morph")
FULL = "backend frontend cli scripts"


def expected(scopes=""):
    return {key: str(key in scopes.split()).lower() for key in OUTPUT_KEYS}


def outputs(paths, event="pull_request", **kwargs):
    return gate.scope_outputs(gate.plan_changes(POLICY, paths, event, **kwargs))


PROBES = [
    ("backend/app/services/trade_service.py", "backend"),
    ("backend/tests/integration/test_trades.py", "backend"),
    ("docs/reference/business-constraints.md", "backend"),
    ("docs/reference/documentation.md", "backend"),
    ("backend/tests/seed_base.py", "backend frontend e2e_morph"),
    ("backend/scripts/seed_e2e.py", "backend frontend e2e_morph"),
    ("frontend/src/app/page.tsx", "frontend"),
    ("frontend/e2e/regression.spec.ts", "frontend e2e_morph"),
    ("frontend/playwright.config.ts", "frontend e2e_morph"),
    ("ir-cli/ir_cli/main.py", "cli"),
    ("scripts/e2e_normalize.py", "scripts e2e_morph"),
    ("scripts/tests/test_e2e_normalize.py", "scripts e2e_morph"),
    ("README.md", ""), ("AGENTS.md", ""), ("CLAUDE.md", ""),
    (".github/PULL_REQUEST_TEMPLATE.md", ""),
    ("backend/README.md", "backend"), ("frontend/README.md", "frontend"),
    ("ir-cli/README.md", "cli"), ("scripts/README.md", "scripts"),
    ("frontend/e2e/README.md", "frontend e2e_morph"),
    (".github/workflows/ci.yml", FULL), (".github/workflows/e2e-stack.yml", FULL),
    (".github/workflows/README.md", FULL),
    ("scripts/ci_gate.py", FULL), ("scripts/ci_policy.json", FULL),
    ("scripts/tests/_ci_text.py", FULL + " e2e_morph"),
    ("scripts/tests/test_ci_gate.py", FULL + " e2e_morph"),
    ("scripts/tests/test_ci_future.md", FULL + " e2e_morph"),
    ("backend/app/routers/trades.py", "backend frontend"),
    ("backend/app/schemas/portfolio.py", "backend frontend"),
    ("backend/app/main.py", "backend frontend"), ("openapi.json", "backend frontend"),
    ("backend/tests/seed_base.py.bak", "backend e2e_morph"),
    ("backend/scripts/seed_e2e.py.bak", "backend e2e_morph"),
    ("frontend/playwright.config.ts.bak", "frontend e2e_morph"),
    ("scripts/e2e_normalize.py.bak", "scripts e2e_morph"),
    ("frontend/e2e/x.bak", "frontend e2e_morph"),
    ("backend/app/routers-old/x.py", "backend"),
    ("backend/app/schemas-old/x.py", "backend"),
    ("backend/app/main.py.bak", "backend"),
    ("scripts/ci_gate.py.bak", "scripts"), ("scripts/ci_policy.json.bak", "scripts"),
    ("README.md.py", FULL), ("Dockerfile", FULL),
]
NEAR_MISSES = [
    "backend-old/app/main.py", "docs-old/rules.txt", "frontendx/src/app/page.tsx",
    "ir-cli-x/main.py", "scripts-x/e2e_normalize.py", ".github/workflows-disabled/ci.yml",
]


@pytest.mark.parametrize("path,scopes", PROBES)
def test_single_path_mapping(path, scopes):
    assert outputs([path]) == expected(scopes)


def assert_unmapped(policy, path):
    assert gate.matching_rules(policy, path) == []
    assert gate.scope_outputs(gate.plan_changes(policy, [path], "pull_request")) == expected(FULL)


@pytest.mark.parametrize("path", NEAR_MISSES)
def test_near_miss_is_unmapped_then_falls_back(path):
    assert_unmapped(POLICY, path)


@pytest.mark.parametrize("prefix,path", list(zip(
    ["backend/", "docs/", "frontend/", "ir-cli/", "scripts/", ".github/workflows/"], NEAR_MISSES,
)))
def test_loosened_prefix_mutation_is_caught(prefix, path):
    policy = copy.deepcopy(POLICY)
    rule = next(rule for rule in policy["rules"] if prefix in rule["paths"])
    rule["paths"][rule["paths"].index(prefix)] = prefix.rstrip("/")
    with pytest.raises(AssertionError):
        assert_unmapped(policy, path)


@pytest.mark.parametrize("paths,scopes", [
    (["backend/x.py", "frontend/x.ts"], "backend frontend"),
    (["backend/x.py", "unmapped.conf"], FULL),
    (["README.md", "unmapped.conf"], FULL),
    ([".github/workflows/ci.yml", "frontend/e2e/x.ts"], FULL + " e2e_morph"),
    ([], FULL),
])
def test_multi_path_union_and_per_path_fallback(paths, scopes):
    assert outputs(paths) == expected(scopes)


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_non_pr_is_full_without_morphology(event):
    assert outputs([], event) == expected(FULL)
    assert outputs(["frontend/e2e/x.ts", "scripts/tests/test_ci_gate.py"], event) == expected(FULL)


def test_control_tree_difference_and_reason_deduplication():
    assert outputs(["README.md"], control_changed=True) == expected(FULL)
    reasons = gate.plan_changes(POLICY, ["backend/x.py"] * 2, "pull_request")
    assert len(reasons["backend"]) == 1
    assert "backend/x.py" in reasons["backend"][0]


@pytest.mark.parametrize("scalar", ["|", "|-", "|+", ">", ">-", ">+"])
def test_run_block_extraction_stays_inside_detect(scalar):
    text = f"      - id: detect\n        run: {scalar}\n          wanted\n      - name: Later\n        run: |\n          neighbor"
    assert step_run_block(text, step="detect", source="synthetic") == "wanted"


def test_inline_run_extraction_fails_loudly():
    text = "      - id: detect\n        run: inline\n      - name: Later\n        run: |\n          neighbor"
    with pytest.raises(pytest.fail.Exception):
        step_run_block(text, step="detect", source="synthetic")


def _assert_context_check_unconditional(text):
    header, body = job_block(text, job="changes", source="ci.yml").split("    steps:\n", 1)
    assert not re.search(r"^    (?:if|needs|continue-on-error):", header, re.M)
    steps = re.findall(r"^      - .*?(?=^      - |\Z)", body, re.M | re.S)
    checkout = next(i for i, step in enumerate(steps) if "uses: actions/checkout@" in step)
    checks = [(i, step) for i, step in enumerate(steps) if "name: Check context documentation\n" in step]
    assert len(checks) == 1
    index, step = checks[0]
    assert index == checkout + 1
    assert not re.search(r"^        (?:if|continue-on-error):", step, re.M)
    assert re.search(r"^        run: python3 scripts/check_context_docs.py$", step, re.M)


@pytest.mark.parametrize("path", ["AGENTS.md", "CLAUDE.md", ".github/PULL_REQUEST_TEMPLATE.md"])
def test_docs_only_pr_runs_context_check_when_stacks_skip(path):
    assert outputs([path]) == expected()
    _assert_context_check_unconditional(CI_YML.read_text())


@pytest.mark.parametrize("old,new", [
    ("      - name: Check context documentation\n        run: python3 scripts/check_context_docs.py\n", ""),
    ("      - name: Check context documentation\n", "      - name: Check context documentation\n        if: false\n"),
    ("      - name: Check context documentation\n", "      - name: Check context documentation\n        continue-on-error: true\n"),
    ("  changes:\n", "  changes:\n    if: false\n"),
    ("  changes:\n", "  changes:\n    needs: backend-test\n"),
    ("  changes:\n", "  changes:\n    continue-on-error: true\n"),
    ("run: python3 scripts/check_context_docs.py", "run: python3 scripts/check_context_docs.py || true"),
])
def test_context_gate_rejects_bypass_mutations(old, new):
    text = CI_YML.read_text()
    assert old in text
    with pytest.raises(AssertionError):
        _assert_context_check_unconditional(text.replace(old, new, 1))
