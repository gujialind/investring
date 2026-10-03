"""server_deploy.sh 的子进程测试（issue #537 第四批 C/D）。

真实执行 bash 脚本，只把 docker/curl 换成场景驱动的假可执行文件（服务器端不依赖
Python，但测试侧用 Python 写 fake 最精确）。覆盖计划要求的失败矩阵：产物缺失/
损坏、同 SHA 不同构建、旧任务晚到、up/reload/探活失败、恢复失败、未知 DB 状态、
迁移授权过期与部分执行失败、手动回滚保留顺序记录。

断言重点不是"命令跑过"，而是状态机结果：current 指向、accepted-releases 记录
序列、LKG、失败现场标记、以及"拒绝路径零副作用"（未拉镜像/未 up/未激活）。

两个假可执行文件都带真实语义的一角，缺了那角对应用例就恒绿：FAKE_CURL 复刻 curl 的
代理继承与 `--noproxy` 优先级（#661 的反证判据依赖它），FAKE_DOCKER 维护一个镜像存在
集（#662 的回收断言落在最终存在什么，而不是调用过什么——回收在 `|| warn` 保护下）。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "server_deploy.sh"
sys.path.insert(0, str(ROOT / "scripts"))
import release_bundle as rb  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
BACKEND_REPO = "registry.example.invalid/ns/investring-backend"
FRONTEND_REPO = "registry.example.invalid/ns/investring-frontend"
BACKEND_REF = f"{BACKEND_REPO}@sha256:{'ab' * 32}"
FRONTEND_REF = f"{FRONTEND_REPO}@sha256:{'cd' * 32}"
NGINX_REF = f"nginx@sha256:{'ef' * 32}"
MIG_FP = "mig-fp"          # 发布包记录的迁移内容指纹（来自冒烟报告）
LIVE_FP = "ab" * 32        # status 输出的完整状态指纹（migrate 授权对象）

# ------------------------------------------------------------------ 假 docker/curl
FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
scenario = json.load(open(os.environ["FAKE_DOCKER"]))
state_dir = os.environ["FAKE_DOCKER_STATE"]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")

def done(rc=0, out="", err=""):
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    sys.exit(rc)

def respond(resp):
    out = ""
    if resp.get("json") is not None:
        out = json.dumps(resp["json"], sort_keys=True) + "\n"
    rc = resp.get("rc", 0)
    done(rc, out, resp.get("stderr", "" if rc == 0 else "fake bootstrap failure\n"))

def release_of(args):
    if "--project-directory" in args:
        return os.path.basename(args[args.index("--project-directory") + 1].rstrip("/"))
    return ""

# 镜像存在集：pull 写入、rmi 移除、image inspect 读取。断言落在**最终状态**而不是
# argv 形状——回收调用点在 mark_success 的 `|| warn` 保护下，只断形状会假绿。
STORE = os.path.join(os.environ["FAKE_DOCKER_STATE"], "images.json")

def load_store():
    return json.load(open(STORE)) if os.path.exists(STORE) else []

def save_store(items):
    json.dump(items, open(STORE, "w"))

if argv and argv[0] == "pull":
    if argv[1] in scenario.get("pull_fail", []):
        done(1, "", "pull access denied\n")
    store = load_store()
    if argv[1] not in store:
        store.append(argv[1])
    save_store(store)
    done(0)
if argv and argv[0] == "rmi":
    ref = argv[1]
    if ref in scenario.get("rmi_fail", []):
        done(1, "", "container is using image\n")
    store = load_store()
    if ref not in store:
        # 候选集算错（比如漏了保护集、拿幸存发布的 ref 来删）会当场在这里失败
        done(1, "", "No such image\n")
    store.remove(ref)
    save_store(store)
    done(0, "Untagged: %s\n" % ref)
if argv and argv[:2] == ["image", "inspect"]:
    ref = argv[2]
    if ref in load_store():
        done(0, json.dumps({"RepoDigests": [ref]}) + "\n")
    done(1, "", "No such image\n")
if argv and argv[0] == "logs":
    done(0, "fake container logs\n")
if argv and argv[:2] == ["image", "prune"]:
    done(0)
if argv and argv[0] == "compose":
    rel = release_of(argv)
    cmd = argv[argv.index("-f") + 2:]
    table = scenario.get("bootstrap_by_release", {}).get(rel) or scenario.get("bootstrap", {})
    if cmd[0] == "run" and "app.bootstrap" in cmd:
        op = cmd[cmd.index("app.bootstrap") + 1]
        flag = os.path.join(state_dir, "prepared-" + rel)
        if op == "prepare":
            open(flag, "w").close()
            respond(table.get("prepare", {"rc": 0, "json": {"state": "ready"}}))
        key = op + "_after_prepare" if os.path.exists(flag) else op
        if key in table:
            respond(table[key])
        if op in table:
            respond(table[op])
        respond({"rc": 0, "json": {"state": "ready"}})
    if cmd[0] == "up":
        if rel in scenario.get("up_fail_ids", []):
            done(1, "", "compose up failed\n")
        done(0)
    if cmd[0] == "restart":
        if rel in scenario.get("restart_fail_ids", []):
            done(1, "", "restart failed\n")
        done(0)
    if cmd[0] == "exec":
        if "wget" in cmd:
            if rel in scenario.get("wget_fail_ids", []):
                done(1, "", "wget failed\n")
            done(0)
        if "stat" in cmd:
            done(0, "999999\n")   # 与宿主 inode 必然漂移 → 走 restart 分支（#119）
        done(0)
done(99, "", "fake docker: unexpected argv %r\n" % (argv,))
'''

FAKE_CURL = r'''#!/usr/bin/env python3
"""假 curl：按场景失败之外，**代理继承语义复刻真实 curl**。

不复刻这一层，「注入不可达 ALL_PROXY 后部署仍成功」会恒绿——探活有没有被主机代理
劫持，取决于命令里有没有 --noproxy，而真实 curl 会照环境变量把连接交给代理（#661）。
"""
import json, os, sys

argv = sys.argv[1:]
cfg = json.load(open(os.environ["FAKE_CURL"]))
with open(os.environ["FAKE_CURL_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")

# 既有接缝：set_curl_fail 与 happy path 断言都按末位取 URL，--noproxy 一律排在 URL 之前
url = argv[-1]

explicit = []
i = 0
while i < len(argv):
    if argv[i] == "--noproxy" and i + 1 < len(argv):
        explicit = [x.strip() for x in argv[i + 1].split(",") if x.strip()]
        i += 2
        continue
    if argv[i].startswith("--noproxy="):
        explicit = [x.strip() for x in argv[i].split("=", 1)[1].split(",") if x.strip()]
    i += 1

host = url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]


def env_of(*names):
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    return ""


proxy = env_of("ALL_PROXY", "all_proxy") or (
    env_of("HTTPS_PROXY", "https_proxy") if url.startswith("https")
    else env_of("HTTP_PROXY", "http_proxy"))
bypass = [x.strip() for x in env_of("NO_PROXY", "no_proxy").split(",") if x.strip()]


def covers(entries):
    return any(e == "*" or host == e.lstrip(".") or host.endswith("." + e.lstrip("."))
               for e in entries)


if proxy and not (covers(explicit) or covers(bypass)):
    sys.stderr.write("curl: (97) Can't complete SOCKS5 connection to %s. (1)\n" % host)
    sys.exit(97)

for pattern in cfg.get("fail", []):
    if pattern in url:
        sys.exit(1)
sys.exit(0)
'''


def status_json(state="ready", *, mig_fp=MIG_FP, fp=LIVE_FP):
    """与 backend/app/bootstrap.py status 输出同构的单行 JSON 载荷。"""
    return {"state": state, "dialect": "mysql", "migration_mode": "mysql-alembic",
            "database_identity": "dbid", "revisions": ["0017"], "expected_heads": ["0017"],
            "migration_fingerprint": mig_fp, "schema_fingerprint": "sf",
            "missing_tables": [], "missing_columns": [], "missing_not_null": [],
            "missing_tasks": [], "fingerprint": fp}


def default_scenario():
    ready = {"rc": 0, "json": status_json()}
    return {"bootstrap": {"status": ready, "status_after_prepare": ready,
                          "check": ready, "check_after_prepare": ready, "prepare": ready},
            "bootstrap_by_release": {}, "pull_fail": [], "rmi_fail": [],
            "up_fail_ids": [], "restart_fail_ids": [], "wget_fail_ids": []}


def image_refs(tag):
    """一次发布的三个 digest 引用。tag=None 返回与模块常量逐字相同的默认形态，
    使既有断言（如 happy path 的拉取顺序）不受新参数影响；nginx 基座镜像跨发布
    恒定共享，与生产一致。"""
    if tag is None:
        return BACKEND_REF, FRONTEND_REF, NGINX_REF
    backend = hashlib.sha256(f"backend-{tag}".encode()).hexdigest()
    frontend = hashlib.sha256(f"frontend-{tag}".encode()).hexdigest()
    return (f"{BACKEND_REPO}@sha256:{backend}", f"{FRONTEND_REPO}@sha256:{frontend}",
            NGINX_REF)


def smoke_report(sha, image_tag=None):
    backend_ref, frontend_ref, nginx_ref = image_refs(image_tag)
    return {
        "ok": True, "error": None, "expected_revision": sha,
        "images": {
            "backend": {"ref": backend_ref, "digest": backend_ref.split("@", 1)[1],
                        "id": "sha256:backend", "revision_label": sha},
            "frontend": {"ref": frontend_ref, "digest": frontend_ref.split("@", 1)[1],
                         "id": "sha256:frontend", "revision_label": sha},
            "nginx": {"ref": "nginx:1.27-alpine", "digest": "sha256:" + "ef" * 32,
                      "digest_ref": nginx_ref, "id": "sha256:nginx"},
        },
        "bootstrap": {"state_before": "empty", "fingerprint_before": "fp-before",
                      "migration_fingerprint": MIG_FP, "expected_heads": ["0017"],
                      "dialect": "mysql", "fingerprint_after_prepare": "fp-after"},
        "checks": [], "cleanup": {},
    }


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def server(tmp_path):
    base = tmp_path / "opt"
    (base / "certbot" / "www").mkdir(parents=True)
    (base / ".env").write_text("SECRET=x\n", encoding="utf-8")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    for name, source in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        path = fakebin / name
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)
    scenario_path = tmp_path / "scenario.json"
    curl_cfg_path = tmp_path / "curl-fail.json"
    state_dir = tmp_path / "fakestate"
    state_dir.mkdir()
    docker_log = tmp_path / "docker.jsonl"
    curl_log = tmp_path / "curl.jsonl"
    scenario_path.write_text(json.dumps(default_scenario()), encoding="utf-8")
    curl_cfg_path.write_text(json.dumps({"fail": []}), encoding="utf-8")

    env = dict(os.environ)
    # 代理变量只能由用例显式注入，不能继承开发者机器：本机若配了
    # `no_proxy=127.0.0.1`（生产那次止血项的形态），#661 的用例在脚本没有
    # --noproxy 时也会通过——反证判据当场失效。
    for key in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
                "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        env.pop(key, None)
    env.update({
        "PATH": f"{fakebin}{os.pathsep}{env['PATH']}",
        "FAKE_DOCKER": str(scenario_path), "FAKE_DOCKER_LOG": str(docker_log),
        "FAKE_DOCKER_STATE": str(state_dir),
        "FAKE_CURL": str(curl_cfg_path), "FAKE_CURL_LOG": str(curl_log),
        "PROBE_ATTEMPTS": "2", "PROBE_INTERVAL": "0",
    })

    def set_bootstrap(op, payload, *, release=None):
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        if release:
            scenario["bootstrap_by_release"].setdefault(release, {})[op] = payload
        else:
            scenario["bootstrap"][op] = payload
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

    def set_scenario_field(field, value):
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        scenario[field] = value
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

    def set_curl_fail(patterns):
        curl_cfg_path.write_text(json.dumps({"fail": patterns}), encoding="utf-8")

    def run(*args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                              capture_output=True, text=True, timeout=300)

    def read_jsonl(path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def current_id():
        link = base / "current"
        return os.path.basename(os.readlink(link)) if link.is_symlink() else None

    def accepted():
        path = base / "state" / "accepted-releases.log"
        if not path.exists():
            return []
        return [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines()]

    def calls_for(release_id, *fragments):
        directory = str(base / "releases" / release_id)
        return [c for c in read_jsonl(docker_log)
                if directory in c and all(fragment in c for fragment in fragments)]

    def images():
        """假 docker 的镜像存在集（pull 写入、rmi 移除）。回收是否真的发生只能看这里。"""
        path = state_dir / "images.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def docker(*args):
        """直接调假 docker，用于 issue 验收里「current/LKG 的 digest 可被 inspect」那条。"""
        return subprocess.run(["docker", *args], env=env, capture_output=True, text=True)

    return SimpleNamespace(
        base=base, env=env, run=run, set_bootstrap=set_bootstrap,
        set_scenario_field=set_scenario_field, set_curl_fail=set_curl_fail,
        docker_calls=lambda: read_jsonl(docker_log), curl_calls=lambda: read_jsonl(curl_log),
        current_id=current_id, accepted=accepted, calls_for=calls_for,
        images=images, docker=docker,
        kinds=lambda: [row[1] for row in accepted()],
    )


@pytest.fixture
def bundle_root(tmp_path):
    root = tmp_path / "src-root"
    (root / "nginx").mkdir(parents=True)
    (root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "nginx" / "nginx.conf").write_text("events {}\n", encoding="utf-8")
    return root


@pytest.fixture
def make_release(tmp_path, bundle_root):
    """用真实 release_bundle.build 产包并复制到 incoming 落地目录。

    `image_tag` 只影响 images.env 里的 backend/frontend digest（nginx 恒共享）：
    同 tag ⇒ 同一组引用，用来模拟「代码变了但镜像层复用」的连续发布；不传则用
    模块常量，保持既有断言逐字不变。
    """
    def make(*, sha=SHA, run_id=55, attempt=1, image_tag=None):
        release_id = f"{sha[:7]}-{run_id}.{attempt}"
        report_path = tmp_path / f"report-{run_id}-{attempt}-{image_tag}.json"
        report_path.write_text(json.dumps(smoke_report(sha, image_tag)), encoding="utf-8")
        bundle_dir = tmp_path / f"bundle-{run_id}-{attempt}"
        assert rb.main(["build", "--smoke-report", str(report_path), "--sha", sha,
                        "--run-id", str(run_id), "--run-attempt", str(attempt),
                        "--workflow", "CI", "--out-dir", str(bundle_dir),
                        "--root", str(bundle_root)]) == 0
        incoming = tmp_path / f"incoming-{release_id}"
        shutil.copytree(bundle_dir, incoming)
        return SimpleNamespace(id=release_id, sha=sha, run_id=run_id, attempt=attempt,
                               incoming=incoming, bundle=bundle_dir)
    return make


def deploy(server, rel, *, mode="deploy", expect="none", extra=()):
    args = [mode, "--release-id", rel.id, "--base", str(server.base),
            "--expect-accepted", expect, *extra]
    if mode in ("deploy", "migrate") and rel.incoming.exists():
        args += ["--incoming", str(rel.incoming)]   # migrate 可带新包，也可复用已 staged 发布
    return server.run(*args)


# ------------------------------------------------------------------ 快乐路径
def test_deploy_happy_path(server, make_release):
    rel = make_release()
    proc = deploy(server, rel)
    assert proc.returncode == 0, proc.stderr
    assert "部署完成" in proc.stdout

    directory = server.base / "releases" / rel.id
    assert directory.is_dir()
    assert not rel.incoming.exists()                       # 落地目录 staging 后清除
    assert os.readlink(directory / ".env") == str(server.base / ".env")   # 秘密只链接不复制
    assert os.readlink(directory / "docker-compose.yml") == "files/docker-compose.yml"
    assert os.readlink(directory / "nginx" / "nginx.conf") == "../files/nginx/nginx.conf"
    assert os.path.islink(directory / "certbot" / "www")
    assert server.current_id() == rel.id
    assert server.kinds() == ["auto"]
    row = server.accepted()[0]
    assert row[2:] == [rel.id, rel.sha, str(rel.run_id), str(rel.attempt)]
    assert (server.base / "state" / "last-known-good").read_text().strip() == rel.id

    pulls = [c for c in server.docker_calls() if c[0] == "pull"]
    assert [c[1] for c in pulls] == [BACKEND_REF, FRONTEND_REF, NGINX_REF]  # digest 化拉取
    status_calls = server.calls_for(rel.id, "run", "--rm", "--no-deps", "-T", "status")
    assert len(status_calls) == 1                          # 只读探测恰好一次
    assert server.calls_for(rel.id, "up", "-d", "--remove-orphans")
    assert server.calls_for(rel.id, "restart", "nginx")    # inode 漂移 → restart（#119）
    assert server.calls_for(rel.id, "exec", "wget")
    urls = [c[-1] for c in server.curl_calls()]
    assert "http://127.0.0.1:8000/health" in urls
    assert "https://127.0.0.1/health" in urls


def test_deploy_is_idempotent_on_retry(server, make_release):
    rel = make_release()
    assert deploy(server, rel).returncode == 0
    again = deploy(server, rel, expect=f"{rel.run_id}.{rel.attempt}")  # 同任务重试
    assert again.returncode == 0 and "复用已 staged 发布" in again.stdout
    assert server.kinds() == ["auto", "auto"]
    assert server.current_id() == rel.id


# ------------------------------------------------------------------ 探活与主机代理隔离（#661）
def test_probe_survives_unreachable_host_proxy(server, make_release):
    """主机配了指向必然不可达地址的代理时，探活仍必须探本机的门。

    判别力在假 curl 的代理语义上（见 FAKE_CURL）：去掉脚本里的 `--noproxy "*"`，
    这条会以 rc=5 + 「无上一发布可恢复」判红，而不是恒绿。断言只落在退出码驱动的
    状态——probe_one 带 `>/dev/null 2>&1`，curl 的报错文案永远看不见。
    """
    server.env["ALL_PROXY"] = "socks5://127.0.0.1:1"
    rel = make_release()
    proc = deploy(server, rel)
    assert proc.returncode == 0, proc.stderr
    assert server.current_id() == rel.id and server.kinds() == ["auto"]
    assert server.curl_calls(), "探活没有发生，本用例什么都没测到"
    assert all("--noproxy" in call for call in server.curl_calls())


# ------------------------------------------------------------------ 产物拒绝路径
def test_corrupt_bundle_rejected_before_anything(server, make_release):
    rel = make_release()
    (rel.incoming / "files" / "docker-compose.yml").write_text("evil\n", encoding="utf-8")
    proc = deploy(server, rel)
    assert proc.returncode == 2 and "发布包校验失败" in proc.stderr
    assert not (server.base / "releases" / rel.id).exists()
    assert server.current_id() is None and not server.docker_calls()


def test_missing_artifact_rejected(server, make_release):
    rel = make_release()
    (rel.incoming / "images.env").unlink()
    proc = deploy(server, rel)
    assert proc.returncode == 2 and "images.env" in proc.stderr
    assert not (server.base / "releases" / rel.id).exists() and not server.docker_calls()


def test_wrong_release_id_rejected_before_staging(server, make_release):
    rel = make_release()
    proc = server.run("deploy", "--release-id", "fffffff-9.9", "--base", str(server.base),
                      "--expect-accepted", "none", "--incoming", str(rel.incoming))
    assert proc.returncode == 2 and "不一致" in proc.stderr
    assert not (server.base / "releases").exists() or not list((server.base / "releases").iterdir())
    assert rel.incoming.exists()                           # 错包现场保留在落地目录


def test_invalid_release_id_and_missing_expect_accepted(server, make_release):
    rel = make_release()
    proc = server.run("deploy", "--release-id", "BAD", "--base", str(server.base),
                      "--expect-accepted", "none", "--incoming", str(rel.incoming))
    assert proc.returncode == 2 and "非法" in proc.stderr
    proc = server.run("deploy", "--release-id", rel.id, "--base", str(server.base),
                      "--incoming", str(rel.incoming))
    assert proc.returncode == 2 and "--expect-accepted" in proc.stderr


def test_missing_shared_env_rejected(server, make_release):
    rel = make_release()
    (server.base / ".env").unlink()
    proc = deploy(server, rel)
    assert proc.returncode == 2 and "人工维护" in proc.stderr


def test_pull_failure_stops_before_activation(server, make_release):
    rel = make_release()
    server.set_scenario_field("pull_fail", [BACKEND_REF])
    proc = deploy(server, rel)
    assert proc.returncode == 2 and "拉取失败" in proc.stderr
    assert server.current_id() is None
    assert not server.calls_for(rel.id, "up")


# ------------------------------------------------------------------ DB 兼容判据
def test_migration_required_stops_before_any_db_write(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json("migration_required")})
    proc = deploy(server, rel)
    assert proc.returncode == 4
    assert "写库启动前停止" in proc.stderr and "migrate" in proc.stderr
    assert (server.base / "releases" / rel.id).is_dir()    # 已 staged，供 migrate 复用
    assert server.current_id() is None
    assert not server.calls_for(rel.id, "up") and not server.calls_for(rel.id, "prepare")
    assert not server.accepted()


@pytest.mark.parametrize("mode", ["deploy", "migrate"])
@pytest.mark.parametrize("payload", [None, {"state": "error", "reason": "OperationalError"}])
def test_unknown_probe_error_keeps_private_diagnostics(server, make_release, mode, payload):
    rel = make_release()
    extra = ("--expect-state", LIVE_FP) if mode == "migrate" else ()
    previous_logs = {}
    for attempt in range(2):
        detail = f"connection failure {attempt}: password=private-test-value\n"
        server.set_bootstrap("status", {"rc": 1, "json": payload, "stderr": detail})
        proc = deploy(server, rel, mode=mode, extra=extra)
        assert proc.returncode == 4
        logs = set((server.base / "state").glob("bootstrap-*.log"))
        new_logs = logs - previous_logs.keys()
        assert len(new_logs) == 1
        diagnostic = new_logs.pop()
        assert str(diagnostic) in proc.stderr
        assert diagnostic.stat().st_mode & 0o777 == 0o600
        assert diagnostic.read_text() == f"\nrelease={rel.id} bootstrap status\n{detail}"
        assert detail.strip() not in proc.stdout + proc.stderr
        assert server.current_id() is None
        assert not server.calls_for(rel.id, "up") and not server.calls_for(rel.id, "prepare")
        assert not server.accepted()
        for path, content in previous_logs.items():
            assert path.read_text() == content
        previous_logs[diagnostic] = diagnostic.read_text()


def test_probe_stderr_does_not_corrupt_ready_json(server, make_release):
    rel = make_release()
    detail = "compose progress on stderr\n"
    server.set_bootstrap("status", {"rc": 0, "json": status_json(), "stderr": detail})
    proc = deploy(server, rel)
    assert proc.returncode == 0, proc.stderr
    assert server.current_id() == rel.id and server.kinds() == ["auto"]
    logs = list((server.base / "state").glob("bootstrap-*.log"))
    assert len(logs) == 1 and detail in logs[0].read_text()
    assert detail.strip() not in proc.stdout + proc.stderr


@pytest.mark.parametrize("state,needle", [
    ("unknown_revision", "runbook"), ("schema_incomplete", "迁移/初始化"),
    ("empty", "迁移/初始化"), ("bogus_state", "未知"),
])
def test_non_ready_states_are_never_presumed_safe(server, make_release, state, needle):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json(state)})
    proc = deploy(server, rel)
    assert proc.returncode == 4 and needle in proc.stderr
    assert server.current_id() is None and not server.calls_for(rel.id, "up")


def test_migration_fingerprint_mismatch_rejected(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json(mig_fp="other-fp")})
    proc = deploy(server, rel)
    assert proc.returncode == 4 and "指纹不一致" in proc.stderr
    assert server.current_id() is None


# ------------------------------------------------------------------ 旧任务/并发
def test_stale_task_rejected_by_record_reread_in_lock(server, make_release):
    rel1 = make_release(run_id=55)
    assert deploy(server, rel1).returncode == 0
    rel2 = make_release(run_id=56)
    proc = deploy(server, rel2, expect="none")             # 排队时看到的记录已过期
    assert proc.returncode == 3 and "旧任务" in proc.stderr
    assert server.current_id() == rel1.id and server.kinds() == ["auto"]
    assert deploy(server, rel2, expect="55.1").returncode == 0
    assert server.current_id() == rel2.id and server.kinds() == ["auto", "auto"]


def test_same_sha_different_builds_coexist(server, make_release):
    rel_a = make_release(run_id=55)
    rel_b = make_release(run_id=56)                        # 同 SHA、不同构建
    assert deploy(server, rel_a).returncode == 0
    assert deploy(server, rel_b, expect="55.1").returncode == 0
    releases = {p.name for p in (server.base / "releases").iterdir()}
    assert {rel_a.id, rel_b.id} <= releases
    assert [row[2] for row in server.accepted()] == [rel_a.id, rel_b.id]  # 记录不互相覆盖


def test_record_last_auto_query(server, make_release):
    proc = server.run("record", "--last-auto", "--base", str(server.base))
    assert proc.returncode == 0 and proc.stdout.strip() == "none"
    rel = make_release()
    assert deploy(server, rel).returncode == 0
    proc = server.run("record", "--last-auto", "--base", str(server.base))
    assert proc.stdout.strip() == f"{rel.sha} {rel.run_id}.{rel.attempt}"


# ------------------------------------------------------------------ 激活后失败
def test_up_failure_restores_previous_release(server, make_release):
    rel1 = make_release(run_id=55)
    assert deploy(server, rel1).returncode == 0
    rel2 = make_release(run_id=56)
    server.set_scenario_field("up_fail_ids", [rel2.id])
    proc = deploy(server, rel2, expect="55.1")
    assert proc.returncode == 5
    assert server.current_id() == rel1.id                  # 已恢复上一发布
    assert server.kinds() == ["auto", "failed", "restored"]
    assert (server.base / "releases" / rel2.id / ".deploy-failed").exists()   # 现场保留
    assert (server.base / "state" / "last-known-good").read_text().strip() == rel1.id
    assert server.calls_for(rel1.id, "run", "status")      # 恢复前做了兼容性探测
    assert server.calls_for(rel1.id, "up")
    assert any(c[0] == "logs" for c in server.docker_calls())


def test_wget_probe_failure_restores_previous(server, make_release):
    rel1 = make_release(run_id=55)
    assert deploy(server, rel1).returncode == 0
    rel2 = make_release(run_id=56)
    server.set_scenario_field("wget_fail_ids", [rel2.id])
    proc = deploy(server, rel2, expect="55.1")
    assert proc.returncode == 5 and server.current_id() == rel1.id
    assert server.kinds() == ["auto", "failed", "restored"]


@pytest.mark.parametrize("payload", [
    {"rc": 0, "json": status_json("unknown_revision")},
    {"rc": 1, "json": None, "stderr": "previous release connection failed\n"},
])
def test_restore_refused_when_previous_incompatible(server, make_release, payload):
    rel1 = make_release(run_id=55)
    assert deploy(server, rel1).returncode == 0
    rel2 = make_release(run_id=56)
    server.set_scenario_field("up_fail_ids", [rel2.id])
    server.set_bootstrap("status", payload, release=rel1.id)
    up_calls_before = len(server.calls_for(rel1.id, "up"))
    proc = deploy(server, rel2, expect="55.1")
    assert proc.returncode == 5 and "不自动恢复" in proc.stderr
    assert server.current_id() == rel2.id                  # 不翻转，现场保留
    assert server.kinds() == ["auto", "failed"]
    assert len(server.calls_for(rel1.id, "up")) == up_calls_before   # 拒绝恢复未触碰上一发布栈
    logs = list((server.base / "state").glob(f"bootstrap-deploy-{rel2.id}.*.log"))
    assert len(logs) == 1 and str(logs[0]) in proc.stderr
    content = logs[0].read_text()
    assert f"release={rel2.id} bootstrap status" in content
    assert f"release={rel1.id} bootstrap status" in content
    assert payload.get("stderr", "") in content


def test_restore_also_failing_keeps_both_scenes(server, make_release):
    rel1 = make_release(run_id=55)
    assert deploy(server, rel1).returncode == 0
    rel2 = make_release(run_id=56)
    server.set_curl_fail(["http://127.0.0.1:8000/health"])  # 探活对两个发布都失败
    proc = deploy(server, rel2, expect="55.1")
    assert proc.returncode == 5 and "立即人工介入" in proc.stderr
    assert server.kinds() == ["auto", "failed", "restore-failed"]
    assert (server.base / "releases" / rel2.id / ".deploy-failed").exists()


def test_first_deploy_failure_without_prev_keeps_scene(server, make_release):
    rel = make_release()
    server.set_scenario_field("up_fail_ids", [rel.id])
    proc = deploy(server, rel)
    assert proc.returncode == 5 and "无上一发布" in proc.stderr
    assert server.current_id() == rel.id
    assert server.kinds() == ["failed"]
    assert not (server.base / "state" / "last-known-good").exists()


# ------------------------------------------------------------------ 手动回滚/重部署
def test_rollback_preserves_auto_record_order(server, make_release):
    rel1 = make_release(run_id=55)
    rel2 = make_release(run_id=56)
    assert deploy(server, rel1).returncode == 0
    assert deploy(server, rel2, expect="55.1").returncode == 0
    proc = deploy(server, rel1, mode="rollback", expect="56.1")
    assert proc.returncode == 0, proc.stderr
    assert server.kinds() == ["auto", "auto", "manual-rollback"]   # 顺序记录完整保留
    assert server.current_id() == rel1.id
    assert (server.base / "state" / "last-known-good").read_text().strip() == rel1.id
    proc = server.run("record", "--last-auto", "--base", str(server.base))
    assert proc.stdout.strip() == f"{rel2.sha} 56.1"       # auto 记录不因回滚降级


def test_rollback_to_incompatible_release_refused(server, make_release):
    rel1 = make_release(run_id=55)
    rel2 = make_release(run_id=56)
    assert deploy(server, rel1).returncode == 0
    assert deploy(server, rel2, expect="55.1").returncode == 0
    server.set_bootstrap("status", {"rc": 0, "json": status_json("unknown_revision")},
                         release=rel1.id)
    proc = deploy(server, rel1, mode="rollback", expect="56.1")
    assert proc.returncode == 4 and "未知 alembic revision" in proc.stderr
    assert server.current_id() == rel2.id and server.kinds() == ["auto", "auto"]


def test_rollback_requires_release_on_server(server, make_release):
    rel = make_release()
    proc = deploy(server, rel, mode="rollback")
    assert proc.returncode == 2 and "服务器不存在该发布" in proc.stderr


def test_redeploy_existing_release(server, make_release):
    rel = make_release()
    assert deploy(server, rel).returncode == 0
    proc = deploy(server, rel, mode="redeploy", expect=f"{rel.run_id}.{rel.attempt}")
    assert proc.returncode == 0 and "复用已 staged 发布" in proc.stdout
    assert server.kinds() == ["auto", "manual-redeploy"]


# ------------------------------------------------------------------ migrate（D）
def test_migrate_happy_path_persists_pre_ddl_record(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json("migration_required")})
    server.set_bootstrap("status_after_prepare", {"rc": 0, "json": status_json()})
    server.set_bootstrap("check_after_prepare", {"rc": 0, "json": status_json()})
    assert deploy(server, rel).returncode == 4             # 自动部署先停在写库之前

    proc = deploy(server, rel, mode="migrate", extra=("--expect-state", LIVE_FP))
    assert proc.returncode == 0, proc.stderr
    prepare = server.calls_for(rel.id, "prepare", "--expect-state", LIVE_FP)
    assert len(prepare) == 1
    assert server.current_id() == rel.id and server.kinds() == ["migrate"]
    records = list((server.base / "state" / "migrate").glob("*.json"))
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["release_id"] == rel.id
    assert payload["expect_state"] == LIVE_FP
    assert payload["status_before"]["state"] == "migration_required"   # DDL 前状态已持久化


def test_migrate_rejects_stale_expect_state(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json("migration_required")})
    proc = deploy(server, rel, mode="migrate", extra=("--expect-state", "cd" * 32))
    assert proc.returncode == 4 and "重新授权" in proc.stderr
    assert not server.calls_for(rel.id, "prepare")
    assert not (server.base / "state" / "migrate").exists()
    assert server.current_id() is None


def test_migrate_rejects_unknown_revision_state(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json("unknown_revision")})
    proc = deploy(server, rel, mode="migrate", extra=("--expect-state", LIVE_FP))
    assert proc.returncode == 4 and "runbook" in proc.stderr
    assert not server.calls_for(rel.id, "prepare")


def test_migrate_prepare_failure_preserves_scene_without_rollback(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json("migration_required")})
    server.set_bootstrap("prepare", {"rc": 2, "json": {"state": "error", "reason": "partial"}})
    proc = deploy(server, rel, mode="migrate", extra=("--expect-state", LIVE_FP))
    assert proc.returncode == 6
    assert "不做自动回滚" in proc.stderr
    assert not server.calls_for(rel.id, "up")              # 未激活
    assert server.current_id() is None
    records = list((server.base / "state" / "migrate").glob("*.json"))
    assert len(records) == 1                               # DDL 前记录仍在，供人工恢复
    assert not server.accepted()


def test_migrate_check_failure_keeps_diagnostics_without_rollback(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json("migration_required")})
    detail = "post-migration check connection failed\n"
    server.set_bootstrap("check_after_prepare", {"rc": 2, "json": None, "stderr": detail})
    proc = deploy(server, rel, mode="migrate", extra=("--expect-state", LIVE_FP))
    assert proc.returncode == 6 and "不自动回退" in proc.stderr
    logs = list((server.base / "state").glob("bootstrap-*.log"))
    assert len(logs) == 1 and str(logs[0]) in proc.stderr
    content = logs[0].read_text()
    assert f"release={rel.id} bootstrap status" in content
    assert f"release={rel.id} bootstrap check\n{detail}" in content
    assert detail.strip() not in proc.stdout + proc.stderr
    assert len(list((server.base / "state" / "migrate").glob("*.json"))) == 1
    assert not server.calls_for(rel.id, "up")
    assert server.current_id() is None and not server.accepted()


def test_migrate_probe_failure_never_rolls_back_image(server, make_release):
    rel = make_release()
    server.set_bootstrap("status", {"rc": 0, "json": status_json("migration_required")})
    server.set_bootstrap("status_after_prepare", {"rc": 0, "json": status_json()})
    server.set_bootstrap("check_after_prepare", {"rc": 0, "json": status_json()})
    server.set_curl_fail(["https://127.0.0.1/health"])
    proc = deploy(server, rel, mode="migrate", extra=("--expect-state", LIVE_FP))
    assert proc.returncode == 5 and "禁止自动回退镜像" in proc.stderr
    assert server.current_id() == rel.id                   # DB 已迁移，镜像保持新版本
    assert server.kinds() == ["failed"]


# ------------------------------------------------------------------ 镜像回收（#662）
def deploy_series(server, make_release, tags):
    """按顺序部署一串发布，返回 [(rel, refs)]。tag 相同 ⇒ 镜像引用相同（层复用）。"""
    made = []
    expect = "none"
    for index, tag in enumerate(tags, start=55):
        rel = make_release(run_id=index, image_tag=tag)
        proc = deploy(server, rel, expect=expect)
        assert proc.returncode == 0, f"{rel.id}: {proc.stderr}"
        made.append((rel, set(image_refs(tag))))
        expect = f"{index}.1"
    return made


def rmi_targets(server):
    return {c[1] for c in server.docker_calls() if c and c[0] == "rmi"}


def test_pruned_release_images_are_reclaimed(server, make_release):
    """超出保留窗口的发布，其独占镜像必须被回收——磁盘占用不随部署次数单调增长。

    断的是假 docker 的**镜像存在集**而不是 argv：回收调用在 mark_success 的 `|| warn`
    保护下，只断「调用过 rmi」会放过「一张都没删成」的静默退化。
    """
    made = deploy_series(server, make_release, [f"t{i}" for i in range(7)])
    refs = [r for _, r in made]
    # KEEP_RELEASES=5 ⇒ 最后 5 条 accepted + LKG + 本次保留，前 2 个发布被剪
    assert rmi_targets(server) == (refs[0] | refs[1]) - {NGINX_REF}
    assert NGINX_REF not in rmi_targets(server)            # 基座镜像跨发布共享，恒不回收
    assert set(server.images()) == set().union(*refs[2:])  # 幸存发布的镜像一个没少
    for ref in refs[0] - {NGINX_REF}:                      # 被剪发布的镜像确实已不在集内
        assert server.docker("image", "inspect", ref).returncode == 1
    for ref in made[-1][1]:                                # 在用与回滚目标仍可解析
        assert server.docker("image", "inspect", ref).returncode == 0


def test_reclaim_protects_failed_scene_images(server, make_release):
    """保护集 = 全部幸存发布目录，而不是 keep 集合本身。

    rel1 留有失败现场（`.deploy-failed`，被保留但不进 keep），rel2 与它复用同一组
    镜像；rel2 出窗被剪时，若保护集只按 keep 求差，这组仍在用的镜像会被删掉——
    「离线回滚到失败发布」当场换成「必须能连 registry」。这条同时抓得住「根本不减
    保护集」那种更粗糙的写法。
    """
    # 8 次部署：rel2（与现场共用镜像）先出窗、rel3 随后出窗作对照
    tags = ["scene", "scene"] + [f"t{i}" for i in range(2, 8)]
    expect = "none"
    scene_release = None
    for index, tag in enumerate(tags, start=55):
        rel = make_release(run_id=index, image_tag=tag)
        proc = deploy(server, rel, expect=expect)
        assert proc.returncode == 0, f"{rel.id}: {proc.stderr}"
        expect = f"{index}.1"
        if index == 55:
            scene_release = rel
            # 复刻 fail_after_activate 留下的现场标记：目录保留，但已不在 accepted 尾窗内
            (server.base / "releases" / rel.id / ".deploy-failed").touch()
    scene_refs = set(image_refs("scene")) - {NGINX_REF}
    assert rmi_targets(server) & scene_refs == set()       # 失败现场的镜像一张都不能动
    assert (server.base / "releases" / scene_release.id).is_dir()
    assert scene_refs <= set(server.images())
    for ref in scene_refs:
        assert server.docker("image", "inspect", ref).returncode == 0
    # 对照：同一次运行里确实发生了回收（rel1 之外的 t2 出窗时无人引用），不是「什么都没做」
    assert rmi_targets(server) >= set(image_refs("t2")) - {NGINX_REF}


def test_rmi_failure_only_warns_and_deploy_still_succeeds(server, make_release):
    """回收失败不得把已探活通过的部署染红（warn-only，不回撤、不重触发）。"""
    # 最后一次部署剪的是第 2 个发布（KEEP_RELEASES=5），让它的 backend 删失败
    doomed_backend = image_refs("t1")[0]
    server.set_scenario_field("rmi_fail", [doomed_backend])
    expect, last = "none", None
    for index in range(55, 62):
        rel = make_release(run_id=index, image_tag=f"t{index - 55}")
        last = deploy(server, rel, expect=expect)
        assert last.returncode == 0, f"{rel.id}: {last.stderr}"
        expect = f"{index}.1"
    assert server.kinds() == ["auto"] * 7
    assert server.current_id() == rel.id
    assert "镜像回收失败" in last.stderr                   # 响亮告知，但不改判死
    assert doomed_backend in server.images()               # 删除被拒，镜像仍占盘
