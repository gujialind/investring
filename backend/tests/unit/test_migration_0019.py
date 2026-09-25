"""#522：在 pytest 自有库验证迁移往返、幂等与拒绝降级零写入。"""

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.base import Base
from app.models.product import Product
from app.models.share_change_event import ShareChangeEvent

PATH = Path(__file__).resolve().parents[2] / "alembic/versions/0019_dividend_cash_pay_date.py"
spec = importlib.util.spec_from_file_location("migration_0019", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

CODE = "IN_TRANSIT_DIVIDEND"
DAY = date(2025, 11, 12)
PRODUCT = Product.__table__
EVENT = ShareChangeEvent.__table__
KEY = sa.and_(PRODUCT.c.code == CODE, PRODUCT.c.market == "")
# 运行时元数据探针表：不属于 Base.metadata，会话开头的 drop_all 收不走它。
PROBE_TABLE = "migration_0019_reference_probe"


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _columns(conn):
    return {col["name"]: col for col in sa.inspect(conn).get_columns("share_change_event")}


def _seed(conn):
    return conn.execute(sa.select(PRODUCT).where(KEY)).mappings().one_or_none()


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    rows = []
    with test_engine.connect() as conn:
        original_seed = dict(_seed(conn))
        conn.info["migration_0019_rows"] = rows
        try:
            yield conn
        finally:
            conn.rollback()
            conn.info.pop("migration_0019_rows", None)
    # MySQL DDL 隐式提交；新连接只清理本用例插入的行并恢复目标态。
    with test_engine.begin() as conn:
        # 探针表的 CREATE 在 SQLite 下走 DDL 自动提交，DROP 却留在用例事务里被上面的
        # rollback 一起撤掉，留下一张空表；它不在 Base.metadata 里，下一轮会话开头
        # drop_all 收不走，db_isolation 归属守卫会以「来源不明的表」拒跑整个会话。
        # 故在这个提交型连接上兜底清理（MySQL 侧用例内已 DROP，此处为幂等空操作）。
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
        if "cash_pay_date" not in _columns(conn):
            Operations(MigrationContext.configure(conn)).add_column(
                "share_change_event", sa.Column("cash_pay_date", sa.Date(), nullable=True)
            )
        for table, key in reversed(rows):
            conn.execute(table.delete().where(table.c.id == key))
        if _seed(conn) is None:
            conn.execute(PRODUCT.insert().values(original_seed))
        else:
            conn.execute(PRODUCT.update().where(KEY).values(original_seed))


def _insert(conn, table, **values):
    key = conn.execute(table.insert().values(**values)).inserted_primary_key[0]
    conn.info["migration_0019_rows"].append((table, key))
    return key


def _event(conn, *, cash_pay_date=None):
    return _insert(
        conn, EVENT, portfolio_code="E2E_PORT",
        product_code="CASH", market="", event_type="cash_dividend",
        ex_date=DAY, entitlement_date=date(2025, 11, 10), cash_pay_date=cash_pay_date,
        platform_code="MYCF", event_source="manual", status="pending",
    )


def _assert_refused_without_writes(conn, message, table=None):
    seed_before = dict(_seed(conn))
    columns_before = _columns(conn)
    rows_before = conn.execute(sa.select(table)).all() if table is not None else None
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().split()[0].upper())

    sa.event.listen(conn, "before_cursor_execute", capture)
    try:
        with pytest.raises(RuntimeError, match=message):
            _run(migration.downgrade, conn)
    finally:
        sa.event.remove(conn, "before_cursor_execute", capture)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE"}
    assert dict(_seed(conn)) == seed_before
    assert set(_columns(conn)) == set(columns_before)
    if table is not None:
        assert conn.execute(sa.select(table)).all() == rows_before


def test_revision_and_model_contract():
    assert migration.revision == "0019"
    assert migration.down_revision == "0018"
    assert EVENT.c.cash_pay_date.nullable
    assert isinstance(EVENT.c.cash_pay_date.type, sa.Date)
    source = PATH.read_text()
    assert "from app.models" not in source
    assert "import app.models" not in source


def test_create_all_target_upgrade_is_idempotent(migration_conn):
    conn = migration_conn
    _event(conn, cash_pay_date=date(2025, 11, 15))
    before = conn.execute(sa.select(EVENT)).all()
    seed_before = dict(_seed(conn))
    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)
    assert conn.execute(sa.select(EVENT)).all() == before
    assert dict(_seed(conn)) == seed_before
    assert _columns(conn)["cash_pay_date"]["nullable"]


def _round_trip(conn):
    event_id = _event(conn)
    before = conn.execute(sa.select(EVENT).where(EVENT.c.id == event_id)).mappings().one()
    _run(migration.downgrade, conn)
    assert "cash_pay_date" not in _columns(conn)
    assert _seed(conn) is None
    old_columns = [col for col in EVENT.c if col.name != "cash_pay_date"]
    old_row = conn.execute(sa.select(*old_columns).where(EVENT.c.id == event_id)).mappings().one()
    assert dict(old_row) == {key: value for key, value in before.items() if key != "cash_pay_date"}
    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)
    assert conn.execute(sa.select(EVENT).where(EVENT.c.id == event_id)).mappings().one() == before
    seed = _seed(conn)
    assert seed["market"] == ""
    assert seed["product_type"] == "IN_TRANSIT"
    assert seed["confirm_days"] == seed["nav_lag_days"] == 0
    assert all(seed[field] is None for field in (
        "asset_class_code", "region_code", "style_code", "size_code", "segment_code",
    ))


def test_unused_seed_round_trip_preserves_existing_events(migration_conn):
    _round_trip(migration_conn)


def test_existing_column_missing_seed_is_repaired(migration_conn):
    conn = migration_conn
    conn.execute(PRODUCT.delete().where(KEY))
    _run(migration.upgrade, conn)
    assert _seed(conn) is not None
    assert "cash_pay_date" in _columns(conn)


@pytest.mark.parametrize("event_type", ["cash_dividend", "forced_adjustment"])
def test_any_non_null_date_refuses_before_changes(migration_conn, event_type):
    conn = migration_conn
    key = _event(conn, cash_pay_date=date(2025, 11, 15))
    conn.execute(EVENT.update().where(EVENT.c.id == key).values(event_type=event_type))
    _assert_refused_without_writes(conn, "非空 cash_pay_date", EVENT)


REFERENCE_ROWS = {
    "portfolio_position": dict(portfolio_code="E2E_PORT", platform_code="MYCF", cash_amount=10, snapshot_date=DAY),
    "price_record": dict(price_date=DAY, unit_price=1),
    "trade": dict(portfolio_code="E2E_PORT", platform_code="MYCF", trade_type="buy", amount=10,
                  trade_date=DAY, transfer_group="migration_0019"),
    "share_change_event": dict(portfolio_code="E2E_PORT", event_type="forced_adjustment", cash_change=10,
                               ex_date=DAY, entitlement_date=date(2025, 11, 10), event_source="manual"),
}


@pytest.mark.parametrize("table_name", REFERENCE_ROWS)
def test_all_current_product_foreign_keys_refuse(migration_conn, table_name):
    conn = migration_conn
    table = Base.metadata.tables[table_name]
    _insert(conn, table, product_code=CODE, market="", **REFERENCE_ROWS[table_name])
    _assert_refused_without_writes(conn, table_name, table)


def test_reference_cases_cover_current_model_foreign_keys():
    referring = {
        table.name for table in Base.metadata.tables.values()
        if any(fk.column.table.name == "product" for fk in table.foreign_keys)
    }
    assert referring == set(REFERENCE_ROWS)


def test_runtime_metadata_finds_unlisted_renamed_columns(migration_conn):
    conn = migration_conn
    metadata = sa.MetaData()
    PRODUCT.to_metadata(metadata)
    extra = sa.Table(
        PROBE_TABLE, metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fund", sa.String(20)), sa.Column("venue", sa.String(20)),
        sa.ForeignKeyConstraint(["fund", "venue"], ["product.code", "product.market"],
                                name="fk_migration_0019_probe", ondelete="CASCADE"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_general_ci",
    )
    extra.create(conn)
    try:
        conn.execute(extra.insert().values(id=1, fund=CODE, venue=""))
        _assert_refused_without_writes(conn, extra.name, extra)
    finally:
        extra.drop(conn)


@pytest.mark.dialect
class TestMysqlMigration:
    def test_reference_refuses_before_implicit_ddl_commit(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL 外键与 DDL 隐式提交验证")
        table = Base.metadata.tables["portfolio_position"]
        _insert(migration_conn, table, product_code=CODE, market="", **REFERENCE_ROWS[table.name])
        migration_conn.commit()
        _assert_refused_without_writes(migration_conn, "portfolio_position", table)

    def test_unused_seed_round_trip(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL 迁移往返验证")
        _round_trip(migration_conn)
