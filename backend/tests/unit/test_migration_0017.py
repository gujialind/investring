# ============================================================================
# 单元测试：issue #580 迁移 0017 price_record.unit_price 收口 NOT NULL
# ============================================================================
# 断言重点：
# ① 修订链挂在 0016 之后；**模型与迁移绑死**——模型声明 nullable=False，迁移必须落下
#    同一语义。迁移一旦合入就冻结在文件里，模型回头改了没人提醒，故这里钉在一起。
# ② 迁移前形态的库（unit_price 可空、混着有价行与 NULL 行）跑 upgrade 后：NULL 行被删、
#    有价行一条不少、列变 NOT NULL、再插 NULL 被拒。
#    ——前两条在 SQLite 上就能验（走 batch 重建表分支），是本文件唯一能本地实跑的部分。
# ③ MySQL 原生 `ALTER ... MODIFY` 分支（SQLite 分支覆盖不到）在 CI backend-test-mysql
#    上验最终态，本地 SQLite 下 skip。
#
# **本次未能验证**：MySQL 上的 alter 语义、以及 `test_migration_*` 系列一贯的 information_schema
# 真库核对——本地 MySQL 隧道（127.0.0.1:13306）不可连接，dialect 用例在 SQLite 下自动 skip。
# ============================================================================

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0017_price_record_unit_price_not_null.py"
)
_spec = importlib.util.spec_from_file_location("mig_0017_price_not_null", MIGRATION_PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

from app.models.price_record import PriceRecord  # noqa: E402


def _run(fn, connection):
    """在给定连接上程序化执行迁移（alembic.op 代理需 Operations.context 安装）"""
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        fn()


#: 迁移**前**的表形态：unit_price 可空
PRE_SCHEMA = """
CREATE TABLE price_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code VARCHAR(20) NOT NULL,
    market VARCHAR(20) NOT NULL,
    price_date DATE NOT NULL,
    unit_price NUMERIC(10, 4),
    accumulated_nav NUMERIC(10, 4),
    pre_close NUMERIC(10, 4),
    pct_change NUMERIC(8, 4),
    source VARCHAR(20),
    created_at DATETIME,
    updated_at DATETIME
)
"""

PRICED = ("P1", "CN_OTC", "2026-01-01", 1.5000)
NULLABLE1 = ("P3", "CN_OTC", "2026-01-03", None)


def _seed(conn):
    conn.execute(
        text(
            "INSERT INTO price_record (product_code, market, price_date, unit_price) "
            "VALUES (:c, :m, :d, :p)"
        ),
        [{"c": PRICED[0], "m": PRICED[1], "d": PRICED[2], "p": PRICED[3]},
         {"c": "P2", "m": "CN_OTC", "d": "2026-01-02", "p": 2.5000},
         {"c": NULLABLE1[0], "m": NULLABLE1[1], "d": NULLABLE1[2], "p": NULLABLE1[3]},
         {"c": "P4", "m": "CN_OTC", "d": "2026-01-04", "p": None}],
    )


def _nullable_of(engine, column="unit_price"):
    cols = [c for c in inspect(engine).get_columns("price_record") if c["name"] == column]
    assert cols, f"price_record 上没有 {column} 列"
    return cols[0]["nullable"]


@pytest.fixture
def legacy_db(tmp_path):
    """迁移前形态的库：unit_price 可空且已混有 NULL 行"""
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        conn.execute(text(PRE_SCHEMA))
        _seed(conn)
    yield engine
    engine.dispose()


class TestRevisionAndModelBinding:
    def test_hangs_off_0016(self):
        assert migration.revision == "0017"
        assert migration.down_revision == "0016"

    def test_model_declares_not_null(self):
        """模型与迁移若各说各话，NOT NULL 这道防线就等于没有"""
        assert PriceRecord.__table__.c.unit_price.nullable is False


class TestSqliteUpgrade:
    def test_null_rows_are_dropped_priced_rows_kept(self, legacy_db):
        with legacy_db.begin() as conn:
            _run(migration.upgrade, conn)

        with legacy_db.connect() as conn:
            remaining = sorted(r[0] for r in conn.execute(text("SELECT product_code FROM price_record")))
        assert remaining == ["P1", "P2"], "无单价行被删、有价行必须一条不少"

    def test_column_becomes_not_null(self, legacy_db):
        with legacy_db.begin() as conn:
            _run(migration.upgrade, conn)
        assert _nullable_of(legacy_db) is False

    def test_inserting_null_price_is_rejected(self, legacy_db):
        """#580 想到达的终点：单价不可为空这件事由数据库自己守着"""
        with legacy_db.begin() as conn:
            _run(migration.upgrade, conn)
        from sqlalchemy.exc import IntegrityError

        with legacy_db.begin() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO price_record (product_code, market, price_date, unit_price) "
                        "VALUES ('PX', 'CN_OTC', '2026-02-01', NULL)"
                    )
                )

    def test_downgrade_restores_nullability(self, legacy_db):
        with legacy_db.begin() as conn:
            _run(migration.upgrade, conn)
            _run(migration.downgrade, conn)
        assert _nullable_of(legacy_db) is True


def test_upgrade_downgrade_on_session_db_does_not_raise(test_engine):
    """`ci.yml` 的 `alembic downgrade -1` 是真跑的路径：已经是目标态时仍不许抛异常"""
    with test_engine.begin() as conn:
        _run(migration.upgrade, conn)
        _run(migration.downgrade, conn)
        _run(migration.upgrade, conn)


@pytest.mark.dialect
class TestMysqlFinalState:
    def test_column_is_not_null_on_mysql(self, test_engine):
        """MySQL 走原生 `ALTER ... MODIFY` 分支，SQLite 的 batch 重建覆盖不到它"""
        if test_engine.dialect.name != "mysql":
            pytest.skip("只在 MySQL 上成立")
        with test_engine.begin() as conn:
            _run(migration.upgrade, conn)
        assert _nullable_of(test_engine) is False
