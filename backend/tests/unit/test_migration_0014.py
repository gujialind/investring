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

from app.constants.log_charset import CHARSET_TABLES, LOG_TABLE_CHARSET, LOG_TABLE_COLLATE
from app.database import Base, SessionLocal, engine as app_engine
from app.models.audit_log import AuditLog
from app.models.login_log import LoginLog
from app.models.nav_sync_detail import NavSyncDetail
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
    # 同步明细与四张日志表同库同型：error_message 接收外部数据源原文，同样继承库级 charset
    "nav_sync_detail": NavSyncDetail,
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
    """一次性 SQLite 库，按 create_all（含模型的 mysql_charset 声明）建表。

    刻意建**全量** metadata：`nav_sync_detail` 的 `job_id` 指向 `sync_job`，按表清单建会因
    外键依赖缺表而失败；生产路径同样是 create_all 全量建。
    """
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'migration_0014.db'}")
    Base.metadata.create_all(bind=eng)
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

    def test_covers_exactly_the_declared_tables(self):
        """纳管范围是声明式清单：漏一张即留一处「静默丢失 / 外抛 500」面。"""
        assert tuple(migration.CHARSET_TABLES) == CHARSET_TABLES
        assert sorted(CHARSET_TABLES) == sorted(MODELS)


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


class _FakeInspector:
    """所有纳管表都「存在」——本类只验跳过/转换的控制流，表存在性由真实 DB 用例覆盖。"""

    def has_table(self, _name):
        return True


class _FakeBind:
    class _Dialect:
        name = "mysql"

    dialect = _Dialect()

    def __init__(self):
        self.executed = []

    def execute(self, statement, *args, **kwargs):
        self.executed.append(str(statement))
        return self


def _patch_downgrade(monkeypatch, four_byte_hits):
    """把方言判定与 4 字节探测替换掉，使 SQLite 也能执行到 downgrade 的跳过/转换分支。

    真实探测已由 TestMysqlFourByteWrites 在 MySQL 上覆盖。
    """
    bind = _FakeBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _FakeInspector())
    monkeypatch.setattr(
        migration, "_four_byte_columns", lambda _bind, _name: list(four_byte_hits)
    )
    return bind


class _FakeProbeBind:
    """`_four_byte_columns` 的假连接：`execute(...).scalar()` 固定返回给定计数。"""

    def __init__(self, count):
        self._count = count

    class _Dialect:
        name = "mysql"

    dialect = _Dialect()

    def execute(self, _statement, *_args, **_kwargs):
        count = self._count

        class _Result:
            def scalar(self):
                return count

        return _Result()


class _CountInspector:
    """只有一列文本列的 inspector——把探测范围压到可断言的最小面。"""

    def get_columns(self, _table_name):
        return [{"name": "error_message", "type": sa.Text()}]


class TestFourByteProbeCounting:
    """探测函数的判据必须是 COUNT > 0，不能是真值判断。

    CI MySQL job 实测：原写法 `if bind.execute(...).scalar():` 把 COUNT 的 0 当假，
    **干净表一律探测不出东西**，downgrade 的跳过守卫形同虚设。真实探测在 MySQL 上由
    TestMysqlFourByteWrites 覆盖；本类用假连接把「0 / 非 0」的边界固定下来，SQLite 也能跑。
    """

    def test_zero_count_is_not_a_hit(self, monkeypatch):
        monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _CountInspector())
        assert migration._four_byte_columns(_FakeProbeBind(0), "t") == []

    def test_positive_count_is_a_hit(self, monkeypatch):
        monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _CountInspector())
        assert migration._four_byte_columns(_FakeProbeBind(1), "t") == ["error_message"]

    def test_none_count_is_not_a_hit(self, monkeypatch):
        """驱动返回 None（无行/类型差异）时按「无 4 字节字符」处理，不得抛异常。"""
        monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _CountInspector())
        assert migration._four_byte_columns(_FakeProbeBind(None), "t") == []


class TestDowngradeSkipGuard:
    """downgrade 因 4 字节数据跳过时必须打 WARNING 且不执行任何 ALTER（不假装成功）。"""

    def test_skips_all_tables_when_four_byte_data_present(self, monkeypatch, caplog):
        bind = _patch_downgrade(monkeypatch, ["error_message"])

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            migration.downgrade()

        skipped = [r.getMessage() for r in caplog.records if "跳过" in r.getMessage()]
        assert len(skipped) == len(CHARSET_TABLES), skipped
        assert not bind.executed, "跳过时必须不执行任何 ALTER"

    def test_converts_when_no_four_byte_data(self, monkeypatch, caplog):
        bind = _patch_downgrade(monkeypatch, [])

        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            migration.downgrade()

        assert len(bind.executed) == len(CHARSET_TABLES), bind.executed
        assert all(LEGACY_CHARSET in sql for sql in bind.executed), bind.executed


def _patch_upgrade(monkeypatch, existing_tables):
    """upgrade 的控制流：只对存在的表执行 ALTER。"""
    bind = _FakeBind()

    class _Inspector:
        def has_table(self, name):
            return name in existing_tables

    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _Inspector())
    return bind


class TestUpgradeTablePresenceGuard:
    """缺表时响亮告警并跳过，不让整条迁移链失败，也不静默漏转。"""

    def test_converts_only_existing_tables(self, monkeypatch, caplog):
        existing = set(CHARSET_TABLES) - {"nav_sync_detail"}
        bind = _patch_upgrade(monkeypatch, existing)

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            migration.upgrade()

        assert len(bind.executed) == len(existing), bind.executed
        warned = [r.getMessage() for r in caplog.records if "nav_sync_detail" in r.getMessage()]
        assert warned, "缺表必须响亮告警"


class TestMysqlColumnCharset:
    """真实列字符集：必须不是库级继承的 utf8mb3。"""

    def test_all_text_columns_are_utf8mb4(self):
        engine = _mysql_only()
        with engine.connect() as conn:
            for name in CHARSET_TABLES:
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


class TestMysqlProbeSemantics:
    """探测表达式本身的行为：在真实 MySQL 上把 `_FOUR_BYTE_PROBE` 跑出来验。

    **只在 MySQL 成立**：SQLite 既无 `CHAR_LENGTH`，也无 `CONVERT(... USING ...)` 语法
    （本地实测 OperationalError），所以探测函数的真实行为只能由 CI 的 MySQL job 覆盖——
    SQLite 侧只有方言 guard 与假 bind 的控制流用例。

    这是整套 downgrade 守卫的地基：探测若失效（认不出 4 字节字符，或把 3 字节中文误判为
    4 字节），守卫会分别退化成「静默丢弃数据」或「永不回退」，且没有别的用例看得出来。
    故用一个临时表把 `_FOUR_BYTE_PROBE` 的 SQL 原样跑通，不依赖任何真实业务表。
    """

    TABLE = "_probe_semantics_tmp"

    @pytest.fixture
    def probe_table(self):
        """临时表**必须显式 utf8mb4**：本库库级 charset 是 utf8mb3，不声明则连 setup 的
        emoji 都插不进去（也正是 #427 本身的现象，CI 实测踩过）。探测函数要防的正是
        「库级 utf8mb3 之内的表能否安全回退」，故测试表本身要落在转码后的终态。
        """
        engine = _mysql_only()
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {self.TABLE}"))
            conn.execute(
                sa.text(
                    f"CREATE TABLE {self.TABLE} ("
                    "v_ascii VARCHAR(50), v_cjk VARCHAR(50), v_emoji VARCHAR(50), n INT"
                    f") CHARSET={LOG_TABLE_CHARSET} COLLATE {LOG_TABLE_COLLATE}"
                )
            )
        yield engine
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {self.TABLE}"))

    def _count(self, conn, column, expr):
        sql = "SELECT COUNT(*) FROM {} WHERE {}".format(
            self.TABLE, expr.format(column=column)
        )
        return conn.execute(sa.text(sql)).scalar()

    def test_probe_discriminates_four_byte_from_three_byte(self, probe_table):
        with probe_table.begin() as conn:
            conn.execute(
                sa.text(
                    f"INSERT INTO {self.TABLE} (v_ascii, v_cjk, v_emoji) VALUES (:a, :c, :e)"
                ),
                {"a": "abc", "c": "净值尚未同步", "e": FOUR_BYTE_TEXT},
            )

        probe_expr = "CONVERT({column} USING utf8mb3) <> BINARY {column}"
        with probe_table.connect() as conn:
            assert self._count(conn, "v_emoji", probe_expr) == 1, (
                "探测式未认出 4 字节字符——downgrade 守卫静默失效"
            )
            assert self._count(conn, "v_cjk", probe_expr) == 0, (
                "3 字节中文被误判为 4 字节——downgrade 会无谓跳过"
            )
            assert self._count(conn, "v_ascii", probe_expr) == 0, (
                "ASCII 被误判为 4 字节"
            )

    def test_four_byte_columns_skips_non_text_and_clean_columns(self, probe_table):
        """`_four_byte_columns` 只报「文本列且含 4 字节字符」——整数列不得进结果。"""
        with probe_table.begin() as conn:
            conn.execute(
                sa.text(
                    f"INSERT INTO {self.TABLE} (v_ascii, v_cjk, v_emoji, n) "
                    "VALUES (:a, :c, :e, :n)"
                ),
                {"a": "abc", "c": "净值", "e": FOUR_BYTE_TEXT, "n": 1},
            )

        with probe_table.connect() as conn:
            hits = migration._four_byte_columns(conn, self.TABLE)

        assert hits == ["v_emoji"], hits

    def test_four_byte_column_survives_upgrade_conversion(self, probe_table):
        """upgrade 的 `CONVERT TO CHARACTER SET utf8mb4` 不得改写既有 4 字节数据。

        若服务端在该 ALTER 下把不可表示字符替换掉，本断言即红——那意味着「先转码再回退」
        会丢数据，需要改成写入侧适配而非表级转码。
        """
        with probe_table.begin() as conn:
            conn.execute(
                sa.text(f"INSERT INTO {self.TABLE} (v_emoji) VALUES (:e)"),
                {"e": FOUR_BYTE_TEXT},
            )
            conn.execute(
                sa.text(
                    f"ALTER TABLE {self.TABLE} CONVERT TO CHARACTER SET utf8mb4 "
                    f"COLLATE {LOG_TABLE_COLLATE}"
                )
            )

        with probe_table.connect() as conn:
            value = conn.execute(sa.text(f"SELECT v_emoji FROM {self.TABLE}")).scalar()
        assert value == FOUR_BYTE_TEXT, f"转码改写了数据：{value!r}"


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
            # 断言文本原样落库，而不是断言某个特定 emoji 在其中——error_stack 里的 emoji
            # 与 FOUR_BYTE_TEXT 里的不是同一个（CI MySQL job 实测踩过这个错）
            assert row.error_stack == FOUR_BYTE_STACK
        finally:
            session.close()

    def test_probe_detects_four_byte_column(self, probes):
        """同一用例内先断言「干净表探测为空」、再断言「有 emoji 行的表探测命中」。

        两次对照必须同处一个用例：探测是**全表扫描**，任何残留的 emoji 行都会让「干净表」
        的断言失真；同一函数体内先清后插，顺序与隔离都由本用例自己保证。
        """
        _mysql_only()
        error_type = "FourByteProbeDetect"
        probes(error_type)
        _cleanup_probe(error_type)  # 清掉可能的上轮残留，确保起点干净

        with app_engine.connect() as conn:
            clean_hits = migration._four_byte_columns(conn, "system_error_log")
        assert "error_message" not in clean_hits, f"干净表不应命中：{clean_hits}"

        _probe_row(error_type, error_message=FOUR_BYTE_TEXT)
        with app_engine.connect() as conn:
            dirty_hits = migration._four_byte_columns(conn, "system_error_log")
        assert "error_message" in dirty_hits, (
            f"含 emoji 行的表必须命中，实际 {dirty_hits}"
            "（探测式失效即 downgrade 守卫静默失效）"
        )

    def test_probe_ignores_three_byte_chinese(self, probes):
        """3 字节中文 utf8mb3 容得下，不得被误判（否则 downgrade 无谓跳过）。"""
        _mysql_only()
        probes("ThreeByteProbe")
        _probe_row("ThreeByteProbe", error_message="净值尚未同步")

        with app_engine.connect() as conn:
            hits = migration._four_byte_columns(conn, "system_error_log")

        assert "error_message" not in hits, hits

    def test_nav_sync_detail_error_message_roundtrips(self):
        """nav_sync_detail 与四张日志表同型（#427 一并纳入）：外部数据源原文含 4 字节字符
        也必须能落库读回——本表写入直接 commit，撞 1366 的形态是外抛、中断净值同步。
        """
        _mysql_only()
        job = _make_sync_job()
        try:
            session = SessionLocal()
            try:
                session.add(
                    NavSyncDetail(
                        job_id=job.id,
                        product_code="FUND_4B",
                        market="CN_OTC",
                        nav_date="2025-01-07",
                        status="failed",
                        error_message=FOUR_BYTE_TEXT,
                    )
                )
                session.commit()
            finally:
                session.close()

            session = SessionLocal()
            try:
                row = (
                    session.query(NavSyncDetail)
                    .filter(NavSyncDetail.job_id == job.id)
                    .one()
                )
                assert row.error_message == FOUR_BYTE_TEXT
            finally:
                session.close()

            with app_engine.connect() as conn:
                hits = migration._four_byte_columns(conn, "nav_sync_detail")
            assert "error_message" in hits, hits
        finally:
            _drop_sync_job(job.id)


def _make_sync_job():
    """造一条 sync_job 供 nav_sync_detail.job_id 外键挂靠（本文件唯一需要的父行）。"""
    from app.models.sync_job import SyncJob

    session = SessionLocal()
    try:
        job = SyncJob(job_type="nav_sync", status="running")
        session.add(job)
        session.commit()
        session.refresh(job)
        return job
    finally:
        session.close()


def _drop_sync_job(job_id: int) -> None:
    """先删子行再删父行——nav_sync_detail.job_id 外键无 ondelete，顺序反了会撞约束。"""
    from app.models.sync_job import SyncJob

    session = SessionLocal()
    try:
        session.query(NavSyncDetail).filter(NavSyncDetail.job_id == job_id).delete()
        session.query(SyncJob).filter(SyncJob.id == job_id).delete()
        session.commit()
    finally:
        session.close()
