# ============================================================================
# 单元测试：issue #427 迁移 0014 把四张日志表字符集提到 utf8mb4
# ============================================================================
# 缺陷形态：库级 charset 是 utf8mb3（对齐生产 RDS 的刻意约定）而连接侧是 utf8mb4，
# 4 字节 UTF-8 字符（emoji、CJK 扩展 B）撞 utf8mb3 列 → errno 1366 → 整条日志记录
# 写不进去（record_system_error 的 best-effort except 吸收后静默丢失）。
#
# 断言重点：
# ① 三方单一事实来源一致（app.constants.log_charset ← 四模型 ← 迁移 0014）——将来
#    有人只改一处的 drift 必须变红；
# ② SQLite 上 upgrade/downgrade 都是安全 no-op（SQLite 无字符集概念，本地测不出缺陷
#    本身，只能测「不炸」）；
# ③ MySQL 上真实生效：列字符集确实不是库级继承的 utf8mb3，且 4 字节文本能落库读回，
#    探测函数不把 3 字节中文误判为 4 字节。
#    ③ 只在 CI backend-test-mysql 生效——本地 SQLite 与 dev MySQL（server 字符集已是
#    utf8mb4）都复现不出原缺陷。
#
# 不走 alembic 命令 API：0001-0013 含 MySQL 专有 SQL，SQLite 上跑不通整条链
# （conftest 也因此把 lifespan 里的 alembic upgrade no-op 掉），只程序化执行 0014。
# ============================================================================

import ast
import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.constants.log_charset import LOG_TABLE_CHARSET, LOG_TABLE_COLLATE, LOG_TABLES
from app.database import Base, SessionLocal, engine as app_engine
from app.models.audit_log import AuditLog
from app.models.login_log import LoginLog
from app.models.system_error_log import SystemErrorLog
from app.models.task_execution_log import TaskExecutionLog

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0014_log_tables_utf8mb4.py"
)

MODELS = {
    "audit_log": AuditLog,
    "system_error_log": SystemErrorLog,
    "login_log": LoginLog,
    "task_execution_log": TaskExecutionLog,
}

# 库级 charset：CI 与生产 RDS 同形态（建表默认继承库级设置）
LEGACY_CHARSET = "utf8mb3"

# 4 字节字符样本：U+1F4A5（emoji）与 U+2000B（CJK 扩展 B）
FOUR_BYTE_TEXT = "炸了 💥 扩展 𠀋"
FOUR_BYTE_STACK = 'File "/app/main.py", line 1, in handler\n    raise RuntimeError("😀")'


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0014_under_test", MIGRATION_PATH)
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


def _mysql_only():
    """本文件里所有字符集相关断言只在 MySQL 有语义；SQLite 跳过而非失败。"""
    if app_engine.dialect.name != "mysql":
        pytest.skip("字符集只在 MySQL 成立（SQLite 无字符集概念）")
    return app_engine


def _cleanup_probe(error_type: str) -> None:
    session = SessionLocal()
    try:
        session.query(SystemErrorLog).filter(SystemErrorLog.error_type == error_type).delete()
        session.commit()
    finally:
        session.close()


def _probe_row(error_type: str, **values) -> None:
    """写一行探针并立即提交（record_system_error 之外的独立 session 语义）。"""
    session = SessionLocal()
    try:
        session.add(SystemErrorLog(error_type=error_type, **values))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def engine(tmp_path):
    """一次性 SQLite 库，按 create_all（含模型的 mysql_charset 声明）建四张日志表。"""
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'migration_0014.db'}")
    Base.metadata.create_all(bind=eng, tables=[m.__table__ for m in MODELS.values()])
    yield eng
    eng.dispose()


@pytest.fixture
def probes():
    """探针行落库后必须自清：残留会污染按行数断言的其他用例。"""
    done = []
    yield done.append
    for error_type in done:
        _cleanup_probe(error_type)


class TestRevisionChain:
    def test_links_onto_0013(self):
        assert migration.revision == "0014"
        assert migration.down_revision == "0013"

    def test_covers_exactly_the_four_log_tables(self):
        """纳管范围是声明式清单：漏一张即留一处静默丢失面。"""
        assert tuple(migration.LOG_TABLES) == LOG_TABLES
        assert sorted(LOG_TABLES) == sorted(MODELS)


class TestSingleSourceOfTruth:
    """常量、模型、迁移三方必须指向同一 charset / collate。"""

    def test_models_declare_the_shared_charset(self):
        for name, model in MODELS.items():
            args = model.__table_args__
            assert args["mysql_charset"] == LOG_TABLE_CHARSET, name
            assert args["mysql_collate"] == LOG_TABLE_COLLATE, name

    def test_migration_uses_the_shared_constants(self):
        assert migration.LOG_TABLE_CHARSET == LOG_TABLE_CHARSET
        assert migration.LOG_TABLE_COLLATE == LOG_TABLE_COLLATE

    def test_migration_has_no_charset_literals(self):
        """迁移里不得再写字面量 charset/collate——改了 constants 而迁移里还留着旧值即红。

        行为面已被上一条覆盖；这里额外防「顺手的字面量」，用 AST 取所有字符串常量，
        比源码文本匹配稳（注释里出现 utf8mb4 是允许且必要的）。
        """
        tree = ast.parse(MIGRATION_PATH.read_text(encoding="utf-8"))
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "utf8mb4" not in literals
        assert "utf8mb4_general_ci" not in literals
        # 反向转码的目标字符集是本迁移自有的历史值（常量模块只描述目标态，不描述逆态）
        assert LEGACY_CHARSET in literals


class TestSqliteNoOp:
    """SQLite 无字符集概念，且 CONVERT TO CHARACTER SET 不是合法 SQL。"""

    def test_upgrade_does_not_touch_schema(self, engine):
        with engine.connect() as conn:
            before = {name: set(_reflect(conn, name).c.keys()) for name in MODELS}
            _run(migration.upgrade, conn)
            conn.commit()
            for name, columns in before.items():
                assert set(_reflect(conn, name).c.keys()) == columns, name

    def test_downgrade_is_safe_noop(self, engine):
        with engine.connect() as conn:
            _run(migration.downgrade, conn)
            conn.commit()
            inspector = sa.inspect(conn)
            for name in MODELS:
                assert inspector.has_table(name), f"{name} 被误删"


class TestDowngradeSkipGuard:
    """downgrade 因 4 字节数据跳过时必须打 WARNING 且不执行任何 ALTER（不假装成功）。

    真实探测已由 TestMysqlFourByteWrites 在 MySQL 上覆盖；本类只验控制流，故把方言判定
    与探测函数替换掉，使 SQLite 也能执行到跳过分支。
    """

    def test_skips_all_tables_when_four_byte_data_present(self, monkeypatch, caplog):
        executed = []

        class _FakeDialect:
            name = "mysql"

        class _FakeBind:
            dialect = _FakeDialect()

            def execute(self, statement, *args, **kwargs):
                executed.append(str(statement))

        monkeypatch.setattr(migration.op, "get_bind", lambda: _FakeBind())
        monkeypatch.setattr(
            migration, "_four_byte_columns", lambda bind, name: ["error_message"]
        )

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            migration.downgrade()

        skipped = [r.getMessage() for r in caplog.records if "跳过" in r.getMessage()]
        assert len(skipped) == len(LOG_TABLES), skipped
        assert not executed, "跳过时必须不执行任何 ALTER"

    def test_converts_when_no_four_byte_data(self, monkeypatch, caplog):
        executed = []

        class _FakeDialect:
            name = "mysql"

        class _FakeBind:
            dialect = _FakeDialect()

            def execute(self, statement, *args, **kwargs):
                executed.append(str(statement))

        monkeypatch.setattr(migration.op, "get_bind", lambda: _FakeBind())
        monkeypatch.setattr(migration, "_four_byte_columns", lambda bind, name: [])

        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            migration.downgrade()

        assert len(executed) == len(LOG_TABLES), executed
        assert all(LEGACY_CHARSET in sql for sql in executed), executed


class TestMysqlColumnCharset:
    """真实列字符集：必须不是库级继承的 utf8mb3。"""

    def test_all_text_columns_are_utf8mb4(self):
        engine = _mysql_only()
        with engine.connect() as conn:
            for name in LOG_TABLES:
                rows = conn.execute(
                    sa.text(
                        "SELECT column_name, character_set_name FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = :t"
                    ),
                    {"t": name},
                ).all()
                assert rows, f"{name} 无列信息"
                bad = [
                    col for col, charset in rows if charset and charset != LOG_TABLE_CHARSET
                ]
                assert not bad, f"{name} 仍有非 {LOG_TABLE_CHARSET} 列：{bad}"


class TestMysqlFourByteWrites:
    """4 字节字符必须能写入并原样读回（#427 的验收断言）。"""

    def test_system_error_text_roundtrips(self, probes):
        _mysql_only()
        probes("FourByteProbe")
        _probe_row(
            "FourByteProbe",
            error_message=FOUR_BYTE_TEXT,
            error_stack=FOUR_BYTE_STACK,
        )

        session = SessionLocal()
        try:
            row = (
                session.query(SystemErrorLog)
                .filter(SystemErrorLog.error_type == "FourByteProbe")
                .one()
            )
            assert row.error_message == FOUR_BYTE_TEXT
            assert "💥" in row.error_stack
        finally:
            session.close()

    def test_probe_detects_four_byte_column(self, probes):
        _mysql_only()
        probes("FourByteProbeDetect")
        _probe_row("FourByteProbeDetect", error_message=FOUR_BYTE_TEXT)

        with app_engine.connect() as conn:
            hits = migration._four_byte_columns(conn, "system_error_log")

        assert "error_message" in hits, hits

    def test_probe_ignores_three_byte_chinese(self, probes):
        """3 字节中文 utf8mb3 容得下，不得被误判（否则 downgrade 无谓跳过）。"""
        _mysql_only()
        probes("ThreeByteProbe")
        _probe_row("ThreeByteProbe", error_message="净值尚未同步")

        with app_engine.connect() as conn:
            hits = migration._four_byte_columns(conn, "system_error_log")

        assert "error_message" not in hits, hits
