# ============================================================================
# PR 审查留痕硬闸门（issue #560）
# ============================================================================
# 来源：2026-09-19 审查复盘的下沉候选 S1 + S6。窗口内 39 个非依赖合入 PR 里 51%
# 在正文读不出审查结论；`docs/reference/code-review.md` §5 的「≤15 文件」规模护栏
# 被 4 个 PR 越过、其中 2 个未写理由。两处的共同形态是：读 PR 正文/元数据就能判，
# 而当时零守门——L2 是本项目唯一的语义防线，它的产出物却不可审计。
#
# 判据刻意只有三条，且必须在 PR 模板里可见（否则作者只能靠试错学习）：
#   A 正文有一行「结论：…」，冒号后非空白。照实写「未做独立 L2 语义审查」判绿——
#     门禁拦的是「无信息」，不是「没审查」（#559 是合规样板）。
#   B 不认小节位置：全文任一结论行即合规。反证是 #478–#481：它们的结论写在
#     `### 第 2 轮（子 Agent 独立评审）` 下、内容完整并据此开了 #494，任何按
#     `## 审查记录` 小节取数的门禁都会把这四次真实审查误判为「未审」。按位置判
#     还会奖励「填对格子」而不是「留下判断」，而复盘真正需要的是可检索的结论。
#   C 改动文件 > 15 且正文没有「规模 / 理由 / 不拆分 / 豁免」类表述 → 红。
#
# **三条判据都只看「作者自己写下的可见文本」**（`author_lines`）：先剥 HTML 注释，再逐行
# 剔掉与 PR 模板**渲染后可见行**逐字相同的行。不这么做，门禁就被它自己的模板绕过——
# 模板「审查记录」节的填写说明（HTML 注释）里含一整句「结论：…」，「自审清单」的复选框
# 文案里含「理由」「豁免」：GitHub UI 建 PR 默认预填模板，作者一个字不写也判绿
# （#615 审查 Blocker 2 实测：模板原文当正文喂判据，3 文件与 23 文件场景均返回空清单）。
# 模板读不到即 fail-closed（判不出样板就等于判据失效，同 base SHA 的处置）。
#
# 豁免：标题 `chore(deps` 或作者 `dependabot[bot]`（窗口内 13 笔，不豁免即制造噪音）。
#
# 检查上限（诚实声明，不要假装没有）：**文字留痕可伪造**——一行假结论就能让本门变绿。
# 这是刻意的选择：改用 AST/正则判断「本 PR 的测试改动里有没有真实反例」同样能被一行
# 假反例骗过，而骗过之后门禁给出的是虚假的绿信心（与 #382「以 grep 零残留验收」同型，
# 比没有更坏）。本门只保证「结论被写下来且可检索」，不保证它为真。剔样板行同理只堵
# 「原样保留模板」这条无意绕过：**改模板本身**的 PR 能把自己的话变成样板行，那种 PR
# 需要人工看一眼模板 diff（属上面同一条可伪造上限，不另设机制）。
#
# 运行方式：ci.yml `changes` job 的 PR-only 步骤（与 check_context_docs.py 同批，
# 因此进 `CI OK`、成为合入硬闸门；该 job 全事件运行，故 PR 侧一定产出 check，
# 不会被路径裁剪成 skipped——`CI OK` 把 skipped 视为通过，那等于门不在）。
# 输入全部走 env（正文经 env 传入而不是内插进 shell：PR 正文是不可信输入）。
# 退出码：0 合规/豁免，1 判红，2 门禁自身失效（fail-closed，不静默放行）。
# ============================================================================

import os
import re
import subprocess
import sys
from pathlib import Path

#: 规模护栏的建议上限（code-review.md §5）。
FILE_COUNT_LIMIT = 15
#: 超规模时正文需出现的表述（出现任一即视为写了理由）。
SIZE_KEYWORDS = ("规模", "理由", "不拆分", "豁免")
EXEMPT_AUTHOR = "dependabot[bot]"
EXEMPT_TITLE_RE = re.compile(r"^chore\(deps")
#: 结论行的判据：三种登记写法（`- 结论：` / `审查结论：` / `**结论：`）、`### 结论：`
#: 这类等价形态，以及**带限定词**的写法（模板那条就是「结论与状态（…）：」）一并认——
#: 判据 B 取「不认位置、只认有内容」，形态上也不该只认三种前缀：否则照模板原样填写
#: （把结论写在那条限定词后面）会被判红，门禁等于照着自己的模板制造假红。
#: 冒号后必须非空白：模板里的空占位 `- 结论与状态（…）：` 不算留痕。
CONCLUSION_RE = re.compile(r"结论[^:：]*[:：]\s*(?P<tail>.*)$")
SHA_RE = re.compile(r"[0-9a-f]{40}")
#: HTML 注释：渲染后不可见，但门禁读的是正文原文（模板的填写说明全在注释里）。
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
#: PR 模板：样板行的唯一来源（见文件头「三条判据都只看作者自己写下的可见文本」）。
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / ".github" / "PULL_REQUEST_TEMPLATE.md"


class TraceError(Exception):
    pass


def visible_body(body):
    """渲染后**可见**的正文：剥掉 HTML 注释。

    未闭合的 `<!--` 之后一并丢弃——CommonMark 的注释型 HTML 块延伸到文档结尾，渲染器
    同样看不见那段文本；留着它就等于又开一条「把结论藏进注释」的绕过路径。
    """
    return HTML_COMMENT_RE.sub("", body or "").split("<!--", 1)[0]


def load_boilerplate():
    """PR 模板的可见行集合（去首尾空白后逐字比较）；读不到即 fail-closed。"""
    try:
        text = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise TraceError(f"读不到 PR 模板 {TEMPLATE_PATH}（{exc}）——判不出哪些行是模板样板") from exc
    lines = {line.strip() for line in visible_body(text).splitlines() if line.strip()}
    if not lines:
        raise TraceError(f"PR 模板 {TEMPLATE_PATH} 剥注释后没有可见行——模板被改空了？请同步本门禁")
    return lines


def author_lines(body, boilerplate=frozenset()):
    """正文里**作者自己写下**的可见行：剥注释，再剔掉与模板逐字相同的样板行。"""
    return [
        line
        for line in visible_body(body).splitlines()
        if line.strip() and line.strip() not in boilerplate
    ]


def exempt_reason(title, author):
    """返回豁免理由（None = 不豁免）。"""
    if EXEMPT_TITLE_RE.match((title or "").strip()):
        return f"标题 `{(title or '').strip()}` 命中依赖升级豁免"
    if (author or "").strip() == EXEMPT_AUTHOR:
        return f"作者 {EXEMPT_AUTHOR} 豁免"
    return None


def conclusion_problem(body, boilerplate=frozenset()):
    for line in author_lines(body, boilerplate):
        match = CONCLUSION_RE.search(line)
        if match and match.group("tail").strip():
            return None
    return (
        "正文读不出审查结论：需要一行「结论：…」写在**正文可见处**（全文任意位置即可，"
        "冒号后不能空白；HTML 注释与模板原样的占位行都不算）；"
        "未做独立 L2 语义审查也请照实写（#559 的写法判绿）——门禁拦的是「无信息」，不是「没审查」（#560 判据 A/B）"
    )


def size_problem(body, file_count, boilerplate=frozenset()):
    if file_count <= FILE_COUNT_LIMIT:
        return None
    written = "\n".join(author_lines(body, boilerplate))
    if any(keyword in written for keyword in SIZE_KEYWORDS):
        return None
    return (
        f"改动 {file_count} 个文件，超过 {FILE_COUNT_LIMIT} 个的建议上限，正文也没有"
        f"「{'/'.join(SIZE_KEYWORDS)}」类表述——请在正文写明不拆分的理由"
        f"（模板自带的复选框文案不算：#560 判据 C，见 code-review.md §5）"
    )


def review_trace_problems(body, file_count, *, title="", author="", boilerplate=None):
    """审查留痕的问题清单（空 = 合规/豁免）。"""
    if exempt_reason(title, author):
        return []
    if boilerplate is None:
        boilerplate = load_boilerplate()
    return [
        problem
        for problem in (
            conclusion_problem(body, boilerplate),
            size_problem(body, file_count, boilerplate),
        )
        if problem
    ]


def changed_file_count(base_sha):
    """改动的**文件数**（不是行数）：规模护栏按文件计。git 失败即抛错，不回落成 0。"""
    if not SHA_RE.fullmatch(base_sha or ""):
        raise TraceError(
            f"PR_BASE_SHA 不是 40 位 commit SHA（{base_sha!r}）——算不出改动文件数，"
            "按 fail-closed 处理（回落成 0 会让规模护栏永久静默）"
        )
    proc = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z",
         f"{base_sha}...HEAD"],
        capture_output=True,
    )
    if proc.returncode:
        detail = proc.stderr.decode(errors="replace").strip()[:200]
        raise TraceError(f"git diff 失败（exit {proc.returncode}）：{detail}")
    return len([path for path in proc.stdout.split(b"\0") if path])


def main():
    title = os.environ.get("PR_TITLE", "")
    author = os.environ.get("PR_AUTHOR", "")
    body = os.environ.get("PR_BODY") or ""
    try:
        file_count = changed_file_count(os.environ.get("PR_BASE_SHA", ""))
        boilerplate = load_boilerplate()
    except (TraceError, OSError) as exc:
        print(f"::error::审查留痕门禁自身失效（不静默放行）：{exc}")
        return 2

    reason = exempt_reason(title, author)
    if reason:
        print(f"::notice::跳过审查留痕门禁：{reason}")
        return 0

    # 豁免在 review_trace_problems 里也判一次：判据与接线共用同一份口径，测试才测得到
    # 「豁免 PR 走完整条判据」而不是只测到 main 的早退。
    problems = review_trace_problems(body, file_count, title=title, author=author,
                                     boilerplate=boilerplate)
    if not problems:
        print(f"::notice::审查留痕合规：正文有结论行；改动 {file_count} 个文件"
              f"（上限 {FILE_COUNT_LIMIT}）")
        return 0
    for problem in problems:
        print(f"::error::{problem}")
    print("填法见 .github/PULL_REQUEST_TEMPLATE.md 的「## 审查记录」节。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
