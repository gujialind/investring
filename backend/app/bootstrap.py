import argparse
from contextlib import contextmanager, redirect_stdout
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.config import get_settings

BACKEND = Path(__file__).resolve().parents[1]


class BootstrapError(RuntimeError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def migration_config(connection=None):
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def migration_fingerprint():
    return _digest({path.name: path.read_text(encoding="utf-8")
                    for path in sorted((BACKEND / "alembic/versions").glob("*.py"))})


def _sqlite_path(url):
    if not url.database or url.database == ":memory:" or url.database.startswith("file:"):
        raise BootstrapError("Bootstrap requires a file-backed SQLite database without URI options")
    return Path(url.database).absolute()


def _engine(settings, *, read_only=False):
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        path = _sqlite_path(url)
        if read_only:
            return create_engine(
                "sqlite://", poolclass=NullPool,
                creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True),
            )
    elif url.get_backend_name() != "mysql":
        raise BootstrapError("Bootstrap supports only MySQL and file-backed SQLite")
    return create_engine(url, poolclass=NullPool)


def _inspect(connection, engine, settings):
    from app.init_tasks import scheduled_task_definitions
    from app.models.base import Base
    from app.models.scheduled_task import ScheduledTask

    url = make_url(settings.database_url)
    dialect = url.get_backend_name()
    scripts = ScriptDirectory.from_config(migration_config())
    heads = sorted(scripts.get_heads())
    known = {revision.revision for revision in scripts.walk_revisions()}
    store = SQLAlchemyJobStore(engine=engine, tablename=settings.scheduler_jobstore_table)
    required = {table.name: table for table in Base.metadata.sorted_tables}
    if store.jobs_t.name in required:
        raise BootstrapError("Scheduler table must not share a model table name")
    required[store.jobs_t.name] = store.jobs_t
    inspector = inspect(connection) if connection is not None else None
    actual = set(inspector.get_table_names()) if inspector is not None else set()
    missing_tables = sorted(required.keys() - actual)
    missing_columns, missing_not_null, columns = {}, {}, {}
    for name, table in required.items():
        if name not in actual:
            continue
        found = inspector.get_columns(name)
        columns[name] = sorted((column["name"], str(column["type"]), column["nullable"],
                                bool(column.get("primary_key"))) for column in found)
        missing = sorted(set(table.columns.keys()) - {column["name"] for column in found})
        if missing:
            missing_columns[name] = missing
        required_not_null = {column.name for column in table.columns if not column.nullable}
        nullable = sorted(column["name"] for column in found if column["nullable"] and column["name"] in required_not_null)
        if nullable:
            missing_not_null[name] = nullable
    revisions = sorted(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) if "alembic_version" in actual else []
    expected_tasks = {task["code"] for task in scheduled_task_definitions()}
    present_tasks = set()
    if "scheduled_task" in actual and "code" not in missing_columns.get("scheduled_task", []):
        present_tasks = set(connection.execute(select(ScheduledTask.code)).scalars())
    missing_tasks = sorted(expected_tasks - present_tasks)
    if not actual:
        state = "empty"
    elif set(revisions) - known:
        state = "unknown_revision"
    elif dialect == "mysql" and not revisions:
        state = "unversioned"
    elif dialect == "mysql" and revisions != heads:
        state = "migration_required"
    elif missing_tables or missing_columns or missing_not_null or missing_tasks:
        state = "schema_incomplete"
    else:
        state = "ready"
    result = {
        "state": state, "dialect": dialect,
        "migration_mode": "sqlite-models" if dialect == "sqlite" else "mysql-alembic",
        "database_identity": _digest([
            url.drivername, url.host, url.port, url.username,
            str(_sqlite_path(url)) if dialect == "sqlite" else url.database,
        ]),
        "revisions": revisions, "expected_heads": heads,
        "migration_fingerprint": migration_fingerprint(),
        "schema_fingerprint": _digest(columns),
        "missing_tables": missing_tables, "missing_columns": missing_columns,
        "missing_not_null": missing_not_null, "missing_tasks": missing_tasks,
    }
    result["fingerprint"] = _digest(result)
    return result


def status(settings=None):
    settings = settings or get_settings()
    engine = _engine(settings, read_only=True)
    try:
        url = make_url(settings.database_url)
        if url.get_backend_name() == "sqlite" and not _sqlite_path(url).exists():
            return _inspect(None, engine, settings)
        with engine.connect() as connection:
            if engine.dialect.name == "mysql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return _inspect(connection, engine, settings)
    finally:
        engine.dispose()


def check(settings=None):
    result = status(settings)
    if result["state"] != "ready":
        raise BootstrapError(f"Database is not prepared ({result['state']}); run the explicit bootstrap prepare command")
    return result


@contextmanager
def _prepare_lock(connection, settings):
    url = make_url(settings.database_url)
    if connection.dialect.name == "mysql":
        name = "investring.bootstrap." + _digest(url.database)[:24]
        if connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": name}).scalar() != 1:
            raise BootstrapError("Another bootstrap owns this database")
        try:
            connection.commit()
            yield
        finally:
            # 仅清理未提交事务；MySQL DDL 的隐式提交不会被此 rollback 撤销。
            connection.rollback()
            if not connection.invalidated:
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
    else:
        with Path(str(_sqlite_path(url)) + ".bootstrap.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BootstrapError("Another bootstrap owns this database") from exc
            yield


def prepare(*, expected_fingerprint, settings=None):
    from app.init_tasks import init_scheduled_tasks
    from app.models.base import Base

    settings = settings or get_settings()
    before = status(settings)
    if before["fingerprint"] != expected_fingerprint:
        raise BootstrapError("Database state changed; inspect status and renew the explicit authorization")
    engine = _engine(settings)
    try:
        with engine.connect() as connection, _prepare_lock(connection, settings):
            current = _inspect(connection, engine, settings)
            if current["fingerprint"] != expected_fingerprint:
                raise BootstrapError("Database state changed before the bootstrap lock was acquired")
            if current["state"] == "unknown_revision":
                raise BootstrapError("Unknown database revision; refusing initialization or migration")
            connection.commit()
            Base.metadata.create_all(connection)
            if engine.dialect.name == "mysql":
                command.upgrade(migration_config(connection), "head")
            SQLAlchemyJobStore(engine=engine, tablename=settings.scheduler_jobstore_table).jobs_t.create(connection, checkfirst=True)
            with Session(bind=connection) as session:
                init_scheduled_tasks(session)
            connection.commit()
            result = _inspect(connection, engine, settings)
            if result["state"] != "ready":
                raise BootstrapError("Preparation did not produce a complete schema; no automatic rollback was attempted")
            return result
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit database preparation; status/check never run DDL")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("check")
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--expect-state", required=True, help="fingerprint from the target database status")
    args = parser.parse_args(argv)
    try:
        if args.operation == "prepare":
            with redirect_stdout(sys.stderr):
                result = prepare(expected_fingerprint=args.expect_state)
        else:
            result = status()
    except BootstrapError as exc:
        result = {"state": "error", "reason": str(exc)}
    except (SQLAlchemyError, OSError, ValueError, RuntimeError) as exc:
        result = {"state": "error", "reason": f"Database preparation/probe could not complete ({type(exc).__name__})"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["state"] == "error":
        return 2
    return 1 if args.operation == "check" and result["state"] != "ready" else 0


if __name__ == "__main__":
    sys.exit(main())
