#!/usr/bin/env python3
"""最终镜像运行冒烟（issue #537 第四批 B）。

对「确定身份的最终镜像」做小型运行验证，而不是把 Docker build 成功当运行验证：

  身份   OCI revision 标签 == 目标提交；digest 引用时核对 RepoDigests。
  初始化 隔离 MySQL 8.4 上走显式引导链：status(empty) → prepare --expect-state →
         check；迁移账号与运行账号分离，运行账号只有 DML 权限——任何运行期
         DDL 回归（create_all / jobstore 建表）都会以权限错误崩启动（#537）。
  运行   后端以无 DDL 账号启动并通过 /health；前端 standalone 服务 /login；
         登录经前端 Next rewrite 代理与 nginx /api/ 反代两条链路各验证一次；
         nginx 用仓库内真实 nginx.conf + 本次生成的自签证书（带 SAN，探针按
         CA 校验主机名，不关闭 TLS 验证）验证 80→301 与 HTTPS 入口。

刻意不做：完整 E2E、业务数据断言、生产凭据/证书/数据库接触。全部资源带本次
运行唯一后缀，只回收自己创建的容器/网络/临时目录；--keep-on-failure 供排障。

退出码：0 全部通过；1 存在失败检查（制品或接线不符合预期）；2 harness 错误
（docker/openssl 缺失、参数非法、镜像不存在等——不假装是检查失败）；130 中断。

用法（镜像须已存在于本地 docker 上下文；本脚本不隐式 pull，防止标签被重指后
冒烟到另一个制品）：
    python3 scripts/smoke_images.py \
        --backend-ref <repo>@sha256:<digest> --frontend-ref <repo>@sha256:<digest> \
        --expected-revision <40位SHA> [--report report.json] [--keep-on-failure]

纯 stdlib（scripts/ 约定，见 ci.yml「Run scripts tests」注释）。
"""
import argparse
import json
import os
import re
import secrets
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REVISION_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_REF_RE = re.compile(r"^(?P<repo>[^\s@]+)@sha256:(?P<digest>[0-9a-f]{64})$")
REVISION_LABEL = "org.opencontainers.image.revision"

MYSQL_READY_SECONDS = 150
BACKEND_READY_SECONDS = 90
FRONTEND_READY_SECONDS = 60
NGINX_READY_SECONDS = 45
PORT_MAPPING_TIMEOUT_SECONDS = 30
PROBE_TIMEOUT_SECONDS = 10
POLL_INTERVAL_SECONDS = 2.0
LOG_TAIL_CHARS = 3000
DOCKER_COMMAND_TIMEOUT_SECONDS = 300

MYSQL_DATABASE = "ir_smoke"
MIGRATE_USER = "ir_migrate"
RUNTIME_USER = "ir_runtime"


class SmokeError(RuntimeError):
    """harness/基础设施错误：无法得出任何检查结论（exit 2）。"""


class CheckFailed(RuntimeError):
    """某项检查失败：中止后续检查并清理（exit 1）。"""

    def __init__(self, name, detail):
        super().__init__(detail)
        self.name = name
        self.detail = detail


# 可注入点：测试用 monkeypatch 替换，避免真实 docker/HTTP/时钟
def _subprocess_run(command, *, input_text=None, env=None, timeout=DOCKER_COMMAND_TIMEOUT_SECONDS):
    return subprocess.run(
        [str(part) for part in command],
        input=input_text,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


_monotonic = time.monotonic
_sleep = time.sleep


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http_request(url, *, method="GET", payload=None, timeout=PROBE_TIMEOUT_SECONDS, cafile=None):
    """返回 (status, body_text, headers)。网络不可达抛 OSError（等待循环视为未就绪）。

    HTTPS 按 cafile 做完整证书与主机名校验——冒烟证书由本次运行生成且带
    IP:127.0.0.1 SAN，不需要也不允许关闭 TLS 验证。
    """
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(65536).decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read(65536).decode("utf-8", "replace") if exc.fp is not None else ""
        return exc.code, body, dict(exc.headers or {})


class SmokeRunner:
    def __init__(self, options):
        self.options = options
        self.suffix = f"{os.getpid()}-{secrets.token_hex(3)}"
        self.network = f"irsmoke-net-{self.suffix}"
        self.workdir = None
        self.cafile = None
        self.containers = []          # 本 runner 创建的容器名（清理只碰这些）
        self.checks = []
        self.report = {
            "ok": False,
            "expected_revision": options.expected_revision,
            "mysql_image": options.mysql_image,
            "nginx_image": options.nginx_image,
            "images": {},
            "bootstrap": {},
            "checks": self.checks,
            "cleanup": {"removed_containers": [], "removed_network": False,
                        "removed_workdir": False, "kept": False},
            "error": None,
        }
        self._secrets = []            # 报告中必须抹掉的口令值
        self.runtime_secret = secrets.token_hex(16)
        self.mysql_root_password = secrets.token_hex(16)
        self.migrate_password = secrets.token_hex(16)
        self.runtime_password = secrets.token_hex(16)
        self._secrets.extend([self.runtime_secret, self.mysql_root_password,
                              self.migrate_password, self.runtime_password])
        self._cleaned = False

    # ------------------------------------------------------------------ 工具
    def mask(self, text):
        for value in self._secrets:
            text = text.replace(value, "***")
        return text

    def name(self, role):
        return f"irsmoke-{role}-{self.suffix}"

    def docker(self, *args, env=None, input_text=None, check=True):
        merged = None
        if env is not None:
            merged = dict(os.environ)
            merged.update(env)
        try:
            proc = _subprocess_run(["docker", *args], input_text=input_text, env=merged)
        except subprocess.TimeoutExpired as exc:
            raise SmokeError(f"docker {args[0]} 超时（>{DOCKER_COMMAND_TIMEOUT_SECONDS}s）") from exc
        if check and proc.returncode != 0:
            raise SmokeError(self.mask(
                f"docker {' '.join(str(a) for a in args[:3])}… 失败（exit {proc.returncode}）: "
                f"{(proc.stderr or proc.stdout).strip()[-800:]}"
            ))
        return proc

    def record(self, name, ok, detail=""):
        entry = {"name": name, "ok": bool(ok), "detail": self.mask(str(detail))[:LOG_TAIL_CHARS]}
        self.checks.append(entry)
        return ok

    def check(self, name, probe):
        """执行一项检查：probe() 返回 detail 字符串；抛 CheckFailed/SmokeError 向外传播。"""
        started = _monotonic()
        detail = probe()
        self.record(name, True, detail or "")
        self.checks[-1]["seconds"] = round(_monotonic() - started, 2)

    def wait_until(self, name, deadline_seconds, probe):
        """probe() 返回 (ok, detail)。超时抛 CheckFailed；probe 抛 SmokeError 直接传播。"""
        started = _monotonic()
        last = "尚未探测"
        while True:
            ok, last = probe()
            if ok:
                return last
            elapsed = _monotonic() - started
            if elapsed >= deadline_seconds:
                raise CheckFailed(name, f"{deadline_seconds}s 内未就绪：{last}")
            _sleep(min(POLL_INTERVAL_SECONDS, deadline_seconds - elapsed))

    def container_running(self, name):
        proc = self.docker("inspect", "-f", "{{.State.Running}}", name, check=False)
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def logs_tail(self, name):
        proc = self.docker("logs", "--tail", "80", name, check=False)
        return self.mask(((proc.stdout or "") + (proc.stderr or "")).strip()[-LOG_TAIL_CHARS:])

    def wait_container_service(self, name, container, deadline_seconds, probe):
        """容器存活 + 服务探测的联合等待：容器死了立刻失败并附日志尾部。"""
        def combined():
            if not self.container_running(container):
                raise CheckFailed(name, f"容器 {container} 已退出；日志尾部：{self.logs_tail(container)}")
            return probe()
        return self.wait_until(name, deadline_seconds, combined)

    # ------------------------------------------------------------------ 身份
    def verify_image_identity(self, role, ref):
        match = DIGEST_REF_RE.match(ref)

        def probe():
            proc = self.docker("image", "inspect", ref, "--format", "{{json .}}", check=False)
            if proc.returncode != 0:
                raise SmokeError(
                    f"镜像 {ref} 不在本地 docker 上下文（本脚本不隐式 pull，避免标签重指后"
                    "冒烟到别的制品）；请先 build/load/pull 目标制品"
                )
            try:
                info = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                raise SmokeError(f"docker image inspect 输出非法 JSON: {exc}") from exc
            labels = (info.get("Config") or {}).get("Labels") or {}
            revision = labels.get(REVISION_LABEL, "")
            if revision != self.options.expected_revision:
                raise CheckFailed(
                    f"{role}-image-identity",
                    f"{REVISION_LABEL}={revision!r}，期望 {self.options.expected_revision!r}"
                    "（构建未注入 GIT_REVISION，或制品不属于该提交）",
                )
            repo_digests = info.get("RepoDigests") or []
            if match and f"{match.group('repo')}@sha256:{match.group('digest')}" not in repo_digests:
                raise CheckFailed(
                    f"{role}-image-identity",
                    f"digest 引用未在 RepoDigests 中出现：{repo_digests!r}",
                )
            self.report["images"][role] = {
                "ref": ref,
                "digest": f"sha256:{match.group('digest')}" if match else None,
                "id": info.get("Id"),
                "revision_label": revision,
            }
            return f"revision={revision[:7]} digest={'已固定' if match else '未固定'}"

        self.check(f"{role}-image-identity", probe)

    # ------------------------------------------------------------------ 数据库
    def mysql_url(self, user, password):
        return (f"mysql+pymysql://{user}:{password}@{self.name('mysql')}:3306/"
                f"{MYSQL_DATABASE}?charset=utf8mb4")

    def start_mysql(self):
        container = self.name("mysql")
        self.containers.append(container)
        self.docker(
            "run", "-d", "--name", container, "--network", self.network,
            "-e", "MYSQL_ROOT_PASSWORD", self.options.mysql_image,
            env={"MYSQL_ROOT_PASSWORD": self.mysql_root_password},
        )

        def ping():
            if not self.container_running(container):
                raise CheckFailed("mysql-ready", f"MySQL 容器已退出；日志尾部：{self.logs_tail(container)}")
            proc = self.docker(
                "exec", "-e", "MYSQL_PWD", container,
                "mysqladmin", "ping", "-h", "127.0.0.1", "-uroot", check=False,
                env={"MYSQL_PWD": self.mysql_root_password},
            )
            return proc.returncode == 0, (proc.stdout + proc.stderr).strip()[-300:]

        self.check("mysql-ready", lambda: self.wait_until("mysql-ready", MYSQL_READY_SECONDS, ping))
        sql = f"""
            CREATE DATABASE {MYSQL_DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
            CREATE USER '{MIGRATE_USER}'@'%' IDENTIFIED BY '{self.migrate_password}';
            CREATE USER '{RUNTIME_USER}'@'%' IDENTIFIED BY '{self.runtime_password}';
            GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES
              ON {MYSQL_DATABASE}.* TO '{MIGRATE_USER}'@'%';
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON {MYSQL_DATABASE}.* TO '{RUNTIME_USER}'@'%';
        """

        def accounts():
            self.docker(
                "exec", "-i", "-e", "MYSQL_PWD", container, "mysql", "-uroot",
                env={"MYSQL_PWD": self.mysql_root_password}, input_text=sql,
            )
            return f"迁移账号 {MIGRATE_USER}（含 DDL）与运行账号 {RUNTIME_USER}（仅 DML）已建立"

        self.check("database-accounts", accounts)

    def bootstrap_once(self, command, url, volumes=()):
        return self.docker(
            "run", "--rm", "--network", self.network,
            "-e", "DATABASE_URL", "-e", "SECRET_KEY", "-e", "DEBUG=false",
            *[arg for volume in volumes for arg in ("-v", volume)],
            self.options.backend_ref, *command,
            env={"DATABASE_URL": url, "SECRET_KEY": self.runtime_secret},
            check=False,
        )

    def explicit_bootstrap(self):
        migrate_url = self.mysql_url(MIGRATE_USER, self.migrate_password)

        def status_probe():
            proc = self.bootstrap_once(["python", "-m", "app.bootstrap", "status"], migrate_url)
            if proc.returncode != 0:
                raise CheckFailed("bootstrap-status-empty",
                                  f"bootstrap status 退出码 {proc.returncode}: "
                                  f"{self.mask((proc.stderr or proc.stdout)[-800:])}")
            try:
                payload = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                raise CheckFailed("bootstrap-status-empty", f"status 输出非法 JSON: {exc}") from exc
            if payload.get("state") != "empty":
                raise CheckFailed("bootstrap-status-empty",
                                  f"state={payload.get('state')!r}（全新库必须为 empty，否则拒绝继续）")
            self.report["bootstrap"] = {
                "state_before": payload.get("state"),
                "fingerprint_before": payload.get("fingerprint"),
                "migration_fingerprint": payload.get("migration_fingerprint"),
                "expected_heads": payload.get("expected_heads"),
                "dialect": payload.get("dialect"),
            }
            return f"state=empty fingerprint={str(payload.get('fingerprint'))[:12]}…"

        self.check("bootstrap-status-empty", status_probe)
        fingerprint = self.report["bootstrap"]["fingerprint_before"]

        def prepare_probe():
            proc = self.bootstrap_once(
                ["python", "-m", "app.bootstrap", "prepare", "--expect-state", fingerprint],
                migrate_url,
            )
            if proc.returncode != 0:
                raise CheckFailed("bootstrap-prepare",
                                  f"prepare 退出码 {proc.returncode}: "
                                  f"{self.mask((proc.stderr or proc.stdout)[-800:])}")
            try:
                prepared = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                raise CheckFailed("bootstrap-prepare", f"prepare 输出非法 JSON: {exc}") from exc
            if prepared.get("state") != "ready":
                raise CheckFailed("bootstrap-prepare", f"prepare 后 state={prepared.get('state')!r}")
            self.report["bootstrap"]["fingerprint_after_prepare"] = prepared.get("fingerprint")
            return "prepare 后 state=ready"

        self.check("bootstrap-prepare", prepare_probe)

        def check_probe():
            proc = self.bootstrap_once(["python", "-m", "app.bootstrap", "check"], migrate_url)
            if proc.returncode != 0:
                raise CheckFailed("bootstrap-check",
                                  f"check 退出码 {proc.returncode}: {self.mask(proc.stdout[-400:])}")
            return "check 通过（state=ready）"

        self.check("bootstrap-check", check_probe)

        def seed_probe():
            # 生产镜像不带测试代码（最小镜像，seed_e2e.py 依赖 tests.seed_base）。
            # 种子属测试脚手架而非制品行为：只读挂载仓库 backend/tests 补齐依赖；
            # 制品侧检查（bootstrap/运行账号启动/探活）保持零挂载。
            tests_dir = ROOT / "backend" / "tests"
            if not tests_dir.is_dir():
                raise SmokeError(f"缺少种子依赖目录 {tests_dir}（须在仓库检出内运行冒烟）")
            proc = self.bootstrap_once(["python", "scripts/seed_e2e.py"], migrate_url,
                                       volumes=[f"{tests_dir}:/app/tests:ro"])
            if proc.returncode != 0:
                raise CheckFailed("seed-e2e",
                                  f"seed_e2e.py 退出码 {proc.returncode}: "
                                  f"{self.mask((proc.stderr or proc.stdout)[-800:])}")
            return "E2E 种子完成（含 ADMIN 登录账号）"

        self.check("seed-e2e", seed_probe)

    # ------------------------------------------------------------------ 运行栈
    def published_port(self, container, port, check_name):
        """docker run -d 返回不等于端口映射就绪：HostPort 分配可稍晚于容器启动
        （WSL2/Docker Desktop 实测数秒），故轮询等待。期间容器退出按检查失败
        处理并带日志尾部（制品/接线问题，exit 1）；始终不出现才是 harness 错误。"""
        deadline = _monotonic() + PORT_MAPPING_TIMEOUT_SECONDS
        while True:
            proc = self.docker("port", container, f"{port}/tcp", check=False)
            lines = proc.stdout.strip().splitlines()
            if lines and ":" in lines[-1]:
                return int(lines[-1].rsplit(":", 1)[1])
            if not self.container_running(container):
                raise CheckFailed(check_name,
                                  f"{container} 在端口映射就绪前已退出；"
                                  f"日志尾部：{self.logs_tail(container)}")
            if _monotonic() >= deadline:
                raise SmokeError(
                    f"等待 {container} 的 {port} 端口映射超时"
                    f"（{PORT_MAPPING_TIMEOUT_SECONDS}s）: {proc.stdout!r}")
            _sleep(POLL_INTERVAL_SECONDS)

    def start_backend(self):
        container = self.name("backend")
        self.containers.append(container)
        self.docker(
            "run", "-d", "--name", container, "--network", self.network, "--network-alias", "backend",
            "-p", "127.0.0.1::8000",
            "-e", "DATABASE_URL", "-e", "SECRET_KEY",
            "-e", "SCHEDULER_ENABLED=true", "-e", "DEBUG=false", "-e", "AKSHARE_ENABLED=false",
            self.options.backend_ref,
            env={
                # 运行账号无任何 DDL 权限：这是「运行期零 DDL」的强证明（#537）
                "DATABASE_URL": self.mysql_url(RUNTIME_USER, self.runtime_password),
                "SECRET_KEY": self.runtime_secret,
            },
        )
        port = self.published_port(container, 8000, "backend-runtime-up")
        self.report["backend_port"] = port

        def probe():
            try:
                status, body, _ = _http_request(f"http://127.0.0.1:{port}/health")
            except OSError as exc:
                return False, f"连接失败: {exc}"
            return status == 200, f"HTTP {status} {body[:120]}"

        self.check("backend-runtime-up", lambda: self.wait_container_service(
            "backend-runtime-up", container, BACKEND_READY_SECONDS, probe))

    def start_frontend(self):
        container = self.name("frontend")
        self.containers.append(container)
        self.docker(
            "run", "-d", "--name", container, "--network", self.network, "--network-alias", "frontend",
            "-p", "127.0.0.1::7860", self.options.frontend_ref,
        )
        port = self.published_port(container, 7860, "frontend-up")
        self.report["frontend_port"] = port

        def probe():
            try:
                status, _, _ = _http_request(f"http://127.0.0.1:{port}/login")
            except OSError as exc:
                return False, f"连接失败: {exc}"
            return status == 200, f"HTTP {status}"

        self.check("frontend-up", lambda: self.wait_container_service(
            "frontend-up", container, FRONTEND_READY_SECONDS, probe))

    def login_through(self, name, base_url):
        def probe():
            status, body, _ = _http_request(
                f"{base_url}/api/auth/login",
                method="POST",
                payload={"code": self.options.login_code, "password": self.options.login_password},
                cafile=self.cafile,
            )
            if status != 200:
                raise CheckFailed(name, f"登录返回 HTTP {status}: {body[:300]}")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise CheckFailed(name, f"登录响应非法 JSON: {exc}") from exc
            user = payload.get("user") or {}
            if not payload.get("token") or user.get("code") != self.options.login_code:
                raise CheckFailed(name, "登录响应缺少 token 或 user.code 不匹配")
            return f"HTTP 200，token 已签发（user={user.get('code')}）"

        self.check(name, probe)

    def start_nginx(self):
        nginx_conf = ROOT / "nginx" / "nginx.conf"
        if not nginx_conf.is_file():
            raise SmokeError(f"缺少 {nginx_conf}（仓库内真实入口配置是冒烟对象）")
        if shutil.which("openssl") is None:
            raise SmokeError("缺少 openssl：无法为冒烟生成自签证书")
        cert_dir = Path(self.workdir) / "letsencrypt" / "live" / "investring.top"
        cert_dir.mkdir(parents=True)
        (Path(self.workdir) / "certbot" / "www").mkdir(parents=True)
        proc = _subprocess_run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=investring.top",
            "-addext", "subjectAltName=DNS:investring.top,IP:127.0.0.1",
            "-keyout", str(cert_dir / "privkey.pem"),
            "-out", str(cert_dir / "fullchain.pem"),
        ])
        if proc.returncode != 0:
            raise SmokeError(f"自签证书生成失败: {proc.stderr.strip()[-300:]}")
        self.cafile = str(cert_dir / "fullchain.pem")
        container = self.name("nginx")
        self.containers.append(container)
        self.docker(
            "run", "-d", "--name", container, "--network", self.network,
            "-p", "127.0.0.1::80", "-p", "127.0.0.1::443",
            "-v", f"{nginx_conf}:/etc/nginx/nginx.conf:ro",
            "-v", f"{Path(self.workdir) / 'letsencrypt'}:/etc/letsencrypt:ro",
            "-v", f"{Path(self.workdir) / 'certbot'}:/var/www/certbot:ro",
            self.options.nginx_image,
        )
        # 发布包需要 nginx 镜像的可恢复 digest（#537 第四批 C：前后端及 nginx 三者齐备）。
        # CI 中该镜像来自注册表 pull，RepoDigests 可用；本地缓存无 digest 时记录为 None，
        # 由 release_bundle.py 在构建发布包时拒绝，而不是在冒烟里失败。
        proc = self.docker("image", "inspect", self.options.nginx_image, "--format", "{{json .}}", check=False)
        info = {}
        if proc.returncode == 0:
            try:
                info = json.loads(proc.stdout)
            except json.JSONDecodeError:
                info = {}
        digests = info.get("RepoDigests") or []
        digest_ref = next((d for d in digests if "@" in d), None)
        self.report["images"]["nginx"] = {
            "ref": self.options.nginx_image,
            "digest": digest_ref.split("@", 1)[1] if digest_ref else None,
            # 完整 repo@digest：发布包用它固定可恢复的 nginx 拉取引用
            "digest_ref": digest_ref,
            "id": info.get("Id"),
        }
        http_port = self.published_port(container, 80, "nginx-up")
        https_port = self.published_port(container, 443, "nginx-up")
        self.report["nginx_http_port"] = http_port
        self.report["nginx_https_port"] = https_port

        def probe():
            try:
                status, _, _ = _http_request(f"http://127.0.0.1:{http_port}/health")
            except OSError as exc:
                return False, f"连接失败: {exc}"
            # 80 端口对 /health 命中 `location /` 的 301；200/301 都证明 nginx 已在服务
            return status in (200, 301), f"HTTP {status}"

        self.check("nginx-up", lambda: self.wait_container_service(
            "nginx-up", container, NGINX_READY_SECONDS, probe))

    def nginx_entry_checks(self):
        http_port = self.report["nginx_http_port"]
        https_port = self.report["nginx_https_port"]

        def redirect_probe():
            status, _, headers = _http_request(f"http://127.0.0.1:{http_port}/anything")
            location = headers.get("Location", "")
            if status != 301 or not location.startswith("https://"):
                raise CheckFailed("nginx-http-redirect",
                                  f"HTTP {status}，Location={location!r}（期望 301→https）")
            return f"301 → {location}"

        self.check("nginx-http-redirect", redirect_probe)

        def https_health():
            status, body, _ = _http_request(f"https://127.0.0.1:{https_port}/health", cafile=self.cafile)
            if status != 200:
                raise CheckFailed("nginx-https-health", f"HTTP {status}: {body[:200]}")
            return "HTTPS /health 200（nginx→backend upstream）"

        self.check("nginx-https-health", https_health)

        def https_frontend():
            status, _, _ = _http_request(f"https://127.0.0.1:{https_port}/login", cafile=self.cafile)
            if status != 200:
                raise CheckFailed("nginx-https-frontend", f"HTTP {status}（期望 200）")
            return "HTTPS /login 200（nginx→frontend upstream）"

        self.check("nginx-https-frontend", https_frontend)
        self.login_through("nginx-https-login", f"https://127.0.0.1:{https_port}")

    # ------------------------------------------------------------------ 生命周期
    def cleanup(self, keep):
        """幂等：只执行一次；只回收本 runner 登记过的资源。"""
        if self._cleaned:
            return
        self._cleaned = True
        cleanup = self.report["cleanup"]
        if keep:
            cleanup["kept"] = True
            print(f"[keep] 保留排障资源：network={self.network} containers={self.containers} "
                  f"workdir={self.workdir}", file=sys.stderr)
            return
        for container in self.containers:
            proc = self.docker("rm", "-f", container, check=False)
            if proc.returncode == 0:
                cleanup["removed_containers"].append(container)
        proc = self.docker("network", "rm", self.network, check=False)
        cleanup["removed_network"] = proc.returncode == 0
        if self.workdir and Path(self.workdir).is_dir():
            shutil.rmtree(self.workdir, ignore_errors=True)
            cleanup["removed_workdir"] = not Path(self.workdir).is_dir()

    def run(self):
        if shutil.which("docker") is None:
            raise SmokeError("缺少 docker CLI，无法执行镜像冒烟")
        self.workdir = tempfile.mkdtemp(prefix="irsmoke-")

        def docker_probe():
            version = self.docker("version", "--format", "{{.Server.Version}}").stdout.strip()
            return f"docker server {version}"

        self.check("docker-available", docker_probe)
        self.verify_image_identity("backend", self.options.backend_ref)
        self.verify_image_identity("frontend", self.options.frontend_ref)
        self.docker("network", "create", self.network)
        self.start_mysql()
        self.explicit_bootstrap()
        self.start_backend()
        self.start_frontend()
        self.login_through("frontend-api-proxy-login", f"http://127.0.0.1:{self.report['frontend_port']}")
        self.start_nginx()
        self.nginx_entry_checks()
        self.report["ok"] = all(entry["ok"] for entry in self.checks)
        return self.report


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="最终镜像运行冒烟（#537 第四批 B）；不隐式 pull、不接触生产")
    parser.add_argument("--backend-ref", required=True, help="后端镜像引用（推荐 repo@sha256:digest）")
    parser.add_argument("--frontend-ref", required=True, help="前端镜像引用（推荐 repo@sha256:digest）")
    parser.add_argument("--expected-revision", required=True, help="制品必须携带的 40 位提交 SHA")
    parser.add_argument("--mysql-image", default="mysql:8.4")
    parser.add_argument("--nginx-image", default="nginx:1.27-alpine")
    parser.add_argument("--login-code", default="ADMIN", help="seed_e2e 种子管理员 code")
    parser.add_argument("--login-password", default="admin@2026", help="seed_e2e 种子管理员口令（一次性冒烟库）")
    parser.add_argument("--report", help="将 JSON 报告另存到该路径")
    parser.add_argument("--keep-on-failure", action="store_true",
                        help="失败时保留容器/网络/临时目录供排障（成功一律清理）")
    options = parser.parse_args(argv)
    revision = options.expected_revision.strip().lower()
    if not REVISION_RE.fullmatch(revision):
        parser.error("--expected-revision 必须是 40 位十六进制提交 SHA")
    options.expected_revision = revision
    for label, ref in (("--backend-ref", options.backend_ref), ("--frontend-ref", options.frontend_ref)):
        if not ref.strip() or re.search(r"\s", ref):
            parser.error(f"{label} 不能为空且不得含空白")
        if "@" in ref and not DIGEST_REF_RE.fullmatch(ref):
            parser.error(f"{label} 的 digest 引用格式非法（期望 repo@sha256:<64位hex>）: {ref!r}")
    return options


def main(argv=None):
    options = parse_args(argv if argv is not None else sys.argv[1:])
    runner = SmokeRunner(options)

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    report = runner.report
    exit_code = 0
    try:
        runner.run()
    except KeyboardInterrupt:
        report["error"] = "已中断"
        exit_code = 130
    except CheckFailed as exc:
        runner.record(exc.name, False, exc.detail)
        report["ok"] = False
        exit_code = 1
    except SmokeError as exc:
        report["error"] = runner.mask(str(exc))
        report["ok"] = False
        exit_code = 2
    except subprocess.TimeoutExpired as exc:
        report["error"] = f"外部命令超时: {exc}"
        report["ok"] = False
        exit_code = 2
    finally:
        # 清理单点归 main：正常/失败/中断路径都恰好执行一次（cleanup 自身幂等）
        try:
            runner.cleanup(keep=exit_code != 0 and options.keep_on_failure)
        except SmokeError:
            report["cleanup"]["kept"] = True
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if options.report:
        try:
            Path(options.report).write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"[warn] 报告写入失败: {exc}", file=sys.stderr)
    print(text)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
