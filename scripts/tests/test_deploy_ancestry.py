"""deploy_ancestry.py 的子进程测试（issue #537 第四批 C）。

覆盖计划要求的判据：旧任务晚到（prev 比 head 新 ⇒ 拒绝）、docs-only main 推进
（祖先关系而非父子/相等 ⇒ 不误挡）、历史分叉拒绝、首次部署放行、参数/对象
缺失是 harness 错误（exit 2）而不是"旧任务"（exit 1）——两类失败必须可区分，
workflow 依此决定是重试还是人工介入。
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_ancestry.py"


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_", "GITHUB_"))}
    env.update(
        HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
    )

    def git(*args):
        proc = subprocess.run(["git", *map(str, args)], cwd=str(repo), env=env,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    git("init", "-q", "--initial-branch=main", repo)

    def commit(name, branch=None):
        if branch:
            git("checkout", "-q", "-B", branch)
        (repo / name).write_text(name + "\n", encoding="utf-8")
        git("add", name)
        git("commit", "-q", "-m", name)
        return git("rev-parse", "HEAD")

    return SimpleNamespace(repo=repo, git=git, commit=commit)


def run_ancestry(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=60)


def test_first_deploy_without_prev_passes(git_repo):
    head = git_repo.commit("a")
    proc = run_ancestry("--prev-sha", "none", "--head-sha", head,
                        "--repo-root", str(git_repo.repo))
    assert proc.returncode == 0
    assert "首次部署" in proc.stdout


def test_linear_ancestor_passes_across_doc_only_gap(git_repo):
    a = git_repo.commit("code")
    git_repo.commit("docs-only")     # 不触发 CD 的中间提交
    c = git_repo.commit("code2")
    proc = run_ancestry("--prev-sha", a, "--head-sha", c, "--repo-root", str(git_repo.repo))
    assert proc.returncode == 0
    assert "docs-only" in proc.stdout


def test_stale_task_rejected_when_prev_is_newer(git_repo):
    git_repo.commit("a")
    newer = git_repo.commit("b")
    older = git_repo.git("rev-parse", "HEAD~1")
    proc = run_ancestry("--prev-sha", newer, "--head-sha", older,
                        "--repo-root", str(git_repo.repo))
    assert proc.returncode == 1
    assert "旧任务" in proc.stderr


def test_diverged_history_rejected(git_repo):
    base = git_repo.commit("base")
    side = git_repo.commit("side", branch="feature")
    git_repo.git("checkout", "-q", base)
    main_tip = git_repo.commit("main2")
    proc = run_ancestry("--prev-sha", side, "--head-sha", main_tip,
                        "--repo-root", str(git_repo.repo))
    assert proc.returncode == 1
    assert "分叉" in proc.stderr


def test_unknown_prev_object_is_harness_error_not_stale(git_repo):
    head = git_repo.commit("a")
    missing = "f" * 40
    proc = run_ancestry("--prev-sha", missing, "--head-sha", head,
                        "--repo-root", str(git_repo.repo))
    assert proc.returncode == 2
    assert "不在仓库中" in proc.stderr and "fetch" in proc.stderr


@pytest.mark.parametrize("prev,head", [
    ("abc", "f" * 40), ("f" * 40, "xyz"), ("none", "short"), ("f" * 39, "f" * 40),
])
def test_bad_sha_formats_are_harness_errors(git_repo, prev, head):
    proc = run_ancestry("--prev-sha", prev, "--head-sha", head,
                        "--repo-root", str(git_repo.repo))
    assert proc.returncode == 2
    assert "40 位" in proc.stderr


def test_prev_sha_is_case_insensitive(git_repo):
    a = git_repo.commit("a")
    c = git_repo.commit("c")
    proc = run_ancestry("--prev-sha", a.upper(), "--head-sha", c.upper(),
                        "--repo-root", str(git_repo.repo))
    assert proc.returncode == 0
