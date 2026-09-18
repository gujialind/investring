"""#539：仅用标准库与 pytest 验证隔离契约工具，不加载真实后端。"""

import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SPEC = {"openapi": "3.1.0", "info": {"title": "测试", "version": "test"},
        "paths": {"/probe": {"get": {"responses": {"200": {"description": "成功"}}}}},
        "components": {"schemas": {"Probe": {"type": "object"}}}}
URL = "https://probe:private-password@example.invalid/openapi.json?token=private-token"


@pytest.fixture
def modules(monkeypatch, tmp_path):
    # 显式路径加载并恢复模块缓存，避开其他 scripts 测试留下的 sys.path。
    monkeypatch.syspath_prepend(str(BACKEND))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    for name in {"app", "requests"} | {n for n in sys.modules if n.startswith(("app.", "requests."))}:
        monkeypatch.setitem(sys.modules, name, None)

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    runtime = load("openapi_runtime", BACKEND / "openapi_runtime.py")
    checker = load("_isolation_checker", BACKEND / "check_openapi.py")
    exporter = load("_isolation_exporter", BACKEND / "export_openapi.py")
    release = load("_isolation_release", ROOT / "scripts" / "release.py")
    assert runtime.OPENAPI_PATH == checker.OPENAPI_PATH == exporter.OPENAPI_PATH == BACKEND / "openapi.json"
    canonical = tmp_path / "canonical" / "openapi.json"
    canonical.parent.mkdir()
    for module in (runtime, checker, exporter):
        monkeypatch.setattr(module, "OPENAPI_PATH", canonical)
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(runtime=runtime, checker=checker, exporter=exporter, release=release)


FAKE_APP = '''
import hashlib, json, os, sqlite3, sys
from pathlib import Path
from types import SimpleNamespace
cwd = Path.cwd()
db = Path(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
connection = sqlite3.connect(db)
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("CREATE TABLE probe (value INTEGER)")
connection.commit()
# 保留 WAL/SHM 残留供父进程清理，避免只验证 SQLite 退出时的自清理。
connection.close()
for suffix in ("-wal", "-shm"):
    Path(str(db) + suffix).touch()
probe = {
    "cwd": str(cwd), "pid": os.getpid(), "dotenv": (cwd / ".env").exists(),
    "env": {k: v for k, v in os.environ.items() if k != "SECRET_KEY"},
    "secret_hash": hashlib.sha256(os.environ["SECRET_KEY"].encode()).hexdigest(),
    "files": sorted(str(p) for p in cwd.glob("openapi.db*")),
    "isolated": sys.flags.isolated, "no_bytecode": sys.dont_write_bytecode,
}
Path(__file__).with_name(f"{os.getpid()}.json").write_text(json.dumps(probe), encoding="utf-8")
app = SimpleNamespace(openapi=lambda: {
    "openapi": "3.1.0", "info": {"title": "隔离测试", "version": "test", "probe": probe}, "paths": {}
})
print("只用于测试的初始化日志")
'''


@pytest.fixture
def worker(modules, monkeypatch, tmp_path):
    runtime = modules.runtime
    package = tmp_path / "fakebackend" / "app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")

    def install(tail=""):
        (package / "main.py").write_text(FAKE_APP + tail + "\n", encoding="utf-8")

    install()
    monkeypatch.setattr(runtime, "BACKEND_DIR", package.parent)
    monkeypatch.setattr(runtime.tempfile, "tempdir", str(tmp_path))
    calls, real_run = [], runtime.subprocess.run

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        result = real_run(argv, **kwargs)
        kwargs["remaining_files"] = list(Path(kwargs["cwd"]).glob("openapi.db*"))
        return result

    monkeypatch.setattr(runtime.subprocess, "run", run)
    return SimpleNamespace(runtime=runtime, package=package, install=install, calls=calls)


def assert_clean(worker):
    assert worker.calls
    assert all(not Path(kwargs["cwd"]).exists() for _, kwargs in worker.calls)


def test_child_environment_and_schema_are_isolated(worker, monkeypatch, tmp_path, capsys):
    keys = "DATABASE_URL TEST_DB_URL DB_PASSWORD MYSQL_PASSWORD MYSQL_ROOT_PASSWORD APP_VERSION TUSHARE_TOKEN SECRET_KEY PYTHONPATH PYTHONHOME"
    injected = {key: f"parent-{key}-539" for key in keys.split()}
    injected.update(DATABASE_URL="mysql+pymysql://user:db-password@production.invalid/db",
                    TEST_DB_URL="postgresql://user:test-password@production.invalid/test")
    for key, value in injected.items():
        monkeypatch.setenv(key, value)
    for key in ("DEBUG", "SCHEDULER_ENABLED"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("PATH", os.environ.get("PATH", os.defpath))
    for key in ("SYSTEMROOT", "WINDIR"):
        monkeypatch.setenv(key, "/test-system")
    (tmp_path / ".env").write_text("SECRET_KEY=dotenv-secret\n", encoding="utf-8")
    (worker.package.parent / ".env").write_text("SECRET_KEY=backend-secret\n", encoding="utf-8")
    before = dict(os.environ)
    schema = worker.runtime.generate_schema()
    argv, options = worker.calls[0]
    env, directory = options["env"], str(options["cwd"])
    assert set(env) == {"PATH", "SYSTEMROOT", "WINDIR", "HOME", "TMPDIR", "TMP", "TEMP",
                        "LANG", "DATABASE_URL", "DEBUG", "SCHEDULER_ENABLED", "SECRET_KEY"}
    assert all(env[k] == before[k] for k in ("PATH", "SYSTEMROOT", "WINDIR"))
    assert all(env[k] == directory for k in ("HOME", "TMPDIR", "TMP", "TEMP"))
    assert env["DATABASE_URL"] == f"sqlite:///{directory}/openapi.db"
    assert env["DEBUG"] == env["SCHEDULER_ENABLED"] == "false"
    assert env["LANG"] == "C.UTF-8" and len(bytes.fromhex(env["SECRET_KEY"])) == 32
    probe = schema["info"]["probe"]
    assert probe["env"] == {k: v for k, v in env.items() if k != "SECRET_KEY"}
    assert probe["cwd"] == directory and not probe["dotenv"]
    assert probe["isolated"] == 1 and probe["no_bytecode"] is True
    assert "-I" in argv and "-B" in argv and options["timeout"] == 60
    assert set(schema) == {"openapi", "info", "paths"} and schema["paths"] == {}
    assert schema["info"]["title"] == "隔离测试" and schema["info"]["version"] == "test"
    text = json.dumps(schema) + capsys.readouterr().out
    assert all(value not in text for value in (*injected.values(), env["SECRET_KEY"], "dotenv-secret", "backend-secret"))
    assert "只用于测试的初始化日志" not in text
    assert dict(os.environ) == before
    assert_clean(worker)


def test_concurrent_workers_have_unique_resources_and_clean_sqlite(worker):
    with ThreadPoolExecutor(max_workers=4) as pool:
        schemas = list(pool.map(lambda _: worker.runtime.generate_schema(), range(4)))
    probes = [schema["info"]["probe"] for schema in schemas]
    assert len(worker.calls) == len({p["cwd"] for p in probes}) == 4
    assert len({p["secret_hash"] for p in probes}) == 4
    assert all(len(kwargs["remaining_files"]) == 3 for _, kwargs in worker.calls)
    for probe in probes:
        assert [Path(p).name for p in probe["files"]] == ["openapi.db", "openapi.db-shm", "openapi.db-wal"]
        assert all(Path(p).parent == Path(probe["cwd"]) and not Path(p).exists() for p in probe["files"])
    assert not list(worker.package.rglob("*.pyc"))
    assert_clean(worker)


@pytest.mark.parametrize("phase", ["import", "schema"])
def test_worker_error_redacts_stderr_and_cleans_database(worker, capsys, phase):
    failure = f'raise ImportError(os.environ["SECRET_KEY"] + " {URL}")'
    if phase == "schema":
        failure = f"def broken_schema():\n    {failure}\napp.openapi = broken_schema"
    worker.install(failure)
    with pytest.raises(RuntimeError, match="ImportError") as error:
        worker.runtime.generate_schema()
    captured = capsys.readouterr()
    diagnostic = str(error.value) + captured.out + captured.err
    assert "[REDACTED]" in diagnostic and "[REDACTED_URL]" in diagnostic
    assert all(secret not in diagnostic for secret in (URL, "private-password", "private-token", worker.calls[0][1]["env"]["SECRET_KEY"]))
    audit, = worker.package.glob("*.json")
    assert len(json.loads(audit.read_text())["files"]) == 3
    assert_clean(worker)


def test_timeout_reaps_real_pid_before_removing_directory(worker, monkeypatch):
    runtime = worker.runtime
    worker.install("import time\ntime.sleep(30)")
    monkeypatch.setattr(runtime, "TIMEOUT_SECONDS", 0.2)
    pids, cleaned = [], []
    popen, cleanup = runtime.subprocess.Popen, runtime.tempfile.TemporaryDirectory.cleanup

    class TrackedPopen(popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pids.append(self.pid)
            os.kill(self.pid, 0)

    def checked_cleanup(directory):
        assert Path(directory.name).is_dir() and len(pids) == 1
        # 必须在删除目录之前确认真实进程已消失，而不是只断言超时异常。
        with pytest.raises(ProcessLookupError):
            os.kill(pids[0], 0)
        cleanup(directory)
        cleaned.append(directory.name)

    monkeypatch.setattr(runtime.subprocess, "Popen", TrackedPopen)
    monkeypatch.setattr(runtime.tempfile.TemporaryDirectory, "cleanup", checked_cleanup)
    with pytest.raises(RuntimeError, match="超时"):
        runtime.generate_schema()
    assert len(cleaned) == 1
    assert_clean(worker)


def test_cleanup_exception_is_not_success(worker, monkeypatch):
    cleanup = worker.runtime.tempfile.TemporaryDirectory.cleanup

    def failing_cleanup(directory):
        cleanup(directory)
        raise OSError("模拟清理失败")

    monkeypatch.setattr(worker.runtime.tempfile.TemporaryDirectory, "cleanup", failing_cleanup)
    with pytest.raises(RuntimeError, match="OSError"):
        worker.runtime.generate_schema()
    assert_clean(worker)


@pytest.mark.parametrize("payload", [None, "not-json", "{}", "[]"])
def test_missing_or_invalid_worker_json(worker, monkeypatch, payload):
    code = "pass" if payload is None else (
        "import sys\nfrom pathlib import Path\n"
        f"Path(sys.argv[2]).write_text({payload!r}, encoding='utf-8')"
    )
    monkeypatch.setattr(worker.runtime, "_SCHEMA_CODE", code)
    with pytest.raises(RuntimeError, match="生成结果处理失败"):
        worker.runtime.generate_schema()
    assert_clean(worker)


@pytest.mark.parametrize("working_directory", ["caller", "fakebackend"])
def test_offline_export_and_checker_share_schema_across_cwd(
    modules, worker, monkeypatch, tmp_path, working_directory,
):
    worker.install(f"app.openapi = lambda: {SPEC!r}")
    cwd = tmp_path / working_directory
    cwd.mkdir(exist_ok=True)
    (cwd / ".env").write_text("SECRET_KEY=dotenv-secret\n", encoding="utf-8")
    # 调用目录下的同名契约不能取代仓库契约，也不能被默认离线导出覆盖。
    decoy = cwd / "openapi.json"
    decoy.write_bytes(b"caller-contract")
    monkeypatch.chdir(cwd)
    assert modules.exporter.main(["--offline"]) == 0
    original = modules.checker.OPENAPI_PATH.read_bytes()
    assert json.loads(original) == SPEC
    assert modules.checker.main() == 0
    assert modules.checker.OPENAPI_PATH.read_bytes() == original
    assert decoy.read_bytes() == b"caller-contract"
    assert len(worker.calls) == 2
    for audit in worker.package.glob("*.json"):
        assert not json.loads(audit.read_text(encoding="utf-8"))["dotenv"]
    assert_clean(worker)


@pytest.mark.parametrize("field", [None, "paths", "operation", "components", "info"])
def test_checker_compares_whole_schema_without_writing(modules, monkeypatch, field):
    checker = modules.checker
    original = json.dumps(SPEC, ensure_ascii=False, indent=4).encode() + b"\r\n"
    checker.OPENAPI_PATH.write_bytes(original)
    generated = dict(reversed(list(json.loads(json.dumps(SPEC)).items())))
    if field == "operation":
        generated["paths"]["/probe"]["get"]["responses"]["200"]["description"] = "定义漂移"
    elif field:
        generated[field] = {"changed": {}}
    monkeypatch.setattr(checker, "generate_schema", Mock(return_value=generated))
    assert checker.main() == (1 if field else 0)
    assert checker.OPENAPI_PATH.read_bytes() == original


@pytest.mark.parametrize("failure", ["missing", "read", "json", "schema", "worker"])
def test_checker_execution_errors_return_two(modules, monkeypatch, failure):
    checker = modules.checker
    original = {"json": b"{", "schema": b"{}"}.get(failure, json.dumps(SPEC).encode())
    if failure != "missing":
        checker.OPENAPI_PATH.write_bytes(original)
    generate = Mock(return_value=SPEC, side_effect=RuntimeError("模拟 worker 失败") if failure == "worker" else None)
    monkeypatch.setattr(checker, "generate_schema", generate)
    if failure == "read":
        monkeypatch.setattr(checker, "open", Mock(side_effect=PermissionError("模拟读取失败")), raising=False)
    assert checker.main() == 2
    if failure == "missing":
        assert not checker.OPENAPI_PATH.exists()
    else:
        assert checker.OPENAPI_PATH.read_bytes() == original
    assert generate.call_count == (1 if failure == "worker" else 0)


@pytest.mark.parametrize("args,filename", [
    (["--offline"], "canonical/openapi.json"), (["--offline", "-o", "chosen.json"], "chosen.json"),
    (["--offline", "--output", "chosen.json"], "chosen.json"), ([], "openapi.json"),
    ([URL, "legacy.json"], "legacy.json"), ([URL, "-o", "chosen.json"], "chosen.json"),
])
def test_exporter_offline_and_legacy_paths(modules, monkeypatch, tmp_path, args, filename):
    exporter = modules.exporter
    generate, fetch = Mock(return_value=SPEC), Mock(return_value=SPEC)
    monkeypatch.setattr(exporter, "generate_schema", generate)
    monkeypatch.setattr(exporter, "fetch_online_schema", fetch)
    assert exporter.main(args) == 0
    assert json.loads((tmp_path / filename).read_text(encoding="utf-8")) == SPEC
    if "--offline" in args:
        generate.assert_called_once_with()
        fetch.assert_not_called()
    else:
        fetch.assert_called_once_with(URL if args else exporter.DEFAULT_URL)
        generate.assert_not_called()


@pytest.mark.parametrize("offline", [True, False])
@pytest.mark.parametrize("invalid", [True, False])
def test_exporter_failure_preserves_target(modules, monkeypatch, tmp_path, offline, invalid):
    target = tmp_path / "old.json"
    target.write_bytes(b"original\r\n")
    source = Mock(return_value={}, side_effect=None if invalid else RuntimeError("模拟生成失败"))
    monkeypatch.setattr(modules.exporter, "generate_schema" if offline else "fetch_online_schema", source)
    args = ["--offline", "-o", str(target)] if offline else [URL, str(target)]
    assert modules.exporter.main(args) == 1
    assert target.read_bytes() == b"original\r\n" and not list(tmp_path.glob(".old.json.*.tmp"))


@pytest.mark.parametrize("failure", [None, "replace", "write"])
def test_atomic_write_and_failure_cleanup(modules, monkeypatch, tmp_path, failure):
    target = tmp_path / "contract.json"
    target.write_bytes(b"original\r\n")
    replace, create = os.replace, modules.runtime.tempfile.NamedTemporaryFile
    replacements = []

    def checked_replace(source, destination):
        assert Path(source).parent == target.parent and Path(destination) == target
        assert json.loads(Path(source).read_text(encoding="utf-8")) == SPEC
        assert target.read_bytes() == b"original\r\n"
        replacements.append(source)
        if failure == "replace":
            raise OSError("模拟 replace 失败")
        return replace(source, destination)

    @contextmanager
    def broken_stream(**kwargs):
        with create(**kwargs) as stream:
            def write(content):
                stream.file.write(content[:8])
                raise OSError("模拟部分写入失败")
            monkeypatch.setattr(stream, "write", write)
            yield stream

    monkeypatch.setattr(os, "replace", checked_replace)
    if failure == "write":
        monkeypatch.setattr(modules.runtime.tempfile, "NamedTemporaryFile", broken_stream)
    monkeypatch.setattr(modules.exporter, "generate_schema", Mock(return_value=SPEC))
    assert modules.exporter.main(["--offline", "-o", str(target)]) == (1 if failure else 0)
    assert len(replacements) == (0 if failure == "write" else 1)
    assert not list(tmp_path.glob(".contract.json.*.tmp"))
    if failure:
        assert target.read_bytes() == b"original\r\n"
    else:
        assert json.loads(target.read_text(encoding="utf-8")) == SPEC


@pytest.mark.parametrize("spec,error", [
    (None, ValueError), ({}, ValueError), ({**SPEC, "openapi": 3}, ValueError),
    ({**SPEC, "info": []}, ValueError), ({**SPEC, "paths": []}, ValueError),
    ({**SPEC, "extra": object()}, TypeError),
])
def test_validation_and_serialization_precede_temporary_file(modules, monkeypatch, tmp_path, spec, error):
    target = tmp_path / "old.json"
    target.write_bytes(b"original\r\n")
    create = Mock(side_effect=AssertionError("不应创建临时文件"))
    monkeypatch.setattr(modules.runtime.tempfile, "NamedTemporaryFile", create)
    with pytest.raises(error):
        modules.runtime.write_schema(spec, target)
    create.assert_not_called()
    assert target.read_bytes() == b"original\r\n"


@pytest.mark.parametrize("failure", [None, "connection", "http", "json", "invalid"])
def test_online_requests_are_fake_and_errors_redacted(modules, monkeypatch, tmp_path, capsys, failure):
    class RequestError(Exception):
        pass

    requests = ModuleType("requests")
    requests.RequestException = RequestError
    response = Mock()
    response.raise_for_status.side_effect = RequestError(URL) if failure == "http" else None
    response.json.side_effect = ValueError(URL) if failure == "json" else None
    response.json.return_value = {} if failure == "invalid" else SPEC
    requests.get = Mock(return_value=response, side_effect=RequestError(URL) if failure == "connection" else None)
    monkeypatch.setitem(sys.modules, "requests", requests)
    target = tmp_path / "online.json"
    target.write_bytes(b"original")
    assert modules.exporter.main([URL, str(target)]) == (1 if failure else 0)
    requests.get.assert_called_once_with(URL, timeout=10)
    assert response.raise_for_status.call_count == (0 if failure == "connection" else 1)
    assert response.json.call_count == (0 if failure in ("connection", "http") else 1)
    captured = capsys.readouterr()
    assert all(secret not in captured.out + captured.err for secret in (URL, "private-password", "private-token"))
    if failure:
        assert target.read_bytes() == b"original"
    else:
        assert json.loads(target.read_text(encoding="utf-8")) == SPEC


def test_release_uses_offline_generation_and_cli_check(modules, monkeypatch):
    release, run = modules.release, Mock()
    monkeypatch.setattr(release, "run", run)
    release.regen_openapi(release.VENV_PY)
    release.verify_contracts(release.VENV_PY)
    assert run.call_args_list == [
        call([release.VENV_PY, release.BACKEND_DIR / "export_openapi.py", "--offline"], cwd=release.BACKEND_DIR),
        call([release.VENV_PY, "check_openapi.py"], cwd=release.BACKEND_DIR),
        call([release.VENV_PY, release.REPO_ROOT / "ir-cli" / "scripts" / "gen_response_fields.py", "--check"]),
    ]
