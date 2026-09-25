"""server_deploy.sh 的子进程测试（issue #537 第四批 C/D）。

真实执行 bash 脚本，只把 docker/curl 换成场景驱动的假可执行文件（服务器端不依赖
Python，但测试侧用 Python 写 fake 最精确）。覆盖计划要求的失败矩阵：产物缺失/
损坏、同 SHA 不同构建、旧任务晚到、up/reload/探活失败、恢复失败、未知 DB 状态、
迁移授权过期与部分执行失败、手动回滚保留顺序记录。

断言重点不是"命令跑过"，而是状态机结果：current 指向、accepted-releases 记录
序列、LKG、失败现场标记、以及"拒绝路径零副作用"（未拉镜像/未 up/未激活）。
"""
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

if argv and argv[0] == "pull":
    if argv[1] in scenario.get("pull_fail", []):
        done(1, "", "pull access denied\n")
    done(0)
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
import json, os, sys

argv = sys.argv[1:]
cfg = json.load(open(os.environ["FAKE_CURL"]))
with open(os.environ["FAKE_CURL_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")
url = argv[-1]
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
            "bootstrap_by_release": {}, "pull_fail": [],
            "up_fail_ids": [], "restart_fail_ids": [], "wget_fail_ids": []}


def smoke_report(sha):
    return {
        "ok": True, "error": None, "expected_revision": sha,
        "images": {
            "backend": {"ref": BACKEND_REF, "digest": "sha256:" + "ab" * 32,
                        "id": "sha256:backend", "revision_label": sha},
            "frontend": {"ref": FRONTEND_REF, "digest": "sha256:" + "cd" * 32,
                         "id": "sha256:frontend", "revision_label": sha},
            "nginx": {"ref": "nginx:1.27-alpine", "digest": "sha256:" + "ef" * 32,
                      "digest_ref": NGINX_REF, "id": "sha256:nginx"},
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

    return SimpleNamespace(
        base=base, env=env, run=run, set_bootstrap=set_bootstrap,
        set_scenario_field=set_scenario_field, set_curl_fail=set_curl_fail,
        docker_calls=lambda: read_jsonl(docker_log), curl_calls=lambda: read_jsonl(curl_log),
        current_id=current_id, accepted=accepted, calls_for=calls_for,
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
    """用真实 release_bundle.build 产包并复制到 incoming 落地目录。"""
    def make(*, sha=SHA, run_id=55, attempt=1):
        release_id = f"{sha[:7]}-{run_id}.{attempt}"
        report_path = tmp_path / f"report-{run_id}-{attempt}.json"
        report_path.write_text(json.dumps(smoke_report(sha)), encoding="utf-8")
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
