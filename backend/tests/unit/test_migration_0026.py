"""#856：product.is_active 迁移往返、存量回填与幂等（仿 0021）。

pytest 会话以 Base.metadata create_all 建库（已含本列），故 upgrade 的 _has_column
守卫常态跳过；要验真正的 ADD COLUMN 回填路径，先 downgrade 删列再 upgrade。
"""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/0026_product_is_active.py"
)
spec = importlib.util.spec_from_file_location("migration_0026", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

PRODUCT = sa.table(
    "product",
    sa.column("code"),
    sa.column("market"),
    sa.column("name"),
    sa.column("product_type"),
    sa.column("is_active"),
)
SEEDED = "510300.SH"
EXTRA = "990026.OF"


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _columns(conn):
    return {col["name"] for col in sa.inspect(conn).get_columns("product")}


def _column(conn, name):
    return next(c for c in sa.inspect(conn).get_columns("product") if c["name"] == name)


def _is_active(conn, code):
    row = conn.execute(
        sa.select(PRODUCT.c.is_active).where(PRODUCT.c.code == code)
    ).one_or_none()
    return row[0] if row else None


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    rows = []
    with test_engine.connect() as conn:
        try:
            yield conn, rows
        finally:
            conn.rollback()
    # MySQL DDL 隐式提交：用例事务的 rollback 撤不掉 ADD/DROP COLUMN，故在提交型
    # 连接上恢复——删本用例插入的行；列缺失时补回（Base.metadata 含该列，留缺列态
    # 会拖垮后续 ORM 用例）。
    with test_engine.begin() as conn:
        for code in rows:
            conn.execute(PRODUCT.delete().where(PRODUCT.c.code == code))
        if "is_active" not in _columns(conn):
            _run(migration.upgrade, conn)


def test_upgrade_idempotent_when_column_present(migration_conn):
    """create_all 已含本列：_has_column 守卫跳过，重跑不报错、种子行恒 true。"""
    conn, _ = migration_conn
    assert "is_active" in _columns(conn)
    _run(migration.upgrade, conn)
    _run(migration.upgrade, conn)
    assert "is_active" in _columns(conn)
    assert _column(conn, "is_active")["nullable"] is False
    assert bool(_is_active(conn, SEEDED)) is True


def test_downgrade_then_upgrade_backfills_existing_rows(migration_conn):
    """删列→插行→加列：ADD COLUMN NOT NULL DEFAULT 1 把存量与新增行一律回填 true。"""
    conn, rows = migration_conn
    _run(migration.downgrade, conn)
    assert "is_active" not in _columns(conn)

    conn.execute(
        PRODUCT.insert().values(
            code=EXTRA, market="CN_OTC", name="测试停用回填", product_type="OEF"
        )
    )
    rows.append(EXTRA)

    _run(migration.upgrade, conn)
    assert "is_active" in _columns(conn)
    assert _column(conn, "is_active")["nullable"] is False
    # 存量种子行与删列后插入的行都被 server_default 回填为 true
    assert bool(_is_active(conn, SEEDED)) is True
    assert bool(_is_active(conn, EXTRA)) is True


def test_roundtrip_restores_column(migration_conn):
    """downgrade→upgrade 往返：列回到 NOT NULL 形态，种子行无数据被销毁。"""
    conn, _ = migration_conn
    _run(migration.downgrade, conn)
    assert "is_active" not in _columns(conn)
    _run(migration.upgrade, conn)
    assert "is_active" in _columns(conn)
    assert _column(conn, "is_active")["nullable"] is False
    assert bool(_is_active(conn, SEEDED)) is True