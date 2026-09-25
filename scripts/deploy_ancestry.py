#!/usr/bin/env python3
"""CD 祖先关系判据（issue #537 第四批 C）。

自动部署前验证：服务器上最近一条已接受的 auto 发布记录（prev）必须是本次
部署目标（head）在主线上的祖先。作用：

  旧任务晚到   prev 比 head 更新（更晚的发布已被接受）⇒ prev 不是 head 祖先 ⇒ 拒绝。
  docs-only    文档提交推进 main 但不触发 CD（ci.yml push paths-ignore），
  推进后 head 仍是 prev 的后代 ⇒ 祖先关系（而非父子/相等）不误挡。
  历史分叉     rebase/强推后 prev 不在 head 历史中 ⇒ 拒绝并留给人工判断。

服务器端没有 git 历史，故本判据只能在 workflow 侧算；「读记录 → 算祖先 → 执行」
之间的并发窗口由 server_deploy.sh 在 flock 内重读记录兜底（--expect-accepted）。

退出码：0 成立或首次部署；1 旧任务/分叉（拒绝部署）；2 harness 错误（参数非法、
对象缺失——调用方应先 git fetch）。纯 stdlib（scripts/ 约定）。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def fail(msg: str, code: int = 2) -> None:
    print(f"[error] {msg}", file=sys.stderr)
    sys.exit(code)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prev-sha", required=True,
                        help="最近 auto 记录的完整 SHA；首次部署传 none")
    parser.add_argument("--head-sha", required=True, help="本次部署目标的完整 SHA")
    parser.add_argument("--repo-root", default=".", help="git 仓库路径（需已 fetch 到两个对象）")
    args = parser.parse_args(argv)

    head = args.head_sha.strip().lower()
    if not SHA_RE.fullmatch(head):
        fail(f"--head-sha 必须是完整 40 位 SHA: {args.head_sha!r}")
    if args.prev_sha.strip().lower() == "none":
        print("[ok] 无已接受的 auto 发布记录（首次部署）；跳过祖先判据")
        return 0
    prev = args.prev_sha.strip().lower()
    if not SHA_RE.fullmatch(prev):
        fail(f"--prev-sha 必须是完整 40 位 SHA 或 none: {args.prev_sha!r}")

    repo = Path(args.repo_root)
    for sha, label in ((prev, "--prev-sha"), (head, "--head-sha")):
        if git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            fail(f"{label} {sha[:12]}… 不在仓库中；请先 git fetch origin main --tags")

    proc = git(repo, "merge-base", "--is-ancestor", prev, head)
    if proc.returncode == 0:
        print(f"[ok] 祖先关系成立：{prev[:12]}… 是 {head[:12]}… 的祖先"
              "（中间的 docs-only 等不触发 CD 的提交不影响）")
        return 0
    if proc.returncode == 1:
        fail(f"旧任务拒绝：最近已接受发布 {prev[:12]}… 不是目标 {head[:12]}… 的祖先；"
             "说明更新的发布已被接受或历史已分叉，本次不得部署", code=1)
    fail(f"git merge-base 失败（exit {proc.returncode}）: {proc.stderr.strip()}")


if __name__ == "__main__":
    sys.exit(main())
