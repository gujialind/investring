"""#887：在 pytest 自有库验证迁移 0027 的回填范围、往返、幂等与拒绝降级零写入。"""

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.base import Base

PATH = Path(__file__).resolve().parents[2] / "alembic/versions/0027_dividend_method_config.py"
spec = importlib.util.spec_from_file_location("migration_0027", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

CONFIG = Base.metadata.tables["dividend_method_config"]
POSITION = Base.metadata.tables["portfolio_position"]
PRODUCT = Base.metadata.tables["product"]
TODAY = date.today()


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _has_table(conn):
    return sa.inspect(conn).has_table("dividend_method_config")


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    rows = []
    products = []
    with test_engine.connect() as conn:
        conn.info["migration_0027_rows"] = rows
        conn.info["migration_0027_products"] = products
        try:
            yield conn
        finally:
            conn.rollback()
            conn.info.pop("migration_0027_rows", None)
            conn.info.pop("migration_0027_products", None)
    # MySQL DDL 隐式提交：提交型连接上清理本用例插入的行并恢复目标态（空配置表）。
    with test_engine.begin() as conn:
        if _has_table(conn):
            conn.execute(CONFIG.delete())
        for table, key in reversed(rows):
            conn.execute(table.delete().where(table.c.id == key))
        for code, market in products:
            conn.execute(PRODUCT.delete().where(
                PRODUCT.c.code == code, PRODUCT.c.market == market,
            ))
        if not _has_table(conn):
            _run(migration.upgrade, conn)
            conn.execute(CONFIG.delete())


def _track(conn, table, key):
    conn.info["migration_0027_rows"].append((table, key))
    return key


def _product(conn, code, market):
    existing = conn.execute(
        sa.select(PRODUCT.c.code).where(PRODUCT.c.code == code, PRODUCT.c.market == market)
    ).first()
    if existing:
        return
    conn.execute(PRODUCT.insert().values(
        code=code, market=market, name="迁移测试", product_type="OEF",
    ))
    conn.info["migration_0027_products"].append((code, market))


def _position(conn, *, product_code="000300.OF", market="CN_OTC", platform_code="MYCF",
              shares=100, snapshot_date=date(2026, 11, 13), portfolio_code="E2E_PORT"):
    key = conn.execute(POSITION.insert().values(
        portfolio_code=portfolio_code, product_code=product_code, market=market,
        platform_code=platform_code, shares=shares, snapshot_date=snapshot_date,
    )).inserted_primary_key[0]
    return _track(conn, POSITION, key)


def _config(conn, *, source="default", method="reinvest_dividend",
            effective_from=TODAY, product_code="000300.OF", market="CN_OTC",
            platform_code="MYCF", portfolio_code="E2E_PORT"):
    # 配置行由夹具 CONFIG.delete() 整体清理，无需逐行跟踪。
    return conn.execute(CONFIG.insert().values(
        portfolio_code=portfolio_code, product_code=product_code, market=market,
        platform_code=platform_code, dividend_method=method,
        effective_from=effective_from, source=source,
    )).inserted_primary_key[0]


def _config_rows(conn):
    return conn.execute(sa.select(CONFIG)).mappings().all()


def test_revision_and_model_contract():
    assert migration.revision == "0027"
    assert migration.down_revision == "0026"
    source = PATH.read_text()
    assert "from app.models" not in source
    assert "import app.models" not in source
    assert "dividend_method_config" in Base.metadata.tables


def test_upgrade_backfills_only_cn_otc_valid_holdings(migration_conn):
    """A9/A14：仅 CN_OTC、shares>0、platform 非 NULL 的最新快照三元组回填 default。"""
    conn = migration_conn
    _product(conn, "000300.OF", "HK_MUTUAL")
    _product(conn, "510300.SH", "CN_EXCHANGE")
    _product(conn, "ZERO.OF", "CN_OTC")
    _product(conn, "NULLP.OF", "CN_OTC")
    _position(conn)  # CN_OTC 有效（000300.OF / MYCF）→ 回填
    _position(conn, market="HK_MUTUAL")  # 港互认不回填
    _position(conn, product_code="510300.SH", market="CN_EXCHANGE")  # 场内排除
    _position(conn, product_code="ZERO.OF", shares=0)  # 零份额排除
    _position(conn, product_code="NULLP.OF", platform_code=None)  # 无平台排除
    _position(conn, snapshot_date=date(2026, 11, 10))  # 旧快照（同键已被最新覆盖）

    _run(migration.upgrade, conn)

    rows = _config_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert (row["portfolio_code"], row["product_code"], row["market"], row["platform_code"]) == (
        "E2E_PORT", "000300.OF", "CN_OTC", "MYCF",
    )
    assert row["source"] == "default"
    assert row["dividend_method"] == "reinvest_dividend"
    assert row["effective_from"] == TODAY


def test_upgrade_is_idempotent(migration_conn):
    conn = migration_conn
    _position(conn)
    _run(migration.upgrade, conn)
    before = _config_rows(conn)
    _run(migration.upgrade, conn)
    assert _config_rows(conn) == before


def test_upgrade_preserves_existing_explicit_row(migration_conn):
    """回填按唯一键去重：已有 explicit 行不被 default 覆盖。"""
    conn = migration_conn
    _position(conn)
    _config(conn, source="explicit", method="cash_dividend")
    _run(migration.upgrade, conn)
    rows = _config_rows(conn)
    assert len(rows) == 1
    assert rows[0]["source"] == "explicit"
    assert rows[0]["dividend_method"] == "cash_dividend"


def test_downgrade_refuses_explicit_before_any_write(migration_conn):
    """R3：存在 explicit 行时降级在任何 DDL/DML 之前拒绝，表与数据零改动。"""
    conn = migration_conn
    _config(conn, source="explicit", method="cash_dividend")
    rows_before = _config_rows(conn)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().split()[0].upper())

    sa.event.listen(conn, "before_cursor_execute", capture)
    try:
        with pytest.raises(RuntimeError, match="explicit"):
            _run(migration.downgrade, conn)
    finally:
        sa.event.remove(conn, "before_cursor_execute", capture)

    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE"}
    assert _has_table(conn)
    assert _config_rows(conn) == rows_before


def test_downgrade_default_only_round_trip(migration_conn):
    """仅 default 行可逆：删行 drop 表，再升级重建并回填。"""
    conn = migration_conn
    _position(conn)
    _run(migration.upgrade, conn)
    assert len(_config_rows(conn)) == 1

    _run(migration.downgrade, conn)
    assert not _has_table(conn)

    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)
    assert _has_table(conn)
    rows = _config_rows(conn)
    assert len(rows) == 1
    assert rows[0]["source"] == "default"


def test_downgrade_without_table_is_noop(migration_conn):
    conn = migration_conn
    _run(migration.downgrade, conn)
    assert not _has_table(conn)
    _run(migration.downgrade, conn)
    assert not _has_table(conn)
    _run(migration.upgrade, conn)
    assert _has_table(conn)


@pytest.mark.dialect
class TestMysqlMigration:
    def test_round_trip(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL DDL 隐式提交下的迁移往返验证")
        _position(migration_conn)
        _run(migration.upgrade, migration_conn)
        _run(migration.downgrade, migration_conn)
        assert not _has_table(migration_conn)
        _run(migration.upgrade, migration_conn)
        assert _has_table(migration_conn)

    def test_explicit_refuses_before_implicit_ddl_commit(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL 拒绝降级零写入验证")
        conn = migration_conn
        _config(conn, source="explicit")
        conn.commit()
        with pytest.raises(RuntimeError, match="explicit"):
            _run(migration.downgrade, conn)
        assert _has_table(conn)