"""#743：在 pytest 自有库验证迁移 0022 的往返、幂等与拒绝降级零写入。"""

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.share_change_event import ShareChangeEvent

PATH = Path(__file__).resolve().parents[2] / "alembic/versions/0022_share_confirm_date.py"
spec = importlib.util.spec_from_file_location("migration_0022", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

EVENT = ShareChangeEvent.__table__
COLUMN = "share_confirm_date"
EX = date(2026, 10, 19)
ENT = date(2026, 10, 19)


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _columns(conn):
    return {col["name"]: col for col in sa.inspect(conn).get_columns("share_change_event")}


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    rows = []
    with test_engine.connect() as conn:
        conn.info["migration_0022_rows"] = rows
        try:
            yield conn
        finally:
            conn.rollback()
            conn.info.pop("migration_0022_rows", None)
    # MySQL DDL 隐式提交：用例事务的 rollback 撤不掉 DROP/ADD COLUMN，故在提交型连接上
    # 恢复目标态——删本用例插入的事件、把残留的确认日置回 NULL、列缺失时补回
    # （Base.metadata 含该列，留缺列态会拖垮后续 ORM 用例）。
    with test_engine.begin() as conn:
        for key in rows:
            conn.execute(EVENT.delete().where(EVENT.c.id == key))
        if COLUMN not in _columns(conn):
            Operations(MigrationContext.configure(conn)).add_column(
                "share_change_event", sa.Column(COLUMN, sa.Date(), nullable=True)
            )
        else:
            conn.execute(EVENT.update().values(**{COLUMN: None}))


def _event(conn, *, share_confirm_date=None):
    key = conn.execute(
        EVENT.insert().values(
            portfolio_code="E2E_PORT", product_code="000300.OF", market="CN_OTC",
            event_type="reinvest_dividend", ex_date=EX, entitlement_date=ENT,
            share_confirm_date=share_confirm_date, platform_code="MYCF",
            div_cash="0.0039", event_source="manual", status="pending",
        )
    ).inserted_primary_key[0]
    conn.info["migration_0022_rows"].append(key)
    return key


def test_revision_and_model_contract():
    assert migration.revision == "0022"
    assert migration.down_revision == "0021"
    assert EVENT.c[COLUMN].nullable
    assert isinstance(EVENT.c[COLUMN].type, sa.Date)
    source = PATH.read_text()
    assert "from app.models" not in source
    assert "import app.models" not in source


def test_upgrade_on_target_schema_is_noop(migration_conn):
    """create_all 目标态已含该列：upgrade 只判存在、不重复 DDL、不动数据。"""
    conn = migration_conn
    key = _event(conn, share_confirm_date=date(2026, 10, 20))
    before = conn.execute(sa.select(EVENT).where(EVENT.c.id == key)).all()

    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)

    assert _columns(conn)[COLUMN]["nullable"]
    assert conn.execute(sa.select(EVENT).where(EVENT.c.id == key)).all() == before


def test_round_trip_with_null_values(migration_conn):
    """全 NULL 时可降级：删列不删行，其余列与值原样保留；再升级补回 NULL 列。"""
    conn = migration_conn
    key = _event(conn)
    before = conn.execute(sa.select(EVENT).where(EVENT.c.id == key)).mappings().one()

    _run(migration.downgrade, conn)
    assert COLUMN not in _columns(conn)

    old_columns = [col for col in EVENT.c if col.name != COLUMN]
    old_row = conn.execute(
        sa.select(*old_columns).where(EVENT.c.id == key)
    ).mappings().one()
    assert dict(old_row) == {k: v for k, v in before.items() if k != COLUMN}

    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)
    after = conn.execute(sa.select(EVENT).where(EVENT.c.id == key)).mappings().one()
    assert after[COLUMN] is None
    assert {k: v for k, v in after.items() if k != COLUMN} == dict(old_row)


def test_non_null_value_refuses_before_any_write(migration_conn):
    """已使用的确认日是记账事实：降级在任何 DDL/DML 之前拒绝，列与数据零改动。"""
    conn = migration_conn
    key = _event(conn, share_confirm_date=date(2026, 10, 20))
    rows_before = conn.execute(sa.select(EVENT)).all()
    columns_before = _columns(conn)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().split()[0].upper())

    sa.event.listen(conn, "before_cursor_execute", capture)
    try:
        with pytest.raises(RuntimeError, match="非空 share_confirm_date"):
            _run(migration.downgrade, conn)
    finally:
        sa.event.remove(conn, "before_cursor_execute", capture)

    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE"}
    assert set(_columns(conn)) == set(columns_before)
    assert conn.execute(sa.select(EVENT)).all() == rows_before
    assert conn.execute(
        sa.select(EVENT.c[COLUMN]).where(EVENT.c.id == key)
    ).one()[0] == date(2026, 10, 20)


def test_downgrade_without_column_is_noop(migration_conn):
    conn = migration_conn
    _run(migration.downgrade, conn)
    assert COLUMN not in _columns(conn)
    _run(migration.downgrade, conn)
    assert COLUMN not in _columns(conn)
    _run(migration.upgrade, conn)
    assert COLUMN in _columns(conn)


@pytest.mark.dialect
class TestMysqlMigration:
    def test_round_trip(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL DDL 隐式提交下的迁移往返验证")
        _event(migration_conn)
        _run(migration.downgrade, migration_conn)
        assert COLUMN not in _columns(migration_conn)
        _run(migration.upgrade, migration_conn)
        assert COLUMN in _columns(migration_conn)

    def test_non_null_value_refuses_before_implicit_ddl_commit(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL 拒绝降级零写入验证")
        conn = migration_conn
        _event(conn, share_confirm_date=date(2026, 10, 20))
        conn.commit()
        with pytest.raises(RuntimeError, match="非空 share_confirm_date"):
            _run(migration.downgrade, conn)
        assert COLUMN in _columns(conn)
