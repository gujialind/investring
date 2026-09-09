# ============================================================================
# 单元测试：issue #405 迁移 0013 把四张日志表纳入 alembic 管理
# ============================================================================
# 迁移的存在意义是消除 schema drift（四张日志表此前只靠 main.py 的 create_all 建表，
# 模型改列后生产库静默不跟随），故断言重点不是「能跑」而是「跑出来的 schema 与 ORM
# 模型逐列一致」，外加幂等（生产库已有这些表且有数据）与 downgrade 的 no-op 契约。
#
# 不走 alembic 命令 API：0001-0012 含 MySQL 专有 SQL，SQLite 上跑不通整条链
# （conftest 也因此把 lifespan 里的 alembic upgrade no-op 掉），只程序化执行 0013。
# ============================================================================

import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import Base
from app.models.audit_log import AuditLog
from app.models.login_log import LoginLog
from app.models.system_error_log import SystemErrorLog
from app.models.task_execution_log import TaskExecutionLog

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0013_add_log_tables.py"
)

MODELS = {
    "audit_log": AuditLog,
    "system_error_log": SystemErrorLog,
    "login_log": LoginLog,
    "task_execution_log": TaskExecutionLog,
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0013_under_test", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _run(fn, connection):
    """在给定连接上执行程序化迁移（alembic.op 代理需 Operations.context 安装）。"""
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        fn()


def _reflect(connection, table_name):
    return sa.Table(table_name, sa.MetaData(), autoload_with=connection)


@pytest.fixture
def engine(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    yield eng
    eng.dispose()


class TestRevisionChain:
    def test_links_onto_0012(self):
        assert migration.revision == "0013"
        assert migration.down_revision == "0012"

    def test_covers_exactly_the_four_log_tables(self):
        """_TABLES 是本迁移纳管范围的声明式清单：漏一张即留一处 schema drift。"""
        assert sorted(migration._TABLES) == sorted(MODELS)


class TestUpgrade:
    def test_creates_all_four_tables(self, engine):
        with engine.connect() as conn:
            _run(migration.upgrade, conn)
            inspector = sa.inspect(conn)
            for name in MODELS:
                assert inspector.has_table(name), f"{name} 未建出"
            conn.commit()

    def test_schema_matches_models(self, engine):
        """逐列比对：列集合、类型（含 String 长度）、nullable 必须与 ORM 模型一致。"""
        with engine.connect() as conn:
            _run(migration.upgrade, conn)
            for name, model in MODELS.items():
                reflected = _reflect(conn, name)
                expected = model.__table__
                assert set(reflected.c.keys()) == set(expected.c.keys()), name
                for column in expected.columns:
                    actual = reflected.c[column.name]
                    assert str(actual.type) == str(column.type), (
                        f"{name}.{column.name}: {actual.type} != {column.type}"
                    )
                    assert actual.nullable == column.nullable, (
                        f"{name}.{column.name} nullable 不一致"
                    )
            conn.commit()

    def test_idempotent_when_tables_already_exist(self, engine, caplog):
        """生产库这些表已由 create_all 建出且有数据：upgrade 必须跳过而非重建。"""
        Base.metadata.create_all(
            bind=engine, tables=[m.__table__ for m in MODELS.values()]
        )
        with engine.begin() as conn:
            conn.execute(
                sa.insert(AuditLog).values(
                    investor_code="ADMIN", action="create", resource_type="trade"
                )
            )

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            with engine.connect() as conn:
                _run(migration.upgrade, conn)
                conn.commit()

        skipped = [r.getMessage() for r in caplog.records if "跳过" in r.getMessage()]
        assert len(skipped) == len(MODELS), skipped

        with engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(AuditLog)).scalar() == 1

    def test_creates_only_missing_tables(self, engine, caplog):
        """部分存在（如手工建过 audit_log）时只补缺的那几张。"""
        Base.metadata.create_all(bind=engine, tables=[AuditLog.__table__])
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            with engine.connect() as conn:
                _run(migration.upgrade, conn)
                inspector = sa.inspect(conn)
                for name in MODELS:
                    assert inspector.has_table(name), f"{name} 未建出"
                conn.commit()

        skipped = [r.getMessage() for r in caplog.records if "跳过" in r.getMessage()]
        assert len(skipped) == 1
        assert "audit_log" in skipped[0]


class TestDowngrade:
    def test_noop_keeps_tables_and_data(self, engine):
        """downgrade 刻意不删表：生产回滚（deploy-rollback.md）不得销毁已存在的审计数据。

        task_execution_log 另被 nav_sync_detail.task_log_id 外键引用，MySQL 下 drop 直接失败
        （errno 3730），故「删表」这条逆操作在本项目里既危险又不可行。
        """
        with engine.connect() as conn:
            _run(migration.upgrade, conn)
            conn.execute(
                sa.insert(AuditLog).values(
                    investor_code="ADMIN", action="create", resource_type="trade"
                )
            )
            conn.commit()

        with engine.connect() as conn:
            _run(migration.downgrade, conn)
            inspector = sa.inspect(conn)
            for name in MODELS:
                assert inspector.has_table(name), f"{name} 被 downgrade 删除"
            assert conn.execute(sa.select(sa.func.count()).select_from(AuditLog)).scalar() == 1
            conn.commit()

    def test_roundtrip_restores_schema(self, engine):
        """CI 的 MySQL job 会对最新迁移做 downgrade/upgrade 往返，这里先在本地兜住。"""
        with engine.connect() as conn:
            _run(migration.upgrade, conn)
            _run(migration.downgrade, conn)
            _run(migration.upgrade, conn)
            for name, model in MODELS.items():
                reflected = _reflect(conn, name)
                assert set(reflected.c.keys()) == set(model.__table__.c.keys()), name
            conn.commit()
