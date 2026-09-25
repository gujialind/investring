# ============================================================================
# 单元测试：issue #537 迁移 0018 恢复 product_code NOT NULL（修复 0006 漂移）
# ============================================================================
# 断言重点：
# ① 修订链挂在 0017 之后；**模型与迁移钉死**——白名单必须精确等于模型中所有
#    nullable=False 的 product_code 列（cash_product_code 模型可空、product.code
#    是主键，均不得混入），未来新增模型列会逼着同步维护迁移。
# ② MySQL 分支（假 bind 单测）：全零 NULL → 六列按白名单顺序 MODIFY NOT NULL；
#    任一 NULL → RuntimeError 且**零 DDL**（两段式：先全量核查再动手）。
# ③ SQLite 分支本地实跑：迁移前形态（product_code 可空）upgrade 后插 NULL 被拒；
#    downgrade 恢复可空。真实 MySQL ALTER 语义在隔离 MySQL 8.4 实库另行验证
#    （见 docs/runbooks/deploy-rollback.md §6 演练记录）。
# ============================================================================

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0018_product_code_not_null_restore.py"
)
_spec = importlib.util.spec_from_file_location("mig_0018_product_code_not_null", MIGRATION_PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _run(fn, connection):
    """在给定连接上程序化执行迁移（alembic.op 代理需 Operations.context 安装）"""
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        fn()


# ---------------------------------------------------------------- 修订链与模型钉死
def test_revision_chain():
    assert migration.revision == "0018"
    assert migration.down_revision == "0017"


def test_whitelist_equals_model_not_null_product_code_columns():
    import app.models  # noqa: F401  确保全部模型注册进 metadata
    from app.models.base import Base

    required = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name == "product_code" and not column.nullable
    }
    assert required == set(migration.PRODUCT_CODE_COLUMNS)
    assert len(migration.PRODUCT_CODE_COLUMNS) == 6


# ---------------------------------------------------------------- MySQL 分支（假 bind）
class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class FakeMySQLBind:
    """按 SQL 子串返回预设 NULL 计数；记录全部执行的语句。"""

    def __init__(self, null_counts=None):
        self.dialect = SimpleNamespace(name="mysql")
        self.null_counts = null_counts or {}
        self.executed = []

    def execute(self, clause):
        sql = str(clause)
        self.executed.append(sql)
        for key, count in self.null_counts.items():
            if key in sql:
                return FakeResult(count)
        return FakeResult(0)


@pytest.fixture
def mysql_bind(monkeypatch):
    def install(bind):
        monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: bind))
        return bind
    return install


def test_mysql_upgrade_alters_all_six_in_whitelist_order(mysql_bind):
    bind = mysql_bind(FakeMySQLBind())
    migration.upgrade()
    alters = [sql for sql in bind.executed if sql.startswith("ALTER")]
    assert alters == [
        f"ALTER TABLE `{table}` MODIFY `{col}` VARCHAR(20) NOT NULL"
        for table, col in migration.PRODUCT_CODE_COLUMNS
    ]
    counts = [sql for sql in bind.executed if sql.startswith("SELECT COUNT")]
    assert len(counts) == 6


def test_mysql_null_rows_refuse_with_zero_ddl(mysql_bind):
    bind = mysql_bind(FakeMySQLBind(null_counts={"`trade` WHERE `product_code`": 3}))
    with pytest.raises(RuntimeError) as error:
        migration.upgrade()
    message = str(error.value)
    assert "trade.product_code=3 行" in message and "人工核对" in message
    assert not [sql for sql in bind.executed if sql.startswith("ALTER")]  # 零 DDL


def test_mysql_violations_are_all_reported_not_first_only(mysql_bind):
    bind = mysql_bind(FakeMySQLBind(null_counts={
        "`trade` WHERE `product_code`": 1, "`price_record` WHERE `product_code`": 2}))
    with pytest.raises(RuntimeError) as error:
        migration.upgrade()
    assert "trade.product_code=1 行" in str(error.value)
    assert "price_record.product_code=2 行" in str(error.value)


def test_mysql_downgrade_relaxes_constraint(mysql_bind):
    bind = mysql_bind(FakeMySQLBind())
    migration.downgrade()
    alters = [sql for sql in bind.executed if sql.startswith("ALTER")]
    assert alters == [
        f"ALTER TABLE `{table}` MODIFY `{col}` VARCHAR(20) NULL"
        for table, col in migration.PRODUCT_CODE_COLUMNS
    ]


# ---------------------------------------------------------------- SQLite 分支（实跑）
@pytest.fixture
def sqlite_db():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for table, col in migration.PRODUCT_CODE_COLUMNS:
            connection.execute(text(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, {col} VARCHAR(20), market VARCHAR(20))"))
    return engine


def test_sqlite_upgrade_enforces_not_null_on_insert(sqlite_db):
    with sqlite_db.begin() as connection:
        _run(migration.upgrade, connection)
    with sqlite_db.begin() as connection:
        connection.execute(text("INSERT INTO trade (product_code) VALUES ('000001')"))
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO trade (product_code) VALUES (NULL)"))


def test_sqlite_downgrade_restores_nullable(sqlite_db):
    with sqlite_db.begin() as connection:
        _run(migration.upgrade, connection)
        _run(migration.downgrade, connection)
    with sqlite_db.begin() as connection:
        connection.execute(text("INSERT INTO price_record (product_code) VALUES (NULL)"))
