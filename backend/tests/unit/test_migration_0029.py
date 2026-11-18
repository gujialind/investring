"""#905 PR2：在 pytest 自有库验证迁移 0029 的往返、幂等与拒绝降级零写入。"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = Path(__file__).resolve().parents[2] / "alembic/versions/0029_ledger_entry_flow.py"
spec = importlib.util.spec_from_file_location("migration_0029", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _tables(conn):
    return set(sa.inspect(conn).get_table_names())


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    with test_engine.connect() as conn:
        try:
            yield conn
        finally:
            conn.rollback()
    # 恢复目标态（MySQL DDL 隐式提交）：清台账行、补回缺失表
    with test_engine.begin() as conn:
        for t in ("ledger_flow", "ledger_entry"):
            if t in _tables(conn):
                conn.execute(sa.text(f"DELETE FROM {t}"))
        if "ledger_entry" not in _tables(conn) or "ledger_flow" not in _tables(conn):
            _run(migration.upgrade, conn)


def test_revision_and_down_revision():
    assert migration.revision == "0029"
    assert migration.down_revision == "0028"
    source = PATH.read_text()
    assert "from app.models" not in source


def test_upgrade_idempotent(migration_conn):
    _run(migration.upgrade, migration_conn)
    _run(migration.upgrade, migration_conn)
    assert "ledger_entry" in _tables(migration_conn)
    assert "ledger_flow" in _tables(migration_conn)


def test_round_trip_empty(migration_conn):
    _run(migration.downgrade, migration_conn)
    assert "ledger_entry" not in _tables(migration_conn)
    assert "ledger_flow" not in _tables(migration_conn)
    _run(migration.upgrade, migration_conn)
    assert "ledger_entry" in _tables(migration_conn)
    assert "ledger_flow" in _tables(migration_conn)


def test_refuses_with_rows_before_any_write(migration_conn):
    conn = migration_conn
    conn.execute(sa.text(
        "INSERT INTO ledger_entry (portfolio_code, platform_code, value_date, market_value, asset_kind) "
        "VALUES ('E2E_PORT', 'MYCF', '2026-01-05', 100, 'non_cash')"
    ))
    tables_before = _tables(conn)
    stmts = []

    def capture(_c, _cur, statement, _p, _ctx, _em):
        stmts.append(statement.strip().split()[0].upper())

    sa.event.listen(conn, "before_cursor_execute", capture)
    try:
        with pytest.raises(RuntimeError, match="台账记录"):
            _run(migration.downgrade, conn)
    finally:
        sa.event.remove(conn, "before_cursor_execute", capture)
    assert not set(stmts) & {"INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE"}
    assert _tables(conn) == tables_before


def test_downgrade_without_tables_is_noop(migration_conn):
    _run(migration.downgrade, migration_conn)
    _run(migration.downgrade, migration_conn)
    assert "ledger_entry" not in _tables(migration_conn)
    _run(migration.upgrade, migration_conn)
    assert "ledger_entry" in _tables(migration_conn)


@pytest.mark.dialect
class TestMysqlMigration:
    def test_round_trip(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL DDL 隐式提交下的迁移往返验证")
        _run(migration.downgrade, migration_conn)
        assert "ledger_entry" not in _tables(migration_conn)
        _run(migration.upgrade, migration_conn)
        assert "ledger_entry" in _tables(migration_conn)