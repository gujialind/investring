# ============================================================================
# frontend/package-lock.json 外来 host 防复发守门（#577 ③）
# ============================================================================
# 背景：d2a12ce（2026-07-31）在一台把 npm registry 配成 npmmirror 的机器上跑
# `npm install`，把 lock 里 2 条 `resolved` 写成了 `https://registry.npmmirror.com/...`。
# npm ≥12 默认禁拉「remote」tarball（EALLOWREMOTE），裸 `npm ci` 直接 exit 1；而
# `npm ci --dry-run` 只走 ideal tree、exit 0 —— 「本地 dry-run 过了」不等于这套 lock
# 能用。PR #607 已把 lock 归一回 registry.npmjs.org（方案①），本守门负责防复发：
# 任何再把镜像/第三方 host 写进 lock 的提交在此变红，报错逐条指名违规包路径。
#
# 第二组断言钉 CI 前端安装步骤的形态（#577 ③ 的另两半）：
#   - `node -v && npm -v` 先落日志——把「runner 的 npm 版本」从隐变量变成证据
#     （EALLOWREMOTE 守卫是否生效完全取决于该版本，run 35454529608 绿只是因为
#     当时 runner 是 npm 11.19.0）；
#   - 随后是**裸 `npm ci`**——禁加 `--allow-remote` / `--registry`。npm ≥12 上
#     `--allow-remote` 没有 host 粒度（只有 all/none/root），加它等于整域放行，
#     恰恰消掉本守门要保的「外来 host 会被拦」；若哪天 CI 真撞 EALLOWREMOTE，
#     正确修法是归一 lock（第一组断言会指名条目），不是扩大放行面。
# 切块复用 _ci_text（与 test_ci_mysql_account.py / test_ci_e2e_compare.py 同族：
# 判据抄两份就必然漂移）。
#
# 判定逻辑收在模块级纯函数里，好让反例用例对合成数据跑同一套判据——#577 验收
# 明确「反例不红 ⇒ 断言恒真 ⇒ 守门未完成」。
#
# 本地脚本 scripts/verify-frontend.sh 里的 `npm ci --allow-remote=all` 不在本守门
# 范围：那是「本机 registry 配成镜像」的兼容（npm ≥12 会把 lock 里的 npmjs tarball
# 视为 remote），与 CI 的默认 registry 环境不同，保留理由写在脚本注释里。
# ============================================================================

import copy
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ci_text import code_lines, job_block, step_run_block  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "frontend" / "package-lock.json"

#: lock 内 `resolved` 唯一允许的 host（#577：归一 npm 官方源）。
ALLOWED_HOSTS = {"registry.npmjs.org"}

#: CI 前端安装步骤的期望形态（code_lines 归一化后的行序列，顺序即执行序）。
EXPECTED_INSTALL_LINES = ["node -v && npm -v", "npm ci"]

#: 受守门的两个安装步骤：(workflow 文件, job)。新增前端安装步骤时在此登记。
CI_TARGETS = [
    (REPO_ROOT / ".github" / "workflows" / "ci.yml", "frontend-check"),
    (REPO_ROOT / ".github" / "workflows" / "e2e-stack.yml", "e2e"),
]


def load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def foreign_resolved(lock: dict) -> list[str]:
    """返回 lock 中 host 不在 ALLOWED_HOSTS 的 resolved 条目（逐条指名包路径与 URL）。

    结构不认识就响亮失败，绝不静默返回空列表（假绿灯比红更坏）：本函数的遍历
    前提是 lockfileVersion 3 的 `packages` 单树（v1/v2 还有 `dependencies` 递归树，
    只遍历 packages 会漏条目）。
    """
    version = lock.get("lockfileVersion")
    if version != 3:
        pytest.fail(
            f"package-lock.json lockfileVersion={version!r}，不是 3——"
            "本守门只遍历 packages 单树，前提失效，请同步更新守门"
        )
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        pytest.fail("package-lock.json 缺少 packages 映射——守门遍历前提失效")
    violations = []
    for path, meta in sorted(packages.items()):
        resolved = meta.get("resolved") if isinstance(meta, dict) else None
        if not resolved:
            continue  # 根条目（""）与 workspace link 条目没有 resolved
        host = urlsplit(resolved).netloc
        if host not in ALLOWED_HOSTS:
            violations.append(f"{path}: resolved 指向外来 host {host}（{resolved}）")
    return violations


def install_step_violations(run_lines: list[str]) -> list[str]:
    """判安装步骤的归一化行是否恰为 EXPECTED_INSTALL_LINES。"""
    if run_lines == EXPECTED_INSTALL_LINES:
        return []
    return [
        "前端安装步骤形态 ≠ 期望（期望 "
        f"{EXPECTED_INSTALL_LINES!r}，实际 {run_lines!r}）——裸 npm ci 禁加 "
        "--allow-remote/--registry（整域放行会消掉外来 host 拦截）；lock 再被写入"
        "外来 host 时应归一 lock（foreign_resolved 守门会指名条目）"
    ]


def _install_run_lines(path: Path, job: str) -> list[str]:
    """切出 (path, job) 里 `Install dependencies` 步骤自己的 run 块并归一化。"""
    text = path.read_text(encoding="utf-8")
    source = f"{path.name}:{job}"
    block = job_block(text, job=job, source=source)
    run = step_run_block(block, step="Install dependencies", source=source)
    return code_lines(run)


class TestLockRegistry:
    def test_real_lock_has_no_foreign_hosts(self):
        """正例：仓库内真实 lock 的 resolved 全部指向 registry.npmjs.org（#577 ①）。"""
        assert foreign_resolved(load_lock()) == []

    def test_counterexample_foreign_host_is_red_and_named(self):
        """反例（#577 验收）：任一条 resolved 改成 npmmirror ⇒ 必须红且指名包路径。

        反例不红 ⇒ 断言恒真 ⇒ 守门未完成。
        """
        lock = copy.deepcopy(load_lock())
        path, meta = next(
            (p, m) for p, m in sorted(lock["packages"].items())
            if isinstance(m, dict) and m.get("resolved")
        )
        meta["resolved"] = meta["resolved"].replace(
            "https://registry.npmjs.org/", "https://registry.npmmirror.com/"
        )
        violations = foreign_resolved(lock)
        assert len(violations) == 1
        assert path in violations[0], "报错必须指名被改的那条包路径"
        assert "registry.npmmirror.com" in violations[0]

    def test_counterexample_lockfile_version_drift_is_loud(self):
        """反例：lockfileVersion 漂移（守门遍历前提失效）⇒ 响亮失败而非静默放行。"""
        lock = copy.deepcopy(load_lock())
        lock["lockfileVersion"] = 2
        with pytest.raises(pytest.fail.Exception, match="lockfileVersion"):
            foreign_resolved(lock)


class TestCiInstallShape:
    @pytest.mark.parametrize(
        "path,job", CI_TARGETS, ids=[f"{p.name}:{j}" for p, j in CI_TARGETS]
    )
    def test_install_step_is_version_echo_plus_bare_npm_ci(self, path, job):
        """正例：两个 workflow 的前端安装步骤 = 版本打印 + 裸 npm ci。"""
        assert install_step_violations(_install_run_lines(path, job)) == []

    @pytest.mark.parametrize(
        "bad_lines",
        [
            ["node -v && npm -v", "npm ci --allow-remote=all"],
            ["node -v && npm -v", "npm ci --registry=https://registry.npmmirror.com"],
            ["npm ci"],  # 摘掉版本打印 = 证据链断，同样红
        ],
        ids=["allow-remote", "registry-mirror", "no-version-echo"],
    )
    def test_counterexample_flagged_install_is_red(self, bad_lines):
        """反例：安装步骤被加放行 flag 或摘掉版本打印 ⇒ 判据必须红。"""
        assert install_step_violations(bad_lines)
