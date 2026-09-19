# ============================================================================
# 前端增量覆盖率门禁守门：三处「守卫自己哑火」的结构性断言 + 守卫自身的档位（issue #509）
# ============================================================================
# 这道门禁的全部价值都在「它会不会响」上，而它的四段判定体写在 shell 里——删掉
# `,json:diff-cover.json`、把分母下限数字改小、把归因步骤退回 `--numstat … frontend/src/lib`
# 的自由文案匹配，都不会让任何前端用例变红，只会让门禁永久失效而 CI 依旧全绿。
# #509 实测的正是这一族：`coverage.include` 被收窄到只剩一个文件时，四项覆盖率
# 反而变成 100%、diff-cover 判定 exit 0，**没有任何一处会红**。
#
# 为什么放 scripts/tests 而不是等一次真实 run：`changes.detect` 对 `.github/workflows/**`
# 有强制位（四栈全 true，见 ci.yml 该步注释），所以只改 workflow 的 PR 必然执行
# cli-contract-check → `pytest scripts/tests`（ci.yml 的该步行）；缺口因此能在纯文本
# 层面堵死，与 test_ci_e2e_compare.py（#490）、test_ci_mysql_account.py（#548）同族。
#
# 判据取向：
# - **钉来源，不钉结果**——断言的是「门禁步骤里同时产出 md 与 json」「下限常量存在且
#   没被改小」，不是「覆盖率数字是多少」；文案怎么改都不误伤，删掉机制必红。
# - 切块只走 _ci_text（同一套判据不抄两份），且**先按 job 切块再找步骤**：ci.yml 里
#   `Diff coverage gate`/`Append diff coverage summary`/`Warn on unmapped diff` 三个名字
#   在 backend-test 与 frontend-check 两个 job 里**重名**，不 scoped 就会抽到后端那一份，
#   拿着别的 job 的步骤当被断言的对象（比失败更坏的静默）。
# - 反例喂的是**合成文本**，与 test_error_codes_doc_sync.py 的 anchor 反例组同形：
#   任一反例不红 ⇒ 该断言恒真 ⇒ 本守门未完成。
# - 能直接改真身文本的反例优先改真身（`vitest_config.replace(...)` / `ratchet_lines` 的
#   文案替换），因为合成 blob 只证明「判据写得对」，真身变异还证明「真身此刻真被它管着」；
#   这类用例都带 `assert mutated != original` 的自校验——真身文案一漂移，反例自己先报错。
# ============================================================================

import glob
import re
import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ci_text import code_lines, job_block, step_body, step_run_block  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
VITEST_CONFIG = REPO_ROOT / "frontend" / "vitest.config.ts"
GITIGNORE = REPO_ROOT / ".gitignore"

FRONTEND_JOB = "frontend-check"
GATE_STEP = "Diff coverage gate (PR changed lines)"
RATCHET_STEP = "Assert lcov data source (PR)"
ATTRIBUTION_STEP = "Warn on attribution gap (PR)"
SUMMARY_STEP = "Append diff coverage summary"

#: 分母下限的高水位（2026-09-19 实测：`include ∖ exclude` 命中 10 个文件）。只升不降，
#: 口径与 frontend/AGENTS.md §3、ci.yml 的 LCOV_SF_MIN 一致；下调这个数就是放松门禁，
#: 必须在 PR 里被显式讨论。
SF_MIN_HIGH_WATER = 10
#: ci.yml 里的运行时下限常量名（与测试共享同一语义，两侧各自出现一次是刻意的：
#: 一处在 CI 里判、一处在 review 时判，任何一处被删都有另一处兜）。
SF_MIN_PATTERN = re.compile(r"LCOV_SF_MIN=(\d+)")


# ============================================================================
# 判据（模块级纯函数：入参是切好的文本，出参是问题清单）
# ============================================================================


def gate_problems(gate_lines: list[str]) -> list[str]:
    """门禁必须**同时**产出 markdown 与 json 两种报告。

    json 是归因断言（attribution_problems）的唯一数据源：只有结构化报告能区分
    「这个文件没改动行」与「这个文件被门禁漏掉了」，markdown 做不到。少了它就等于
    把 #509 的头号失效形态重新变成不可见。
    """
    joined = " ".join(gate_lines)
    problems = []
    if not re.search(r"--format\s+\S*markdown:diff-cover\.md", joined):
        problems.append("门禁步骤不再产出 `markdown:diff-cover.md`（Step Summary 与 artifact 依赖它）")
    if "json:diff-cover.json" not in joined:
        problems.append(
            "门禁步骤不再产出 `json:diff-cover.json`——归因断言失去数据源，"
            "「报告非空而单文件无归因」这一档重新变成静默（#509）"
        )
    return problems


def attribution_problems(step_lines: list[str]) -> list[str]:
    """归因断言必须吃结构化 JSON，且不得再自己重算一遍分母。"""
    joined = " ".join(step_lines)
    problems = []
    if "diff-cover.json" not in joined:
        problems.append("归因步骤没有引用 diff-cover.json")
    if "src_stats" not in joined:
        problems.append(
            "归因步骤没有比对 `src_stats`——退回到 grep 自由文案的话，diff-cover 升级改词即永久失配（#509 第 2 条）"
        )
    if "--numstat" in joined or re.search(r"--\s+frontend/src/lib", joined):
        problems.append(
            "归因步骤又硬编码了一遍分母路径（`frontend/src/lib`）——目录改名时 git 不报错、"
            "added=0 会让守卫静默变哑，且它与 vitest.config.ts 的 include 必然漂移"
        )
    if "No lines with coverage information" in joined:
        problems.append("归因步骤仍在匹配 diff-cover 的 markdown 文案，升级改词即永久失配")
    return problems


def ratchet_problems(step_lines: list[str], denominator_size: int) -> list[str]:
    """分母棘轮：`LCOV_SF_MIN` 必须存在、不得跌破高水位、也不得高于配置能给出的数量。

    三个方向各自堵一种漂移：
    - 删掉常量/判据 ⇒ #509 的实测场景（收窄 include 后全绿）无人可拦；
    - 把数字改小 ⇒ 放松门禁必须先改这个测试，等于把决策摆到 review 台上；
    - 数字高于配置 ⇒ 说明有人改了 `coverage.include` 而没同步这里（CI 会永久红，
      比静默更坏的是「红到所有人习惯」）。
    """
    joined = " ".join(step_lines)
    m = SF_MIN_PATTERN.search(joined)
    if not m:
        return [
            f"{RATCHET_STEP} 里没有 `LCOV_SF_MIN=<数量>` 棘轮——收窄 coverage.include 时"
            "覆盖率数字会变好而门禁 exit 0，只有分母文件数看得见（#509 实测）"
        ]
    declared = int(m.group(1))
    problems = []
    if declared < SF_MIN_HIGH_WATER:
        problems.append(
            f"LCOV_SF_MIN={declared} 低于高水位 {SF_MIN_HIGH_WATER}——分母下限只升不降，"
            "要下调请在 issue 里说明理由并同步本测试"
        )
    if declared > denominator_size:
        problems.append(
            f"LCOV_SF_MIN={declared} 高于 coverage.include∖exclude 当前命中的 {denominator_size} 个文件"
            "——分母被收窄了但下限没改（CI 侧该步会恒红）"
        )
    if not any(line == "exit 1" for line in step_lines):
        problems.append("分母棘轮没有独立的 `exit 1`——只会打印不会拦")
    elif joined.count("::error::") < joined.count("exit 1"):
        problems.append(
            f"`::error::`（{joined.count('::error::')} 处）少于 `exit 1`（{joined.count('exit 1')} 处）"
            "——有判红分支被降级成 warning/打印：棘轮有三条退出路径，只有一条真拦"
        )
    return problems


def diagnostic_step_problems(step_meta: str) -> list[str]:
    """`if: always()` 的诊断步骤必须 `continue-on-error: true`。

    这类步骤跑在上游已经红了的场合，脚本自身崩（sed 读不到文件、node 解析坏 JSON）会把
    「第二个红」挂到 job 上，而它本该只发 warning——失败原因被指到错误的步骤。#414 对
    `Append coverage summary` 已经立了这条口径。
    """
    meta = step_meta.split("run:")[0]
    problems = []
    if "always()" not in meta:
        problems.append("诊断步骤不再有 `if: always()`——上游先红时它根本不跑，出口文案形同虚设")
    if "continue-on-error: true" not in meta:
        problems.append("诊断步骤缺 `continue-on-error: true`——脚本自身崩会把 job 弄红（#414 口径）")
    return problems


def blocking_step_problems(step_meta: str) -> list[str]:
    """判红步骤反过来必须**不许**被 continue-on-error 卸掉（那等于删守卫）。"""
    meta = step_meta.split("run:")[0]
    if "continue-on-error" in meta:
        return [f"{RATCHET_STEP} 是唯一的分母守卫，不能被 `continue-on-error` 卸成打印"]
    return []


def summary_problems(step_lines: list[str]) -> list[str]:
    """摘要步骤必须有「报告不存在」的出口。

    旧写法 `cat diff-cover.md >> "$GITHUB_STEP_SUMMARY" 2>/dev/null || true` 在文件不
    存在时静默追加 0 行——摘要里连「没有摘要」都看不出来（#509 第 3 条）。
    这里**确实钉了出口文案**（`未生成 diff-cover.md`）而不是只钉「有分支」：这一步唯一的
    机制就是往 Step Summary 写一句话，除了那句话没有别的可钉对象；代价是改文案要连带改
    这里，方向上是守门响亮误伤（可改）而非门禁静默失效。
    """
    joined = " ".join(step_lines)
    problems = []
    if "未生成 diff-cover.md" not in joined:
        problems.append(
            "摘要步骤没有「报告未生成」的出口——diff-cover.md 缺失时会静默追加 0 行（#509 第 3 条）"
        )
    if re.search(r"cat\s+diff-cover\.md\s*>>\s*\"\$GITHUB_STEP_SUMMARY\"\s*2>/dev/null\s*\|\|\s*true", joined):
        problems.append("摘要步骤退回 `cat … 2>/dev/null || true` 的静默形态")
    return problems


def _code_only(config_text: str) -> str:
    """剥掉注释后再做文本断言。

    不剥就会自缚：本文件用注释解释「为什么不该写 `all: false`」，那句话本身即禁句
    （实测踩过）。误判方向也确认过：假若将来把禁句写进行尾注释，红的是守门（响亮、
    可改），不是门禁静默失效，所以只做整行注释这一档即可。

    ⚠️ 只用**整行**判据，绝不用跨字符的正则去抠块注释：glob 里就含 `/*` 这两个字符
    （`src/lib/**/*.{ts,tsx}` 的 `**/` 紧跟 `*`），一把梭会把真代码吃掉、让反推的分母
    凭空少几个文件（实测踩过：include 被削成 `src/lib*.ts`，命中 0 个文件）。
    """
    return "\n".join(
        l for l in config_text.splitlines()
        if not l.strip().startswith(("//", "/*", "*", "*/"))
    )


#: `all` 被显式置假的三种书写形态：裸键、双引号键、单引号键（TS 对象字面量三种都合法）。
#: `\b` 挡住 `install: false` 这类误伤；不认 `all: someVar` 这种间接写法——它是理论形态，
#: 真出现时先由分母棘轮与归因断言承担（该文件不进 lcov ⇒ SF 数跌破下限）。
_ALL_FALSE_RE = re.compile(r"""\ball\s*:\s*false\b|(["'])all\1\s*:\s*false\b""")


def config_problems(config_text: str) -> list[str]:
    """vitest 配置侧：分母开关必须在 **coverage 段内**，且不得显式关掉 `all`。

    判据必须 scoped：`include:` 在本文件出现两次（`test.include` 与 `coverage.include`），
    全文件级的正则会被单测文件集满足——实测把 `coverage.include` 整行删掉后
    `config_problems` 仍返回 []，这道断言恒真（正是 #509 第 2 条要拆的「守卫自己哑火」，
    以反例形式复现于守门本身）。故先按花括号配对切出 coverage 块再判。

    `all`（vitest 5 默认开）是「0% 的新 lib 文件进 lcov」的机制本身——实测：新建一个无人
    import 的 src/lib 文件，SF 由 10 变 11、以 LF=2/LH=0 在列。注意它给的是**可见性**而非
    必然判红：进了 lcov 之后仍按加权判定（全局侧 280 计量行 + 2 行未覆盖 = 99.29% 仍绿；
    增量侧 `--fail-under` 比的是本 PR 全部计量行的聚合比例）。而写 `all: false` 关掉的是
    可见性本身——该文件同时从 D 与 C∩D 消失，棘轮以外的守卫连"它存在过"都看不见，所以钉住
    配置文本而不是钉结果。
    """
    section = _coverage_section(config_text)
    problems = []
    if not re.search(r"^\s*include:\s*\[", section, re.MULTILINE):
        problems.append("vitest.config.ts 的 coverage 段里没有 include——分母开关不见了")
    m = _ALL_FALSE_RE.search(section)
    if m:
        problems.append(
            f"显式关掉 `all`（写法 {m.group(0)!r}）会让未被测试 import 的分母文件完全不进 lcov，"
            "0% 新文件从「可见且按加权判定」退化为「无人计量」（#509 实测的反例形态）"
        )
    if 'projectRoot: ".."' not in section and "projectRoot: '..'" not in section:
        problems.append(
            "lcovonly 的 projectRoot 不再是 \"..\"——SF 会变成非仓库根相对路径（去掉它是 "
            "`src/lib/x.ts`，cwd 不在 frontend/ 时是含中间目录的长路径），diff-cover 按 git root "
            "解析 SF ⇒ 匹配不到任何改动行、门禁静默空转"
        )
    return problems


def ignore_problems(gitignore_lines: list[str], report_names: list[str]) -> list[str]:
    """门禁写在仓库根的产物必须被 ignore，否则每次本地跑都污染 `git status`。"""
    ignored = {line.strip() for line in gitignore_lines if line.strip() and not line.startswith("#")}
    return [f"仓库根产物 {name!r} 未登记在 .gitignore（本地产出会脏 git status）"
            for name in report_names if name not in ignored]


def v8_ignore_problems(hits: list[str]) -> list[str]:
    """全仓禁 `/* v8 ignore */`（#509 第 4 条的同类小项）。

    它是**逐行关掉分母**的后门：一处注释就能让一行代码永久不计覆盖率，而全局阈值与
    增量门禁都不会红。当前零使用，故钉零——出现第一处时必须走 issue 讨论，不能顺手加。
    """
    if not hits:
        return []
    return ["frontend/src 下出现 `v8 ignore` 指令（关掉分母的后门，必须先提 issue 讨论）：" + "、".join(sorted(hits))]


# ============================================================================
# 从 vitest.config.ts 反推分母文件集（判据的来源，不是第二份硬编码）
# ============================================================================

_BLOCK_RE = re.compile(r"(?P<key>include|exclude)\s*:\s*\[(?P<body>[^\]]*)\]", re.DOTALL)
_QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")


def _coverage_section(config_text: str) -> str:
    """按**花括号配对**切出 `coverage: { … }` 这一块。

    `include:` 在文件里出现两次（test.include 与 coverage.include），不 scoped 就会拿单测
    文件集当分母（实测踩过：整行删掉 coverage.include 后判据仍返回 []）。位置式 scoped
    （「从 `coverage: {` 切到文件尾」）只在这个配置里恰好正确——`test:` 一旦挪到 `coverage:`
    之后就把 test.include 也算进分母，于是同一处失效重演；按配对切则与书写顺序无关。
    同样先剥注释：注释里出现 `include: [` 会让反推的口径变成注释内容（同样是「拿别的文本
    当被断言的对象」）。
    """
    stripped = _code_only(config_text)
    m = re.search(r"^\s*coverage:\s*\{", stripped, re.MULTILINE)
    if not m:
        pytest.fail("vitest.config.ts 里找不到 `coverage: {`——覆盖率配置改名或挪走了？请同步本守门")
    start = stripped.index("{", m.start())
    depth = 0
    for i in range(start, len(stripped)):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                return stripped[start:i + 1]
    pytest.fail("vitest.config.ts 的 `coverage: {` 没有配对的右花括号——请同步本守门")


def _brace_expand(pattern: str) -> list[str]:
    """展开 `{a,b}`（vitest 的 tinyglobby 支持，glob/fnmatch 不支持）。只处理不嵌套的
    单层花括号——当前配置里就是 `{ts,tsx}` 一处，出现别的形态时报错而不是静默漏匹配。"""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    if "{" in m.group(1) or "}" in m.group(1):
        pytest.fail(f"分母 glob `{pattern}` 里的花括号形态不认识——请同步本守门的展开逻辑")
    return [
        expanded
        for alt in m.group(1).split(",")
        for expanded in _brace_expand(f"{pattern[:m.start()]}{alt}{pattern[m.end():]}")
    ]


def denominator_globs(config_text: str) -> tuple[list[str], list[str]]:
    section = _coverage_section(config_text)
    found = {m.group("key"): [p for raw in _QUOTED_RE.findall(m.group("body")) for p in _brace_expand(raw)]
             for m in _BLOCK_RE.finditer(section)}
    for key in ("include", "exclude"):
        if key not in found:
            pytest.fail(f"vitest.config.ts 的 coverage 段里没有 `{key}: [...]`——口径改形态了？请同步本守门")
    return found["include"], found["exclude"]


def denominator_files(config_text: str) -> set[str]:
    """按 vitest 的口径（include ∖ exclude，相对 frontend/）列出真实文件集。

    用 glob 而非手写匹配：`**` 的「零或多级目录」语义自己实现一遍就是第二个 bug 源。
    """
    includes, excludes = denominator_globs(config_text)
    front = REPO_ROOT / "frontend"

    def expand(patterns: list[str]) -> set[str]:
        hits: set[str] = set()
        for pattern in patterns:
            for path in glob.glob(str(front / pattern), recursive=True):
                p = PurePosixPath(Path(path).relative_to(front).as_posix())
                if p.suffix in {".ts", ".tsx"} and p.is_relative_to("src"):
                    hits.add(str(p))
        return hits

    return expand(includes) - expand(excludes)


# ============================================================================
# 真身
# ============================================================================


@pytest.fixture(scope="module")
def ci_text() -> str:
    return CI_YML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frontend_block(ci_text) -> str:
    return job_block(ci_text, job=FRONTEND_JOB, source=f"ci.yml:{FRONTEND_JOB}")


@pytest.fixture(scope="module")
def gate_lines(frontend_block) -> list[str]:
    return code_lines(step_run_block(frontend_block, step=GATE_STEP, source=f"ci.yml:{FRONTEND_JOB}"))


@pytest.fixture(scope="module")
def attribution_lines(frontend_block) -> list[str]:
    return code_lines(step_run_block(frontend_block, step=ATTRIBUTION_STEP, source=f"ci.yml:{FRONTEND_JOB}"))


@pytest.fixture(scope="module")
def ratchet_lines(frontend_block) -> list[str]:
    return code_lines(step_run_block(frontend_block, step=RATCHET_STEP, source=f"ci.yml:{FRONTEND_JOB}"))


@pytest.fixture(scope="module")
def summary_lines(frontend_block) -> list[str]:
    return code_lines(step_run_block(frontend_block, step=SUMMARY_STEP, source=f"ci.yml:{FRONTEND_JOB}"))


@pytest.fixture(scope="module")
def vitest_config() -> str:
    return VITEST_CONFIG.read_text(encoding="utf-8")


def _step_meta(frontend_block: str, step: str) -> str:
    """步骤的**元数据**行（`- name:` 到 `run:` 之前）——`if:` / `continue-on-error:` 住在这里。"""
    return step_body(frontend_block, step=step, source=f"ci.yml:{FRONTEND_JOB}")


@pytest.fixture(scope="module")
def attribution_meta(frontend_block) -> str:
    return _step_meta(frontend_block, ATTRIBUTION_STEP)


@pytest.fixture(scope="module")
def summary_meta(frontend_block) -> str:
    return _step_meta(frontend_block, SUMMARY_STEP)


@pytest.fixture(scope="module")
def ratchet_meta(frontend_block) -> str:
    return _step_meta(frontend_block, RATCHET_STEP)


def _synth_coverage_config(body: str) -> str:
    """合成一份与真身**同形**的最小配置：coverage 嵌在 test 里、test.include 在前。
    同形不是讲究，是因为 config_problems 的判据就依赖「按块切而不是按位置切」。"""
    return (
        "export default defineConfig({\n"
        "  test: {\n"
        '    include: ["src/**/*.test.{ts,tsx}"],\n'
        "    coverage: {\n"
        + body +
        "    },\n"
        "  },\n"
        "});\n"
    )


_COMPLIANT_BODY = (
    '      provider: "v8",\n'
    '      include: ["src/lib/**/*.{ts,tsx}"],\n'
    '      exclude: ["src/lib/api/**", "**/*.test.{ts,tsx}"],\n'
    '      reporter: ["text", ["lcovonly", { projectRoot: ".." }]],\n'
)


class TestGateEmitsStructuredReport:
    def test_compliant_template_is_compliant(self):
        assert gate_problems([
            "diff-cover frontend/coverage/lcov.info \\",
            "--fail-under=80 --format markdown:diff-cover.md,json:diff-cover.json",
        ]) == []

    def test_real_gate_step_is_compliant(self, gate_lines):
        problems = gate_problems(gate_lines)
        assert not problems, "ci.yml 的增量门禁步骤不再满足：" + "；".join(problems)

    def test_markdown_only_gate_is_caught(self):
        """反例：把 `,json:…` 删掉（评审点名的 #509 第 1 条形态）。"""
        problems = gate_problems([
            "diff-cover frontend/coverage/lcov.info \\",
            "--fail-under=80 --format markdown:diff-cover.md",
        ])
        assert any("json:diff-cover.json" in p for p in problems), problems


class TestAttributionAssertion:
    def test_compliant_template_is_compliant(self):
        assert attribution_problems([
            "node -e '…src_stats…' diff-cover.json | sort -u > reported",
            "comm -23 in_scope reported > missing",
        ]) == []

    def test_real_step_is_compliant(self, attribution_lines):
        problems = attribution_problems(attribution_lines)
        assert not problems, "归因断言步骤不再满足：" + "；".join(problems)

    def test_free_text_marker_is_caught(self):
        """反例：退回 grep markdown 文案（升级改词即永久失配）。"""
        problems = attribution_problems([
            "if grep -q 'No lines with coverage information' diff-cover.md; then echo warn; fi",
        ])
        assert any("diff-cover.json" in p for p in problems), problems
        assert any("markdown 文案" in p for p in problems), problems

    def test_hardcoded_denominator_is_caught(self):
        """反例：自己 `--numstat … frontend/src/lib` 重算分母（旧写法的第二处哑火）。"""
        problems = attribution_problems([
            "added=$(git diff --numstat base...HEAD -- frontend/src/lib | awk …)",
            "node -e '…src_stats…' diff-cover.json",
        ])
        assert any("又硬编码了一遍分母路径" in p for p in problems), problems


class TestDenominatorRatchet:
    def test_compliant_template_is_compliant(self):
        assert ratchet_problems([
            "LCOV_SF_MIN=10",
            "if [ \"${sf}\" -lt \"${LCOV_SF_MIN}\" ]; then",
            "echo \"::error::lcov 只描述了 ${sf} 个\"",
            "exit 1",
        ], denominator_size=10) == []

    def test_real_step_is_compliant(self, ratchet_lines, vitest_config):
        problems = ratchet_problems(ratchet_lines, len(denominator_files(vitest_config)))
        assert not problems, "分母棘轮步骤不再满足：" + "；".join(problems)

    def test_missing_ratchet_is_caught(self):
        """反例：只留前缀断言（#509 之前的形态）——收窄分母无人可见。"""
        problems = ratchet_problems([
            "grep -q '^SF:frontend/' frontend/coverage/lcov.info",
            "exit 1",
        ], denominator_size=10)
        assert any("LCOV_SF_MIN" in p for p in problems), problems

    def test_lowered_floor_is_caught(self):
        problems = ratchet_problems([
            "LCOV_SF_MIN=4", "::error::x", "exit 1",
        ], denominator_size=10)
        assert any("低于高水位" in p for p in problems), problems

    def test_stale_floor_after_narrowing_include_is_caught(self):
        """反例：改了 coverage.include 而没同步下限（下限高于配置能给的数量）。"""
        problems = ratchet_problems([
            "LCOV_SF_MIN=10", "::error::x", "exit 1",
        ], denominator_size=3)
        assert any("高于 coverage.include∖exclude" in p for p in problems), problems

    def test_silent_ratchet_is_caught(self):
        """判红分支没有 exit 1 ⇒ 只打印不拦。"""
        problems = ratchet_problems([
            "LCOV_SF_MIN=10", "echo \"lcov 只描述了 ${sf} 个\"",
        ], denominator_size=10)
        assert any("独立的 `exit 1`" in p for p in problems), problems

    def test_downgraded_ratchet_message_is_caught(self, ratchet_lines):
        """反例（真身文本变异）：把棘轮那条 `::error::` 降级成 `::warning::`——
        退出路径还在、job 照样红，但**注解从「错误」变成「提示」**，正是评审点名的
        「只判红不指认」的镜像形态：三处判红里有一处被偷偷卸掉。"""
        mutated = [l.replace("::error::lcov 只描述了", "::warning::lcov 只描述了") for l in ratchet_lines]
        assert mutated != ratchet_lines, "真身文案变了，本反例已失效——请同步判据"
        problems = ratchet_problems(mutated, 10)
        assert any("少于 `exit 1`" in p for p in problems), problems

    def test_denominator_derivation_matches_documented_size(self, vitest_config):
        """配置反推的分母集合必须真是 10 个文件——否则上面几条都在跟影子较劲。"""
        files = denominator_files(vitest_config)
        assert len(files) == SF_MIN_HIGH_WATER, (
            f"coverage.include∖exclude 现在命中 {len(files)} 个文件，高水位钉的是 {SF_MIN_HIGH_WATER}。"
            "扩张分母须同批改三处：本测试的 SF_MIN_HIGH_WATER、ci.yml 的 LCOV_SF_MIN、"
            f"以及 frontend/AGENTS.md §3 的口径说明。实际文件集：{sorted(files)}"
        )
        assert not any(f.startswith("src/lib/api/") for f in files), sorted(files)
        assert not any(".test." in f for f in files), sorted(files)


class TestSummaryExit:
    def test_compliant_template_is_compliant(self):
        assert summary_problems([
            "if [ -s diff-cover.md ]; then", "cat diff-cover.md >> \"$GITHUB_STEP_SUMMARY\"",
            "else", "echo \"⚠️ 未生成 diff-cover.md，无法汇总\" >> \"$GITHUB_STEP_SUMMARY\"", "fi",
        ]) == []

    def test_real_step_is_compliant(self, summary_lines):
        problems = summary_problems(summary_lines)
        assert not problems, "摘要步骤不再满足：" + "；".join(problems)

    def test_silent_cat_is_caught(self):
        """反例：#509 之前的原样（文件不存在时静默 0 行）。"""
        problems = summary_problems([
            "cat diff-cover.md >> \"$GITHUB_STEP_SUMMARY\" 2>/dev/null || true",
        ])
        assert any("未生成" in p for p in problems), problems
        assert any("静默形态" in p for p in problems), problems


class TestConfigSide:
    def test_compliant_template_is_compliant(self):
        assert config_problems(_synth_coverage_config(_COMPLIANT_BODY)) == []

    def test_real_config_is_compliant(self, vitest_config):
        problems = config_problems(vitest_config)
        assert not problems, "vitest.config.ts 不再满足：" + "；".join(problems)

    def test_coverage_include_missing_is_caught(self):
        """反例：coverage 段里没有 include（分母开关整体没了）。"""
        problems = config_problems(_synth_coverage_config(
            '      provider: "v8",\n'
            '      exclude: ["src/lib/api/**"],\n'
            '      reporter: ["lcovonly", { projectRoot: ".." }],\n'
        ))
        assert any("coverage 段里没有 include" in p for p in problems), problems

    def test_real_config_without_coverage_include_is_caught(self, vitest_config):
        """反例（真身文本变异）：把 `coverage.include` 那一行删掉，只留 `test.include`。
        旧判据是全文件级正则，会被 test.include 满足而恒真——这条就是它的存在理由。"""
        mutated = vitest_config.replace('      include: ["src/lib/**/*.{ts,tsx}"],\n', "")
        assert mutated != vitest_config, "真身文案变了，本反例已失效——请同步判据"
        problems = config_problems(mutated)
        assert any("coverage 段里没有 include" in p for p in problems), problems

    def test_include_after_the_coverage_block_is_not_mistaken(self):
        """反例：`test:` 写在 `coverage:` **之后**且 coverage 段没有 include——
        按位置 scoped（切到文件尾）会在这里重新变成假绿灯，按花括号配对切才不会。"""
        problems = config_problems(
            "export default defineConfig({\n"
            "  test: {\n"
            "    coverage: { provider: \"v8\", exclude: [], "
            'reporter: ["lcovonly", { projectRoot: ".." }] },\n'
            '    include: ["src/**/*.test.{ts,tsx}"],\n'
            "  },\n"
            "});\n"
        )
        assert any("coverage 段里没有 include" in p for p in problems), problems
        assert not any("projectRoot" in p for p in problems), problems

    def test_explicit_all_false_is_caught(self):
        """反例：把 vitest 5 默认打开的 `all` 显式关掉 ⇒ 0% 新文件彻底隐形。"""
        for spelling in ('all: false,', '"all": false,', "'all': false,", "all : false ,"):
            problems = config_problems(_synth_coverage_config(
                '      provider: "v8",\n'
                '      include: ["src/lib/**/*.{ts,tsx}"],\n'
                f"      {spelling}\n"
                '      reporter: ["text", ["lcovonly", { projectRoot: ".." }]],\n'
            ))
            assert any("显式关掉 `all`" in p for p in problems), (spelling, problems)

    def test_all_true_is_not_caught(self):
        """正对照：显式写 `all: true` 是本文件反对的写法的反面，不该误伤。"""
        assert config_problems(_synth_coverage_config(_COMPLIANT_BODY + '      all: true,\n')) == []

    def test_install_like_key_is_not_mistaken(self):
        """正对照：`install: false` 这种含 `all` 的键名不能被当成禁句（否则是假阳性）。"""
        assert config_problems(_synth_coverage_config(_COMPLIANT_BODY + '      install: false,\n')) == []

    def test_missing_project_root_is_caught(self):
        """反例：去掉 lcovonly 的 projectRoot ⇒ SF 变成 `src/lib/x.ts`，diff-cover 匹配不到。"""
        problems = config_problems(_synth_coverage_config(
            '      provider: "v8",\n'
            '      include: ["src/lib/**/*.{ts,tsx}"],\n'
            '      exclude: ["src/lib/api/**"],\n'
            '      reporter: ["text", "lcovonly"],\n'
        ))
        assert any("projectRoot" in p for p in problems), problems

    def test_glob_expansion_covers_both_suffixes(self, vitest_config):
        includes, _ = denominator_globs(vitest_config)
        assert "src/lib/**/*.ts" in includes and "src/lib/**/*.tsx" in includes, includes


class TestStepGrade:
    """诊断步骤只能发 warning，判红步骤不能被卸成 warning——两头的漂移都要拦。"""

    def test_real_diagnostic_steps_are_compliant(self, attribution_meta, summary_meta):
        for meta in (attribution_meta, summary_meta):
            problems = diagnostic_step_problems(meta)
            assert not problems, "诊断步骤的元数据不再满足：" + "；".join(problems)

    def test_ratchet_step_is_blocking(self, ratchet_meta):
        assert blocking_step_problems(ratchet_meta) == []

    def test_diagnostic_step_without_continue_on_error_is_caught(self, attribution_meta):
        """反例（真身文本变异）：删掉那行 `continue-on-error: true`。"""
        mutated = attribution_meta.replace("        continue-on-error: true", "")
        assert mutated != attribution_meta, "真身文案变了，本反例已失效——请同步判据"
        problems = diagnostic_step_problems(mutated)
        assert any("continue-on-error" in p for p in problems), problems

    def test_diagnostic_step_without_always_is_caught(self, summary_meta):
        """反例：`if:` 去掉 always() ⇒ 上游先红时这一步不跑，出口文案形同虚设。"""
        mutated = summary_meta.replace("if: always() &&", "if:")
        assert mutated != summary_meta, "真身文案变了，本反例已失效——请同步判据"
        assert any("always()" in p for p in diagnostic_step_problems(mutated)), mutated

    def test_guard_unloaded_by_continue_on_error_is_caught(self, ratchet_meta):
        """反例：给唯一的分母守卫加上 continue-on-error —— 那是把守卫卸成打印。"""
        mutated = ratchet_meta.replace(
            "        if: github.event_name == 'pull_request'",
            "        if: github.event_name == 'pull_request'\n        continue-on-error: true",
        )
        assert mutated != ratchet_meta, "真身文案变了，本反例已失效——请同步判据"
        assert blocking_step_problems(mutated), mutated


class TestHousekeeping:
    def test_root_artifacts_are_ignored(self, gate_lines):
        names = re.findall(r"(?:markdown|json):([\w./-]+)", " ".join(gate_lines))
        assert set(names) == {"diff-cover.md", "diff-cover.json"}, names
        problems = ignore_problems(GITIGNORE.read_text(encoding="utf-8").splitlines(), names)
        assert not problems, "；".join(problems)

    def test_unignored_artifact_is_caught(self):
        assert ignore_problems(["coverage/", "diff-cover.md"], ["diff-cover.json"]) == [
            "仓库根产物 'diff-cover.json' 未登记在 .gitignore（本地产出会脏 git status）"
        ]

    def test_no_v8_ignore_directives_in_frontend_src(self):
        sources = [p for ext in ("*.ts", "*.tsx", "*.js", "*.jsx")
                   for p in (REPO_ROOT / "frontend" / "src").rglob(ext)]
        hits = [str(p.relative_to(REPO_ROOT)) for p in sources
                if "v8 ignore" in p.read_text(encoding="utf-8")]
        assert v8_ignore_problems(hits) == []

    def test_v8_ignore_directive_is_caught(self):
        assert any("后门" in p for p in v8_ignore_problems(["src/lib/utils.ts"]))
