"""deploy.yml 的守门测试（issue #537 第四批 C-6）。

CD 重写后的不变量，逐条变成可执行判据；每条判据配反例变异证明"判据真的会红"。
守护的关节（每一个被悄悄弱化都会让部署链退回到事故前状态）：
  1. CD 不再构建/推送镜像（build-push/login-action 不得回流）；
  2. workflow_run 的 event=='push' 守卫（#456 教训）不被删；
  3. 来源验证走 API 按 run+attempt 核对仓库/workflow/event/SHA/结论，不只信事件载荷；
  4. 发布包经 gh run download 按 run 下载（不得用 runner 不存在的 --attempt），
     过 release_bundle verify 同一判据，并以「包身份 ↔ release-id 互证」钉住 attempt；
  5. 记录快照 → 祖先判据 → 锁内 --expect-accepted 重验的旧任务拒绝链完整；
  6. SSH 主机指纹强制（known_hosts + StrictHostKeyChecking=yes，拒绝 appleboy 回流）；
  7. 手动 inputs 只经 env 映射进入 shell（注入卫生）且格式严格校验；
  8. 失败退出码有人工处置指引（尤其 4=停在写库前、6=不自动回滚）；
  9. 权限最小化：顶层 read，仅 deploy job 为 deploy/ tag 放宽 contents: write；
 10. deploy/ tag 仅 auto 与 migrate 打（rollback/redeploy 不打），tag 目标必须先解析为
     完整 commit，且已存在则跳过。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "deploy.yml"


def _deploy_text():
    return (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")


def section(text, start, end=None):
    assert start in text, f"{SOURCE} 缺少锚点: {start!r}"
    i = text.index(start)
    j = text.index(end, i) if end else len(text)
    return text[i:j]


# ------------------------------------------------------------------ 判据
def assert_no_build(text, source=SOURCE):
    for needle in ("docker/build-push-action", "docker/setup-buildx-action",
                   "docker/login-action", "Build and push"):
        assert needle not in text, f"{source}: CD 不再构建/推送镜像，不得出现 {needle}"


def assert_event_guard(text, source=SOURCE):
    for needle in ("github.event.workflow_run.conclusion == 'success'",
                   "github.event.workflow_run.head_branch == 'main'",
                   "github.event.workflow_run.event == 'push'"):
        assert needle in text, f"{source}: workflow_run 守卫缺失（#456 教训）: {needle}"


def assert_source_verification(text, source=SOURCE):
    sec = section(text, "- name: 验证触发 CI run 来源", "- name: 下载并校验发布包")
    assert "if: env.MODE == 'auto'" in sec, f"{source}: 来源验证必须只在 auto 模式执行"
    assert "actions/runs/$CI_RUN_ID/attempts/$CI_RUN_ATTEMPT" in sec, \
        f"{source}: 必须按 run ID + attempt 拉取权威记录"
    for needle in ('.head_sha == $sha', '.head_branch == "main"', '.event == "push"',
                   '.conclusion == "success"', 'head_repository.full_name', '.run_attempt',
                   '.path == ".github/workflows/ci.yml" and .name == "CI"'):
        assert needle in sec, f"{source}: 来源验证判据缺失: {needle}"


def assert_bundle_consumption(text, source=SOURCE):
    sec = section(text, "- name: 下载并校验发布包", "- name: 配置 SSH")
    assert 'gh run download "$CI_RUN_ID"' in sec, f"{source}: 必须按经验证的 run ID 下载发布包"
    assert "--attempt" not in sec, \
        f"{source}: gh run download 无 --attempt（2026-09-25 生产 CD 实证 unknown flag 致红）"
    assert "--name release-bundle" in sec, f"{source}: artifact 名不符"
    assert 'release_bundle.py verify --bundle-dir bundle --sha "$HEAD_SHA"' in sec, \
        f"{source}: 必须复用 release_bundle verify 同一判据"
    # attempt 绑定靠互证：包身份 rid（sha/run/attempt）必须与目标 release-id 比对并拒绝不一致
    assert 'rid = f"{b[\'git\'][\'sha\'][:7]}-{b[\'build\'][\'run_id\']}.{b[\'build\'][\'run_attempt\']}"' in sec, \
        f"{source}: 缺少包身份 rid 构造（attempt 绑定的唯一执行点）"
    assert "if rid != sys.argv[1]:" in sec, f"{source}: 包身份 ↔ release-id 互证被移除"


def assert_ancestry_chain(text, source=SOURCE):
    sec = section(text, "- name: 读取服务器发布记录并验证祖先关系", "- name: 服务器端 ACR 登录")
    assert "record --last-auto" in sec, f"{source}: 必须先读服务器记录快照"
    assert "deploy_ancestry.py" in sec, f"{source}: 必须验证主线祖先关系"
    assert sec.index("record --last-auto") < sec.index("deploy_ancestry.py"), \
        f"{source}: 记录快照必须先于祖先判据"
    assert '--prev-sha "$PREV_SHA" --head-sha "$HEAD_SHA"' in sec, f"{source}: 祖先判据参数不符"
    assert "^(none|[0-9]+\\.[0-9]+)$" in sec, f"{source}: PREV_SPEC 必须格式校验后才可拼接"
    assert 'if [ "$MODE" = "auto" ]' in sec, f"{source}: 祖先判据只约束 auto 部署"


def assert_ssh_hardening(text, source=SOURCE):
    assert "appleboy" not in text, f"{source}: 不得回退到不做主机指纹校验的 appleboy action"
    sec = section(text, "- name: 配置 SSH", "- name: 同步部署脚本")
    for needle in ("StrictHostKeyChecking=yes", "UserKnownHostsFile", "BatchMode=yes",
                   'if [ -z "$SERVER_KNOWN_HOSTS" ]'):
        assert needle in sec, f"{source}: SSH 主机指纹校验缺失: {needle}"


def assert_input_hygiene(text, source=SOURCE):
    for line in text.splitlines():
        if "github.event.inputs." in line:
            stripped = line.strip()
            assert stripped.startswith(("INPUT_ACTION:", "INPUT_RELEASE_ID:", "INPUT_EXPECT_STATE:")) \
                and stripped.endswith("}}"), \
                f"{source}: inputs 只允许经 env 映射传递，不得内插进 shell: {line!r}"
    assert 'INPUT_RELEASE_ID" =~ ^[0-9a-f]{7}-[0-9]+\\.[0-9]+$' in text, \
        f"{source}: release_id 必须严格校验格式"
    assert 'INPUT_EXPECT_STATE" =~ ^[0-9a-f]{64}$' in text, \
        f"{source}: expect_state 必须严格校验格式"
    assert "expect_state 只允许用于 migrate" in text, f"{source}: 非 migrate 不得携带 expect_state"


def assert_lock_stale_args(text, source=SOURCE):
    sec = section(text, "- name: 执行部署", "- name: Tag deployed commit")
    assert "--expect-accepted $PREV_SPEC" in sec, f"{source}: 必须传递锁内重验的记录快照"
    assert "bash '$BASE/server_deploy.sh' $ARGS" in sec, f"{source}: 必须经 server_deploy.sh 执行"
    assert 'if [ "$MODE" = "auto" ]' in sec and "--incoming $BASE/incoming/$TARGET" in sec, \
        f"{source}: auto 必须消费落地的发布包"
    assert 'if [ "$MODE" = "migrate" ]' in sec and "--expect-state $INPUT_EXPECT_STATE" in sec, \
        f"{source}: migrate 授权指纹必须显式传递"


def assert_failure_guidance(text, source=SOURCE):
    sec = section(text, 'case "$RC" in', "esac")
    for code, needle in (("3)", "旧任务"), ("4)", "写库启动之前"), ("4)", "action=migrate"),
                         ("5)", "探活"), ("6)", "未做自动回滚")):
        assert code in sec and needle in sec, f"{source}: 退出码 {code} 缺少处置指引: {needle}"


def assert_permissions(text, source=SOURCE):
    top = section(text, "\npermissions:", "\njobs:")
    assert "contents: read" in top and "actions: read" in top, \
        f"{source}: 顶层权限必须最小化（read）"
    job = section(text, "  deploy:", "    steps:")
    assert "contents: write" in job and "actions: read" in job, \
        f"{source}: deploy job 权限不符（tag 需要 contents: write）"
    assert "name: production" in job, f"{source}: deploy 必须绑定 production 环境保护"


def assert_manual_modes(text, source=SOURCE):
    assert "options: [redeploy, rollback, migrate]" in text, f"{source}: 手动模式必须显式三选一"
    setup = section(text, "  setup:", "  deploy:")
    assert "redeploy|rollback|migrate" in setup, f"{source}: setup 必须校验 action 枚举"
    assert "INPUT_RELEASE_ID" in setup and "INPUT_EXPECT_STATE" in setup, \
        f"{source}: 手动目标必须在 setup 严格校验"


def assert_tag_gated(text, source=SOURCE):
    sec = section(text, "- name: Tag deployed commit", None)
    # 整行精确匹配（含行尾换行）：子串匹配会被 "auto || 任意其他模式" 静默满足。
    # migrate 必须打——它的目标发布通常来自一次停在 exit 4 的自动部署，那次没走到本步骤，
    # 只认 auto 会让标签停在迁移前的 commit（docs/reference/versioning.md §3 的追溯口径）。
    # rollback/redeploy 不打：目标是既有的已部署发布，标签已存在，再按当天日期打只会给
    # 同一 SHA 造出第二个标签。
    assert "if: env.MODE == 'auto' || env.MODE == 'migrate'\n" in sec, \
        f"{source}: deploy/ tag 仅 auto 与 migrate 打"
    assert 'git ls-remote --tags origin "refs/tags/$TAG"' in sec, f"{source}: tag 已存在必须跳过"
    # 手动模式没有来源 CI run（HEAD_SHA 为空），改取 release-id 的 sha7；两种模式都先
    # rev-parse 成完整 commit——解析不到就让步骤红，不静默漏打标签。
    assert 'git rev-parse --verify "${HEAD_SHA:-${RELEASE_ID%%-*}}^{commit}"' in sec, \
        f"{source}: tag 目标必须先解析为完整 commit SHA"
    assert 'git tag "$TAG" "$SHA"' in sec, f"{source}: tag 必须打在解析过的 commit 上"


ALL_JUDGMENTS = [
    assert_no_build, assert_event_guard, assert_source_verification, assert_bundle_consumption,
    assert_ancestry_chain, assert_ssh_hardening, assert_input_hygiene, assert_lock_stale_args,
    assert_failure_guidance, assert_permissions, assert_manual_modes, assert_tag_gated,
]


def test_real_deploy_workflow_passes_all_judgments():
    text = _deploy_text()
    for judgment in ALL_JUDGMENTS:
        judgment(text)


# ------------------------------------------------------------------ 反例变异
def _mutate(old, new):
    def mutate(text):
        assert text.count(old) >= 1, f"变异锚点不存在: {old!r}"
        return text.replace(old, new, 1)
    return mutate


MUTATIONS = [
    # CD 重新长出构建步骤
    (_mutate("      - name: Checkout code",
             "      - uses: docker/build-push-action@v7\n      - name: Checkout code"),
     assert_no_build),
    # 来源验证退化为只按 run id（attempt 被偷换也不可见）
    (_mutate("actions/runs/$CI_RUN_ID/attempts/$CI_RUN_ATTEMPT", "actions/runs/$CI_RUN_ID"),
     assert_source_verification),
    # 主机指纹校验被"临时"关掉
    (_mutate("StrictHostKeyChecking=yes", "StrictHostKeyChecking=no"), assert_ssh_hardening),
    # 锁内旧任务重验被省略
    (_mutate("--expect-accepted $PREV_SPEC", ""), assert_lock_stale_args),
    # inputs 被直接内插进 shell（注入面）
    (_mutate('          PREV_SHA="$(awk',
             '          echo "${{ github.event.inputs.release_id }}"\n          PREV_SHA="$(awk'),
     assert_input_hygiene),
    # #456 的 event=='push' 守卫被删
    (_mutate("       github.event.workflow_run.event == 'push') ||", ") ||"),
     assert_event_guard),
    # deploy/ tag 对手动回滚/重部署也打（污染回滚目标视图，同一 SHA 出现双标签）
    (_mutate("      - name: Tag deployed commit\n        if: env.MODE == 'auto' || env.MODE == 'migrate'",
             "      - name: Tag deployed commit"), assert_tag_gated),
    # 发布包校验被跳过（直接信任 artifact 内容）
    (_mutate('python3 scripts/release_bundle.py verify --bundle-dir bundle --sha "$HEAD_SHA"',
             "true"), assert_bundle_consumption),
    # 误以为 gh 支持按 attempt 下载（runner 实际无该标志，生产 CD 会直接红）
    (_mutate('gh run download "$CI_RUN_ID" --name release-bundle --dir bundle',
             'gh run download "$CI_RUN_ID" --attempt "$CI_RUN_ATTEMPT" \\\n'
             '               --name release-bundle --dir bundle'), assert_bundle_consumption),
    # 包身份互证被拆掉（错误 attempt 的包不再现形——attempt 绑定失去唯一执行点）
    (_mutate("if rid != sys.argv[1]:", "if False:"), assert_bundle_consumption),
    # 祖先判据被注释掉（旧任务晚到不再被拦）
    (_mutate("python3 scripts/deploy_ancestry.py", "true #"), assert_ancestry_chain),
    # 顶层权限被放宽
    (_mutate("permissions:\n  contents: read\n  actions: read",
             "permissions:\n  contents: write\n  actions: write"), assert_permissions),
]


@pytest.mark.parametrize("mutate,judgment", MUTATIONS,
                         ids=[j.__name__ + str(i) for i, (_, j) in enumerate(MUTATIONS)])
def test_weakening_mutations_are_caught(mutate, judgment):
    with pytest.raises(AssertionError):
        judgment(mutate(_deploy_text()))
