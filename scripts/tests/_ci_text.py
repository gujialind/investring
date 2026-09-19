# ============================================================================
# workflow 文本的结构切分（CI 守门共用）
# ============================================================================
# 为什么单独成模块：这几道守门（test_ci_mysql_account.py / test_ci_e2e_compare.py /
# test_ci_path_mapping.py）判的都是「workflow 文本里某个块长什么样、里面有什么」，
# 而**判据抄两份就必然漂移**是仓库明文原则（见 test_ci_mysql_account.py 头部 #548 的
# 决策）。切块逻辑一旦各写一份，同一处 YAML 重构会让一边响亮失败、另一边静默抽错块——
# 后者比失败更坏，因为它拿着别的块当被断言的对象。
#
# 共同立场：
# - 不解析 YAML 语义（scripts/tests 是 stdlib-only，见 ci.yml 的 CLI Contract Check
#   注释），只按缩进切原文；
# - 结构不认识就 pytest.fail 指路，绝不退而抽邻居块，也不静默返回空串——
#   「守门看不见位置」比「守门判失败」更坏（那是假绿灯）；
# - 块标量接受 `|`/`|-`/`|+`/`>`/`>-`/`>+` 全部写法：用哪一种只是书写细节，不该改变
#   抽到的内容（#492 就是因为只认 `run: |`，改成 `|-` 会跳过目标步骤去抽邻居）。
# ============================================================================

import re
import textwrap

import pytest

#: YAML 块标量的全部写法（`run: |`、`run: >-` 等）。
_BLOCK_SCALAR_RE = re.compile(r"^(\s*)run:\s*[|>][-+]?\s*$")
#: 步骤头：`- id: x` 或 `- name: x`（两种键都能当锚点，因为真身里 id 与 name 各有用处）。
_STEP_HEAD_RE = re.compile(r"^(\s*)-\s+(?:id|name):\s*(?P<key>\S.*?)\s*$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_skippable(line: str) -> bool:
    """空行与整行注释：不改变归属，也不参与结构判定。"""
    return not line.strip() or line.lstrip().startswith("#")


def job_block(text: str, *, job: str, source: str) -> str:
    """切出 `job` 的原文（到下一个同级 job 为止；**没有下一个就到文件尾**），并去掉注释行。

    `source` 只服务于失败消息：真身调用传 `<文件名>:<job>`，反例调用传合成标签——
    否则「job 改名」这条消息会指着错的文件。

    去注释不是美化：说明性注释里会出现 `GRANT OPTION`、`*.*` 这些**正是要禁止**的字样
    （e2e-stack.yml 的「刻意不给什么」就是），留着它们，禁止项检查会被自己的文档喂出
    假阳性。断言只关心真实 SQL 与配置文本。
    """
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l == f"  {job}:"), None)
    if start is None:
        pytest.fail(f"{source} 中找不到 `  {job}:`——job 改名或挪走了？请同步更新本守门")
    end = next(
        (j for j in range(start + 1, len(lines)) if re.match(r"^  [\w-]+:\s*$", lines[j])),
        len(lines),
    )
    return "\n".join(l for l in lines[start:end] if not l.lstrip().startswith("#"))


def step_body(text: str, *, step: str, source: str) -> str:
    """切出步骤 `step`（按 `- id:` 或 `- name:` 匹配）从头部到下一个步骤头之间的原文。

    搜索范围限制在本步骤内：缩进不深于步骤头的非空行即视为下一步骤/job，就地收口。
    末尾可能带上属于下一步骤的整行注释——调用方要么走 code_lines（已剥注释），
    要么判据本身与注释无关。
    """
    lines = text.splitlines()
    start = indent = None
    for i, line in enumerate(lines):
        m = _STEP_HEAD_RE.match(line)
        if m and m.group("key").strip("\"'") == step:
            start, indent = i, len(m.group(1))
            break
    if start is None:
        pytest.fail(
            f"{source} 中找不到 `- {step}` 步骤——workflow 结构变了？请同步更新本守门"
        )
    end = next(
        (j for j in range(start + 1, len(lines))
         if not _is_skippable(lines[j]) and _indent(lines[j]) <= indent),
        len(lines),
    )
    return "\n".join(lines[start:end])


def step_run_block(text: str, *, step: str, source: str) -> str:
    """切出步骤 `step` **自己的** run 块（dedent 后返回）；没有 run 块即响亮失败。"""
    lines = step_body(text, step=step, source=source).splitlines()
    for i, line in enumerate(lines[1:], 1):
        if _is_skippable(line):
            continue
        m = _BLOCK_SCALAR_RE.match(line)
        if not m:
            continue
        run_indent = len(m.group(1))
        body = []
        for inner in lines[i + 1:]:
            if inner.strip() and _indent(inner) <= run_indent:
                break
            body.append(inner)
        return textwrap.dedent("\n".join(body))
    pytest.fail(
        f"{source} 的步骤 `{step}` 内没找到它的 `run: <块标量>` 体"
        "（run 被改成内联、这一步被拆走、还是本来就不跑脚本？）——请同步更新本守门"
    )


def code_lines(text: str) -> list[str]:
    """按行归一化：去缩进、去整行注释、去 shell 续行反斜杠，便于整行比对与顺序判定。

    守门钉的是「语义关键词与先后」，不是整句文案与缩进：把行首缩进和续行 `\\` 一并抹掉，
    调整缩进或换行位置就不需要动任何期望值（否则每次排版都会打红守门，很快就被加豁免）。
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(stripped.rstrip("\\").strip())
    return out
