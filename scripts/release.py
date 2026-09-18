#!/usr/bin/env python3
"""
InvestRing 发布脚本（issue #375）

以根 VERSION 文件为项目版本单一事实来源。main 的 ruleset 要求一切改动经 PR 且
CI OK（直接推送会被拒绝），而 v 标签必须落在 main tip（merge-commit）上
deploy.yml 才能给镜像追加 :vX.Y.Z 语义标签，故发布分两阶段：

  阶段一（本脚本默认命令）：同步版本文件 → 用钉版 .venv-openapi 隔离重导出
    openapi.json → 契约验证 → 从 conventional commits 生成 CHANGELOG →
    在 release/vX.Y.Z 分支单 commit → 推送分支并创建发布 PR。
  阶段二（release.py tag vX.Y.Z）：发布 PR 合并后，校验 origin/main 的 VERSION
    与 chore(release) 提交，在 origin/main tip 打附注标签并推送。
    应在合并后立即执行——deploy 在 CI（约 8-10 分钟）之后才构建，窗口充裕；
    若错过，重跑该 commit 的 CI run 会重新触发 deploy 补上语义标签。

版本号规范见 docs/reference/versioning.md（Semver；0.x 初始阶段；无 pre-release）。

- 纯 stdlib 实现；阶段一必须在 main 分支、工作区干净、与 origin/main 同步时运行。
- openapi 重导出走隔离子进程（与 check_openapi.py 共用 openapi_runtime，不继承业务配置）；
  依赖钉版环境 .venv-openapi/（缺失时给出重建命令后中止）。
- 版本文件读写保留原行尾（部分文件为 CRLF，文本模式规范化会翻转全文件行尾）。

用法（任意 cwd，用 __file__ 定位仓库根）:
    python3 scripts/release.py --suggest             # 按上个 v tag 以来的提交建议 bump 类型
    python3 scripts/release.py patch --dry-run       # 预览全部改动（含 CHANGELOG 草稿），不落盘
    python3 scripts/release.py patch                 # 阶段一（推送前交互确认）
    python3 scripts/release.py patch --yes           # 阶段一（跳过确认，非交互环境必须）
    python3 scripts/release.py --initial v0.1.0 --fixes 375
                                                     # 首个发布：无 v tag 时用指定版本作基线
    python3 scripts/release.py tag v0.1.0            # 阶段二：发布 PR 合并后打 tag 并推送
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO_ROOT / "VERSION"
CHANGELOG_FILE = REPO_ROOT / "CHANGELOG.md"
VENV_PY = REPO_ROOT / ".venv-openapi" / "bin" / "python"
BACKEND_DIR = REPO_ROOT / "backend"

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
CONVENTIONAL_RE = re.compile(r"^([a-zA-Z]+)(\(([^)]+)\))?(!)?:\s*(.+)$")

# 版本同步目标：(文件, 说明)。openapi.json 不经文本替换，由重导出生成。
CHANGELOG_HEADER = (
    "# InvestRing Changelog\n\n"
    "版本号规范与发布流程见 `docs/reference/versioning.md`；"
    "条目由 `scripts/release.py` 从 conventional commits 生成，标题含发布日期。\n\n"
)
GROUP_ORDER = [("feat", "Features"), ("fix", "Bug Fixes")]
OTHER_GROUP = "Other"
OTHER_TYPES = {"chore", "docs", "refactor", "test", "perf", "ci", "build", "style", "revert"}


def fail(msg: str) -> None:
    print(f"[error] {msg}", file=sys.stderr)
    sys.exit(1)


def read_text(path: Path) -> str:
    """读文本并保留原行尾（newline=""）。部分版本文件是 CRLF，
    文本模式默认规范化为 LF，写回会翻转全文件行尾、产生整文件 diff。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path: Path, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def run(cmd, cwd=REPO_ROOT, env=None, check=True):
    """执行命令并返回 stdout；check=True 时非零退出即中止。"""
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        fail(f"命令失败（exit {proc.returncode}）: {' '.join(str(c) for c in cmd)}")
    return proc.stdout


def git(*args, check=True) -> str:
    return run(["git", *args], check=check).strip()


def parse_semver(text: str):
    m = SEMVER_RE.match(text.strip())
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def bump_version(current: str, kind: str) -> str:
    major, minor, patch = parse_semver(current)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def venv_python() -> Path:
    if not VENV_PY.is_file():
        fail(
            f"钉版契约环境缺失: {VENV_PY}\n"
            "重建（常规操作）:\n"
            "  python3 -m venv .venv-openapi && .venv-openapi/bin/pip install -r backend/requirements.txt"
        )
    return VENV_PY


def latest_v_tag() -> str | None:
    tags = [t for t in git("tag", "-l", "v*").splitlines() if parse_semver(t)]
    if not tags:
        return None
    return max(tags, key=lambda t: parse_semver(t))


def commits_since(ref: str | None) -> list[tuple[str, str]]:
    """返回 (short_sha, subject) 列表；排除 merge-commit 与历史 release 提交。"""
    rng = f"{ref}..HEAD" if ref else "HEAD"
    out = git("log", "--pretty=%h%x09%s", rng)
    result = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\t")
        if subject.startswith("Merge pull request") or subject.startswith("chore(release):"):
            continue
        result.append((sha, subject))
    return result


def parse_conventional(subject: str):
    m = CONVENTIONAL_RE.match(subject)
    if not m:
        return "other", None, False, subject
    ctype = m.group(1).lower()
    return ctype, m.group(3), bool(m.group(4)), m.group(5)


def suggest_bump(commits: list[tuple[str, str]]) -> tuple[str, str]:
    has_feat = False
    for _, subject in commits:
        ctype, _, breaking, _ = parse_conventional(subject)
        if breaking or "BREAKING CHANGE" in subject:
            return "major", f"含不兼容变更标记: {subject}"
        if ctype == "feat":
            has_feat = True
    if has_feat:
        return "minor", "含 feat 提交"
    return "patch", "仅 fix/chore 等向后兼容提交"


def render_file_edits(target: str) -> list[tuple[Path, str, str]]:
    """计算各版本文件替换后的内容（不写盘），供 dry-run 预览与执行共用。"""
    old_version = read_text(VERSION_FILE)
    eol = "\r\n" if old_version.endswith("\r\n") else "\n"
    edits = [(VERSION_FILE, old_version, f"{target}{eol}")]
    for path, pattern, repl, count in [
        (BACKEND_DIR / "pyproject.toml", r'^version = "[^"]*"', f'version = "{target}"', 1),
        (REPO_ROOT / "ir-cli" / "pyproject.toml", r'^version = "[^"]*"', f'version = "{target}"', 1),
        (REPO_ROOT / "frontend" / "package.json", r'^  "version": "[^"]*"', f'  "version": "{target}"', 1),
        # package-lock 的前两处 "version" 恰为顶层与 packages[""]，不同步会击穿 npm ci
        (REPO_ROOT / "frontend" / "package-lock.json", r'"version": "[^"]*"', f'"version": "{target}"', 2),
    ]:
        old = read_text(path)
        new, n = re.subn(pattern, repl, old, count=count, flags=re.MULTILINE)
        if n != count:
            fail(f"{path} 中版本行匹配 {n} 处（期望 {count}），文件结构可能已变化，请人工检查")
        edits.append((path, old, new))
    return edits


def regen_openapi(py: Path) -> None:
    run([py, BACKEND_DIR / "export_openapi.py", "--offline"], cwd=BACKEND_DIR)


def verify_contracts(py: Path) -> None:
    run([py, "check_openapi.py"], cwd=BACKEND_DIR)
    run([py, REPO_ROOT / "ir-cli" / "scripts" / "gen_response_fields.py", "--check"])


def build_changelog_entry(target: str, commits: list[tuple[str, str]], baseline: bool) -> str:
    lines = [f"## v{target} - {date.today().isoformat()}", ""]
    if baseline:
        lines += [
            "初始版本化发布（统一版本机制基线，issue #375）。",
            "",
            "此前历史未逐条回溯，见 `git log` 与 `deploy/*` 标签。",
            "",
        ]
        return "\n".join(lines)
    groups: dict[str, list[str]] = {}
    for sha, subject in commits:
        ctype, scope, _, desc = parse_conventional(subject)
        if ctype in OTHER_TYPES or ctype == "other":
            groups.setdefault(OTHER_GROUP, []).append(f"- {subject} ({sha})")
        else:
            title = dict(GROUP_ORDER).get(ctype, ctype.capitalize())
            prefix = f"**{scope}**: " if scope else ""
            groups.setdefault(title, []).append(f"- {prefix}{desc} ({sha})")
    if not groups:
        lines += ["（无提交记录）", ""]
        return "\n".join(lines)
    for _, title in GROUP_ORDER:
        if title in groups:
            lines += [f"### {title}", "", *groups.pop(title), ""]
    if OTHER_GROUP in groups:
        lines += [f"### {OTHER_GROUP}", "", *groups[OTHER_GROUP], ""]
    return "\n".join(lines)


def prepend_changelog(entry: str) -> str:
    """返回写入后的完整内容（不写盘）。"""
    if not CHANGELOG_FILE.exists():
        return CHANGELOG_HEADER + entry
    content = read_text(CHANGELOG_FILE)
    idx = content.find("\n## ")
    if idx == -1:
        return content.rstrip("\n") + "\n\n" + entry
    return content[: idx + 1] + "\n" + entry + content[idx + 1 :].lstrip("\n")


def preflight() -> None:
    if git("status", "--porcelain"):
        fail("工作区不干净，请先提交或 stash 后重试")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        fail(f"当前分支为 {branch}，发布必须在 main 上进行")
    git("fetch", "origin")
    if git("rev-parse", "main") != git("rev-parse", "origin/main"):
        fail("本地 main 与 origin/main 不同步，请先 pull/rebase")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "tag":
        return tag_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="InvestRing 发布脚本（docs/reference/versioning.md）")
    parser.add_argument("bump", nargs="?", choices=["major", "minor", "patch"], help="bump 类型；省略则用 --suggest 结果")
    parser.add_argument("--suggest", action="store_true", help="仅打印建议的 bump 类型，不写文件")
    parser.add_argument("--initial", metavar="vX.Y.Z", help="首个发布：无任何 v tag 时用指定版本作基线")
    parser.add_argument("--fixes", type=int, metavar="N", help="发布 PR 正文加 fixes #N（合并后自动关闭 issue）")
    parser.add_argument("--dry-run", action="store_true", help="预览全部改动，不落盘")
    parser.add_argument("--yes", action="store_true", help="跳过推送前交互确认")
    args = parser.parse_args()

    last = latest_v_tag()

    if args.suggest:
        commits = commits_since(last)
        kind, reason = suggest_bump(commits)
        cur = VERSION_FILE.read_text(encoding="utf-8").strip()
        print(f"上个 v tag: {last or '（无）'}；此后提交 {len(commits)} 条")
        print(f"建议 bump: {kind}（{reason}）→ v{bump_version(cur, kind)}")
        return 0

    # --- 前置检查与目标版本计算 ---
    preflight()
    cur = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not parse_semver(cur):
        fail(f"根 VERSION 内容非法: {cur!r}（期望 X.Y.Z）")

    baseline = False
    if args.initial:
        if last is not None:
            fail(f"已存在 v tag（最新 {last}），--initial 仅限无任何 v tag 的首次发布")
        if not parse_semver(args.initial):
            fail(f"--initial 版本非法: {args.initial!r}（期望 vX.Y.Z）")
        target = args.initial.lstrip("v")
        baseline = True
    elif last is None:
        target = bump_version(cur, args.bump) if args.bump else cur
        baseline = target == cur
    else:
        if last != f"v{cur}":
            fail(f"根 VERSION（{cur}）与最新 tag（{last}）不一致，疑似手工漂移，请先对齐")
        kind = args.bump or suggest_bump(commits_since(last))[0]
        target = bump_version(cur, kind)

    commits = [] if baseline else commits_since(last)
    entry = build_changelog_entry(target, commits, baseline)
    edits = render_file_edits(target)
    release_files = [p for p, _, _ in edits] + [BACKEND_DIR / "openapi.json", CHANGELOG_FILE]

    # --- 预览 ---
    print(f"发布 v{target}（上个 tag: {last or '（无）'}，纳入提交 {len(commits)} 条）")
    for path, old, new in edits:
        if old != new:
            print(f"  ~ {path.relative_to(REPO_ROOT)}")
        else:
            print(f"  = {path.relative_to(REPO_ROOT)}（无变化）")
    print(f"  ~ backend/openapi.json（重导出，info.version → {target}）")
    print(f"  ~ CHANGELOG.md（顶部插入新条目）")
    print("\n--- CHANGELOG 草稿 ---\n" + entry + "--- 草稿结束 ---\n")
    if args.dry_run:
        print("[dry-run] 未写任何文件。")
        return 0

    # --- 执行：在 release/v{target} 分支完成 commit（commit 前失败时 git restore 可回滚）---
    py = venv_python()
    branch = f"release/v{target}"
    if git("rev-parse", "--verify", "--quiet", branch, check=False):
        fail(f"本地分支 {branch} 已存在；请先处理（git branch -D {branch}）或改用其他版本")
    if git("ls-remote", "--heads", "origin", branch):
        fail(f"远程分支 {branch} 已存在；请先处理对应的发布 PR")
    git("switch", "-c", branch)
    for path, _, new in edits:
        write_text(path, new)
    regen_openapi(py)
    verify_contracts(py)
    write_text(CHANGELOG_FILE, prepend_changelog(entry))

    git("add", *release_files)
    git("commit", "-m", f"chore(release): v{target}", "-m", entry.splitlines()[0])
    print(f"已在 {branch} 提交。")

    if not args.yes:
        if not sys.stdin.isatty():
            fail(f"非交互环境请带 --yes（commit 已在本地 {branch}；手动: git push -u origin {branch} 后创建 PR）")
        answer = input(f"将推送 {branch} 并创建发布 PR，继续？[y/N] ").strip().lower()
        if answer != "y":
            print(f"已取消推送。手动: git push -u origin {branch}；PR 合并后运行 python3 scripts/release.py tag v{target}")
            return 0
    git("push", "-u", "origin", branch)
    git("switch", "main")
    create_release_pr(branch, target, entry, args.fixes)
    print(f"[next] 发布 PR 合并后立即运行: python3 scripts/release.py tag v{target}")
    return 0


def create_release_pr(branch: str, target: str, entry: str, fixes: int | None) -> None:
    body = "\n".join(
        [
            "## 改动内容",
            "",
            f"发布 v{target}：同步版本文件、重导出 openapi.json、生成 CHANGELOG 条目（`scripts/release.py` 自动生成）。",
            "",
            "## 关联 issue",
            "",
            f"- fixes #{fixes}" if fixes else "- （无）",
            "",
            "## 合并后动作（必须）",
            "",
            "立即在 main 上运行阶段二打 tag（v 标签必须落在 main tip，deploy 才会给镜像追加语义标签）：",
            "",
            "```bash",
            f"python3 scripts/release.py tag v{target}",
            "```",
            "",
            "--- CHANGELOG 条目预览 ---",
            "",
            entry,
        ]
    )
    gh = shutil.which("gh")
    if not gh:
        print(f"未检测到 gh CLI；请手动创建 PR（base: main, head: {branch}, 标题: chore(release): v{target}）")
        return
    url = run(
        [gh, "pr", "create", "--base", "main", "--head", branch,
         "--title", f"chore(release): v{target}", "--body", body]
    ).strip()
    print(f"发布 PR 已创建: {url}")


def tag_main(argv: list[str]) -> int:
    """阶段二：发布 PR 合并后，在 origin/main tip 打附注标签 vX.Y.Z 并推送。"""
    parser = argparse.ArgumentParser(description="在 main tip 打 v 标签并推送（docs/reference/versioning.md §4）")
    parser.add_argument("version", metavar="vX.Y.Z", help="目标版本，须与 origin/main 的 VERSION 一致")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args(argv)
    if not parse_semver(args.version):
        fail(f"版本非法: {args.version!r}（期望 vX.Y.Z）")
    target = args.version.lstrip("v")
    tag = f"v{target}"

    git("fetch", "origin", "--tags", "--force")
    if git("tag", "-l", tag):
        fail(f"标签 {tag} 已存在，无需重复打")
    remote_tip = git("rev-parse", "origin/main")
    version_on_main = git("show", "origin/main:VERSION").strip()
    if version_on_main != target:
        fail(f"origin/main 的 VERSION 为 {version_on_main}，与目标 {target} 不一致；发布 PR 是否已合并？")
    subjects = git("log", "origin/main", "-20", "--pretty=%s").splitlines()
    if f"chore(release): {tag}" not in subjects:
        fail(f"origin/main 近 20 条提交中未见 chore(release): {tag}；发布 PR 可能尚未合并")

    if not args.yes:
        if not sys.stdin.isatty():
            fail(f"非交互环境请带 --yes（手动: git tag -a {tag} {remote_tip} -m 'Release {tag}' && git push origin {tag}）")
        answer = input(f"将在 origin/main（{remote_tip[:7]}）打标签 {tag} 并推送，继续？[y/N] ").strip().lower()
        if answer != "y":
            print("已取消。")
            return 0
    git("tag", "-a", tag, remote_tip, "-m", f"Release {tag}")
    git("push", "origin", tag)
    print(f"[ok] {tag} 已推送，指向 main tip {remote_tip[:7]}；CI 通过后 deploy.yml 将为镜像追加 :{tag} 标签。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        fail("已中断")
