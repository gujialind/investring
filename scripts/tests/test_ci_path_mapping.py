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
#
# 判定面覆盖**两个事件形态**（#492）：PR 侧靠探针矩阵逐条钉，非 PR 事件（push /
# workflow_dispatch）由 test_push_side_is_conservative_full 钉——那一支是「PR 侧裁剪
# 失手的最后兜底」，早先本文件把 event_name 硬替换成 pull_request，它一次都没执行过。
# ============================================================================

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ci_text import step_run_block  # noqa: E402  切块能力与 test_ci_e2e_compare.py 共用

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

OUTPUT_KEYS = ("backend", "frontend", "cli", "scripts", "e2e_morph")


_STEP_MARKER = "detect"


def _extract_detect_run_block(source: str | None = None) -> str:
    """抽出 detect 步骤**自己的** run 块。结构变化即响亮失败。

    切块逻辑在 _ci_text.step_run_block（与 test_ci_e2e_compare.py 共用：同一份逻辑抄
    两处就必然漂移）；本文件的两条形态用例（`run: |-` 与内联 run）因此同时在守那一道。
    """
    return step_run_block(
        CI_YML.read_text(encoding="utf-8") if source is None else source,
        step=_STEP_MARKER,
        source="ci.yml:changes" if source is None else "合成 workflow",
    )


def _detect_script(event_name: str) -> str:
    """抽出 detect 块，只替换 `${{ github.* }}` 占位符。

    push 侧在 `git diff` 之前 `exit 0`，故 `base.sha` 那一条替换对它无影响。
    """
    return (
        _extract_detect_run_block()
        .replace("${{ github.event_name }}", event_name)
        .replace("${{ github.event.pull_request.base.sha }}", "probe-base")
    )


def _run_detect(changed_files: list[str], *, event_name: str = "pull_request") -> dict[str, str]:
    return _run_script(_detect_script(event_name), changed_files)


def _run_script(script: str, changed_files: list[str]) -> dict[str, str]:
    """用 fake git 把探针清单喂给给定的 detect 脚本文本，返回各输出的 true/false。

    脚本以参数传入而非内部抽取：反例用例要能把「注入后的 detect 块」喂进**同一套**判据，
    否则「守门会不会红」这件事本身没有验证路径。
    """
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

# near-miss 探针：路径**形似**某栈但不该命中（#492）。矩阵里其余条目全是「应命中」，
# 只测得出正则不够宽，测不出正则被放宽——`^(backend/|docs/)` 退化成 `^(backend|docs)`
# 正是 #462 记录的原始 bug 形态，没有这一组它仍然全绿。
#
# 本清单只守**前缀边界**，刻意不守尾部：`hit()` 的模式没有 `$`、也不按扩展名过滤，所以
# `frontend/e2e/x.bak` 这类垃圾文件同样会触发 e2e_morph（实测）。多跑一轮只是浪费 CI 时间，
# 属 fail-closed；反过来按后缀收紧，代价是漏跑一个栈——那才是假绿灯（根 AGENTS.md §3.5-1）。
# 因此别往这里补「尾部路径 + 期望全 false」的用例：那是把刻意的宽当漏洞在填。真要补尾部
# 探针，期望值必须是**命中**。
NEAR_MISS_PROBES = [
    ("backend-old/app/main.py", _expect()),
    ("frontendx/src/app/page.tsx", _expect()),
    ("ir-cli-x/main.py", _expect()),
    ("scripts-x/e2e_normalize.py", _expect()),
    (".github/workflows-disabled/ci.yml", _expect()),
]
PROBES += NEAR_MISS_PROBES


@pytest.mark.parametrize("changed,expected", PROBES, ids=[p[0] for p in PROBES])
def test_single_path_mapping(changed, expected):
    assert _run_detect([changed]) == expected


@pytest.mark.parametrize("old,new", [
    ("'^(backend/|docs/)'", "'^(backend|docs)'"),
    ("'^(frontend/|backend", "'^(frontend|backend"),
    ("'^ir-cli/'", "'^ir-cli'"),
    ("'^scripts/'", "'^script'"),
    (r"'^\.github/workflows/'", r"'^\.github/workflows'"),
])
def test_near_miss_probes_are_discriminating(old, new):
    """把每个栈前缀的正则放宽一档（少个斜杠），near-miss 探针必须至少抓住一条。

    没有本用例，「期望全 false」的探针可能只是在跟着正则的既有宽度躺平：放宽型改动让
    它们变红，才证明它们真的在守这条边界。
    """
    script = _detect_script("pull_request")
    assert old in script, f"detect 里的模式 {old} 变了——本反例已空跑，请同步它"
    loosened = script.replace(old, new, 1)
    caught = [p for p, exp in NEAR_MISS_PROBES if _run_script(loosened, [p]) != exp]
    assert caught, f"放宽成 {new} 后没有任何 near-miss 探针变红——它们没在守任何东西"


#: push / workflow_dispatch 的期望输出，两个用例共用：反例判据必须钉的是**同一个量**，
#: 否则「反例证明会红」和「真身断言会红」各说各话。
PUSH_SIDE_EXPECTED = _expect(backend="true", frontend="true", cli="true", scripts="true")


def test_push_side_is_conservative_full():
    """非 PR 事件（push / workflow_dispatch）：四个栈保守全 true，e2e_morph 恒 false。

    这一支是「PR 侧裁剪失手」时的最后兜底（根 AGENTS.md §3.5-1），坏法全是静默的：
    少置一个 true 则该栈在 main 上根本不跑，而 `ci-ok` 把 skipped 判成通过、CD 照常
    部署（#482）。e2e_morph 置 true 则是每次合入 main 白跑两轮全量 E2E。#492 之前
    本文件把 event_name 硬替换成 pull_request，这条分支一次都没执行过。
    """
    assert _run_detect([], event_name="push") == PUSH_SIDE_EXPECTED


@pytest.mark.parametrize("old,new", [
    ('echo "cli=true" >> "$GITHUB_OUTPUT"', 'echo "cli=false" >> "$GITHUB_OUTPUT"'),
    ('echo "e2e_morph=false" >> "$GITHUB_OUTPUT"',
     'echo "e2e_morph=true" >> "$GITHUB_OUTPUT"'),
])
def test_push_side_mutation_is_caught(old, new):
    """push 分支的两类静默坏法：少跑一栈、与多跑两轮全量 E2E——各注入一次，必须红。

    判据是「输出偏离 PUSH_SIDE_EXPECTED」而**不是**「脚本报错」：注入不改变 rc，靠丢掉
    `exit 0` 让流程走到 emit 会因重复输出而 rc≠0，那样两种注入都「红」，但不注入也红——
    反例就成了空跑。故这里保留整块原文（含 exit 0），只比对结果。
    """
    head, tail = _detect_script("push").split("exit 0", 1)
    assert old in head, f"push 分支文本变了，反例已空跑：{old}"
    assert new not in head, f"注入目标本就在原文里，替换是空操作：{new}"
    mutated = _run_script(head.replace(old, new, 1) + "exit 0" + tail, [])
    assert mutated != PUSH_SIDE_EXPECTED, (
        f"注入 {new} 后 push 侧输出仍与期望一致——"
        "test_push_side_is_conservative_full 认不出这种坏法，兜底分支无人守"
    )


def test_multi_path_is_union():
    """多栈并存取并集，且不误触未命中栈。"""
    got = _run_detect([
        "backend/app/services/trade_service.py",
        "frontend/src/app/page.tsx",
    ])
    assert got == _expect(backend="true", frontend="true")


#: 只匹配 emit 调用本体、遇 `#` 或换行即停：detect 块正是密集讨论 nowf 的地方，行尾注释里
#: 出现「不吃 nowf」这类字样不该被判成「这个输出吃了 nowf」（#492 的假阳性方向）。
#: 禁掉换行是承重的——否则 `emit backend …` 会一路吃到下一行的 `nowf`，把每个输出都算上。
_NOWF_CALL_RE = re.compile(r"^\s*emit\s+(\w+)\b[^#\n]*\bnowf\b", flags=re.MULTILINE)


def _nowf_calls(block: str) -> list[str]:
    """detect 块里带 nowf 的 emit 调用名（按出现顺序）；整行注释与行尾注释都不计。"""
    code = "\n".join(l.split("#", 1)[0] for l in block.splitlines())
    return _NOWF_CALL_RE.findall(code)


@pytest.mark.parametrize("line,expected", [
    ('emit backend "$(hit \'^(backend/)\')" nowf', ["backend"]),
    ('emit backend "$(hit \'^(backend/)\')"  # 吃 wf 强制位，不加 nowf', []),
])
def test_nowf_call_recognition(line, expected):
    """nowf 真落在某输出上必须认出（否则归属断言是不会红的空跑）；注释里提及不误伤。"""
    assert _nowf_calls(line) == expected


def test_nowf_marker_is_pinned_to_e2e_morph_only():
    """回归：`nowf` 恰好只落在 e2e_morph 的 emit 调用上（注释不计）。

    去掉它，改 workflow 会重新触发两轮全量 E2E（#467 已移除该 dogfood）；挪到别的
    输出上，则对应栈不再被「四栈全 true」兜住。两条语义都靠本断言钉死。
    """
    calls_with_nowf = _nowf_calls(_extract_detect_run_block())
    assert calls_with_nowf == ["e2e_morph"], (
        f"应恰好 e2e_morph 的 emit 带 nowf（实得 {calls_with_nowf}）——它决定「workflow "
        "改动是否触发两轮全量 E2E」，变更请同步本测试与 ci.yml 注释"
    )


def test_run_block_extraction_stays_inside_detect_step():
    """detect 写成 `run: |-`、且邻居步骤也有 `run: |` 时，抽到的必须仍是 detect 那块。

    旧实现只认字面 `run: |`：detect 一改成 `|-` 就跳过它去抽后面的步骤（用例是红了，
    但把诊断方向整个带偏——#492）。合成文本而非改真身，是为了让这条判据与真身解耦。
    """
    synthetic = "\n".join([
        "      - name: Checkout",
        "        run: |",
        "          echo neighbor-before",
        "      - id: detect",
        "        run: |-",
        '          echo "backend=true" >> "$GITHUB_OUTPUT"',
        "      - name: Later",
        "        run: |",
        "          echo neighbor-after",
    ])
    assert _extract_detect_run_block(synthetic) == 'echo "backend=true" >> "$GITHUB_OUTPUT"'


def test_run_block_extraction_fails_loudly_when_shape_is_unrecognized():
    """detect 的 run 被改成内联时，必须响亮失败，而不是去抽邻居步骤的块。"""
    synthetic = "\n".join([
        "      - id: detect",
        "        run: echo hi",
        "      - name: Later",
        "        run: |",
        "          echo neighbor-after",
    ])
    with pytest.raises(pytest.fail.Exception):
        _extract_detect_run_block(synthetic)


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
