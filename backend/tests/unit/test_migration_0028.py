"""#905 PR1：在 pytest 自有库验证迁移 0028 的往返、幂等与拒绝降级零写入。"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.portfolio import Portfolio

PATH = Path(__file__).resolve().parents[2] / "alembic/versions/0028_portfolio_kind_owner.py"
spec = importlib.util.spec_from_file_location("migration_0028", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

PORT = Portfolio.__table__
KIND = "kind"
OWNER = "owner_investor_code"


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _columns(conn):
    return {c["name"]: c for c in sa.inspect(conn).get_columns("portfolio")}


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    with test_engine.connect() as conn:
        try:
            yield conn
        finally:
            conn.rollback()
    # 恢复目标态（MySQL DDL 隐式提交，rollback 撤不掉 ADD/DROP COLUMN）：
    # 补回缺失列、把 kind 重置 nav / owner 重置 NULL，避免留缺列态拖垮后续 ORM 用例。
    with test_engine.begin() as conn:
        # SQLite batch_alter_table 在事务回滚后可能残留 _alembic_tmp_portfolio；
        # 仅当主表 portfolio 仍在（临时表是 stale 副本）时清除，避免误删真实数据。
        tables = sa.inspect(conn).get_table_names()
        if "portfolio" in tables and "_alembic_tmp_portfolio" in tables:
            conn.execute(sa.text("DROP TABLE _alembic_tmp_portfolio"))
        cols = _columns(conn)
        if KIND not in cols or OWNER not in cols:
            _run(migration.upgrade, conn)
        conn.execute(PORT.update().values(kind="nav", owner_investor_code=None))


def test_revision_and_model_contract():
    assert migration.revision == "0028"
    assert migration.down_revision == "0027"
    assert not PORT.c[KIND].nullable
    assert PORT.c[OWNER].nullable
    source = PATH.read_text()
    assert "from app.models" not in source
    assert "import app.models" not in source


def test_upgrade_on_target_schema_is_noop(migration_conn):
    conn = migration_conn
    before = conn.execute(sa.select(PORT)).all()
    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)
    assert KIND in _columns(conn) and OWNER in _columns(conn)
    assert conn.execute(sa.select(PORT)).all() == before


def test_round_trip_nav_only(migration_conn):
    conn = migration_conn
    before = conn.execute(sa.select(PORT)).mappings().all()
    _run(migration.downgrade, conn)
    cols = _columns(conn)
    assert KIND not in cols and OWNER not in cols
    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)
    after = conn.execute(sa.select(PORT)).mappings().all()
    assert len(after) == len(before)
    for row in after:
        assert row[KIND] == "nav"
        assert row[OWNER] is None


def _refuses(conn, match):
    rows_before = conn.execute(sa.select(PORT)).all()
    cols_before = _columns(conn)
    statements = []

    def capture(_c, _cur, statement, _p, _ctx, _em):
        statements.append(statement.strip().split()[0].upper())

    sa.event.listen(conn, "before_cursor_execute", capture)
    try:
        with pytest.raises(RuntimeError, match=match):
            _run(migration.downgrade, conn)
    finally:
        sa.event.remove(conn, "before_cursor_execute", capture)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE"}
    assert set(_columns(conn)) == set(cols_before)
    assert conn.execute(sa.select(PORT)).all() == rows_before


def test_ledger_kind_refuses_before_any_write(migration_conn):
    conn = migration_conn
    conn.execute(PORT.update().where(PORT.c.code == "E2E_PORT").values(kind="ledger"))
    _refuses(conn, "台账型组合")


def test_owner_refuses_before_any_write(migration_conn):
    conn = migration_conn
    inv = conn.execute(sa.text("SELECT code FROM investor LIMIT 1")).scalar()
    conn.execute(
        PORT.update().where(PORT.c.code == "E2E_PORT").values(owner_investor_code=inv)
    )
    _refuses(conn, "归属投资人")


def test_downgrade_without_column_is_noop(migration_conn):
    conn = migration_conn
    _run(migration.downgrade, conn)
    assert KIND not in _columns(conn)
    _run(migration.downgrade, conn)
    _run(migration.upgrade, conn)
    assert KIND in _columns(conn)


@pytest.mark.dialect
class TestMysqlMigration:
    def test_round_trip(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL DDL 隐式提交下的迁移往返验证")
        _run(migration.downgrade, migration_conn)
        assert KIND not in _columns(migration_conn)
        _run(migration.upgrade, migration_conn)
        assert KIND in _columns(migration_conn)

    def test_ledger_refuses_before_implicit_ddl_commit(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL 拒绝降级零写入验证")
        conn = migration_conn
        conn.execute(PORT.update().where(PORT.c.code == "E2E_PORT").values(kind="ledger"))
        conn.commit()
        with pytest.raises(RuntimeError, match="台账型组合"):
            _run(migration.downgrade, conn)
        assert KIND in _columns(conn)