"""release_bundle.py 的守门测试（issue #537 第四批 C）。

判据与计划一致：来源冒充（revision/digest 不一致）、产物缺失/损坏、同 SHA 不同
构建不可覆盖、以及「verify 逐字节重建期望产物」——篡改任何一层（磁盘文件、内嵌
内容、images.env、manifest）都必须现形。真实 sha256sum -c 兼容性用子进程证明，
不以 Python 重实现代替服务器端工具行为。
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(Path(__file__).resolve().parent)]
import release_bundle as rb  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
BACKEND_REPO = "registry.example.invalid/ns/investring-backend"
FRONTEND_REPO = "registry.example.invalid/ns/investring-frontend"
BACKEND_DIGEST = "sha256:" + "ab" * 32
FRONTEND_DIGEST = "sha256:" + "cd" * 32
NGINX_DIGEST = "sha256:" + "ef" * 32


def report_payload(**overrides):
    report = {
        "ok": True, "error": None, "expected_revision": SHA,
        "images": {
            "backend": {"ref": f"{BACKEND_REPO}@{BACKEND_DIGEST}", "digest": BACKEND_DIGEST,
                        "id": "sha256:backend", "revision_label": SHA},
            "frontend": {"ref": f"{FRONTEND_REPO}@{FRONTEND_DIGEST}", "digest": FRONTEND_DIGEST,
                         "id": "sha256:frontend", "revision_label": SHA},
            "nginx": {"ref": "nginx:1.27-alpine", "digest": NGINX_DIGEST,
                      "digest_ref": f"nginx@{NGINX_DIGEST}", "id": "sha256:nginx"},
        },
        "bootstrap": {"state_before": "empty", "fingerprint_before": "fp-before",
                      "migration_fingerprint": "mig-fp", "expected_heads": ["0017"],
                      "dialect": "mysql", "fingerprint_after_prepare": "fp-after"},
        "checks": [], "cleanup": {},
    }
    report.update(overrides)
    return report


@pytest.fixture
def root(tmp_path):
    repo = tmp_path / "root"
    (repo / "nginx").mkdir(parents=True)
    (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (repo / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (repo / "nginx" / "nginx.conf").write_text("events {}\n", encoding="utf-8")
    return repo


@pytest.fixture
def make_bundle(tmp_path, root):
    """build 一次并返回 (bundle目录, 报告文件)；报告可定制以驱动拒绝路径。"""
    def make(report=None, out="bundle", **kwargs):
        report = report_payload() if report is None else report
        report_path = tmp_path / f"report-{out}.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        out_dir = tmp_path / out
        argv = ["build", "--smoke-report", str(report_path), "--sha", SHA,
                "--run-id", str(kwargs.pop("run_id", 123)),
                "--run-attempt", str(kwargs.pop("run_attempt", 1)),
                "--out-dir", str(out_dir), "--root", str(root), *kwargs.pop("extra", [])]
        rc = rb.main(argv)
        return rc, out_dir, report_path
    return make


def verify(bundle_dir, *extra):
    return rb.main(["verify", "--bundle-dir", str(bundle_dir), *extra])


# ---------------------------------------------------------------------- 构建
def test_build_then_verify_happy_path(make_bundle):
    rc, bundle_dir, report_path = make_bundle()
    assert rc == 0
    assert verify(bundle_dir, "--sha", SHA) == 0
    bundle = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["schema"] == 1
    assert bundle["git"] == {"sha": SHA}
    assert bundle["build"]["run_id"] == 123 and bundle["build"]["run_attempt"] == 1
    assert bundle["build"]["smoke_report_sha256"] == "sha256:" + hashlib.sha256(
        report_path.read_bytes()).hexdigest()
    assert bundle["version"] == "9.9.9"
    assert bundle["images"]["backend"] == {"repo": BACKEND_REPO, "digest": BACKEND_DIGEST,
                                           "ref": f"{BACKEND_REPO}@{BACKEND_DIGEST}"}
    assert bundle["images"]["nginx"]["digest_ref"] == f"nginx@{NGINX_DIGEST}"
    assert bundle["database"] == {"migration_fingerprint": "mig-fp", "expected_heads": ["0017"]}
    assert bundle["files"]["docker-compose.yml"]["content"] == "services: {}\n"
    assert bundle["files"]["nginx/nginx.conf"]["content"] == "events {}\n"


def test_build_writes_recoverable_files_and_images_env(make_bundle, root):
    rc, bundle_dir, _ = make_bundle()
    assert rc == 0
    for rel in rb.BUNDLE_FILES:
        assert (bundle_dir / "files" / rel).read_bytes() == (root / rel).read_bytes()
    env = (bundle_dir / "images.env").read_text(encoding="utf-8")
    assert f"BACKEND_IMAGE_REF={BACKEND_REPO}@{BACKEND_DIGEST}\n" in env
    assert f"FRONTEND_IMAGE_REF={FRONTEND_REPO}@{FRONTEND_DIGEST}\n" in env
    assert f"NGINX_IMAGE_REF=nginx@{NGINX_DIGEST}\n" in env


@pytest.mark.skipif(shutil.which("sha256sum") is None, reason="sha256sum 不可用")
def test_manifest_is_sha256sum_compatible(make_bundle):
    _, bundle_dir, _ = make_bundle()
    proc = subprocess.run(["sha256sum", "-c", "manifest.sha256"], cwd=bundle_dir,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count(": OK") == len(rb.MANIFEST_PATHS)


def test_same_sha_different_builds_coexist(make_bundle):
    rc1, first, _ = make_bundle(out="b1", run_id=111)
    rc2, second, _ = make_bundle(out="b2", run_id=222)
    assert (rc1, rc2) == (0, 0)
    assert first != second
    assert verify(first) == 0 and verify(second) == 0
    a = json.loads((first / "bundle.json").read_text(encoding="utf-8"))
    b = json.loads((second / "bundle.json").read_text(encoding="utf-8"))
    assert a["build"]["run_id"] != b["build"]["run_id"]


def test_existing_out_dir_refuses_overwrite(make_bundle, tmp_path):
    rc, bundle_dir, _ = make_bundle()
    assert rc == 0
    # 同目录重建必须拒绝：已生成/已选发布记录不可覆盖
    report_path = tmp_path / "report-bundle.json"
    rc2 = rb.main(["build", "--smoke-report", str(report_path), "--sha", SHA,
                   "--run-id", "999", "--run-attempt", "1",
                   "--out-dir", str(bundle_dir), "--root", str(bundle_dir.parent / "root")])
    assert rc2 == 2


# ------------------------------------------------------------------ 构建拒绝
@pytest.mark.parametrize("mutate,message", [
    (lambda r: r.update(ok=False), "冒烟未通过"),
    (lambda r: r.update(expected_revision="f" * 40), "revision"),
    (lambda r: r["images"].pop("nginx"), "images.nginx"),
    (lambda r: r["images"]["nginx"].update(digest=None), "nginx.digest"),
    (lambda r: r["images"]["nginx"].update(digest_ref=None), "digest_ref"),
    (lambda r: r["images"]["backend"].update(ref="investring-backend:smoke"), "digest 引用"),
    (lambda r: r["images"]["backend"].update(digest="sha256:" + "00" * 32), "不一致"),
    (lambda r: r["bootstrap"].update(dialect="sqlite"), "MySQL"),
    (lambda r: r["bootstrap"].update(state_before="ready"), "显式引导链"),
    (lambda r: r["bootstrap"].update(migration_fingerprint=""), "migration_fingerprint"),
    (lambda r: r["bootstrap"].update(expected_heads=[]), "expected_heads"),
])
def test_build_rejects_bad_smoke_report(make_bundle, mutate, message):
    report = report_payload()
    mutate(report)
    rc, _, _ = make_bundle(report=report)
    assert rc == 2


def test_build_rejects_bad_arguments(make_bundle, tmp_path, root):
    def build_with(flag, value, name):
        report_path = tmp_path / f"report-{name}.json"
        report_path.write_text(json.dumps(report_payload()), encoding="utf-8")
        argv = ["build", "--smoke-report", str(report_path), "--sha", SHA,
                "--run-id", "1", "--run-attempt", "1",
                "--out-dir", str(tmp_path / name), "--root", str(root)]
        if flag in argv:
            argv[argv.index(flag) + 1] = value
        else:
            argv += [flag, value]
        return rb.main(argv)

    assert build_with("--sha", "abc", "bad-sha") == 2            # 非 40 位十六进制
    assert build_with("--sha", SHA.upper(), "upper-sha") == 0    # 大写归一化后与报告一致
    assert build_with("--run-id", "0", "bad-run") == 2          # 非正整数
    assert build_with("--workflow", "", "bad-workflow") == 2    # 构建身份不可为空


def test_build_rejects_missing_or_bad_repo_files(make_bundle, tmp_path, root):
    (root / "nginx" / "nginx.conf").unlink()
    rc, _, _ = make_bundle(out="missing-conf")
    assert rc == 2
    (root / "nginx" / "nginx.conf").write_text("events {}\n", encoding="utf-8")
    (root / "VERSION").write_text("  \n", encoding="utf-8")
    rc, _, _ = make_bundle(out="bad-version")
    assert rc == 2


# ------------------------------------------------------------------ 校验拒绝
def test_verify_rejects_sha_mismatch(make_bundle):
    _, bundle_dir, _ = make_bundle()
    assert verify(bundle_dir, "--sha", "f" * 40) == 1


def test_verify_detects_on_disk_file_tamper(make_bundle):
    _, bundle_dir, _ = make_bundle()
    target = bundle_dir / "files" / "docker-compose.yml"
    target.write_text("services: {evil: true}\n", encoding="utf-8")
    assert verify(bundle_dir) == 1


def test_verify_detects_embedded_content_tamper(make_bundle):
    _, bundle_dir, _ = make_bundle()
    path = bundle_dir / "bundle.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["files"]["nginx/nginx.conf"]["content"] = "events {evil}\n"
    path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    # 内嵌内容与其 sha256 不再一致
    assert verify(bundle_dir) == 1


def test_verify_detects_coherent_bundle_swap_via_manifest(make_bundle):
    """替换整个 bundle.json 并重算内嵌摘要与 images.env——manifest 仍须现形。"""
    _, bundle_dir, _ = make_bundle()
    path = bundle_dir / "bundle.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["version"] = "10.0.0"
    path.write_text(json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    assert verify(bundle_dir) == 1


def test_verify_detects_images_env_tamper(make_bundle):
    _, bundle_dir, _ = make_bundle()
    env = bundle_dir / "images.env"
    env.write_text(env.read_text(encoding="utf-8").replace(
        f"NGINX_IMAGE_REF=nginx@{NGINX_DIGEST}", "NGINX_IMAGE_REF=nginx:1.27-alpine"),
        encoding="utf-8")
    assert verify(bundle_dir) == 1


def test_verify_detects_manifest_tamper(make_bundle):
    _, bundle_dir, _ = make_bundle()
    manifest = bundle_dir / "manifest.sha256"
    lines = manifest.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = "0" * 64 + lines[0][64:]
    manifest.write_text("".join(lines), encoding="utf-8")
    assert verify(bundle_dir) == 1


def test_verify_missing_parts(make_bundle):
    _, bundle_dir, _ = make_bundle()
    (bundle_dir / "manifest.sha256").unlink()
    assert verify(bundle_dir) == 1
    assert verify(bundle_dir.parent / "nonexistent") == 2


def test_verify_rejects_unknown_top_level_key(make_bundle):
    _, bundle_dir, _ = make_bundle()
    path = bundle_dir / "bundle.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["attestation"] = {"trusted": True}
    path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    assert verify(bundle_dir) == 1


def test_verify_rejects_duplicate_json_key(make_bundle):
    _, bundle_dir, _ = make_bundle()
    path = bundle_dir / "bundle.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"schema": 1,', '"schema": 1, "schema": 1,', 1),
                    encoding="utf-8")
    assert verify(bundle_dir) == 2  # 非法 JSON 属 harness 错误，与 ci_gate 语义一致


def test_verify_detects_images_field_tamper(make_bundle):
    """digest 换绑到别的制品：images.env 同步重算也过不了 manifest 层。"""
    _, bundle_dir, _ = make_bundle()
    path = bundle_dir / "bundle.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["images"]["backend"]["digest"] = "sha256:" + "99" * 32
    bundle["images"]["backend"]["ref"] = f"{BACKEND_REPO}@sha256:" + "99" * 32
    path.write_text(json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    (bundle_dir / "images.env").write_text(rb._render_images_env(bundle["images"]),
                                           encoding="utf-8")
    assert verify(bundle_dir) == 1
