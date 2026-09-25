"""scripts/smoke_images.py 的回归测试：不依赖 Docker。

外部世界通过三个接缝注入：_subprocess_run（docker/openssl）、_http_request（探针）、
_monotonic/_sleep（时钟）。守门重点：
- 身份先于一切资源创建（标签/digest 不符时不留任何容器）；
- 引导链顺序 status(empty)→prepare(--expect-state)→check→seed，迁移/运行账号分离，
  运行账号最小权限清单逐字钉死；
- HTTPS 探针必须携带本次生成的 CA 文件（不允许退回关闭 TLS 校验）；
- fail(1)/error(2)/中断(130) 三态区分；失败仍清理，--keep-on-failure 只保留失败现场；
- 报告与 stdout 不泄漏任何一次性口令。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(Path(__file__).resolve().parent)]
import smoke_images as smoke  # noqa: E402

REVISION = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
BACKEND_REF = f"registry.example.invalid/ns/investring-backend@sha256:{'ab' * 32}"
FRONTEND_REF = f"registry.example.invalid/ns/investring-frontend@sha256:{'cd' * 32}"

EXPECTED_CHECKS = [
    "docker-available", "backend-image-identity", "frontend-image-identity",
    "mysql-ready", "database-accounts", "bootstrap-status-empty", "bootstrap-prepare",
    "bootstrap-check", "seed-e2e", "backend-runtime-up", "frontend-up",
    "frontend-api-proxy-login", "nginx-up", "nginx-http-redirect",
    "nginx-https-health", "nginx-https-frontend", "nginx-https-login",
]

PORTS = {("backend", "8000/tcp"): 49101, ("frontend", "7860/tcp"): 49102,
         ("nginx", "80/tcp"): 49103, ("nginx", "443/tcp"): 49104}

STATUS_PAYLOAD = {
    "state": "empty", "fingerprint": "fp-before", "migration_fingerprint": "mig-fp",
    "expected_heads": ["0017"], "dialect": "mysql",
}


def proc(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class FakeDocker:
    """按角色分发 docker/openssl 调用；全部调用记录在 calls。"""

    def __init__(self):
        self.calls = []                 # (args, env, input_text)
        self.running = True
        self.not_running = set()        # 按角色名（backend/frontend/…）单独标记已退出
        self.empty_port_roles = ()      # 这些角色的 docker port 返回空（模拟秒退）
        self.logs = {}
        self.backend_labels = {smoke.REVISION_LABEL: REVISION}
        self.frontend_labels = {smoke.REVISION_LABEL: REVISION}
        self.repo_digests = {"backend": [BACKEND_REF], "frontend": [FRONTEND_REF],
                             "nginx": [f"nginx@sha256:{'ef' * 32}"]}
        self.one_shot = {
            "status": proc(0, json.dumps(STATUS_PAYLOAD)),
            "prepare": proc(0, json.dumps({"state": "ready", "fingerprint": "fp-after"})),
            "check": proc(0, json.dumps({"state": "ready"})),
            "seed": proc(0, "E2E 种子完成"),
        }
        self.image_inspect_rc = 0

    def inspect_payload(self, ref):
        if "nginx" in ref:
            # nginx 基础镜像无 revision 标签：冒烟只记录其 digest 供发布包固定
            return json.dumps({"Config": {"Labels": {}},
                               "RepoDigests": self.repo_digests["nginx"], "Id": "sha256:nginx"})
        role = "backend" if "backend" in ref else "frontend"
        labels = self.backend_labels if role == "backend" else self.frontend_labels
        return json.dumps({"Config": {"Labels": labels},
                           "RepoDigests": self.repo_digests[role], "Id": f"sha256:{role}"})

    def __call__(self, command, *, input_text=None, env=None, timeout=None):
        args = [str(part) for part in command]
        self.calls.append((args, env, input_text))
        if args[0] == "openssl":
            return proc(0)
        if args[0] != "docker":
            raise AssertionError(f"未预期的外部命令: {args}")
        return self.dispatch(args[1:])

    def dispatch(self, args):
        if args[0] == "version":
            return proc(0, "24.0.7\n")
        if args[:2] == ["image", "inspect"]:
            if self.image_inspect_rc:
                return proc(self.image_inspect_rc, "", "Error: No such image")
            return proc(0, self.inspect_payload(args[2]))
        if args[0] == "network":
            return proc(0, "net-created\n" if args[1] == "create" else "")
        if args[0] == "run":
            if "--rm" in args:
                for key, marker in (("status", "status"), ("prepare", "prepare"),
                                    ("check", "check"), ("seed", "seed_e2e.py")):
                    if any(marker == arg or marker in arg for arg in args):
                        return self.one_shot[key]
                raise AssertionError(f"未预期的一次性容器命令: {args}")
            return proc(0, "container-id\n")
        if args[0] == "exec":
            if "mysqladmin" in args:
                return proc(0, "mysqld is alive\n")
            if "mysql" in args:
                return proc(0)
            raise AssertionError(f"未预期的 exec: {args}")
        if args[0] == "inspect":
            target = args[-1]
            if any(role in target for role in self.not_running):
                return proc(0, "false")
            return proc(0, "true" if self.running else "false")
        if args[0] == "port":
            role = next(role for role in ("backend", "frontend", "nginx") if role in args[1])
            if role in self.empty_port_roles:
                return proc(0, "")
            return proc(0, f"127.0.0.1:{PORTS[(role, args[2])]}\n")
        if args[0] == "logs":
            return proc(0, self.logs.get(args[-1], ""), "")
        if args[:2] == ["rm", "-f"]:
            return proc(0)
        raise AssertionError(f"未预期的 docker 子命令: {args}")

    def calls_with(self, *fragments):
        # 整参精确匹配：子串匹配会把 `docker version --format`（含 "rm" 与 "-f"）误判成 rm -f
        return [args for args, _, _ in self.calls if all(f in args for f in fragments)]


def default_http(url, *, method="GET", payload=None, timeout=None, cafile=None):
    login = json.dumps({"token": "tok", "expires_at": "x",
                        "user": {"code": "ADMIN", "name": "A", "role": "admin"}})
    table = {
        ("GET", "http://127.0.0.1:49101/health"): (200, '{"status":"healthy"}', {}),
        ("GET", "http://127.0.0.1:49102/login"): (200, "<html>", {}),
        ("POST", "http://127.0.0.1:49102/api/auth/login"): (200, login, {}),
        ("GET", "http://127.0.0.1:49103/health"): (301, "", {"Location": "https://investring.top/health"}),
        ("GET", "http://127.0.0.1:49103/anything"): (301, "", {"Location": "https://investring.top/anything"}),
        ("GET", "https://127.0.0.1:49104/health"): (200, "{}", {}),
        ("GET", "https://127.0.0.1:49104/login"): (200, "<html>", {}),
        ("POST", "https://127.0.0.1:49104/api/auth/login"): (200, login, {}),
    }
    key = (method, url)
    if key not in table:
        raise AssertionError(f"未预期的 HTTP 探针: {key}")
    if key[1].startswith("https://") and cafile is None:
        raise AssertionError("HTTPS 探针必须携带本次生成的 CA 文件（不允许关闭 TLS 校验）")
    return table[key]


class FakeClock:
    def __init__(self, step=0.1):
        self.now = 0.0
        self.step = step
        self.slept = []

    def monotonic(self):
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def harness(tmp_path, monkeypatch):
    def install(*, http=default_http, clock_step=0.1, docker_missing=False, which=None):
        fake = FakeDocker()
        clock = FakeClock(clock_step)
        monkeypatch.setattr(smoke, "_subprocess_run", fake)
        monkeypatch.setattr(smoke, "_http_request", http)
        monkeypatch.setattr(smoke, "_monotonic", clock.monotonic)
        monkeypatch.setattr(smoke, "_sleep", clock.sleep)
        if which is None:
            which = lambda name: None if docker_missing else f"/usr/bin/{name}"  # noqa: E731
        monkeypatch.setattr(smoke.shutil, "which", which)
        workdir = tmp_path / "workdir"

        def fake_mkdtemp(prefix=None):
            # 真 mkdtemp 会创建目录：cleanup 的 removed_workdir 与 keep 的存在性断言都依赖这一点
            workdir.mkdir(parents=True, exist_ok=True)
            return str(workdir)

        monkeypatch.setattr(smoke.tempfile, "mkdtemp", fake_mkdtemp)
        return fake, clock, workdir

    return install


def run_main(tmp_path, *extra):
    argv = ["--backend-ref", BACKEND_REF, "--frontend-ref", FRONTEND_REF,
            "--expected-revision", REVISION, "--report", str(tmp_path / "report.json"), *extra]
    return smoke.main(argv)


def read_report(tmp_path):
    return json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))


def check_names(report):
    return [entry["name"] for entry in report["checks"]]


# ---------------------------------------------------------------------- 快乐路径
def test_happy_path_passes_and_cleans_up(tmp_path, harness):
    fake, _, workdir = harness()
    assert run_main(tmp_path) == 0
    report = read_report(tmp_path)
    assert report["ok"] is True and report["error"] is None
    assert check_names(report) == EXPECTED_CHECKS
    assert all(entry["ok"] for entry in report["checks"])
    assert report["images"]["backend"]["digest"] == f"sha256:{'ab' * 32}"
    assert report["images"]["frontend"]["revision_label"] == REVISION
    # nginx 基础镜像 digest 一并入报告：发布包三镜像齐备（#537 第四批 C）
    assert report["images"]["nginx"]["digest"] == f"sha256:{'ef' * 32}"
    assert report["images"]["nginx"]["digest_ref"] == f"nginx@sha256:{'ef' * 32}"
    assert report["bootstrap"]["migration_fingerprint"] == "mig-fp"
    assert report["bootstrap"]["expected_heads"] == ["0017"]
    assert report["bootstrap"]["fingerprint_after_prepare"] == "fp-after"
    # 清理：4 个长驻容器 + 网络 + 工作目录（一次性容器用 --rm 不追踪）
    assert len(fake.calls_with("rm", "-f")) == 4
    assert report["cleanup"]["removed_network"] is True
    assert report["cleanup"]["kept"] is False and report["cleanup"]["removed_workdir"] is True
    assert not workdir.exists()


def test_resource_order_and_least_privilege_accounts(tmp_path, harness):
    fake, _, _ = harness()
    assert run_main(tmp_path) == 0
    run_calls = [args for args, _, _ in fake.calls if args[:2] == ["docker", "run"]]
    order = []
    for args in run_calls:
        if "mysql:8.4" in args:
            order.append("mysql")
        elif "--rm" in args:
            order.append("oneshot")
        elif BACKEND_REF in args:
            order.append("backend")
        elif FRONTEND_REF in args:
            order.append("frontend")
        else:
            order.append("nginx")
    # MySQL 先于引导，引导先于后端运行容器，后端先于 nginx（upstream 按名解析）
    assert order[0] == "mysql"
    assert order.count("oneshot") == 4
    assert order.index("backend") == max(i for i, role in enumerate(order) if role == "oneshot") + 1
    assert order.index("nginx") > order.index("backend")
    # 网络别名：前端 rewrite 与 nginx upstream 按生产主机名解析
    # （引导一次性容器同样以 BACKEND_REF 运行，须按 --rm 排除）
    backend_args = next(args for args in run_calls if BACKEND_REF in args and "--rm" not in args)
    assert backend_args[backend_args.index("--network-alias") + 1] == "backend"
    frontend_args = next(args for args in run_calls if FRONTEND_REF in args)
    assert frontend_args[frontend_args.index("--network-alias") + 1] == "frontend"
    # 引导一次性容器用迁移账号；运行容器用仅 DML 账号
    oneshot_envs = [env for args, env, _ in fake.calls
                    if args[:2] == ["docker", "run"] and "--rm" in args and env]
    assert len(oneshot_envs) == 4
    assert all("ir_migrate" in env["DATABASE_URL"] for env in oneshot_envs)
    backend_env = next(env for args, env, _ in fake.calls
                       if args[:2] == ["docker", "run"] and BACKEND_REF in args and "--rm" not in args)
    assert "ir_runtime" in backend_env["DATABASE_URL"] and backend_env["SECRET_KEY"]
    assert "SCHEDULER_ENABLED=true" in backend_args          # 调度器随真实启动路径受检
    # prepare 必须携带 status 的指纹（显式授权语义）
    prepare = next(args for args, _, _ in fake.calls if "--expect-state" in args)
    assert prepare[prepare.index("--expect-state") + 1] == "fp-before"
    # 最小权限清单逐字钉死：运行账号不得出现任何 DDL 权限
    sql_inputs = [text for _, _, text in fake.calls if text and "CREATE DATABASE" in text]
    assert len(sql_inputs) == 1
    sql = " ".join(sql_inputs[0].split())
    assert ("GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES"
            " ON ir_smoke.* TO 'ir_migrate'@'%';") in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ir_smoke.* TO 'ir_runtime'@'%';" in sql
    assert sql.count("GRANT") == 2 and "WITH GRANT OPTION" not in sql


def test_report_written_to_stdout_and_no_secret_leaks(tmp_path, harness, capsys):
    fake, _, _ = harness()
    assert run_main(tmp_path) == 0
    secrets_seen = set()
    for _, env, _ in fake.calls:
        if not env:
            continue
        for key in ("DATABASE_URL", "SECRET_KEY", "MYSQL_ROOT_PASSWORD", "MYSQL_PWD"):
            for part in re.split(r"[:/@]", env.get(key, "")):
                if len(part) == 32:
                    secrets_seen.add(part)
    assert secrets_seen, "测试自身应捕获到一次性口令用于泄漏断言"
    out = capsys.readouterr().out
    assert out.strip() == (tmp_path / "report.json").read_text(encoding="utf-8").strip()
    for value in secrets_seen:
        assert value not in out


def test_tag_refs_without_digest_allowed(tmp_path, harness):
    fake, _, _ = harness()
    fake.repo_digests = {"backend": [], "frontend": [], "nginx": []}
    argv = ["--backend-ref", "investring-backend:smoke", "--frontend-ref", "investring-frontend:smoke",
            "--expected-revision", REVISION, "--report", str(tmp_path / "report.json")]
    assert smoke.main(argv) == 0
    report = read_report(tmp_path)
    assert report["ok"] is True
    assert report["images"]["backend"]["digest"] is None
    assert report["images"]["frontend"]["digest"] is None
    assert report["images"]["nginx"]["digest"] is None


# ---------------------------------------------------------------------- 参数校验
@pytest.mark.parametrize("argv", [
    ["--backend-ref", BACKEND_REF, "--frontend-ref", FRONTEND_REF, "--expected-revision", "abc"],
    ["--backend-ref", BACKEND_REF, "--frontend-ref", FRONTEND_REF, "--expected-revision", REVISION + "0"],
    ["--backend-ref", " repo@sha256:" + "a" * 64, "--frontend-ref", FRONTEND_REF, "--expected-revision", REVISION],
    ["--backend-ref", "repo@sha256:zz" + "a" * 62, "--frontend-ref", FRONTEND_REF, "--expected-revision", REVISION],
    ["--backend-ref", "", "--frontend-ref", FRONTEND_REF, "--expected-revision", REVISION],
])
def test_invalid_arguments_exit_two_without_docker(argv, harness):
    fake, _, _ = harness()
    with pytest.raises(SystemExit) as exc:
        smoke.main(argv)
    assert exc.value.code == 2
    assert fake.calls == []


def test_uppercase_revision_normalized(tmp_path, harness):
    harness()
    argv = ["--backend-ref", BACKEND_REF, "--frontend-ref", FRONTEND_REF,
            "--expected-revision", REVISION.upper(), "--report", str(tmp_path / "report.json")]
    assert smoke.main(argv) == 0
    assert read_report(tmp_path)["expected_revision"] == REVISION


# ---------------------------------------------------------------------- 身份失败
def test_revision_label_mismatch_fails_before_any_resource(tmp_path, harness):
    fake, _, _ = harness()
    fake.backend_labels = {smoke.REVISION_LABEL: "f" * 40}
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert report["ok"] is False
    assert check_names(report) == ["docker-available", "backend-image-identity"]
    assert report["checks"][-1]["ok"] is False and "期望" in report["checks"][-1]["detail"]
    # 身份失败前不得创建任何容器/网络（所有权纪律）
    assert fake.calls_with("run") == [] and fake.calls_with("network", "create") == []


def test_missing_revision_label_fails(tmp_path, harness):
    fake, _, _ = harness()
    fake.frontend_labels = {}
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "frontend-image-identity"
    assert "GIT_REVISION" in report["checks"][-1]["detail"]


def test_digest_not_in_repo_digests_fails(tmp_path, harness):
    fake, _, _ = harness()
    fake.repo_digests["backend"] = [f"registry.example.invalid/ns/investring-backend@sha256:{'ee' * 32}"]
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert "RepoDigests" in report["checks"][-1]["detail"]


def test_image_absent_is_harness_error(tmp_path, harness):
    fake, _, _ = harness()
    fake.image_inspect_rc = 1
    assert run_main(tmp_path) == 2
    report = read_report(tmp_path)
    assert "不隐式 pull" in report["error"] and report["ok"] is False


def test_docker_missing_is_harness_error(tmp_path, harness):
    harness(docker_missing=True)
    assert run_main(tmp_path) == 2
    assert "docker" in read_report(tmp_path)["error"]


# ---------------------------------------------------------------------- 引导链失败
def test_non_empty_state_refuses_prepare(tmp_path, harness):
    fake, _, _ = harness()
    fake.one_shot["status"] = proc(0, json.dumps({**STATUS_PAYLOAD, "state": "migration_required"}))
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "bootstrap-status-empty"
    assert "migration_required" in report["checks"][-1]["detail"]
    assert fake.calls_with("prepare") == []                  # 不得继续准备
    assert fake.calls_with("run", "-d", BACKEND_REF) == []   # 不得启动运行容器
    assert len(fake.calls_with("rm", "-f")) == 1             # 已建的 mysql 容器仍被清理


def test_prepare_failure_reports_detail(tmp_path, harness):
    fake, _, _ = harness()
    fake.one_shot["prepare"] = proc(2, "", "BootstrapError: state changed")
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "bootstrap-prepare"
    assert "BootstrapError" in report["checks"][-1]["detail"]


def test_prepare_not_ready_state_fails(tmp_path, harness):
    fake, _, _ = harness()
    fake.one_shot["prepare"] = proc(0, json.dumps({"state": "schema_incomplete"}))
    assert run_main(tmp_path) == 1
    assert check_names(read_report(tmp_path))[-1] == "bootstrap-prepare"


def test_seed_failure_fails_and_cleans(tmp_path, harness):
    fake, _, _ = harness()
    fake.one_shot["seed"] = proc(1, "", "boom")
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "seed-e2e"
    assert report["cleanup"]["kept"] is False                # 未指定 keep 时失败也清理
    assert len(fake.calls_with("rm", "-f")) == 1


def test_seed_mounts_repo_tests_readonly_and_backend_artifact_stays_pure(tmp_path, harness):
    """生产镜像不带测试代码：seed 只读挂载仓库 backend/tests；其余后端镜像调用零挂载。"""
    fake, _, _ = harness()
    assert run_main(tmp_path) == 0
    seed_calls = fake.calls_with("scripts/seed_e2e.py")
    assert len(seed_calls) == 1
    args = seed_calls[0]
    mount = args[args.index("-v") + 1]
    assert mount == f"{smoke.ROOT / 'backend' / 'tests'}:/app/tests:ro"
    backend_calls = [argv for argv, _, _ in fake.calls if BACKEND_REF in argv]
    assert len(backend_calls) >= 5                           # status/prepare/check/seed + 长驻启动
    with_mount = [argv for argv in backend_calls if "-v" in argv]
    assert len(with_mount) == 1 and "scripts/seed_e2e.py" in with_mount[0]


def test_backend_exited_before_port_ready_records_check_failure(tmp_path, harness):
    """backend 在端口映射就绪前退出：属制品/接线检查失败（exit 1），且必须带日志尾部。"""
    fake, _, _ = harness()
    fake.empty_port_roles = ("backend",)
    fake.not_running = {"backend"}
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert report["ok"] is False and report["error"] is None
    assert check_names(report)[-1] == "backend-runtime-up"
    detail = report["checks"][-1]["detail"]
    assert "端口映射" in detail and "已退出" in detail and "日志尾部" in detail


def test_port_mapping_never_appearing_is_harness_error(tmp_path, harness):
    """容器活着但映射迟迟不出现：harness/环境问题（exit 2），不伪装成检查失败。"""
    fake, _, _ = harness()
    fake.empty_port_roles = ("backend",)
    assert run_main(tmp_path) == 2
    report = read_report(tmp_path)
    assert report["ok"] is False and "超时" in report["error"]


# ---------------------------------------------------------------------- 运行期失败
def test_backend_never_healthy_times_out(tmp_path, harness):
    def http(url, **kwargs):
        if "49101" in url:
            raise OSError("connection refused")
        return default_http(url, **kwargs)

    # 只 install 一次：重复 install 会换上全新 FakeClock，先拿到的引用将永远为空
    _, clock, _ = harness(http=http, clock_step=45.0)
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "backend-runtime-up"
    assert "未就绪" in report["checks"][-1]["detail"]
    assert clock.slept, "等待循环必须休眠轮询"


def test_dead_container_fails_fast(tmp_path, harness):
    fake, _, _ = harness()
    fake.running = False
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    failed = report["checks"][-1]
    assert failed["name"] == "mysql-ready"                   # 首个存活检查即失败
    assert "已退出" in failed["detail"]


def test_runtime_password_masked_in_log_tail(tmp_path, harness, monkeypatch):
    fake, _, _ = harness()
    captured = {}

    def wrapper(command, *, input_text=None, env=None, timeout=None):
        result = fake(command, input_text=input_text, env=env, timeout=timeout)
        args = [str(part) for part in command]
        if (args[:2] == ["docker", "run"] and "-d" in args and env
                and "ir_runtime" in env.get("DATABASE_URL", "")):
            password = env["DATABASE_URL"].split("ir_runtime:")[1].split("@")[0]
            captured["password"] = password
            name = args[args.index("--name") + 1]
            fake.logs[name] = f"connection failed for ir_runtime password {password}"
            fake.running = False
        return result

    monkeypatch.setattr(smoke, "_subprocess_run", wrapper)
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "backend-runtime-up"
    assert captured["password"] not in (tmp_path / "report.json").read_text(encoding="utf-8")
    assert "***" in report["checks"][-1]["detail"]


def test_login_rejected_through_frontend_proxy_fails(tmp_path, harness):
    def http(url, *, method="GET", payload=None, timeout=None, cafile=None):
        if method == "POST" and url == "http://127.0.0.1:49102/api/auth/login":
            return 401, '{"detail":"口令错误"}', {}
        return default_http(url, method=method, payload=payload, timeout=timeout, cafile=cafile)

    harness(http=http)
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "frontend-api-proxy-login"
    assert "401" in report["checks"][-1]["detail"]


def test_login_without_token_fails(tmp_path, harness):
    def http(url, *, method="GET", payload=None, timeout=None, cafile=None):
        if method == "POST":
            return 200, json.dumps({"user": {"code": "ADMIN"}}), {}
        return default_http(url, method=method, payload=payload, timeout=timeout, cafile=cafile)

    harness(http=http)
    assert run_main(tmp_path) == 1
    assert check_names(read_report(tmp_path))[-1] == "frontend-api-proxy-login"


def test_nginx_redirect_regression_fails(tmp_path, harness):
    def http(url, *, method="GET", payload=None, timeout=None, cafile=None):
        if url == "http://127.0.0.1:49103/anything":
            return 200, "should-be-301", {}
        return default_http(url, method=method, payload=payload, timeout=timeout, cafile=cafile)

    harness(http=http)
    assert run_main(tmp_path) == 1
    report = read_report(tmp_path)
    assert check_names(report)[-1] == "nginx-http-redirect"
    assert "301" in report["checks"][-1]["detail"]


def test_https_health_failure_fails(tmp_path, harness):
    def http(url, *, method="GET", payload=None, timeout=None, cafile=None):
        if url == "https://127.0.0.1:49104/health":
            return 502, "bad gateway", {}
        return default_http(url, method=method, payload=payload, timeout=timeout, cafile=cafile)

    harness(http=http)
    assert run_main(tmp_path) == 1
    assert check_names(read_report(tmp_path))[-1] == "nginx-https-health"


def test_openssl_missing_is_harness_error(tmp_path, harness):
    fake, _, _ = harness(which=lambda name: None if name == "openssl" else f"/usr/bin/{name}")
    assert run_main(tmp_path) == 2
    assert "openssl" in read_report(tmp_path)["error"]
    assert len(fake.calls_with("rm", "-f")) == 3             # mysql/backend/frontend 已建并清理


# ---------------------------------------------------------------------- 清理与保留
def test_keep_on_failure_preserves_scene(tmp_path, harness):
    fake, _, workdir = harness()
    fake.one_shot["status"] = proc(0, json.dumps({**STATUS_PAYLOAD, "state": "ready"}))
    assert run_main(tmp_path, "--keep-on-failure") == 1
    report = read_report(tmp_path)
    assert report["cleanup"]["kept"] is True
    assert fake.calls_with("rm", "-f") == [] and fake.calls_with("network", "rm") == []
    assert workdir.exists()


def test_keep_on_failure_still_cleans_success(tmp_path, harness):
    fake, _, _ = harness()
    assert run_main(tmp_path, "--keep-on-failure") == 0
    report = read_report(tmp_path)
    assert report["cleanup"]["kept"] is False
    assert len(fake.calls_with("rm", "-f")) == 4


def test_interrupt_cleans_up_and_exits_130(tmp_path, harness):
    def http(url, **kwargs):
        raise KeyboardInterrupt

    # 只 install 一次：重复 install 会换上全新 FakeDocker，先拿到的引用将录不到任何调用
    fake, _, _ = harness(http=http)
    assert run_main(tmp_path) == 130
    report = read_report(tmp_path)
    assert report["error"] == "已中断"
    assert len(fake.calls_with("rm", "-f")) == 2             # 中断时已建 mysql/backend
    assert report["cleanup"]["removed_network"] is True


def test_docker_command_timeout_is_harness_error(tmp_path, harness, monkeypatch):
    fallback = FakeDocker()

    def timeout_run(command, *, input_text=None, env=None, timeout=None):
        if command[0] == "docker" and command[1] == "version":
            raise subprocess.TimeoutExpired(cmd=command, timeout=1)
        return fallback(command, input_text=input_text, env=env, timeout=timeout)

    harness()
    monkeypatch.setattr(smoke, "_subprocess_run", timeout_run)
    assert run_main(tmp_path) == 2
    assert "超时" in read_report(tmp_path)["error"]
