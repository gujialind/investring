# ============================================================================
# E2E 形态对比链路守门：判定体、豁免来源、needs 与 artifact 命名耦合（issue #490）
# ============================================================================
# compare 系 job 是「纯测试重构 PR 假绿灯」的唯一防线（#410）：两侧各跑一遍全量 E2E，
# 归一化成 TSV 后期望空 diff。而**判定本身写在 shell 里**——删掉收尾的 `exit 1`、把
# EXEMPT 写死成 true、或从 ci-ok.needs 摘掉 e2e-compare，都不会让任何用例变红，只会
# 让这道门禁永久失效而 CI 依旧全绿。第二同类缺口是 artifact 命名：前缀字面量散在两个
# workflow 的 7 行里（caller 声明 1 行 + 由它派生的 6 个比对位点），改一处就采不到数据
# （多数会响亮但难懂地红，「同步改错」则是静默）。
#
# 为什么放 scripts/tests 而不是等一次真实 run：`changes.detect` 对 `.github/workflows/**`
# 有强制位（四栈全 true，见 ci.yml 该步注释），所以**只改 workflow 的 PR 必然执行
# cli-contract-check → pytest scripts/tests**——正是 #490 说「compare 永不执行」的那类
# 改动。缺口因此能在纯文本层面堵死。capture 分支的 runtime 面（只有形态 PR 才实跑）
# 仍按 e2e-stack.yml 头部登记 + 手动确认，理由见 #490 评论。
#
# 与 test_ci_mysql_account.py 同族：切块走 _ci_text；判据是模块级纯函数，反例用例把
# 注入后的**合成文本**喂进同一套判据（否则「守门会不会红」本身没有验证路径）；断言只
# 钉语义关键词与先后，不钉整句文案与缩进。真身读文件一律在 fixture 体内做——若在
# import 期读，job 改名会塌成一条 collection error（一个红格子、丢掉逐用例粒度）。
# ============================================================================

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ci_text import (  # noqa: E402
    code_lines,
    job_block,
    step_body,
    step_run_block,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
STACK_YML = REPO_ROOT / ".github" / "workflows" / "e2e-stack.yml"

#: 豁免标签。改名要同时动仓库 label、这里与 label-hygiene 巡检（#483）。
MORPH_LABEL = "e2e-morph-expected"
SIDES = ("baseline", "candidate")
#: workflow 上下文占位符——它们出现在被断言的字面量里，命名出来只为不被 f-string 的
#: 花括号转义绕晕（`${{ }}` 在 f-string 里要写四遍花括号）。
MATRIX_SIDE = "${{ matrix.side }}"
INPUTS_SIDE = "${{ inputs.side }}"
BASE_SHA = "${{ github.event.pull_request.base.sha }}"

COMPARE_JOB = "e2e-compare"
CAPTURE_JOB = "e2e-compare-capture"
DIFF_STEP = "Diff morphology"
SWAP_STEP = "Swap e2e to base (baseline only)"
CAPTURE_RUN_STEP = "Run Playwright tests (capture)"
RAW_UPLOAD_STEP = "Upload raw JSON"
#: 诊断品上传步：缺文件**不该**染红 job（#414），故它们带 continue-on-error 且不带
#: `if-no-files-found: error`。登记在案，新增诊断品步骤时加一行。
DIAGNOSTIC_UPLOAD_STEPS = ("Upload Playwright report", "Upload failure evidence")

#: 前缀字面量在 workflow 里的登记清单（**含该前缀的行数**，不是行号也不是出现次数：
#: 行号会随无关改动漂移，而一行可能提到两次，如 `--input .../e2e-raw-x/e2e-raw-x.json`）。
#: 数字对不上即红：要么是新加的引用点没登记，要么是有人改名没同步本守门。
#: e2e-stack.yml 的第 3 处是 input 的 description 文本——它算引用点，因为那是使用者
#: 在 `uses:` 界面里看到的前缀契约，说错就是文档在骗人。
PREFIX_LITERAL_SITES = {
    ".github/workflows/ci.yml": 4,
    ".github/workflows/e2e-stack.yml": 3,
}


def _lines_with(text_lines: list[str], directive: str) -> bool:
    """某条 YAML 键是否存在（忽略行尾注释：code_lines 不剥行内注释，理由见其文档）。"""
    return any(line.startswith(directive) for line in text_lines)


# ============================================================================
# 判据（纯函数：真身与反例共用同一套）
# ============================================================================


def judge_body_problems(run_text: str) -> list[str]:
    """compare 判定体的约束 → 问题清单（空 = 合规）。

    钉的是**语义与顺序**：显式捕获 diff 退出码 → 空 diff 早退 → 豁免分支（且必须在
    报错之前 return）→ ::error:: → 以 exit 1 收尾。顺序反了或少了收尾的 exit 1，
    门禁就从「拦合入」变成「打印一条红字后继续绿」。
    """
    lines = code_lines(run_text)
    problems: list[str] = []

    def first(needle, what):
        for i, line in enumerate(lines):
            if needle in line:
                return i
        problems.append(f"缺少{what}")
        return None

    i_diff = first("|| rc=$?", "显式捕获 diff 退出码（`… || rc=$?`）")
    i_rc0 = first('[ "$rc" -eq 0 ]', "空 diff 的早退分支")
    i_exempt = first('[ "$EXEMPT" = "true" ]', "豁免分支的条件判断")
    i_error = first("::error::", "非豁免路径的 ::error:: 注解")
    exit_ones = [i for i, line in enumerate(lines) if line == "exit 1"]
    if not exit_ones:
        problems.append("没有收尾的 `exit 1`——非预期形态变化只报注解、不影响 job 结果")
    if problems:
        return problems

    if lines[-1] != "exit 1":
        problems.append(f"`exit 1` 不是判定体最后一条语句（最后是 `{lines[-1]}`）")
    if len(exit_ones) != 1:
        problems.append(f"`exit 1` 有 {len(exit_ones)} 行，期望恰好 1 行")
    if not any(line == "exit 0" for line in lines[i_exempt:i_error]):
        problems.append("豁免分支里没有独立的 `exit 0`——豁免形同虚设")
    order = {"diff 捕获": i_diff, "空 diff 早退": i_rc0, "豁免分支": i_exempt, "::error::": i_error}
    labels = list(order)
    for a, b in zip(labels, labels[1:]):
        if not order[a] < order[b]:
            problems.append(f"判定体顺序错了：`{a}` 必须早于 `{b}`")
    return problems


def exempt_problems(compare_lines: list[str]) -> list[str]:
    """EXEMPT 必须由标签派生，不能是常量或别的来源。"""
    expected = (
        f"EXEMPT: ${{{{ contains(github.event.pull_request.labels.*.name, "
        f"'{MORPH_LABEL}') }}}}"
    )
    if not any(line.startswith("EXEMPT:") for line in compare_lines):
        return ["compare job 里没有 EXEMPT 这个 env"]
    if expected not in compare_lines:
        return [f"EXEMPT 不是由 `{expected}` 派生——写死或换来源都能让豁免永久放行"]
    return []


def needs_problems(compare_lines: list[str], ci_ok_lines: list[str]) -> list[str]:
    """compare 必须依赖采集、且 ci-ok 必须依赖 compare——否则红也不拦。"""
    problems = []
    if f"needs: {CAPTURE_JOB}" not in compare_lines:
        problems.append(f"{COMPARE_JOB} 不再 `needs: {CAPTURE_JOB}`")
    for needed in (CAPTURE_JOB, COMPARE_JOB):
        if f"- {needed}" not in ci_ok_lines:
            problems.append(
                f"ci-ok.needs 里没有 {needed}——它红了也不会拦住 CI OK（required check 只认 ci-ok）"
            )
    return problems


def artifact_prefix(capture_lines: list[str]) -> str:
    """从 caller 的 `artifact_prefix` 反推前缀字面量（六处耦合的唯一基准）。"""
    for line in capture_lines:
        if line.startswith("artifact_prefix:"):
            value = line.split(":", 1)[1].strip()
            if not value.endswith(MATRIX_SIDE):
                pytest.fail(
                    f"`artifact_prefix: {value}` 不再是 `{MATRIX_SIDE}` 结尾的形态——"
                    "两侧 artifact 名不再由 matrix 决定？请同步本守门"
                )
            return value[: -len(MATRIX_SIDE)]
    pytest.fail(
        f"{CAPTURE_JOB} 里没有 `artifact_prefix:`——前缀改到别处声明了？请同步本守门"
    )


def naming_problems(prefix: str, compare_lines: list[str], stack_lines: list[str]) -> list[str]:
    """六处命名耦合必须与 caller 前缀同源。"""
    expected = [
        (compare_lines, "ci.yml e2e-compare 的 download pattern", f"pattern: {prefix}*"),
        *[
            (
                compare_lines,
                f"ci.yml e2e-compare 的 {side} 侧 --input",
                f"--input /tmp/raw/{prefix}{side}/{prefix}{side}.json",
            )
            for side in SIDES
        ],
        (
            stack_lines,
            "e2e-stack.yml 的 JSON 落盘路径",
            f"PLAYWRIGHT_JSON_OUTPUT_FILE: /tmp/{prefix}{INPUTS_SIDE}.json",
        ),
        (
            stack_lines,
            "e2e-stack.yml 的 raw JSON 上传 path",
            f"path: /tmp/{prefix}{INPUTS_SIDE}.json",
        ),
        (
            stack_lines,
            "e2e-stack.yml 的 raw JSON 上传 name",
            "name: ${{ inputs.artifact_prefix }}",
        ),
    ]
    return [
        f"{where} 应为 `{text}`——artifact 名由 caller 的 artifact_prefix 单点决定，"
        "只改一处会让 compare 采不到数据"
        for lines, where, text in expected
        if text not in lines
    ]


def capture_problems(stack_text: str, stack_lines: list[str], source: str) -> list[str]:
    """capture 分支与 baseline 换目录步里「改了就静默失真」的结构约束。"""
    problems = []
    run_text = step_run_block(stack_text, step=CAPTURE_RUN_STEP, source=source)
    joined = " ".join(code_lines(run_text))
    for token in ("--retries=${{ inputs.retries }}", "--workers=2"):
        if token not in joined:
            problems.append(
                f"capture 运行步丢了 `{token}`"
                + ("（retry 会把 flaky 洗成 passed，形态对比失真）" if "retries" in token
                   else "（并发数一变，两侧时序分布不同，假 diff）")
            )
    swap = code_lines(step_run_block(stack_text, step=SWAP_STEP, source=source))
    if f"if: inputs.mode == 'capture' && inputs.side == '{SIDES[0]}'" not in code_lines(
        step_body(stack_text, step=SWAP_STEP, source=source)
    ):
        problems.append(
            f"`{SWAP_STEP}` 的条件不再同时看 mode 与 side——baseline 会被换到不该换的侧"
        )
    if "rm -rf frontend/e2e" not in swap:
        problems.append("swap 步丢了 `rm -rf frontend/e2e`——head 独有 spec 残留成并集")
    if f'git checkout "{BASE_SHA}" -- frontend/e2e' not in swap:
        problems.append(
            f"swap 步的 base.sha 不再是**带引号**的 `{BASE_SHA}`——"
            "空值展开会让 baseline 静默等同 candidate，恒空 diff 且全绿（#490）"
        )
    raw = code_lines(step_body(stack_text, step=RAW_UPLOAD_STEP, source=source))
    if not _lines_with(raw, "if-no-files-found: error"):
        problems.append(
            f"`{RAW_UPLOAD_STEP}` 丢了 `if-no-files-found: error`——"
            "raw JSON 是 normalizer 的必需输入，缺失必须响亮失败"
        )
    moved = sum(1 for line in stack_lines if line.startswith("if-no-files-found: error"))
    if moved != 1:
        problems.append(
            f"`if-no-files-found: error` 全 job 有 {moved} 行（期望只在 {RAW_UPLOAD_STEP}）"
            "——诊断品缺文件不该染红 job（#414）"
        )
    for step in DIAGNOSTIC_UPLOAD_STEPS:
        body = code_lines(step_body(stack_text, step=step, source=source))
        if not _lines_with(body, "continue-on-error: true"):
            problems.append(f"诊断品步骤 `{step}` 丢了 continue-on-error: true（#414）")
    return problems


# ============================================================================
# 反例底本：合规形态（与真身同形，但由本文件自持，见类注释）
# ============================================================================

COMPLIANT_JUDGE_BODY = """
rc=0
diff /tmp/base.tsv /tmp/cand.tsv > /tmp/morph.diff || rc=$?
cat /tmp/morph.diff
if [ "$rc" -eq 0 ]; then echo "✅ 形态一致（空 diff）"; exit 0; fi
if [ "$EXEMPT" = "true" ]; then
  echo "::warning::形态有差异，经标签放行"
  exit 0
fi
echo "::error::E2E 形态非预期变化"
exit 1
"""


def compliant_naming(prefix: str) -> tuple[list[str], list[str]]:
    """六处命名耦合的合规行文本（真身断言的是**同一批**字符串）。"""
    return (
        [
            f"pattern: {prefix}*",
            *[
                f"--input /tmp/raw/{prefix}{side}/{prefix}{side}.json"
                for side in SIDES
            ],
        ],
        [
            f"PLAYWRIGHT_JSON_OUTPUT_FILE: /tmp/{prefix}{INPUTS_SIDE}.json",
            f"path: /tmp/{prefix}{INPUTS_SIDE}.json",
            "name: ${{ inputs.artifact_prefix }}",
        ],
    )


def compliant_needs() -> tuple[list[str], list[str]]:
    return (
        [f"needs: {CAPTURE_JOB}"],
        [f"- {CAPTURE_JOB}", f"- {COMPARE_JOB}"],
    )


def compliant_capture_stack() -> str:
    """capture 分支的合规骨架（step 头缩进与真身一致：6 空格）。"""
    return "\n".join(
        [
            f"      - name: {SWAP_STEP}",
            f"        if: inputs.mode == 'capture' && inputs.side == '{SIDES[0]}'",
            "        run: |",
            "          rm -rf frontend/e2e",
            f'          git checkout "{BASE_SHA}" -- frontend/e2e',
            f"      - name: {CAPTURE_RUN_STEP}",
            "        run: |",
            "          rc=0",
            "          npx playwright test --retries=${{ inputs.retries }} --workers=2"
            " --reporter=list,json || rc=$?",
            f"      - name: {RAW_UPLOAD_STEP}",
            "        uses: actions/upload-artifact@v7",
            "        with:",
            "          name: ${{ inputs.artifact_prefix }}",
            f"          path: /tmp/e2e-placeholder{INPUTS_SIDE}.json",
            "          if-no-files-found: error",
            "      - name: Upload failure evidence",
            "        continue-on-error: true",
            "        uses: actions/upload-artifact@v7",
            "      - name: Upload Playwright report",
            "        continue-on-error: true",
            "        uses: actions/upload-artifact@v7",
        ]
    )


# ============================================================================
# 真身
# ============================================================================


@pytest.fixture(scope="module")
def ci_text() -> str:
    return CI_YML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stack_text() -> str:
    return STACK_YML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def capture_block(ci_text) -> str:
    return job_block(ci_text, job=CAPTURE_JOB, source=f"ci.yml:{CAPTURE_JOB}")


@pytest.fixture(scope="module")
def compare_block(ci_text) -> str:
    return job_block(ci_text, job=COMPARE_JOB, source=f"ci.yml:{COMPARE_JOB}")


@pytest.fixture(scope="module")
def ci_ok_block(ci_text) -> str:
    return job_block(ci_text, job="ci-ok", source="ci.yml:ci-ok")


@pytest.fixture(scope="module")
def judge_body(ci_text) -> str:
    return step_run_block(ci_text, step=DIFF_STEP, source="ci.yml:e2e-compare")


@pytest.fixture(scope="module")
def prefix(capture_block) -> str:
    return artifact_prefix(code_lines(capture_block))


class TestJudgeBody:
    """判定体：#490 的第一条——它过去在任何用例里都没有对应断言。"""

    def test_compliant_template_is_compliant(self):
        """底本自身必须过判据，否则下面的反例组全在跟影子较劲。"""
        assert judge_body_problems(COMPLIANT_JUDGE_BODY) == []

    def test_real_judge_body_is_compliant(self, judge_body):
        problems = judge_body_problems(judge_body)
        assert not problems, "ci.yml 的 Diff morphology 判定体不再满足：" + "；".join(problems)

    def test_exempt_derives_from_the_label(self, compare_block):
        problems = exempt_problems(code_lines(compare_block))
        assert not problems, "；".join(problems)

    def test_compare_is_wired_into_the_gate(self, compare_block, ci_ok_block):
        problems = needs_problems(code_lines(compare_block), code_lines(ci_ok_block))
        assert not problems, "；".join(problems)

    def test_capture_stays_morph_gated(self, capture_block):
        """"只改 e2e 形态输入才跑两轮全量 E2E"——去掉这个条件等于每次 PR 白跑两轮。"""
        assert (
            "if: needs.changes.outputs.e2e_morph == 'true'"
            in code_lines(capture_block)
        ), f"{CAPTURE_JOB} 不再由 e2e_morph 触发，形态采集会变成常跑开销"


class TestArtifactNaming:
    def test_naming_sites_are_single_sourced(self, prefix, compare_block, stack_text):
        problems = naming_problems(
            prefix, code_lines(compare_block), code_lines(job_block(
                stack_text, job="e2e", source="e2e-stack.yml:e2e"
            ))
        )
        assert not problems, "；".join(problems)

    def test_prefix_is_the_registered_literal(self, prefix):
        """前缀改名必须显式登记（连同下一条例外的引用点清单）。"""
        assert prefix == "e2e-raw-", (
            f"caller 声明的 artifact 前缀是 {prefix!r}，与本守门登记的 literal 不一致"
            "——改名请同步 PREFIX_LITERAL_SITES 与本条"
        )

    def test_every_prefix_literal_is_registered(self, prefix):
        """workflow 里该前缀的字面量位点数 == 登记清单。

        job 块只覆盖到下一个同级键，故 workflow 级文本（input description、其他 job）
        里的引用在判定面之外——没有这条，新增一处没登记的引用点就是守门的盲区，
        而「守门看不见的位置」比「守门判失败」更坏。
        """
        seen = {}
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
            count = sum(
                1 for line in path.read_text(encoding="utf-8").splitlines()
                if prefix in line
            )
            if count:
                seen[str(path.relative_to(REPO_ROOT))] = count
        assert seen == PREFIX_LITERAL_SITES, (
            f"实际引用点 {seen} ≠ 登记清单 {PREFIX_LITERAL_SITES}——"
            "新增/删除引用点时同步本清单（前缀六处同源由 TestArtifactNaming 另一条守）"
        )


class TestCaptureStructure:
    def test_real_capture_branch_is_pinned(self, stack_text):
        problems = capture_problems(
            stack_text,
            code_lines(job_block(stack_text, job="e2e", source="e2e-stack.yml:e2e")),
            "e2e-stack.yml:e2e",
        )
        assert not problems, "e2e-stack.yml 的 capture 分支不再满足：" + "；".join(problems)


# ============================================================================
# 反例：守门本身必须会红（全部走合成文本，真身结构漂移时这组仍要能跑）
# ============================================================================


class TestGuardIsDiscriminating:
    """每条注入一个违规形态，判据必须认出——否则上面那些断言只是不会红的空跑。"""

    def test_dropped_exit_one_is_caught(self):
        assert judge_body_problems(COMPLIANT_JUDGE_BODY.replace("\nexit 1\n", "\n"))

    def test_exit_one_before_the_error_annotation_is_caught(self):
        """"先红着退出、再打注解"看似等价，实际是让 ::error:: 永远进不了日志。"""
        mutated = COMPLIANT_JUDGE_BODY.replace(
            'echo "::error::E2E 形态非预期变化"\nexit 1\n',
            'exit 1\necho "::error::E2E 形态非预期变化"\n',
        )
        assert mutated != COMPLIANT_JUDGE_BODY
        assert judge_body_problems(mutated)

    def test_exempt_without_exit_is_caught(self):
        mutated = COMPLIANT_JUDGE_BODY.replace('  echo "::warning::形态有差异，经标签放行"\n  exit 0\n',
                                               '  echo "::warning::形态有差异，经标签放行"\n')
        assert mutated != COMPLIANT_JUDGE_BODY
        assert judge_body_problems(mutated)

    def test_hardcoded_exempt_is_caught(self):
        assert exempt_problems(["EXEMPT: true"])
        assert exempt_problems(code_lines(COMPLIANT_JUDGE_BODY))
        assert not exempt_problems(
            [f"EXEMPT: ${{{{ contains(github.event.pull_request.labels.*.name, "
             f"'{MORPH_LABEL}') }}}}", "run: |"]
        )

    def test_compare_removed_from_ci_ok_is_caught(self):
        compare, ci_ok = compliant_needs()
        assert not needs_problems(compare, ci_ok)
        assert needs_problems(compare, [x for x in ci_ok if x != f"- {COMPARE_JOB}"])
        assert needs_problems([], ci_ok)

    def test_single_sited_prefix_rename_is_caught(self):
        """只改 caller 前缀（其余五处仍是旧名）必须被逐处点名。

        刻意不借用真身的前缀（不 request prefix）：这组反例要在 workflow 结构漂移时
        照样能跑，而且「改名后的新前缀」与「真身当前前缀」撞车时，断言会变成空跑。
        """
        old, new = "e2e-raw-", "e2e-shape-"
        compare, stack = compliant_naming(old)
        assert not naming_problems(old, compare, stack)
        problems = naming_problems(new, compare, stack)
        assert len(problems) == 5, f"改名后只报出 {len(problems)} 处，期望 5 处（caller 自身不算）"
        assert all(new in p for p in problems), "报错必须给出该改成的样子，否则又要人肉比对"

    def test_capture_workers_lost_is_caught(self):
        mutated = compliant_capture_stack().replace(" --workers=2", "")
        assert capture_problems(mutated, code_lines(mutated), "合成 capture")

    def test_unquoted_base_sha_is_caught(self):
        mutated = compliant_capture_stack().replace(f'"{BASE_SHA}"', BASE_SHA)
        problems = capture_problems(mutated, code_lines(mutated), "合成 capture")
        assert any("引号" in p for p in problems), problems

    def test_swap_condition_degraded_to_mode_is_caught(self):
        mutated = compliant_capture_stack().replace(
            f"        if: inputs.mode == 'capture' && inputs.side == '{SIDES[0]}'",
            "        if: inputs.mode == 'capture'",
        )
        assert capture_problems(mutated, code_lines(mutated), "合成 capture")

    def test_missing_rm_rf_is_caught(self):
        mutated = compliant_capture_stack().replace("          rm -rf frontend/e2e\n", "")
        assert capture_problems(mutated, code_lines(mutated), "合成 capture")

    def test_if_no_files_found_moved_to_a_diagnostic_is_caught(self):
        mutated = compliant_capture_stack().replace(
            "          path: /tmp/e2e-placeholder" + INPUTS_SIDE + ".json\n"
            "          if-no-files-found: error\n",
            "          path: /tmp/e2e-placeholder" + INPUTS_SIDE + ".json\n",
        ).replace(
            "      - name: Upload failure evidence\n        continue-on-error: true\n",
            "      - name: Upload failure evidence\n        continue-on-error: true\n"
            "        if-no-files-found: error\n",
        )
        assert mutated != compliant_capture_stack()
        problems = capture_problems(mutated, code_lines(mutated), "合成 capture")
        assert any(RAW_UPLOAD_STEP in p for p in problems), problems

    def test_compliant_capture_template_is_compliant(self):
        text = compliant_capture_stack()
        assert capture_problems(text, code_lines(text), "合成 capture") == []
