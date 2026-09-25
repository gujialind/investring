from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/scripts/run_e2e_backend.py"

FAKE_MAIN = '''
import hashlib, json, os, stat, sys
from pathlib import Path
from types import SimpleNamespace
backend = Path(__file__).resolve().parents[1]
database = Path(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
probe = {
    "cwd": str(Path.cwd()), "database": str(database),
    "database_mode": stat.S_IMODE(database.stat().st_mode),
    "dotenv": (Path.cwd() / ".env").exists(),
    "env": {key: value for key, value in os.environ.items() if key != "SECRET_KEY"},
    "secret_hash": hashlib.sha256(os.environ["SECRET_KEY"].encode()).hexdigest(),
    "secret_bytes": len(bytes.fromhex(os.environ["SECRET_KEY"])),
    "isolated": sys.flags.isolated, "no_bytecode": sys.dont_write_bytecode,
}
(backend / "probe.json").write_text(json.dumps(probe), encoding="utf-8")
app = SimpleNamespace(router=SimpleNamespace(lifespan_context=None))
'''

FAKE_DATABASE = '''
from contextlib import contextmanager
import json, os, sqlite3
from pathlib import Path
from types import SimpleNamespace
backend = Path(__file__).resolve().parents[1]
database = Path(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
connection = None
def create_all(*, bind):
    global connection
    assert bind is engine
    assert connection is None, "schema preparation must be explicit and happen once"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE seeded (name TEXT)")
    connection.execute("CREATE TABLE initialization (name TEXT)")
    connection.commit()
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).write_text("fixture-sidecar", encoding="utf-8")
@contextmanager
def SessionLocal():
    assert connection is not None, "create_all must precede seeding"
    yield connection
    connection.commit()
def dispose():
    if connection is not None:
        connection.close()
    probe_file = backend / "probe.json"
    probe = json.loads(probe_file.read_text(encoding="utf-8"))
    probe["disposed"] = True
    probe_file.write_text(json.dumps(probe), encoding="utf-8")
engine = SimpleNamespace(dispose=dispose)
'''

FAKE_BASE = '''
from types import SimpleNamespace
from app.database import create_all
Base = SimpleNamespace(metadata=SimpleNamespace(create_all=create_all))
'''

FAKE_TASKS = '''
def init_scheduled_tasks(db):
    assert db.execute("SELECT name FROM seeded").fetchall() == []
    db.execute("INSERT INTO initialization VALUES ('tasks')")
'''

FAKE_SEED = '''
def seed_base_data(db):
    assert db.execute("SELECT name FROM initialization").fetchall() == [("tasks",)]
    db.execute("INSERT INTO seeded VALUES ('base')")
def seed_e2e_active(db):
    db.execute("INSERT INTO seeded VALUES ('active')")
'''

FAKE_UVICORN = '''
import json, socket
from pathlib import Path
from types import SimpleNamespace
backend = Path(__file__).resolve().parent
class Config(SimpleNamespace):
    def __init__(self, app, **kwargs):
        super().__init__(app=app, **kwargs)
class Server:
    def __init__(self, config):
        self.config = config
        self.started = False
    def run(self, *, sockets):
        assert len(sockets) == 1
        listener = sockets[0]
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        assert listener.getsockname() == (self.config.host, self.config.port)
        assert self.config.app.router.lifespan_context.__name__ == "_noop_lifespan"
        probe_file = backend / "probe.json"
        probe = json.loads(probe_file.read_text(encoding="utf-8"))
        probe["listen"] = list(listener.getsockname())
        probe_file.write_text(json.dumps(probe), encoding="utf-8")
        behavior = (backend / "behavior").read_text(encoding="utf-8")
        if behavior == "error":
            raise RuntimeError("fixture server failure")
        if behavior == "interrupt":
            raise KeyboardInterrupt
        self.started = behavior == "success"
'''


@pytest.fixture
def backend(tmp_path):
    directory = tmp_path / "backend"
    for name in ("scripts", "app", "app/models", "tests"):
        package = directory / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    script = directory / "scripts/run_e2e_backend.py"
    shutil.copyfile(SCRIPT, script)
    for name, source in {
        "app/main.py": FAKE_MAIN,
        "app/database.py": FAKE_DATABASE,
        "app/models/base.py": FAKE_BASE,
        "app/init_tasks.py": FAKE_TASKS,
        "tests/seed_base.py": FAKE_SEED,
        "uvicorn.py": FAKE_UVICORN,
    }.items():
        (directory / name).write_text(source, encoding="utf-8")
    (directory / "behavior").write_text("success", encoding="utf-8")
    caller, temporary = tmp_path / "caller", tmp_path / "temporary"
    caller.mkdir()
    temporary.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(caller), "TMPDIR": str(temporary), "LANG": "C.UTF-8",
    }

    def run(*args, env=None):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(script), *map(str, args)],
            cwd=caller, env={**environment, **(env or {})},
            capture_output=True, text=True, timeout=5, check=False,
        )

    return SimpleNamespace(
        directory=directory, script=script, caller=caller, temporary=temporary,
        environment=environment, run=run,
        probe=lambda: json.loads((directory / "probe.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_import_does_not_load_backend_create_database_or_change_environment(backend):
    code = '''
import importlib.util, json, os, socket, sys, tempfile
from pathlib import Path
from unittest.mock import patch
for name in ("app", "app.main", "app.database", "tests.seed_base", "uvicorn", "sqlalchemy"):
    sys.modules[name] = None
before, cwd, search_path = dict(os.environ), Path.cwd(), list(sys.path)
spec = importlib.util.spec_from_file_location("_import_probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
with patch.object(socket, "socket", side_effect=AssertionError("socket on import")), \\
     patch.object(os, "open", side_effect=AssertionError("database on import")), \\
     patch.object(tempfile, "TemporaryDirectory", side_effect=AssertionError("tempdir on import")):
    spec.loader.exec_module(module)
assert callable(module.main)
assert dict(os.environ) == before
assert Path.cwd() == cwd and sys.path == search_path
print("import-isolated")
'''
    database = backend.caller / "must-not-exist.db"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(backend.script)],
        cwd=backend.caller,
        env={**backend.environment, "E2E_DB_PATH": str(database), "APP_VERSION": "fixture-parent"},
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-isolated"
    assert not database.exists() and not (backend.directory / "probe.json").exists()
    assert not list(backend.temporary.iterdir())


@pytest.mark.parametrize("port", ["0", "-1", "65536", "not-a-port"])
def test_invalid_port_is_rejected_before_database_creation(backend, port):
    database = backend.caller / "new.db"
    result = backend.run("--port", port, "--database", database)
    assert result.returncode == 2
    assert "port" in result.stderr
    assert not database.exists() and not (backend.directory / "probe.json").exists()
    assert not list(backend.temporary.iterdir())


@pytest.mark.parametrize("existing_database", [False, True])
def test_occupied_port_is_rejected_before_touching_database(backend, existing_database):
    database = backend.caller / "selected.db"
    if existing_database:
        database.write_bytes(b"fixture-original-database")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = backend.run("--port", listener.getsockname()[1], "--database", database)
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
    assert result.returncode == 2
    assert "Cannot own backend port" in result.stderr
    assert "Cannot create" not in result.stderr
    if existing_database:
        assert database.read_bytes() == b"fixture-original-database"
    else:
        assert not database.exists()
    assert not (backend.directory / "probe.json").exists()
    assert not list(backend.temporary.iterdir())


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling-symlink"])
def test_existing_database_and_symlinks_are_refused_unchanged(backend, free_port, kind):
    target = backend.caller / "original.db"
    database = backend.caller / "selected.db"
    if kind != "dangling-symlink":
        target.write_bytes(b"fixture-original-database\x00\r\n")
    if kind == "file":
        database = target
    else:
        database.symlink_to(target)
    result = backend.run("--port", free_port, "--database", database)
    assert result.returncode == 2
    assert "Cannot create a new E2E database" in result.stderr
    if kind == "dangling-symlink":
        assert not target.exists()
    else:
        assert target.read_bytes() == b"fixture-original-database\x00\r\n"
    if kind != "file":
        assert database.is_symlink() and database.readlink() == target
    assert not (backend.directory / "probe.json").exists()
    assert not list(backend.temporary.iterdir())


def test_runtime_drops_parent_settings_and_business_dotenv(backend, free_port):
    poison = {key: f"fixture-parent-{key}" for key in (
        "DATABASE_URL", "TEST_DB_URL", "DB_PASSWORD", "APP_VERSION", "TUSHARE_TOKEN",
        "GITHUB_TOKEN", "SECRET_KEY", "PYTHONPATH", "PYTHONHOME", "HTTP_PROXY",
    )}
    poison.update(SCHEDULER_ENABLED="true", AKSHARE_ENABLED="true", DEBUG="true")
    system = {"PATH": backend.environment["PATH"], "SYSTEMROOT": "/fixture-system", "WINDIR": "/fixture-windows"}
    for directory in (backend.caller, backend.directory):
        (directory / ".env").write_text("APP_VERSION=fixture-dotenv\nTUSHARE_TOKEN=fixture-dotenv-token\n", encoding="utf-8")
    before = dict(os.environ)
    result = backend.run("--port", free_port, env={**poison, **system})
    assert result.returncode == 0, result.stderr
    probe = backend.probe()
    environment, directory = probe["env"], probe["cwd"]
    assert set(environment) == {
        "PATH", "SYSTEMROOT", "WINDIR", "HOME", "TMPDIR", "LANG", "DATABASE_URL",
        "SCHEDULER_ENABLED", "AKSHARE_ENABLED", "DEBUG",
    }
    assert all(environment[key] == value for key, value in system.items())
    assert environment["HOME"] == environment["TMPDIR"] == directory
    assert environment["DATABASE_URL"] == f"sqlite:///{probe['database']}"
    assert Path(probe["database"]).parent == Path(directory)
    assert environment["SCHEDULER_ENABLED"] == environment["AKSHARE_ENABLED"] == environment["DEBUG"] == "false"
    assert environment["LANG"] == "C.UTF-8"
    assert probe["secret_bytes"] == 32
    assert probe["secret_hash"] != hashlib.sha256(poison["SECRET_KEY"].encode()).hexdigest()
    assert not probe["dotenv"] and probe["isolated"] == 1 and probe["no_bytecode"] is True
    assert probe["database_mode"] == 0o600 and probe["listen"] == ["127.0.0.1", free_port]
    diagnostic = result.stdout + result.stderr + json.dumps(probe)
    assert all(value not in diagnostic for key, value in poison.items() if key not in {"DEBUG", "AKSHARE_ENABLED", "SCHEDULER_ENABLED"})
    assert "fixture-dotenv" not in diagnostic
    assert dict(os.environ) == before
    assert not list(backend.directory.rglob("*.pyc"))
    assert not Path(directory).exists() and probe["disposed"]


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("behavior,returncode", [("success", 0), ("not-started", 1), ("error", 1), ("interrupt", 130)])
def test_database_lifetime_and_disposal_on_exit(backend, free_port, explicit, behavior, returncode):
    (backend.directory / "behavior").write_text(behavior, encoding="utf-8")
    database = backend.caller / "retained.db"
    args = ["--database", str(database)] if explicit else []
    result = backend.run("--port", free_port, *args)
    assert result.returncode == returncode, result.stderr
    probe = backend.probe()
    assert probe["disposed"] and not Path(probe["cwd"]).exists()
    assert not list(backend.temporary.iterdir())
    if explicit:
        assert probe["database"] == str(database)
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("SELECT name FROM seeded ORDER BY rowid").fetchall() == [("base",), ("active",)]
        assert database.stat().st_mode & 0o777 == 0o600
    else:
        assert not Path(probe["database"]).exists()
        assert all(not Path(probe["database"] + suffix).exists() for suffix in ("-wal", "-shm"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", free_port))


def test_legacy_environment_defaults_and_explicit_arguments(backend, free_port):
    default_database = backend.caller / "legacy.db"
    result = backend.run(env={"E2E_PORT": str(free_port), "E2E_DB_PATH": str(default_database)})
    assert result.returncode == 0, result.stderr
    assert backend.probe()["database"] == str(default_database)
    assert backend.probe()["listen"] == ["127.0.0.1", free_port]
    original = default_database.read_bytes()
    explicit_database = backend.caller / "explicit.db"
    result = backend.run(
        "--port", free_port, "--database", explicit_database,
        env={"E2E_PORT": "0", "E2E_DB_PATH": str(default_database)},
    )
    assert result.returncode == 0, result.stderr
    assert backend.probe()["database"] == str(explicit_database)
    assert default_database.read_bytes() == original
