"""#705：在 pytest 自有库验证 0021 往返、存量回填与幂等。"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/0021_product_hk_sale_code.py"
)
spec = importlib.util.spec_from_file_location("migration_0021", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

PRODUCT = sa.table(
    "product",
    sa.column("code"),
    sa.column("market"),
    sa.column("name"),
    sa.column("product_type"),
    sa.column("hk_sale_code"),
)
# seed_base 已种的港互认产品（迁移静态映射的两只之一）
SEEDED_HK = "1001767344"
EXTRA_HK = "1001767346"
EXTRA_OTC = "990001.OF"


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _columns(conn):
    return {col["name"] for col in sa.inspect(conn).get_columns("product")}


def _sale_code(conn, code):
    row = conn.execute(
        sa.select(PRODUCT.c.hk_sale_code).where(PRODUCT.c.code == code)
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
    # MySQL DDL 隐式提交：用例事务的 rollback 撤不掉 upgrade 的 ADD COLUMN 与
    # UPDATE，故在提交型连接上恢复——删本用例插入的行、把种子行的 hk_sale_code
    # 置回 NULL、列缺失时补回（Base.metadata 含该列，留缺列态会拖垮后续 ORM 用例）。
    with test_engine.begin() as conn:
        for code in rows:
            conn.execute(PRODUCT.delete().where(PRODUCT.c.code == code))
        if "hk_sale_code" in _columns(conn):
            conn.execute(
                PRODUCT.update()
                .where(PRODUCT.c.code == SEEDED_HK)
                .values(hk_sale_code=None)
            )
        else:
            Operations(MigrationContext.configure(conn)).add_column(
                "product",
                sa.Column("hk_sale_code", sa.String(length=10), nullable=True),
            )


def _insert_product(conn, rows, code, market):
    conn.execute(
        PRODUCT.insert().values(
            code=code, market=market, name=f"测试 {code}", product_type="ETF"
        )
    )
    rows.append(code)


def test_upgrade_backfills_known_hk_products(migration_conn):
    conn, rows = migration_conn
    _insert_product(conn, rows, EXTRA_HK, "HK_MUTUAL")
    _insert_product(conn, rows, EXTRA_OTC, "CN_OTC")

    _run(migration.upgrade, conn)

    assert "hk_sale_code" in _columns(conn)
    # 存量种子行与用例插入行都按静态映射回填
    assert _sale_code(conn, SEEDED_HK) == "968050"
    assert _sale_code(conn, EXTRA_HK) == "968052"
    # 清单外/非港互认产品保持 NULL：读侧回退原码，不伪造显示码
    assert _sale_code(conn, EXTRA_OTC) is None

    # 幂等：重跑只重写同一常量值
    _run(migration.upgrade, conn)
    assert _sale_code(conn, SEEDED_HK) == "968050"

    _run(migration.downgrade, conn)
    assert "hk_sale_code" not in _columns(conn)
