from contextlib import closing, contextmanager, nullcontext
import json
import os
from pathlib import Path
import re
import runpy
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import Table, create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app import bootstrap
from app.init_tasks import scheduled_task_definitions
from app.models.base import Base
from app.services.scheduler_service import PreparedSQLAlchemyJobStore

BACKEND = Path(__file__).resolve().parents[2]
TASK_CODES = {"nav_sync", "snapshot_generate", "trading_calendar_sync", "log_cleanup"}
DDL = {"CREATE", "ALTER", "DROP", "TRUNCATE", "VACUUM", "ATTACH", "DETACH", "REINDEX"}
DML = {"INSERT", "UPDATE", "DELETE", "REPLACE"}


@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "bootstrap.db"
    settings = SimpleNamespace(
        database_url=f"sqlite:///{path}", scheduler_jobstore_table="bootstrap_test_jobs",
    )
    engine = create_engine(settings.database_url, poolclass=NullPool)
    yield SimpleNamespace(path=path, settings=settings, engine=engine)
    engine.dispose()


@pytest.fixture
def prepared_db(sqlite_db):
    before = bootstrap.status(sqlite_db.settings)
    result = bootstrap.prepare(expected_fingerprint=before["fingerprint"], settings=sqlite_db.settings)
    assert result["state"] == "ready"
    return sqlite_db


def _sql(database, statement, parameters=()):
    with closing(sqlite3.connect(database.path)) as connection, connection:
        return connection.execute(statement, parameters).fetchall()


def _persisted(path):
    """Ignore SHM coordination writes, but not database/WAL bytes or mtime."""
    return (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None


@contextmanager
def _forbid_sql(*, readonly=False):
    statements = []

    def guard(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        verb = statement.lstrip().split()[0].upper()
        assert verb not in DDL | (DML if readonly else set()), statement

    event.listen(Engine, "before_cursor_execute", guard)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", guard)


@contextmanager
def _forbid_preparation():
    # Existing tables can hide create(checkfirst=True) from a SQL-only assertion.
    with patch.object(Base.metadata, "create_all", side_effect=AssertionError("create_all")), \
         patch.object(Table, "create", side_effect=AssertionError("Table.create")), \
         patch.object(bootstrap.command, "upgrade", side_effect=AssertionError("upgrade")), \
         patch.object(bootstrap.command, "stamp", side_effect=AssertionError("stamp")):
        yield


@pytest.mark.parametrize("initial", ["missing", "zero-byte", "empty-schema"])
def test_status_empty_sqlite_does_not_create_or_write_database(sqlite_db, initial):
    if initial == "zero-byte":
        sqlite_db.path.touch()
    elif initial == "empty-schema":
        _sql(sqlite_db, "PRAGMA user_version=537")
    before = _persisted(sqlite_db.path)
    with _forbid_sql(readonly=True), _forbid_preparation():
        result = bootstrap.status(sqlite_db.settings)
        with pytest.raises(bootstrap.BootstrapError, match="not prepared.*empty"):
            bootstrap.check(sqlite_db.settings)
    assert result["state"] == "empty"
    assert set(result["missing_tasks"]) == TASK_CODES
    assert _persisted(sqlite_db.path) == before


def test_prepare_sqlite_creates_models_jobs_and_seeds_without_alembic(sqlite_db):
    with patch.object(bootstrap.command, "upgrade", side_effect=AssertionError("SQLite migration")), \
         patch.object(bootstrap.command, "stamp", side_effect=AssertionError("SQLite stamp")):
        before = bootstrap.status(sqlite_db.settings)
        prepared = bootstrap.prepare(expected_fingerprint=before["fingerprint"], settings=sqlite_db.settings)
    assert prepared == bootstrap.check(sqlite_db.settings)
    assert prepared["state"] == "ready"
    assert prepared["migration_mode"] == "sqlite-models" and prepared["revisions"] == []
    assert prepared["missing_tables"] == prepared["missing_tasks"] == []
    assert prepared["missing_columns"] == {}
    tables = set(inspect(sqlite_db.engine).get_table_names())
    assert tables == set(Base.metadata.tables) | {sqlite_db.settings.scheduler_jobstore_table}
    assert "alembic_version" not in tables
    assert set(row[0] for row in _sql(sqlite_db, "SELECT code FROM scheduled_task")) == TASK_CODES


def test_prepare_is_idempotent_and_preserves_task_cron_and_runtime_state(prepared_db):
    _sql(prepared_db, """UPDATE scheduled_task SET name='old name', description='old description',
        cron_expr='13 2 * * 2', is_enabled=0, last_run_status='failed', timeout_seconds=123,
        last_run_at='2026-01-02 03:04:05', next_run_at='2026-02-03 04:05:06' WHERE code='nav_sync'""")
    fields = "code, cron_expr, is_enabled, last_run_status, timeout_seconds, last_run_at, next_run_at, created_at"
    before = _sql(prepared_db, f"SELECT {fields} FROM scheduled_task ORDER BY code")
    for _ in range(2):
        snapshot = bootstrap.status(prepared_db.settings)
        result = bootstrap.prepare(expected_fingerprint=snapshot["fingerprint"], settings=prepared_db.settings)
        assert result["state"] == "ready"
        assert _sql(prepared_db, f"SELECT {fields} FROM scheduled_task ORDER BY code") == before
    assert _sql(prepared_db, "SELECT code, name, description FROM scheduled_task ORDER BY code") == sorted(
        (task["code"], task["name"], task["description"]) for task in scheduled_task_definitions()
    )


@pytest.mark.parametrize("existing", [False, True])
def test_wrong_fingerprint_never_creates_or_changes_schema(sqlite_db, existing):
    if existing:
        _sql(sqlite_db, "CREATE TABLE sentinel (value TEXT)")
        _sql(sqlite_db, "INSERT INTO sentinel VALUES ('keep')")
    before = _persisted(sqlite_db.path)
    with _forbid_sql(readonly=True), _forbid_preparation():
        with pytest.raises(bootstrap.BootstrapError, match="state changed"):
            bootstrap.prepare(expected_fingerprint="unauthorized", settings=sqlite_db.settings)
    assert _persisted(sqlite_db.path) == before
    assert not Path(str(sqlite_db.path) + ".bootstrap.lock").exists()


def test_unknown_revision_refuses_prepare_without_ddl(sqlite_db):
    _sql(sqlite_db, "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    _sql(sqlite_db, "INSERT INTO alembic_version VALUES ('unknown_future_revision')")
    before = _persisted(sqlite_db.path)
    with _forbid_sql(readonly=True), _forbid_preparation():
        state = bootstrap.status(sqlite_db.settings)
        assert state["state"] == "unknown_revision"
        with pytest.raises(bootstrap.BootstrapError, match="Unknown database revision"):
            bootstrap.prepare(expected_fingerprint=state["fingerprint"], settings=sqlite_db.settings)
        with pytest.raises(bootstrap.BootstrapError, match="unknown_revision"):
            bootstrap.check(sqlite_db.settings)
    assert _persisted(sqlite_db.path) == before


@pytest.mark.parametrize("damage,field,missing", [
    ("DROP TABLE notification", "missing_tables", "notification"),
    ("DROP TABLE scheduled_task", "missing_tables", "scheduled_task"),
    ("DROP TABLE bootstrap_test_jobs", "missing_tables", "bootstrap_test_jobs"),
    ("ALTER TABLE scheduled_task DROP COLUMN description", "missing_columns", "scheduled_task"),
    ("ALTER TABLE scheduled_task RENAME COLUMN code TO obsolete_code", "missing_columns", "scheduled_task"),
    ("ALTER TABLE bootstrap_test_jobs DROP COLUMN job_state", "missing_columns", "bootstrap_test_jobs"),
    *[(f"DELETE FROM scheduled_task WHERE code='{code}'", "missing_tasks", code) for code in sorted(TASK_CODES)],
])
def test_check_rejects_missing_table_column_or_each_required_seed(prepared_db, damage, field, missing):
    ready = bootstrap.status(prepared_db.settings)
    _sql(prepared_db, damage)
    before = _persisted(prepared_db.path)
    with _forbid_sql(readonly=True), _forbid_preparation():
        state = bootstrap.status(prepared_db.settings)
        assert state["state"] == "schema_incomplete"
        assert missing in state[field]
        assert state["fingerprint"] != ready["fingerprint"]
        with pytest.raises(bootstrap.BootstrapError, match="schema_incomplete"):
            bootstrap.check(prepared_db.settings)
    assert _persisted(prepared_db.path) == before


def test_status_uses_independent_readonly_connection(prepared_db):
    from app.database import engine as application_engine

    real_connect = sqlite3.connect
    connections = []

    def readonly_connect(database, *args, **kwargs):
        assert database == prepared_db.path.as_uri() + "?mode=ro"
        assert kwargs.get("uri") is True
        connection = real_connect(database, *args, **kwargs)
        connections.append(connection)
        return connection

    with patch.object(application_engine, "connect", side_effect=AssertionError("application connection")), \
         patch.object(sqlite3, "connect", side_effect=readonly_connect), _forbid_sql(readonly=True):
        assert bootstrap.status(prepared_db.settings)["state"] == "ready"
        assert bootstrap.check(prepared_db.settings)["state"] == "ready"
    assert len(connections) == 2 and connections[0] is not connections[1]
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_status_and_check_preserve_readonly_database(prepared_db):
    prepared_db.path.chmod(0o444)
    try:
        before = _persisted(prepared_db.path)
        with _forbid_sql(readonly=True), _forbid_preparation():
            assert bootstrap.status(prepared_db.settings)["state"] == "ready"
            assert bootstrap.check(prepared_db.settings)["state"] == "ready"
        assert _persisted(prepared_db.path) == before
        assert prepared_db.path.stat().st_mode & 0o777 == 0o444
    finally:
        prepared_db.path.chmod(0o600)


def test_status_reads_committed_wal_without_checkpointing_or_changing_data(prepared_db):
    wal = Path(str(prepared_db.path) + "-wal")
    with closing(sqlite3.connect(prepared_db.path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("DELETE FROM scheduled_task WHERE code='nav_sync'")
        writer.execute("UPDATE scheduled_task SET cron_expr='19 3 * * 2' WHERE code='log_cleanup'")
        writer.commit()
        assert wal.stat().st_size > 0
        # Prove that the current committed state is in WAL, not the main file.
        with closing(sqlite3.connect(prepared_db.path.as_uri() + "?immutable=1", uri=True)) as stale:
            assert stale.execute("SELECT count(*) FROM scheduled_task").fetchone() == (4,)
        before = (_persisted(prepared_db.path), _persisted(wal))
        with _forbid_sql(readonly=True), _forbid_preparation():
            state = bootstrap.status(prepared_db.settings)
            assert state["state"] == "schema_incomplete" and state["missing_tasks"] == ["nav_sync"]
            with pytest.raises(bootstrap.BootstrapError, match="schema_incomplete"):
                bootstrap.check(prepared_db.settings)
        assert (_persisted(prepared_db.path), _persisted(wal)) == before
        with closing(sqlite3.connect(prepared_db.path.as_uri() + "?mode=ro", uri=True)) as reader:
            assert reader.execute("SELECT count(*) FROM scheduled_task").fetchone() == (3,)
            assert reader.execute("SELECT cron_expr FROM scheduled_task WHERE code='log_cleanup'").fetchone() == ("19 3 * * 2",)


def test_fingerprint_tracks_database_identity_schema_and_migration_source(prepared_db, tmp_path, monkeypatch):
    initial = bootstrap.status(prepared_db.settings)
    for field in ("database_identity", "schema_fingerprint", "migration_fingerprint", "fingerprint"):
        assert re.fullmatch(r"[0-9a-f]{64}", initial[field])
    serialized = json.dumps(initial)
    assert prepared_db.settings.database_url not in serialized
    assert str(prepared_db.path) not in serialized
    other = SimpleNamespace(database_url=f"sqlite:///{tmp_path / 'other.db'}", scheduler_jobstore_table="bootstrap_test_jobs")
    other_state = bootstrap.status(other)
    other_ready = bootstrap.prepare(expected_fingerprint=other_state["fingerprint"], settings=other)
    assert other_ready["schema_fingerprint"] == initial["schema_fingerprint"]
    assert other_ready["migration_fingerprint"] == initial["migration_fingerprint"]
    assert other_ready["database_identity"] != initial["database_identity"]
    assert other_ready["fingerprint"] != initial["fingerprint"]
    _sql(prepared_db, "ALTER TABLE scheduled_task ADD COLUMN fixture_extra TEXT")
    changed_schema = bootstrap.status(prepared_db.settings)
    assert changed_schema["schema_fingerprint"] != initial["schema_fingerprint"]
    assert changed_schema["fingerprint"] != initial["fingerprint"]
    monkeypatch.setattr(bootstrap, "migration_fingerprint", lambda: "f" * 64)
    changed_migrations = bootstrap.status(prepared_db.settings)
    assert changed_migrations["schema_fingerprint"] == changed_schema["schema_fingerprint"]
    assert changed_migrations["fingerprint"] != changed_schema["fingerprint"]


def test_relative_sqlite_authorization_cannot_be_reused_from_another_directory(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    settings = SimpleNamespace(database_url="sqlite:///target.db", scheduler_jobstore_table="jobs")
    monkeypatch.chdir(first)
    before = bootstrap.status(settings)
    monkeypatch.chdir(second)
    after = bootstrap.status(settings)
    assert before["state"] == after["state"] == "empty"
    assert before["database_identity"] != after["database_identity"]
    assert before["fingerprint"] != after["fingerprint"]
    with pytest.raises(bootstrap.BootstrapError, match="state changed"):
        bootstrap.prepare(expected_fingerprint=before["fingerprint"], settings=settings)
    assert list(first.iterdir()) == list(second.iterdir()) == []


def test_nullable_legacy_price_column_is_not_a_ready_schema(prepared_db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with prepared_db.engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("price_record") as batch:
            batch.alter_column("unit_price", nullable=True,
                               existing_type=Base.metadata.tables["price_record"].c.unit_price.type)
    before = _persisted(prepared_db.path)
    with _forbid_sql(readonly=True), _forbid_preparation():
        state = bootstrap.status(prepared_db.settings)
        assert state["state"] == "schema_incomplete"
        assert state["missing_not_null"] == {"price_record": ["unit_price"]}
        with pytest.raises(bootstrap.BootstrapError, match="schema_incomplete"):
            bootstrap.check(prepared_db.settings)
    assert _persisted(prepared_db.path) == before
    with pytest.raises(bootstrap.BootstrapError, match="did not produce a complete schema"):
        bootstrap.prepare(expected_fingerprint=state["fingerprint"], settings=prepared_db.settings)
    columns = {column["name"]: column for column in inspect(prepared_db.engine).get_columns("price_record")}
    assert columns["unit_price"]["nullable"] is True


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "sqlite:///file:fixture?mode=ro", "postgresql://localhost/fixture"])
def test_probe_rejects_unsupported_database_locations_without_connecting(url):
    settings = SimpleNamespace(database_url=url, scheduler_jobstore_table="jobs")
    with patch.object(Engine, "connect", side_effect=AssertionError("unexpected connection")):
        with pytest.raises(bootstrap.BootstrapError):
            bootstrap.status(settings)


def test_scheduler_table_cannot_replace_a_model_table(sqlite_db):
    sqlite_db.settings.scheduler_jobstore_table = "scheduled_task"
    with pytest.raises(bootstrap.BootstrapError, match="must not share a model table name"):
        bootstrap.status(sqlite_db.settings)
    assert not sqlite_db.path.exists()


def test_mysql_probe_uses_read_only_transaction_and_disposes_its_engine(monkeypatch):
    settings = SimpleNamespace(database_url="mysql+pymysql://user:secret@fixture.invalid/database")
    engine = bootstrap._engine(settings, read_only=True)
    assert engine.url.get_backend_name() == "mysql"
    engine.dispose()
    connection = MagicMock()
    fake_engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), connect=Mock(), dispose=Mock())
    fake_engine.connect.return_value = nullcontext(connection)
    create = Mock(return_value=fake_engine)
    read = Mock(return_value={"state": "ready"})
    monkeypatch.setattr(bootstrap, "_engine", create)
    monkeypatch.setattr(bootstrap, "_inspect", read)
    assert bootstrap.status(settings) == {"state": "ready"}
    create.assert_called_once_with(settings, read_only=True)
    connection.exec_driver_sql.assert_called_once_with("SET TRANSACTION READ ONLY")
    read.assert_called_once_with(connection, fake_engine, settings)
    fake_engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("fail_at", [None, "check", "scheduler"])
def test_lifespan_checks_before_runtime_writes_and_always_stops_started_scheduler(monkeypatch, fail_at):
    import asyncio
    import app.main as main
    from app.services import market_data_service, scheduler_service

    calls = []

    def checked():
        calls.append("check")
        if fail_at == "check":
            raise bootstrap.BootstrapError("fixture unprepared")

    @contextmanager
    def session():
        calls.append("session")
        yield object()
        calls.append("close")

    def start():
        calls.append("scheduler")
        if fail_at == "scheduler":
            raise RuntimeError("fixture scheduler failure")

    monkeypatch.setattr(main, "check_database", checked)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(main, "init_scheduled_tasks", lambda db: calls.append("tasks"))
    monkeypatch.setattr(market_data_service, "recover_orphan_jobs", lambda: calls.append("recover"))
    monkeypatch.setattr(scheduler_service, "init_scheduler", start)
    monkeypatch.setattr(scheduler_service, "shutdown_scheduler", lambda: calls.append("stop"))

    async def run():
        async with main.lifespan(main.app):
            calls.append("serve")

    if fail_at:
        with pytest.raises(RuntimeError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert calls == (["check"] if fail_at == "check" else
                     ["check", "session", "tasks", "close", "recover", "scheduler"] +
                     ([] if fail_at == "scheduler" else ["serve"]) + ["stop"])


@pytest.mark.parametrize("enabled", [False, True])
def test_scheduler_initialization_selects_prepared_store_without_database_ddl(sqlite_db, monkeypatch, enabled):
    from app.services import market_data_service, scheduler_service

    settings = SimpleNamespace(**vars(sqlite_db.settings), scheduler_enabled=enabled,
                               scheduler_cron_daily="0 7 * * *", scheduler_cron_snapshot="30 7 * * *")
    scheduler = Mock()
    factory = Mock(return_value=scheduler)
    monkeypatch.setattr(scheduler_service, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler_service, "_scheduler", None)
    monkeypatch.setattr(market_data_service, "recover_orphan_jobs", Mock(return_value=0))
    with patch("apscheduler.schedulers.background.BackgroundScheduler", factory), _forbid_preparation():
        scheduler_service.init_scheduler()
    if not enabled:
        factory.assert_not_called()
        assert scheduler_service._scheduler is None
    else:
        store = factory.call_args.kwargs["jobstores"]["default"]
        try:
            assert isinstance(store, PreparedSQLAlchemyJobStore)
            scheduler.start.assert_called_once_with()
            assert scheduler.add_job.call_count == 2
            assert scheduler_service._scheduler is scheduler
            scheduler_service.shutdown_scheduler()
            scheduler.shutdown.assert_called_once_with(wait=False)
            assert scheduler_service._scheduler is None
        finally:
            store.shutdown()


@pytest.mark.parametrize("has_table", [False, True])
def test_real_prepared_jobstore_start_never_calls_create(sqlite_db, has_table):
    store = PreparedSQLAlchemyJobStore(engine=sqlite_db.engine, tablename="custom_jobs")
    assert isinstance(store, SQLAlchemyJobStore)
    if has_table:
        store.jobs_t.create(sqlite_db.engine)
    scheduler = SimpleNamespace(_logger=Mock())
    try:
        with _forbid_sql(readonly=True), _forbid_preparation():
            if has_table:
                store.start(scheduler, "prepared")
                assert store._scheduler is scheduler and store._alias == "prepared"
            else:
                with pytest.raises(RuntimeError, match="Scheduler table is missing"):
                    store.start(scheduler, "prepared")
        assert inspect(sqlite_db.engine).has_table("custom_jobs") is has_table
    finally:
        store.shutdown()


@pytest.fixture
def isolated_process(tmp_path, sqlite_db):
    environment = {
        "PATH": os.defpath, "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "LANG": "C.UTF-8",
        "DATABASE_URL": sqlite_db.settings.database_url, "SECRET_KEY": "bootstrap-fixture-" + "x" * 64,
        "SCHEDULER_ENABLED": "false", "AKSHARE_ENABLED": "false", "DEBUG": "false",
        "SCHEDULER_JOBSTORE_TABLE": sqlite_db.settings.scheduler_jobstore_table,
    }

    def run(source, *args, env=None):
        return subprocess.run(
            [sys.executable, "-I", "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]);\n" + source,
             str(BACKEND), *args],
            cwd=tmp_path, env={**environment, **(env or {})},
            capture_output=True, text=True, timeout=45, check=False,
        )

    return run


def test_application_import_opens_no_connection_in_fresh_process(sqlite_db, isolated_process):
    result = isolated_process('''
import sqlite3
from unittest.mock import patch
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool
with patch.object(Engine, "connect", side_effect=AssertionError("Engine.connect on import")) as engine, \\
     patch.object(Pool, "connect", side_effect=AssertionError("Pool.connect on import")) as pool, \\
     patch.object(sqlite3, "connect", side_effect=AssertionError("sqlite connect on import")) as sqlite, \\
     patch.object(sqlite3.dbapi2, "connect", side_effect=AssertionError("DBAPI connect on import")) as dbapi:
    import app.main
    assert app.main.app is not None
    for probe in (engine, pool, sqlite, dbapi):
        probe.assert_not_called()
print("import-with-zero-connections")
''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("import-with-zero-connections")
    assert not sqlite_db.path.exists()


@pytest.mark.parametrize("state", ["ready", "empty", "missing-table", "missing-column", "missing-seed"])
def test_real_lifespan_checks_before_dml_and_never_prepares(sqlite_db, isolated_process, state):
    if state != "empty":
        before = bootstrap.status(sqlite_db.settings)
        bootstrap.prepare(expected_fingerprint=before["fingerprint"], settings=sqlite_db.settings)
        _sql(sqlite_db, "UPDATE scheduled_task SET name='outdated', cron_expr='13 2 * * 2', is_enabled=0 WHERE code='nav_sync'")
        _sql(sqlite_db, "INSERT INTO sync_job (job_type, status) VALUES ('price_sync', 'pending')")
    damage = {
        "missing-table": "DROP TABLE bootstrap_test_jobs",
        "missing-column": "ALTER TABLE scheduled_task DROP COLUMN description",
        "missing-seed": "DELETE FROM scheduled_task WHERE code='nav_sync'",
    }
    if state in damage:
        _sql(sqlite_db, damage[state])
    result = isolated_process('''
import asyncio
from contextlib import ExitStack
from unittest.mock import patch
from sqlalchemy import Table, event, text
from sqlalchemy.engine import Engine
from apscheduler.schedulers.background import BackgroundScheduler
from app import main, bootstrap
from app.database import SessionLocal, engine
from app.models.base import Base
from app.init_tasks import scheduled_task_definitions
from app.services import scheduler_service, market_data_service
state = sys.argv[2]
statements = []
def guard(connection, cursor, statement, parameters, context, executemany):
    statements.append(statement)
    verb = statement.lstrip().split()[0].upper()
    assert verb not in {"CREATE", "ALTER", "DROP", "TRUNCATE", "VACUUM"}, statement
    if state != "ready":
        assert verb not in {"INSERT", "UPDATE", "DELETE", "REPLACE"}, statement
event.listen(Engine, "before_cursor_execute", guard)
real_start = BackgroundScheduler.start
def start_paused(self, *args, **kwargs):
    return real_start(self, paused=True)
async def exercise():
    if state != "ready":
        try:
            async with main.app.router.lifespan_context(main.app):
                raise AssertionError("unprepared application yielded")
        except bootstrap.BootstrapError as exc:
            assert "not prepared" in str(exc)
        return
    async with main.app.router.lifespan_context(main.app):
        assert scheduler_service._scheduler is not None
        with SessionLocal() as session:
            row = session.execute(text("SELECT name, cron_expr, is_enabled FROM scheduled_task WHERE code='nav_sync'")).one()
            expected = next(task["name"] for task in scheduled_task_definitions() if task["code"] == "nav_sync")
            assert tuple(row) == (expected, "13 2 * * 2", 0)
            assert session.execute(text("SELECT status FROM sync_job")).scalar_one() == "interrupted"
            assert session.execute(text("SELECT count(*) FROM bootstrap_test_jobs")).scalar_one() == 2
    assert scheduler_service._scheduler is None
with ExitStack() as stack:
    for target, name in ((Base.metadata, "create_all"), (Table, "create"),
                         (bootstrap.command, "upgrade"), (bootstrap.command, "stamp")):
        stack.enter_context(patch.object(target, name, side_effect=AssertionError(name)))
    stack.enter_context(patch.object(BackgroundScheduler, "start", start_paused))
    checked = stack.enter_context(patch.object(main, "check_database", wraps=main.check_database))
    if state != "ready":
        for target, name in ((main, "SessionLocal"), (main, "init_scheduled_tasks"),
                             (market_data_service, "recover_orphan_jobs"),
                             (scheduler_service, "init_scheduler")):
            stack.enter_context(patch.object(target, name, side_effect=AssertionError("before check: " + name)))
    asyncio.run(exercise())
    checked.assert_called_once_with()
if state == "empty":
    assert not statements, "a missing SQLite file can be rejected without connecting"
else:
    assert statements, "must execute the real database check"
engine.dispose()
print("lifespan-checked-without-ddl")
''', state, env={"SCHEDULER_ENABLED": "true"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("lifespan-checked-without-ddl")
    if state == "empty":
        assert not sqlite_db.path.exists()


def test_entrypoint_executes_only_requested_command(sqlite_db, tmp_path):
    command = [
        "/bin/sh", str(BACKEND / "docker-entrypoint.sh"), sys.executable, "-I", "-B", "-c",
        "import json, os, sys; print(json.dumps([os.getpid(), sys.argv[1:]])); sys.exit(37)",
        "argument with spaces", "--not-a-bootstrap-option",
    ]
    with subprocess.Popen(
        command, cwd=tmp_path,
        env={"PATH": str(tmp_path), "DATABASE_URL": sqlite_db.settings.database_url},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 37, stdout + stderr
        assert json.loads(stdout) == [process.pid, ["argument with spaces", "--not-a-bootstrap-option"]]
    assert not sqlite_db.path.exists() and not list(tmp_path.glob("*.bootstrap.lock"))


@pytest.mark.parametrize("operation,exit_code", [("status", 0), ("check", 1)])
def test_cli_empty_probe_is_readonly_and_machine_readable(sqlite_db, monkeypatch, capsys, operation, exit_code):
    monkeypatch.setattr(bootstrap, "get_settings", lambda: sqlite_db.settings)
    with _forbid_sql(readonly=True), _forbid_preparation():
        assert bootstrap.main([operation]) == exit_code
    output = capsys.readouterr()
    assert json.loads(output.out)["state"] == "empty"
    assert output.err == "" and not sqlite_db.path.exists()


def test_cli_prepare_requires_fingerprint_and_check_succeeds_after_prepare(sqlite_db, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "get_settings", lambda: sqlite_db.settings)
    with pytest.raises(SystemExit) as exc:
        bootstrap.main(["prepare"])
    assert exc.value.code == 2 and not sqlite_db.path.exists()
    capsys.readouterr()
    assert bootstrap.main(["prepare", "--expect-state", "wrong"]) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "error"
    assert not sqlite_db.path.exists()
    state = bootstrap.status(sqlite_db.settings)
    original_prepare = bootstrap.prepare

    def noisy_prepare(**kwargs):
        print("fixture migration diagnostic")
        return original_prepare(**kwargs)

    monkeypatch.setattr(bootstrap, "prepare", noisy_prepare)
    assert bootstrap.main(["prepare", "--expect-state", state["fingerprint"]]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["state"] == "ready"
    assert output.err == "fixture migration diagnostic\n"
    assert bootstrap.main(["check"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_cli_database_errors_do_not_expose_url_or_credentials(monkeypatch, capsys):
    secret_url = "mysql+pymysql://fixture_user:fixture_password@fixture.invalid/private_database"
    failure = OperationalError("SELECT private", {}, RuntimeError(secret_url))
    monkeypatch.setattr(bootstrap, "status", Mock(side_effect=failure))
    assert bootstrap.main(["status"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["state"] == "error"
    assert all(secret not in output.out + output.err for secret in
               (secret_url, "fixture_user", "fixture_password", "fixture.invalid", "private_database"))


@pytest.fixture
def mysql_control(monkeypatch):
    """No server: enforce connection identity, lock lifetime and mutation order."""
    settings = SimpleNamespace(database_url="mysql+pymysql://fixture:secret@invalid/bootstrap", scheduler_jobstore_table="jobs")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.dialect.name = "mysql"
    connection.invalidated = False
    engine = MagicMock()
    engine.dialect.name = "mysql"
    engine.connect.return_value = connection
    events = []
    control = SimpleNamespace(
        settings=settings, connection=connection, engine=engine, events=events,
        held=False, acquired=1, current={"fingerprint": "approved", "state": "migration_required"},
    )

    def execute(statement, parameters):
        if "GET_LOCK" in str(statement):
            assert not control.held
            control.held = control.acquired == 1
            control.lock_name = parameters["name"]
            events.append("get_lock")
            return SimpleNamespace(scalar=lambda: control.acquired)
        assert "RELEASE_LOCK" in str(statement) and control.held
        assert parameters["name"] == control.lock_name
        events.append("release_lock")
        control.held = False
        return SimpleNamespace(scalar=lambda: 1)

    def inspect_locked(actual_connection, actual_engine, actual_settings):
        assert actual_connection is connection and actual_engine is engine and actual_settings is settings
        assert control.held
        events.append("inspect")
        return control.current if events.count("inspect") == 1 else {"fingerprint": "prepared", "state": "ready"}

    def create_models(bind):
        assert bind is connection and control.held
        events.append("create_models")

    def upgrade(config, revision):
        assert config.attributes["connection"] is connection and revision == "head" and control.held
        events.append("upgrade")

    def create_jobs(bind, checkfirst):
        assert bind is connection and checkfirst is True and control.held
        events.append("create_jobs")

    session = MagicMock()
    session.__enter__.return_value = session

    def seed(actual_session):
        assert actual_session is session and control.held
        events.append("seed")

    connection.execute.side_effect = execute
    control.status = Mock(return_value={"fingerprint": "approved", "state": "migration_required"})
    monkeypatch.setattr(bootstrap, "status", control.status)
    monkeypatch.setattr(bootstrap, "_engine", Mock(return_value=engine))
    monkeypatch.setattr(bootstrap, "_inspect", inspect_locked)
    control.create_models = Mock(side_effect=create_models)
    monkeypatch.setattr(Base.metadata, "create_all", control.create_models)
    control.upgrade = Mock(side_effect=upgrade)
    monkeypatch.setattr(bootstrap.command, "upgrade", control.upgrade)
    control.store = Mock(return_value=SimpleNamespace(jobs_t=SimpleNamespace(create=create_jobs)))
    monkeypatch.setattr(bootstrap, "SQLAlchemyJobStore", control.store)
    control.session_factory = Mock(return_value=session)
    monkeypatch.setattr(bootstrap, "Session", control.session_factory)
    monkeypatch.setattr("app.init_tasks.init_scheduled_tasks", seed)
    return control


def test_mysql_prepare_holds_lock_on_original_alembic_and_seed_connection(mysql_control):
    control = mysql_control
    result = bootstrap.prepare(expected_fingerprint="approved", settings=control.settings)
    assert result["state"] == "ready"
    assert control.events == ["get_lock", "inspect", "create_models", "upgrade", "create_jobs", "seed", "inspect", "release_lock"]
    control.session_factory.assert_called_once_with(bind=control.connection)
    control.engine.connect.assert_called_once_with()
    control.engine.dispose.assert_called_once_with()
    assert not control.held


@pytest.mark.parametrize("reason", ["wrong-fingerprint", "lock-busy", "lock-state-changed", "unknown-revision"])
def test_mysql_prepare_rejects_without_mutation_and_releases_acquired_lock(mysql_control, reason):
    control = mysql_control
    expected = "approved"
    if reason == "wrong-fingerprint":
        expected = "wrong"
    elif reason == "lock-busy":
        control.acquired = 0
    elif reason == "lock-state-changed":
        control.current = {"fingerprint": "changed-under-lock", "state": "empty"}
    else:
        control.current = {"fingerprint": "approved", "state": "unknown_revision"}
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.prepare(expected_fingerprint=expected, settings=control.settings)
    control.create_models.assert_not_called()
    control.upgrade.assert_not_called()
    control.store.assert_not_called()
    control.session_factory.assert_not_called()
    assert control.events == {
        "wrong-fingerprint": [], "lock-busy": ["get_lock"],
        "lock-state-changed": ["get_lock", "inspect", "release_lock"],
        "unknown-revision": ["get_lock", "inspect", "release_lock"],
    }[reason]
    assert not control.held
    if reason == "wrong-fingerprint":
        control.engine.connect.assert_not_called()
    else:
        control.engine.dispose.assert_called_once_with()


def test_mysql_migration_failure_releases_lock_and_stops_before_seeding(mysql_control):
    control = mysql_control
    control.upgrade.side_effect = RuntimeError("fixture migration failed")
    with pytest.raises(RuntimeError, match="fixture migration failed"):
        bootstrap.prepare(expected_fingerprint="approved", settings=control.settings)
    assert control.events == ["get_lock", "inspect", "create_models", "release_lock"]
    control.session_factory.assert_not_called()
    control.connection.rollback.assert_called()
    control.engine.dispose.assert_called_once_with()
    assert not control.held


def test_alembic_environment_uses_supplied_connection_without_opening_another():
    from alembic import context

    connection = Mock()
    configuration = bootstrap.migration_config(connection)
    with patch.object(context, "config", configuration, create=True), \
         patch.object(context, "is_offline_mode", return_value=False), \
         patch.object(context, "configure") as configure, \
         patch.object(context, "begin_transaction", return_value=nullcontext()), \
         patch.object(context, "run_migrations") as migrate, \
         patch("sqlalchemy.engine_from_config", side_effect=AssertionError("new migration connection")), \
         patch("app.config.get_settings", side_effect=AssertionError("unneeded settings")), \
         patch.object(sys, "path", list(sys.path)):
        runpy.run_path(str(BACKEND / "alembic/env.py"))
    configure.assert_called_once_with(connection=connection, target_metadata=Base.metadata)
    migrate.assert_called_once_with()


@pytest.mark.parametrize("state", ["empty", "unversioned", "migration_required", "unknown_revision", "schema_incomplete", "ready"])
def test_mysql_status_state_classification_with_controlled_inspection(prepared_db, state):
    # Use real SQLite column reflection solely as fixture metadata, not a MySQL server.
    inspector = inspect(prepared_db.engine)
    scripts = bootstrap.ScriptDirectory.from_config(bootstrap.migration_config())
    heads = scripts.get_heads()
    older = next(revision.revision for revision in scripts.walk_revisions() if revision.revision not in heads)
    revisions = {"empty": [], "unversioned": [], "migration_required": [older],
                 "unknown_revision": ["unknown_future"], "schema_incomplete": heads, "ready": heads}[state]
    tables = inspector.get_table_names() if state != "empty" else []
    if revisions:
        tables.append("alembic_version")
    connection = Mock()
    connection.execute.side_effect = [
        SimpleNamespace(scalars=lambda: revisions),
        SimpleNamespace(scalars=lambda: sorted(TASK_CODES - ({"nav_sync"} if state == "schema_incomplete" else set()))),
    ] if revisions else [SimpleNamespace(scalars=lambda: sorted(TASK_CODES))]
    settings = SimpleNamespace(
        database_url="mysql+pymysql://private_user:private_password@private.invalid/private_database",
        scheduler_jobstore_table=prepared_db.settings.scheduler_jobstore_table,
    )
    reflected = SimpleNamespace(get_table_names=lambda: tables, get_columns=inspector.get_columns)
    with patch.object(bootstrap, "inspect", return_value=reflected):
        result = bootstrap._inspect(connection, prepared_db.engine, settings)
    assert result["state"] == state and result["migration_mode"] == "mysql-alembic"
    assert all(secret not in json.dumps(result) for secret in
               (settings.database_url, "private_user", "private_password", "private.invalid", "private_database"))
