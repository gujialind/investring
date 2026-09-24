#!/usr/bin/env python3
"""
InvestRing 发布脚本（issue #375）

以根 VERSION 文件为项目版本单一事实来源。main 的 ruleset 要求一切改动经 PR 且
CI OK（直接推送会被拒绝），故发布分两阶段：

  阶段一（本脚本默认命令）：同步版本文件 → 用钉版 .venv-openapi 隔离重导出
    openapi.json → 契约验证 → 从 conventional commits 生成 CHANGELOG →
    在 release/vX.Y.Z 分支单 commit → 推送分支并创建发布 PR。
  阶段二（release.py tag vX.Y.Z --pr N 或 --sha 完整SHA）：校验发布 PR 身份、
    main 可达性、目标版本投影与该 SHA 的 main push CI，在精确合并提交打标签。
    本步骤不补镜像标签；现有 CD 仍只在构建时检测 v 标签。
  doctor：只读检查当前工作区版本投影，不要求 HEAD 位于版本标签上。

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
    python3 scripts/release.py tag v0.1.0 --pr 123    # 阶段二：发布 PR 合并后打 tag 并推送
    python3 scripts/release.py doctor               # 只读版本投影检查
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO_ROOT / "VERSION"
CHANGELOG_FILE = REPO_ROOT / "CHANGELOG.md"
VENV_PY = REPO_ROOT / ".venv-openapi" / "bin" / "python"
BACKEND_DIR = REPO_ROOT / "backend"

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
CONVENTIONAL_RE = re.compile(r"^([a-zA-Z]+)(\(([^)]+)\))?(!)?:\s*(.+)$")
SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
CI_WORKFLOW = ".github/workflows/ci.yml"

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


def run(cmd, cwd=None, env=None, check=True, redact=False):
    """执行命令并返回 stdout；网络命令失败时不回显可能含凭据的输出。"""
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd or REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
    except OSError:
        fail(f"无法执行 {Path(cmd[0]).name}")
    if check and proc.returncode != 0:
        if redact:
            fail(f"{Path(cmd[0]).name} {cmd[1]} 失败（exit {proc.returncode}）；请检查认证、权限与网络")
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        fail(f"命令失败（exit {proc.returncode}）: {' '.join(str(c) for c in cmd)}")
    return proc.stdout


def git(*args, check=True) -> str:
    return run(
        ["git", *args], check=check,
        redact=args[0] in {"fetch", "push", "ls-remote", "remote"},
    ).strip()


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


def render_file_edits(target: str, *, reader=None) -> list[tuple[Path, str, str]]:
    """计算版本投影（不写盘），供发布、预览及只读检查共用。"""
    reader = reader or read_text
    old_version = reader(VERSION_FILE)
    eol = "\r\n" if old_version.endswith("\r\n") else "\n"
    edits = [(VERSION_FILE, old_version, f"{target}{eol}")]
    for path, pattern, repl, count in [
        (BACKEND_DIR / "pyproject.toml", r'^version = "[^"]*"', f'version = "{target}"', 1),
        (REPO_ROOT / "ir-cli" / "pyproject.toml", r'^version = "[^"]*"', f'version = "{target}"', 1),
        (REPO_ROOT / "frontend" / "package.json", r'^  "version": "[^"]*"', f'  "version": "{target}"', 1),
        # package-lock 的前两处 "version" 恰为顶层与 packages[""]，不同步会击穿 npm ci
        (REPO_ROOT / "frontend" / "package-lock.json", r'"version": "[^"]*"', f'"version": "{target}"', 2),
    ]:
        old = reader(path)
        new, n = re.subn(pattern, repl, old, count=count, flags=re.MULTILINE)
        if n != count:
            fail(f"{path} 中版本行匹配 {n} 处（期望 {count}），文件结构可能已变化，请人工检查")
        edits.append((path, old, new))
    return edits


def check_version_projection(target: str | None = None, *, ref: str | None = None) -> str:
    def reader(path):
        if ref:
            return run(["git", "show", f"{ref}:{path.relative_to(REPO_ROOT).as_posix()}"])
        return read_text(path)

    try:
        current = reader(VERSION_FILE).strip()
        if not parse_semver(current) or current.startswith("v"):
            fail("根 VERSION 内容非法（期望 X.Y.Z）")
        target = target or current
        drift = [str(p.relative_to(REPO_ROOT)) for p, old, new in render_file_edits(target, reader=reader) if old != new]
        schema = json.loads(reader(BACKEND_DIR / "openapi.json"))
        info = schema.get("info") if isinstance(schema, dict) else None
        if not isinstance(info, dict) or info.get("version") != target:
            drift.append("backend/openapi.json info.version")
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("版本投影读取失败：文件缺失、编码错误或 OpenAPI JSON 非法")
    if drift:
        fail(f"版本投影与 VERSION/目标 {target} 不一致: {', '.join(drift)}")
    return target


def doctor_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="只读检查工作区版本投影，不 fetch、不调用应用或生成器")
    parser.parse_args(argv)
    version = check_version_projection()
    print(f"[ok] 版本投影一致: {version}（不要求 HEAD 位于 v 标签上）")
    return 0


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
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        return doctor_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "alias":
        return alias_main(sys.argv[2:])
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
            print(f"已取消推送。手动: git push -u origin {branch}；PR 合并后使用 tag v{target} --pr <PR号>")
            return 0
    git("push", "-u", "origin", branch)
    git("switch", "main")
    create_release_pr(branch, target, entry, args.fixes)
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
            "本 PR 合入 main 且合并提交的 push CI 成功后，用本 PR 编号定位发布提交（不依赖 main tip）：",
            "",
            "```bash",
            f"python3 scripts/release.py tag v{target} --pr <本PR号>",
            "```",
            "",
            "也可使用 --sha <完整合并SHA>；创建 PR 时合并 SHA 尚未产生，不预先记录。",
            "此步骤仅打 Git 标签；现有 CD 仍在构建时检测标签，不保证补齐已构建镜像的语义标签。",
            "",
            "--- CHANGELOG 条目预览 ---",
            "",
            entry,
        ]
    )
    gh = shutil.which("gh")
    if not gh:
        print(f"未检测到 gh CLI；请手动创建 PR（base: main, head: {branch}, 标题: chore(release): v{target}）")
        print(f"[next] PR 合并且 push CI 成功后: python3 scripts/release.py tag v{target} --pr <PR号>")
        return
    repo = origin_repository()
    url = run(
        [gh, "pr", "create", "--repo", repo, "--base", "main", "--head", branch,
         "--title", f"chore(release): v{target}", "--body", body], redact=True,
    ).strip()
    match = re.fullmatch(rf"https://github\.com/{re.escape(repo)}/pull/([1-9][0-9]*)", url)
    if not match:
        fail("gh 未返回有效的发布 PR URL；请检查 release 分支对应的 PR，勿盲目重复创建")
    print(f"发布 PR 已创建: {repo}#{match[1]} ({url})")
    print(f"[next] PR 合并且 push CI 成功后: python3 scripts/release.py tag v{target} --pr {match[1]}")
    print("也可使用 --sha <完整合并SHA>；此时尚无合并 SHA。")


def origin_repository() -> str:
    remote = git("remote", "get-url", "origin")
    if remote.startswith("git@github.com:"):
        path = remote.removeprefix("git@github.com:")
    else:
        try:
            url = urlsplit(remote)
            if url.scheme not in {"https", "ssh"} or url.hostname != "github.com" or url.query or url.fragment:
                fail("origin 必须指向 github.com 仓库（不回显 remote URL）")
            path = url.path.lstrip("/")
        except ValueError:
            fail("无法解析 origin 仓库地址（不回显 remote URL）")
    repo = path.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        fail("无法解析 origin 仓库身份（不回显 remote URL）")
    return repo


def gh_api(endpoint: str, *args):
    gh = shutil.which("gh")
    if not gh:
        fail("未检测到 gh CLI；无法验证发布 PR 与 CI")
    output = run([gh, "api", "--hostname", "github.com", "--method", "GET", endpoint, *args], redact=True)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        fail("gh API 未返回有效 JSON；无法验证发布 PR 与 CI")


def is_release_pr(pr, repo: str, tag: str) -> bool:
    if not isinstance(pr, dict):
        return False
    base, head = pr.get("base"), pr.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        return False
    base_repo, head_repo = base.get("repo"), head.get("repo")
    return (
        isinstance(pr.get("merged_at"), str) and bool(pr["merged_at"])
        and pr.get("merged", True) is True and pr.get("state") == "closed"
        and type(pr.get("number")) is int and pr["number"] > 0
        and pr.get("title") == f"chore(release): {tag}"
        and base.get("ref") == "main" and head.get("ref") == f"release/{tag}"
        and isinstance(base_repo, dict) and str(base_repo.get("full_name", "")).lower() == repo.lower()
        and isinstance(head_repo, dict) and str(head_repo.get("full_name", "")).lower() == repo.lower()
        and isinstance(pr.get("merge_commit_sha"), str) and SHA_RE.fullmatch(pr["merge_commit_sha"]) is not None
    )


def resolve_release_target(repo: str, tag: str, *, pr_number: int | None, sha: str | None) -> tuple[str, int]:
    if pr_number is not None:
        pr = gh_api(f"repos/{repo}/pulls/{pr_number}")
        if not is_release_pr(pr, repo, tag) or pr["number"] != pr_number:
            fail("目标 PR 不是已合并到 main 的对应 release PR（标题、分支、仓库或合并 SHA 不符）")
        return pr["merge_commit_sha"].lower(), pr_number
    pages = gh_api(f"repos/{repo}/commits/{sha}/pulls", "--paginate", "--slurp")
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        fail("gh API 返回的提交关联 PR 列表无效")
    matches = [pr for page in pages for pr in page if is_release_pr(pr, repo, tag) and pr["merge_commit_sha"].lower() == sha]
    if len(matches) != 1:
        fail("目标 SHA 必须唯一对应一个已合并到 main 的 release PR 合并提交")
    return sha, matches[0]["number"]


def verify_release_ci(repo: str, sha: str) -> list:
    """验证目标 SHA 的可信 main push CI；返回可信 run 列表（alias 用它绑定发布包 run）。"""
    workflow = gh_api(f"repos/{repo}/actions/workflows/ci.yml")
    if (
        not isinstance(workflow, dict) or type(workflow.get("id")) is not int
        or workflow["id"] <= 0 or workflow.get("path") != CI_WORKFLOW or workflow.get("name") != "CI"
    ):
        fail("无法确认可信 CI workflow（.github/workflows/ci.yml，名称 CI）")
    pages = gh_api(
        f"repos/{repo}/actions/workflows/{workflow['id']}/runs",
        "-f", f"head_sha={sha}", "-f", "branch=main", "-f", "event=push", "-f", "per_page=100",
        "--paginate", "--slurp",
    )
    if not isinstance(pages, list) or any(
        not isinstance(page, dict) or not isinstance(page.get("workflow_runs"), list) for page in pages
    ):
        fail("gh API 返回的 CI run 列表无效")
    trusted = []
    for page in pages:
        for run_info in page["workflow_runs"]:
            if not isinstance(run_info, dict):
                fail("gh API 返回的 CI run 无效")
            head_repo = run_info.get("head_repository")
            if (
                run_info.get("workflow_id") == workflow["id"] and run_info.get("path") == CI_WORKFLOW
                and run_info.get("head_sha") == sha and run_info.get("head_branch") == "main"
                and run_info.get("event") == "push" and isinstance(head_repo, dict)
                and str(head_repo.get("full_name", "")).lower() == repo.lower()
                and type(run_info.get("id")) is int and run_info["id"] > 0
            ):
                trusted.append(run_info)
    if not trusted:
        fail("目标 SHA 缺少可信的 main push CI run")
    latest = max(trusted, key=lambda item: item["id"])
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        fail("目标 SHA 最新的 main push CI 未成功完成")
    return trusted


def existing_tag_targets(tag: str) -> tuple[str | None, str | None]:
    ref = f"refs/tags/{tag}"
    local = None
    if git("rev-parse", "--verify", "--quiet", ref, check=False):
        local = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    output = git("ls-remote", "--tags", "origin", ref, f"{ref}^{{}}")
    remote_refs = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[0]) or parts[1] not in {ref, f"{ref}^{{}}"} or parts[1] in remote_refs:
            fail("远程标签查询返回无效结果")
        remote_refs[parts[1]] = parts[0].lower()
    if f"{ref}^{{}}" in remote_refs and ref not in remote_refs:
        fail("远程附注标签缺少原始引用")
    return local, remote_refs.get(f"{ref}^{{}}", remote_refs.get(ref))


def full_sha(value: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("--sha 必须是完整的 40 位 commit SHA")
    return value.lower()


def positive_pr(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError("--pr 必须是正整数 PR 号")
    return int(value)


def tag_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="在已验证的 release PR 合并 SHA 打 v 标签并推送")
    parser.add_argument("version", metavar="vX.Y.Z", help="目标版本，须与目标 SHA 的版本投影一致")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sha", type=full_sha, help="发布 PR 的完整合并 SHA")
    source.add_argument("--pr", type=positive_pr, help="已合并的发布 PR 号")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args(argv)
    if not parse_semver(args.version):
        fail(f"版本非法: {args.version!r}（期望 vX.Y.Z）")
    target = args.version.lstrip("v")
    tag = f"v{target}"
    repo = origin_repository()
    sha, number = resolve_release_target(repo, tag, pr_number=args.pr, sha=args.sha)
    git("fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
    if git("rev-parse", "--verify", f"{sha}^{{commit}}") != sha:
        fail("目标 SHA 不是 commit")
    if git("merge-base", sha, "refs/remotes/origin/main", check=False) != sha:
        fail("目标 SHA 不可从 origin/main 到达；不允许给未合入 main 的提交打标签")
    check_version_projection(target, ref=sha)
    verify_release_ci(repo, sha)
    local, remote = existing_tag_targets(tag)
    if any(existing is not None and existing != sha for existing in (local, remote)):
        fail(f"标签 {tag} 已指向其他 SHA；拒绝覆盖本地或远程标签")
    if remote == sha:
        print(f"[ok] {tag} 已指向 {sha}（PR #{number}）；无需重复推送")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            fail("非交互环境请带 --yes；尚未创建或推送标签")
        answer = input(f"将发布 PR #{number} 的 {sha} 标记为 {tag} 并推送，继续？[y/N] ").strip().lower()
        if answer != "y":
            print("已取消。")
            return 0
    if local is None:
        git("tag", "-a", tag, sha, "-m", f"Release {tag}")
    git("push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    print(f"[ok] {tag} 已推送，指向 {sha}（PR #{number}）；本步骤不补镜像标签、不触发 CD。")
    return 0


def docker(*args, check=True):
    """执行 docker CLI 并返回 CompletedProcess（alias 需要按 returncode 分支）。"""
    cmd = ["docker", *[str(a) for a in args]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        fail("无法执行 docker（alias 需要本机 docker CLI 与已登录的 ACR 凭据）")
    if check and proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        fail(f"docker {args[0]} 失败（exit {proc.returncode}）")
    return proc


def alias_image(repo: str, digest: str, tag: str, *, dry_run: bool) -> None:
    """把 repo:tag 指到 repo@digest。冲突拒绝、同 digest 幂等（#540）。

    判定用注册表实况（pull 后比对 RepoDigests），不缓存、不猜测：
      - :tag 不存在        → 按 digest 拉取、打标签、推送；
      - :tag == 本 digest  → 幂等成功（部分成功后重试走这条路）；
      - :tag == 其他 digest → 拒绝覆盖。alias 冲突必须人工核对来源，
        不通过重建/重部署「补」出一个语义标签。
    """
    source, target = f"{repo}@{digest}", f"{repo}:{tag}"
    if dry_run:
        print(f"[dry-run] alias {target} → {digest[:19]}…")
        return
    docker("pull", source)
    if docker("pull", target, check=False).returncode == 0:
        digests = json.loads(
            docker("image", "inspect", target, "--format", "{{json .RepoDigests}}").stdout or "[]")
        if source in digests:
            print(f"[ok] {target} 已指向 {digest[:19]}…（幂等，无需推送）")
            return
        fail(f"{target} 已指向其他 digest（{digests!r}）；拒绝覆盖语义标签")
    image_id = docker("image", "inspect", source, "--format", "{{.Id}}").stdout.strip()
    if not image_id:
        fail(f"无法解析 {source} 的镜像 ID")
    docker("tag", image_id, target)
    docker("push", target)
    print(f"[ok] {target} → {digest[:19]}…")


def alias_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="把 vX.Y.Z 语义镜像标签 alias 到已验证发布包的 digest（与构建时机解耦，#540）")
    parser.add_argument("version", metavar="vX.Y.Z", help="目标语义版本")
    parser.add_argument("--bundle", required=True,
                        help="release-bundle 目录（scripts/release_bundle.py build 的产物）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的 alias，不动注册表")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args(argv)
    if not parse_semver(args.version):
        fail(f"版本非法: {args.version!r}（期望 vX.Y.Z）")
    tag = f"v{args.version.lstrip('v')}"

    # 发布包校验与 release_bundle.py verify 同一判据（不抄第二份）。
    # 按 __file__ 定位同目录兄弟模块，不依赖 REPO_ROOT（测试会替换它）。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from release_bundle import BundleError, BundleMismatch, validate_bundle_dir
    except ImportError:
        fail("无法导入 scripts/release_bundle.py；alias 与发布包必须同源")
    try:
        bundle = validate_bundle_dir(args.bundle)
    except (BundleError, BundleMismatch) as exc:
        fail(f"发布包校验失败：{exc}")

    sha = bundle["git"]["sha"]
    repo = origin_repository()
    # 1) 该版本必须已走完可信 git tag 流程且指向发布包 SHA（先 tag 后 alias）
    _, remote_tag = existing_tag_targets(tag)
    if remote_tag != sha:
        fail(f"远程标签 {tag} 未指向发布包 SHA {sha[:12]}…；"
             f"请先完成 release.py tag 流程（当前远程指向 {remote_tag or '（无）'}）")
    # 2) 发布包必须产自该 SHA 的可信成功 CI run（不只信 JSON 自报字段）
    trusted = verify_release_ci(repo, sha)
    succeeded = {r["id"] for r in trusted
                 if r.get("status") == "completed" and r.get("conclusion") == "success"}
    if bundle["build"]["run_id"] not in succeeded:
        fail(f"发布包 run_id={bundle['build']['run_id']} 不在 {sha[:12]}… 的可信成功 CI run 中；"
             "拒绝以此包 alias")
    if not args.dry_run:
        if shutil.which("docker") is None:
            fail("未检测到 docker CLI；alias 需要本机 docker 与已登录的 ACR 凭据")
        if not args.yes:
            if not sys.stdin.isatty():
                fail("非交互环境请带 --yes（或 --dry-run 预览）；尚未改动注册表")
            answer = input(
                f"将把 {tag} 指向 {sha[:12]}… 的前后端镜像 digest 并推送，继续？[y/N] ").strip().lower()
            if answer != "y":
                print("已取消。")
                return 0
    for role in ("backend", "frontend"):
        image = bundle["images"][role]
        alias_image(image["repo"], image["digest"], tag, dry_run=args.dry_run)
    if args.dry_run:
        print("[dry-run] 未改动注册表。")
    else:
        print(f"[ok] {tag} 语义标签已对齐 {sha[:12]}…（run {bundle['build']['run_id']}."
              f"{bundle['build']['run_attempt']}）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        fail("已中断")
