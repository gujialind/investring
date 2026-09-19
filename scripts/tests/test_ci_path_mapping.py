# ============================================================================
# ci.yml `changes` job 的路径映射自测（issue #462 的仿真固化，#475 评审）
# ============================================================================
# 背景：PR 侧 job 裁剪靠 detect 步骤里的一串正则。写错（如 `^backend` 少个斜杠）会让
# 对应栈静默不跑而 `CI OK` 仍绿——只能靠 push 侧保守全量与下一个触碰该栈的 PR 兜底
# （刻意不引入 nightly 定时体检，#482）。#462 落地时的 8 场景仿真是人肉跑的（结论
# 没留在仓库），本文件把它固化为机器门禁。
#
# 做法：从 .github/workflows/ci.yml 抽出 `- id: detect` 的 run 块（不做任何语义
# 改写，只把 `${{ github.* }}` 占位符替换为固定值），再用 PATH 里的 fake `git`
# 把 `git diff --name-only` 的输出换成探针清单，跑**真实的 shell**并读
# `$GITHUB_OUTPUT`。任何对映射正则 / emit 语义 / nowf 语义的改动都会立刻体现在
# 下面的期望矩阵里——改映射必须同步改本文件（否则本测试红）。
# ============================================================================

import os
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

OUTPUT_KEYS = ("backend", "frontend", "cli", "scripts", "e2e_morph")


def _extract_detect_run_block() -> str:
    """抽出 `- id: detect` 步骤的 run 块（dedent 后返回）。结构变化即响亮失败。"""
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "- id: detect"), None)
    if start is None:
        pytest.fail("ci.yml 中找不到 `- id: detect` 步骤——changes job 结构变了？请同步更新本测试")
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(\s*)run:\s*\|\s*$", lines[j])
        if not m:
            continue
        run_indent = len(m.group(1))
        body: list[str] = []
        for line in lines[j + 1:]:
            if line.strip() and (len(line) - len(line.lstrip())) <= run_indent:
                break
            body.append(line)
        return textwrap.dedent("\n".join(body))
    pytest.fail("detect 步骤下找不到 `run: |` 块")
    raise AssertionError  # pragma: no cover


def _run_detect(changed_files: list[str]) -> dict[str, str]:
    """用 fake git 把探针清单喂给真实 detect 块，返回各输出的 true/false。"""
    script = (
        _extract_detect_run_block()
        .replace("${{ github.event_name }}", "pull_request")
        .replace("${{ github.event.pull_request.base.sha }}", "probe-base")
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        probe = tmp_path / "probe.txt"
        probe.write_text("\n".join(changed_files) + "\n", encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_git = bin_dir / "git"
        fake_git.write_text('#!/bin/sh\ncat "$PROBE_FILE"\n', encoding="utf-8")
        fake_git.chmod(0o755)
        github_output = tmp_path / "github_output"
        github_output.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "PROBE_FILE": str(probe),
            "GITHUB_OUTPUT": str(github_output),
        }
        proc = subprocess.run(
            ["bash", "-e", "-c", script], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            f"detect 块执行失败（rc={proc.returncode}）：\n{proc.stdout}\n{proc.stderr}"
        )
        result = {}
        for line in github_output.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
        return result


def _expect(**kwargs) -> dict[str, str]:
    """缺省全 false 的期望输出。"""
    return {key: kwargs.get(key, "false") for key in OUTPUT_KEYS}


# 探针矩阵：一行 = 一个改动文件（或一组）；期望值 = detect 应产出的 5 个输出。
# 语义见 ci.yml `changes` job 注释（含 wf 强制位与 e2e_morph 的 nowf 例外）。
PROBES = [
    ("backend/app/services/trade_service.py", _expect(backend="true")),
    ("backend/tests/integration/test_trades.py", _expect(backend="true")),
    ("docs/reference/business-constraints.md", _expect(backend="true")),
    ("backend/tests/seed_base.py",
     _expect(backend="true", frontend="true", e2e_morph="true")),
    ("backend/scripts/seed_e2e.py",
     _expect(backend="true", frontend="true", e2e_morph="true")),
    ("frontend/src/app/page.tsx", _expect(frontend="true")),
    ("frontend/e2e/regression.spec.ts", _expect(frontend="true", e2e_morph="true")),
    ("frontend/playwright.config.ts", _expect(frontend="true", e2e_morph="true")),
    ("ir-cli/ir_cli/main.py", _expect(cli="true")),
    ("scripts/e2e_normalize.py", _expect(scripts="true", e2e_morph="true")),
    ("scripts/tests/test_e2e_normalize.py", _expect(scripts="true", e2e_morph="true")),
    ("README.md", _expect()),
    ("AGENTS.md", _expect()),
    ("CLAUDE.md", _expect()),
    (".github/PULL_REQUEST_TEMPLATE.md", _expect()),
    ("docs/reference/documentation.md", _expect(backend="true")),
    # `.github/workflows/**` → 四个栈全 true，但 e2e_morph 不吃该强制（#467：改 CI
    # 配置不再多跑两轮全量 E2E；正面验证由 frontend-e2e 承担）
    (".github/workflows/ci.yml",
     _expect(backend="true", frontend="true", cli="true", scripts="true")),
    (".github/workflows/e2e-stack.yml",
     _expect(backend="true", frontend="true", cli="true", scripts="true")),
]


@pytest.mark.parametrize("changed,expected", PROBES, ids=[p[0] for p in PROBES])
def test_single_path_mapping(changed, expected):
    assert _run_detect([changed]) == expected


def test_multi_path_is_union():
    """多栈并存取并集，且不误触未命中栈。"""
    got = _run_detect([
        "backend/app/services/trade_service.py",
        "frontend/src/app/page.tsx",
    ])
    assert got == _expect(backend="true", frontend="true")


def test_nowf_marker_is_pinned_to_e2e_morph_only():
    """回归：`nowf` 恰好只落在 e2e_morph 的 emit 调用上（注释不计）。

    去掉它，改 workflow 会重新触发两轮全量 E2E（#467 已移除该 dogfood）；挪到别的
    输出上，则对应栈不再被「四栈全 true」兜住。两条语义都靠本断言钉死。
    """
    block = _extract_detect_run_block()
    code = "\n".join(l for l in block.splitlines() if not l.lstrip().startswith("#"))
    calls_with_nowf = re.findall(r"^\s*emit\s+(\w+)\b.*nowf", code, flags=re.MULTILINE)
    assert calls_with_nowf == ["e2e_morph"], (
        f"应恰好 e2e_morph 的 emit 带 nowf（实得 {calls_with_nowf}）——它决定「workflow "
        "改动是否触发两轮全量 E2E」，变更请同步本测试与 ci.yml 注释"
    )


def _assert_context_check_unconditional(ci_text: str) -> None:
    match = re.search(r"^  changes:\n(.*?)(?=^  [\w-]+:|\Z)", ci_text, re.MULTILINE | re.DOTALL)
    assert match, "missing changes job"
    header, body = match[1].split("    steps:\n", 1)
    assert not re.search(r"^    (?:if|needs|continue-on-error):", header, re.MULTILINE)
    steps = re.findall(r"^      - .*?(?=^      - |\Z)", body, re.MULTILINE | re.DOTALL)
    checkout = next(i for i, step in enumerate(steps) if "uses: actions/checkout@" in step)
    checks = [(i, step) for i, step in enumerate(steps) if "name: Check context documentation\n" in step]
    assert len(checks) == 1, "changes must run the context check exactly once"
    index, step = checks[0]
    assert index == checkout + 1, "context check must run immediately after checkout"
    assert not re.search(r"^        (?:if|continue-on-error):", step, re.MULTILINE)
    assert re.search(r"^        run: python3 scripts/check_context_docs.py$", step, re.MULTILINE)


@pytest.mark.parametrize("changed", ["AGENTS.md", "CLAUDE.md", ".github/PULL_REQUEST_TEMPLATE.md"])
def test_docs_only_pr_runs_context_check_even_when_stacks_skip(changed):
    assert _run_detect([changed]) == _expect()
    _assert_context_check_unconditional(CI_YML.read_text(encoding="utf-8"))


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
    text = CI_YML.read_text(encoding="utf-8")
    assert old in text
    with pytest.raises(AssertionError):
        _assert_context_check_unconditional(text.replace(old, new, 1))
