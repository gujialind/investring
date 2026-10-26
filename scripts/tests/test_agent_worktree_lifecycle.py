"""agent-spin/merge/clean 的只读反例测试（#793 评审 S3，code-review.md §4.5）。

护栏必须"拒绝并非零退出"，且失败出口可验证；不建 worktree、不写台账。
"""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPIN = ROOT / "scripts" / "agent-spin"
MERGE = ROOT / "scripts" / "agent-merge"
CLEAN = ROOT / "scripts" / "agent-clean"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )


@pytest.mark.parametrize("script", [SPIN, MERGE, CLEAN], ids=lambda p: p.name)
def test_scripts_are_executable_and_valid_bash(script: Path):
    assert script.stat().st_mode & 0o111, f"{script.name} 缺少可执行位"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_spin_without_feature_fails():
    result = _run(SPIN)
    assert result.returncode != 0
    assert "usage: agent-spin" in result.stderr


def test_clean_unknown_feature_fails_without_touching_state():
    result = _run(CLEAN, "不存在的功能名-test")
    assert result.returncode == 1
    assert "没有 worktree" in result.stderr


def test_merge_unknown_feature_fails_without_touching_state():
    result = _run(MERGE, "不存在的功能名-test")
    assert result.returncode == 1
    assert "没有 worktree" in result.stderr
    assert "先跑 agent-spin" in result.stderr


def test_spin_exhaustion_hint_and_state_paths_present():
    # 失败出口产物：端口池耗尽时必须提示用 agent-clean 回收已建 worktree（S1）
    spin_src = SPIN.read_text(encoding="utf-8")
    assert "回收：scripts/agent-clean" in spin_src
    # 状态落点为中性目录 .worktrees/（B 方案），且已被 .gitignore 覆盖
    assert '.worktrees/"' in spin_src or "/.worktrees/" in spin_src
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".worktrees/" in gitignore
