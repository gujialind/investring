# ============================================================================
# 审查留痕门禁的判据自检（issue #560）
# ============================================================================
# 为什么要有这个文件：判据写在 scripts/check_pr_review_trace.py，而「判红分支」只有喂进
# 合成输入才验证得到——真身 PR 要么合规、要么已经不在复盘窗口里。与
# backend/tests/unit/test_error_codes_doc_sync.py 的「判红口径自检」同型：**每条判红分支
# 各一条合成反例**，外加一条「合规底本必须判绿」的自检（否则整组反例都在跟影子较劲）。
#
# 诚实边界（不要假装没有）：本组只验证「判据会不会红」，不验证「结论是不是真的」——
# 文字留痕可伪造，一行假结论就能让门禁变绿，这是 #560 明写的检查上限。
#
# 接线断言（TestWorkflowWiring）：判据再准，步骤被删或被挪出 `changes` job 就等于门不在
# ——而 `CI OK` 把 skipped 视为通过，那种失效形态在 CI 上是「全绿」。
# ============================================================================

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ci_text import code_lines, job_block, step_body  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "check_pr_review_trace.py"
SPEC = importlib.util.spec_from_file_location("check_pr_review_trace", SCRIPT)
trace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trace)

CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
REVIEW_TRACE_STEP = "Assert review trace"

#: 合规底本：结论写在 `## 审查记录` 里（判据 A），文件数在限内（判据 C）。
COMPLIANT_BODY = "\n".join(
    [
        "## 改动内容",
        "",
        "收紧一处门禁。",
        "",
        "## 审查记录",
        "",
        "- 审查对象与覆盖范围（base/head SHA；未审部分及原因）：95a24a2…bff1769 全量",
        "- 结论与状态（打回修改 / 审查未完成 / 通过，可进入合入确认）：通过，可进入合入确认",
    ]
)

#: #559 的原句：明确写了没做独立 L2 语义审查——门禁拦「无信息」，不拦「没审查」。
NO_L2_BODY = "单人维护项目，所有者授权直接合入；未做独立 L2 语义审查。\n- 结论：未做独立 L2 语义审查\n"

#: 判据 B 取 (ii) 的反证形态（#478–#481）：结论写在小节之外，内容完整。
OUTSIDE_SECTION_BODY = "### 第 2 轮（子 Agent 独立评审）\n\n- 结论：通过，遗留两点已开 #494 跟进\n"

#: 超规模但写了理由（判据 C 的绿侧）。
OVERSIZE_WITH_REASON = COMPLIANT_BODY + "\n规模：23 个文件同属一次机械改名，不拆分。\n"


class TestConclusionCriterion:
    def test_compliant_body_is_compliant(self):
        assert trace.review_trace_problems(COMPLIANT_BODY, 3) == []

    def test_missing_review_section_is_red(self):
        problems = trace.review_trace_problems("## 改动内容\n\n只改了一行配置。\n", 1)
        assert len(problems) == 1, problems
        assert "结论" in problems[0], problems

    def test_blank_conclusion_is_red(self):
        """模板空占位不算留痕：冒号后空白（整节留白就是复盘里那 51%）。"""
        body = "\n".join(
            [
                "## 审查记录",
                "",
                "- 审查对象与覆盖范围（base/head SHA；未审部分及原因）：",
                "- 结论与状态（打回修改 / 审查未完成 / 通过，可进入合入确认）：",
                "- Blocker / Suggestion / Nit：",
            ]
        )
        problems = trace.review_trace_problems(body, 1)
        assert [p for p in problems if "结论" in p], problems

    def test_conclusion_outside_the_section_is_green(self):
        """判据 B 取 (ii)：位置不限。按小节取数会把 #478–#481 误判为未审。"""
        assert trace.review_trace_problems(OUTSIDE_SECTION_BODY, 5) == []

    def test_declared_no_l2_review_is_green(self):
        """#559 的**结论句**是合规样板：照实写「未做独立 L2 语义审查」判绿。

        只借它的结论句、不借它的规模：#559 改了 21 个文件且未写拆分理由，按判据 C 本就
        不合规（那种形态由 TestSizeCriterion 单独覆盖，两者不必在同一条用例里重叠）。
        这里取小规模文件数隔离判据 C，单验判据 A——门禁拦「无信息」，不拦「没审查」。
        """
        assert trace.review_trace_problems(NO_L2_BODY, 5) == []

    def test_template_placeholder_filled_in_place_is_green(self):
        """照模板原样填写（结论写在「结论与状态（…）：」后面）不能判红。

        门禁若只认「结论：」紧跟冒号这一种形态，照着仓库自己的模板填也会被判红——
        那是门禁在照自己的模板制造假红。
        """
        assert trace.review_trace_problems(COMPLIANT_BODY, 3) == []


class TestSizeCriterion:
    def test_limit_is_inclusive(self):
        assert trace.review_trace_problems(COMPLIANT_BODY, trace.FILE_COUNT_LIMIT) == []
        problems = trace.review_trace_problems(COMPLIANT_BODY, trace.FILE_COUNT_LIMIT + 1)
        assert len(problems) == 1, problems
        assert str(trace.FILE_COUNT_LIMIT) in problems[0], problems

    def test_oversize_with_reason_is_green(self):
        assert trace.review_trace_problems(OVERSIZE_WITH_REASON, 23) == []

    def test_oversize_without_reason_is_red(self):
        problems = trace.review_trace_problems(COMPLIANT_BODY, 23)
        assert len(problems) == 1, problems
        for keyword in trace.SIZE_KEYWORDS:
            assert keyword in problems[0], f"报错要点名该写什么（缺 {keyword}）：{problems}"

    def test_oversize_red_is_independent_of_conclusion(self):
        """超规模且无结论 → 两条都要报，不能只报一条（否则补了结论就漏掉规模）。"""
        assert len(trace.review_trace_problems("什么都没写", 23)) == 2


class TestExemption:
    def test_dependency_prs_are_exempt(self):
        assert trace.review_trace_problems("", 40, title="chore(deps): bump httpx", author="collyn") == []
        assert trace.review_trace_problems("", 40, title="fix: 修个 bug", author=trace.EXEMPT_AUTHOR) == []

    def test_exemption_does_not_cover_other_prs(self):
        """豁免写宽了就是零误报的反面：普通 PR 照样要判红。"""
        assert trace.review_trace_problems("", 40, title="fix: 修个 bug", author="collyn")
        assert trace.review_trace_problems("", 40, title="chore: 整理脚本", author="collyn")

    def test_exemption_reason_is_reported(self):
        assert trace.exempt_reason("chore(deps): bump httpx", "collyn")
        assert trace.exempt_reason("fix: x", trace.EXEMPT_AUTHOR)
        assert trace.exempt_reason("fix: x", "collyn") is None


# ============================================================================
# 接线：判据再准，步骤不在 `changes` job 里就等于门不在
# ============================================================================

#: 步骤体的必备行（缺一行即门禁失效或失去输入）。
WIRING_LINES = (
    "if: github.event_name == 'pull_request'",
    "PR_TITLE: ${{ github.event.pull_request.title }}",
    "PR_AUTHOR: ${{ github.event.pull_request.user.login }}",
    "PR_BODY: ${{ github.event.pull_request.body }}",
    "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}",
    "run: python3 scripts/check_pr_review_trace.py",
)


def wiring_problems(step_lines: list[str]) -> list[str]:
    """接线的问题清单（空 = 接线完整）。"""
    return [
        f"`{REVIEW_TRACE_STEP}` 丢了 `{text}`"
        + ("——非 PR 事件下该步骤会跑空或拿不到正文（PR 事件外 body 为空，判据无从生效）"
           if text.startswith("if:") else "")
        for text in WIRING_LINES
        if text not in step_lines
    ]


def compliant_step() -> list[str]:
    return [f"- name: {REVIEW_TRACE_STEP}", *WIRING_LINES]


@pytest.fixture(scope="module")
def ci_text() -> str:
    return CI_YML.read_text(encoding="utf-8")


class TestWorkflowWiring:
    def test_compliant_step_is_compliant(self):
        assert wiring_problems(compliant_step()) == []

    def test_step_is_wired_in_the_always_run_job(self, ci_text):
        """步骤必须在 `changes` job 里：它是 ci-ok.needs 中唯一无路径裁剪的 job。

        挂到别的 job 上，docs-only PR 会因裁剪把它变成 skipped，而 `CI OK` 把 skipped
        视为通过——门在 PR 页上还是绿的，只是它根本没跑（#456/#377 型死锁的前置检查）。
        """
        assert (
            f"- name: {REVIEW_TRACE_STEP}"
            in code_lines(job_block(ci_text, job="changes", source="ci.yml:changes"))
        ), "Assert review trace 不在 changes job 里——换 job 会让它被路径裁剪成 skipped"

    def test_real_step_is_complete(self, ci_text):
        problems = wiring_problems(
            code_lines(step_body(ci_text, step=REVIEW_TRACE_STEP, source="ci.yml:changes"))
        )
        assert not problems, "；".join(problems)

    def test_dropped_pr_condition_is_caught(self):
        mutated = [line for line in compliant_step() if line != WIRING_LINES[0]]
        problems = wiring_problems(mutated)
        assert problems and "if:" in problems[0], problems

    def test_dropped_body_input_is_caught(self):
        mutated = [line for line in compliant_step() if not line.startswith("PR_BODY:")]
        assert wiring_problems(mutated)

    def test_swallowed_script_call_is_caught(self):
        mutated = [line for line in compliant_step() if not line.startswith("run:")]
        problems = wiring_problems(mutated)
        assert problems and "run:" in problems[0], problems
