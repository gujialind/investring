"""#797：凭证迁移往返、幂等与使用后零写入拒绝降级。"""
import importlib.util
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.auth import AuthSession, ApiKey
from app.models.audit_log import AuditLog

PATH = Path(__file__).resolve().parents[2] / "alembic/versions/0025_persistent_credentials.py"
spec = importlib.util.spec_from_file_location("migration_0025", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


@pytest.fixture
def conn(test_engine, _seed_base_data):
    key = uuid4().hex
    with test_engine.connect() as connection:
        connection.info["credential_test_id"] = key
        try:
            yield connection
        finally:
            connection.rollback()
    # Restore only this test's data and schema after MySQL's implicit DDL commits.
    with test_engine.begin() as connection:
        run(migration.upgrade, connection)
        for table in (AuthSession.__table__, ApiKey.__table__):
            connection.execute(table.delete().where(table.c.id == key))
        connection.execute(AuditLog.__table__.delete().where(AuditLog.credential_id == key))


def _type_str(value: object) -> str:
    """MySQL inspector 会把 CHARACTER SET/COLLATE 拼进列类型串（SQLite 不会），
    与模型声明比较前统一剥离，只比对裸类型。"""
    return re.sub(r"\s+(?:CHARACTER SET|CHARSET|COLLATE)\s+\S+", "", str(value), flags=re.IGNORECASE)


def test_upgrade_is_idempotent_and_matches_models(conn):
    run(migration.upgrade, conn)
    run(migration.upgrade, conn)
    inspector = sa.inspect(conn)
    for model in (AuthSession, ApiKey):
        actual = {c["name"]: c for c in inspector.get_columns(model.__tablename__)}
        assert set(actual) == set(model.__table__.columns.keys())
        for col in model.__table__.columns:
            assert actual[col.name]["nullable"] == col.nullable
            assert _type_str(actual[col.name]["type"]) == str(col.type)
        fk = inspector.get_foreign_keys(model.__tablename__)
        expected_fk = next(iter(model.__table__.foreign_key_constraints))
        assert len(fk) == 1 and fk[0]["options"]["ondelete"] == "RESTRICT"
        assert fk[0]["name"] == expected_fk.name
        assert fk[0]["constrained_columns"] == ["investor_code"]
        assert fk[0]["referred_table"] == "investor" and fk[0]["referred_columns"] == ["code"]
        indexes = inspector.get_indexes(model.__tablename__)
        assert any(index["name"] == f"ix_{model.__tablename__}_investor_code"
                   and index["column_names"] == ["investor_code"] for index in indexes)
    audit = {c["name"]: c for c in inspector.get_columns("audit_log")}
    for name in ("credential_id", "credential_label"):
        expected = AuditLog.__table__.c[name]
        assert audit[name]["nullable"] == expected.nullable
        assert _type_str(audit[name]["type"]) == str(expected.type)


def test_unused_schema_roundtrip(conn):
    run(migration.downgrade, conn)
    assert not sa.inspect(conn).has_table("auth_session")
    assert not sa.inspect(conn).has_table("api_key")
    assert "credential_id" not in {c["name"] for c in sa.inspect(conn).get_columns("audit_log")}
    run(migration.downgrade, conn)
    run(migration.upgrade, conn)
    test_upgrade_is_idempotent_and_matches_models(conn)


@pytest.mark.parametrize("kind", ["session", "key", "audit"])
def test_used_credentials_refuse_before_any_write(conn, kind):
    key = conn.info["credential_test_id"]
    now = datetime.now()
    if kind == "session":
        conn.execute(AuthSession.__table__.insert().values(id=key, investor_code="ADMIN", created_at=now,
                     expires_at=now + timedelta(days=7), revoked_at=now))
    elif kind == "key":
        conn.execute(ApiKey.__table__.insert().values(id=key, investor_code="ADMIN", label="migration-test",
                     secret_hash="0" * 64, scopes=["ledger:read"], created_at=now))
    else:
        conn.execute(AuditLog.__table__.insert().values(investor_code="ADMIN", action="create",
                     resource_type="api_key", credential_id=key, credential_label="migration-test"))
    conn.commit()
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().split()[0].upper())
    sa.event.listen(conn, "before_cursor_execute", capture)
    try:
        with pytest.raises(RuntimeError, match="拒绝降级"):
            run(migration.downgrade, conn)
    finally:
        sa.event.remove(conn, "before_cursor_execute", capture)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE"}
    assert sa.inspect(conn).has_table("auth_session")
    assert sa.inspect(conn).has_table("api_key")
    assert "credential_id" in {c["name"] for c in sa.inspect(conn).get_columns("audit_log")}
