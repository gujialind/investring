# ============================================================================
# image-smoke 发布链守门（issue #537 第四批 B/C）
# ============================================================================
# image-smoke 是「构建一次 → 按精确身份冒烟 → 生成可恢复发布包」的唯一生产者，
# CD 侧（deploy.yml）只消费它的产物。这道链上有几个**静默削弱不会让任何测试变红**
# 的关节，只能钉在 workflow 文本层：
#
#   1. 注册表登录必须只在 publish（可信同仓 main push）侧——门被摘掉等于 PR 拿发布凭据；
#   2. publish 判定必须是 `GITHUB_EVENT_NAME = push` 且 ACR 配置齐——放宽到
#      workflow_dispatch/PR 就把「可信构建」的边界拆了；
#   3. 构建必须注入 GIT_REVISION=github.sha——冒烟按 OCI revision 标签认制品，
#      摘掉它 identity 检查会红，但换成别的表达式（如 run_id）则冒烟照过、身份错绑；
#   4. 发布侧必须按 digest 拉回再冒烟——直接冒烟 build 缓存里的标签引用，
#      「冒烟的制品 == 发布的制品」这条等式就断了；
#   5. 发布包必须 build 后立即 verify、且只在 publish 侧生成/上传——verify 被摘掉，
#      生成器回归要等到部署时才现形；
#   6. provenance/sbom 必须保持 false（ACR 个人版限制，issue #141 的教训）。
#
# 与 test_ci_mysql_account.py / test_ci_path_mapping.py 同族：不解析 YAML 语义，
# 用 _ci_text 按缩进切块做结构断言；判定收在模块级函数里，反例对合成文本跑同一套
# 判据——否则断言因 workflow 重构而空跑时，本身就成了不会红的门禁。
# job 级结构（needs/无 if:/ci-ok 收口）由 test_ci_gate.assert_workflow 钉，不在此重复。
# ============================================================================

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ci_text import job_block, step_body, step_run_block  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
JOB = "image-smoke"
SOURCE = f"ci.yml:{JOB}"
PUBLISH_GATE = "if: steps.mode.outputs.publish == 'true'"


def block(text):
    return job_block(text, job=JOB, source=SOURCE)


def step(text, name):
    return step_body(text, step=name, source=SOURCE)


def run(text, name):
    return step_run_block(text, step=name, source=SOURCE)


def assert_publish_gated(text, name):
    body = step(text, name)
    assert re.search(rf"^        {re.escape(PUBLISH_GATE)}$", body, re.M), \
        f"{name} 缺少 publish 门（{PUBLISH_GATE}）"


def assert_release_chain(text):
    blk = block(text)

    # 1. 登录只在发布侧，且用 login-action（凭据不经 shell 明文）
    login = step(blk, "Login to Aliyun ACR")
    assert "uses: docker/login-action@" in login
    assert re.search(rf"^        {re.escape(PUBLISH_GATE)}$", login, re.M), "登录步骤必须带 publish 门"

    # 2. publish 判定：仅同仓 main push 且 ACR 配置齐备
    mode = run(blk, "Resolve image mode")
    assert '[ "$GITHUB_EVENT_NAME" = "push" ]' in mode, "publish 必须限定 push 事件"
    assert '-n "$ACR_REGISTRY"' in mode and '-n "$ACR_NAMESPACE"' in mode

    # 3. 构建：push/load 互斥门 + GIT_REVISION 身份注入 + ACR attestation 限制
    for name in ("Build backend image", "Build frontend image"):
        build = step(blk, name)
        assert "push: ${{ steps.mode.outputs.publish == 'true' }}" in build, name
        assert "load: ${{ steps.mode.outputs.publish != 'true' }}" in build, name
        assert "GIT_REVISION=${{ github.sha }}" in build, f"{name} 缺少身份注入"
        assert "provenance: false" in build and "sbom: false" in build, name

    # 4. 发布侧按 digest 拉回：冒烟对象 == 已发布制品，标签重指偷换不了
    refs = run(blk, "Resolve smoke refs")
    assert 'investring-backend@$BACKEND_DIGEST' in refs
    assert 'investring-frontend@$FRONTEND_DIGEST' in refs
    assert 'docker pull "$BACKEND_REF"' in refs and 'docker pull "$FRONTEND_REF"' in refs

    # 5. 冒烟：期望身份必须来自 github.sha，报告落盘供发布包与诊断产物消费
    smoke = step(blk, "Smoke images")
    assert re.search(r"EXPECTED_REVISION: \$\{\{ github\.sha \}\}", smoke), \
        "冒烟期望身份必须绑定 github.sha"
    smoke_run = run(blk, "Smoke images")
    assert "scripts/smoke_images.py" in smoke_run
    assert '--expected-revision "$EXPECTED_REVISION"' in smoke_run
    assert "--report smoke-report.json" in smoke_run

    # 6. 发布包：publish 门 + build 后立即 verify（同一 run 块，先后有序）
    assert_publish_gated(blk, "Build release bundle")
    bundle_run = run(blk, "Build release bundle")
    assert "scripts/release_bundle.py build" in bundle_run
    assert "scripts/release_bundle.py verify" in bundle_run
    assert bundle_run.index("release_bundle.py build") < bundle_run.index("release_bundle.py verify")
    assert_publish_gated(blk, "Upload release bundle")
    assert "name: release-bundle" in step(blk, "Upload release bundle")

    # 7. 诊断报告失败也上传，且缺文件即红（不静默丢证据）
    report_upload = step(blk, "Upload smoke report")
    assert "if: always()" in report_upload
    assert "if-no-files-found: error" in report_upload


def test_image_smoke_release_chain():
    assert_release_chain(CI_YML.read_text())


@pytest.mark.parametrize("old,new", [
    # 登录门被摘：PR 拿到发布凭据
    (f"      - name: Login to Aliyun ACR\n        {PUBLISH_GATE}\n",
     "      - name: Login to Aliyun ACR\n"),
    # publish 判定放宽：任何事件都发布
    ('if [ "$GITHUB_EVENT_NAME" = "push" ]', 'if [ -n "$GITHUB_EVENT_NAME" ]'),
    # 构建改成无条件推送 / 身份注入被摘或换绑
    ("push: ${{ steps.mode.outputs.publish == 'true' }}", "push: true"),
    ("GIT_REVISION=${{ github.sha }}", "GIT_REVISION=${{ github.run_id }}"),
    ("GIT_REVISION=${{ github.sha }}\n", ""),
    # attestation 重新打开（ACR 个人版会拒推，#141）。
    # 取材带 cache-to scope=backend 上下文：`provenance: false` 在 docker-build-smoke
    # 里也出现，裸串 replace(…,1) 会改错块、反例空转成假绿。
    ("cache-to: type=gha,mode=max,scope=backend\n"
     "          # ACR 个人版不支持 OCI attestation（issue #141）\n"
     "          provenance: false",
     "cache-to: type=gha,mode=max,scope=backend\n"
     "          # ACR 个人版不支持 OCI attestation（issue #141）\n"
     "          provenance: true"),
    # digest 拉回被摘：冒烟的不再是已发布制品
    ('docker pull "$BACKEND_REF"\n', ""),
    ('BACKEND_REF="$ACR_REGISTRY/$ACR_NAMESPACE/investring-backend@$BACKEND_DIGEST"',
     'BACKEND_REF="$ACR_REGISTRY/$ACR_NAMESPACE/investring-backend:latest"'),
    # 冒烟身份来源被换绑 / 报告不落盘
    ("EXPECTED_REVISION: ${{ github.sha }}", "EXPECTED_REVISION: ${{ github.run_id }}"),
    ('--report smoke-report.json\n', ""),
    # 发布包 verify 被摘 / 门被摘
    ("          python3 scripts/release_bundle.py verify \\\n", ""),
    (f"      - name: Build release bundle\n        {PUBLISH_GATE}\n",
     "      - name: Build release bundle\n"),
    (f"      - name: Upload release bundle\n        {PUBLISH_GATE}\n",
     "      - name: Upload release bundle\n"),
    # 诊断产物静默丢失（同样带唯一上下文：Upload release bundle 也有同名键）
    ("name: image-smoke-report\n"
     "          path: smoke-report.json\n"
     "          if-no-files-found: error",
     "name: image-smoke-report\n"
     "          path: smoke-report.json\n"
     "          if-no-files-found: warn"),
])
def test_release_chain_rejects_silent_weakening(old, new):
    text = CI_YML.read_text()
    assert old in text, f"反例取材失效，请同步更新守门：{old!r}"
    with pytest.raises(AssertionError):
        assert_release_chain(text.replace(old, new, 1))
