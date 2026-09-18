"""#539 第二单元守门：pytest 数据库隔离判据与 conftest 接线。

这些用例是「门禁的反例测试」——隔离是否生效不靠人读代码相信，而靠：
- 注入外部 DATABASE_URL 后，runner 既不连它、也不改它；
- 非空且无归属标记的目标被拒绝（SQLite 与 MySQL 共用同一纯判据，无需真服务端即可验证）；
- 探测失败不被当成「空库」放行；
- 会话即使一个用例都没用到 test_engine，也不留下自建的临时目录；
- conftest 里 setdefault 继承不再存在，且归属判定排在 app.main 导入之前。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tests import db_isolation
from tests.db_isolation import (
    OWNERSHIP_TABLE,
    IsolationRefused,
    describe_url,
    evaluate_ownership,
    prepare_test_database,
    write_ownership_marker,
)

CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """断开一切显式测试通道，并回收 prepare_test_database 自建的临时目录。"""
    created = []

    def prepare(**env):
        for key in ("TEST_DB_URL", "DATABASE_URL", "SCHEDULER_ENABLED"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        # 本地 .env.test（gitignored）会劫持缺省路径，测试里一律指向不存在的位置
        monkeypatch.setattr(db_isolation, "ENV_TEST_FILE", tmp_path / "absent.env.test")
        result = prepare_test_database()
        if result.tmp is not None:
            created.append(result.tmp)
        return result

    yield prepare

    for handle in created:
        handle.cleanup()


def _make_business_db(path: Path) -> None:
    """造一个「像是业务库」的 SQLite 文件：有表、有数据、没有归属标记。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE portfolio (code TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO portfolio VALUES ('REAL', '不得被触碰')")
        conn.commit()
    finally:
        conn.close()


def _sqlite_tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def _marker_tokens(engine) -> list[str]:
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(
            text(f"SELECT token FROM {OWNERSHIP_TABLE} ORDER BY token")
        )]


# ============================================================================
# 纯判据 evaluate_ownership：不连库即可跑全矩阵
# ============================================================================

class TestEvaluateOwnership:
    def test_empty_target_is_allowed(self):
        evaluate_ownership(set())

    def test_marked_db_with_model_tables_only_is_allowed(self):
        from app.models.base import Base

        evaluate_ownership(set(Base.metadata.tables) | {OWNERSHIP_TABLE})

    def test_populated_db_without_marker_is_refused(self):
        """库名含 test 不构成许可——判据里根本没有名字，只有表集合。"""
        with pytest.raises(IsolationRefused, match="拒绝 drop_all"):
            evaluate_ownership({"portfolio", "trade", "ir_test_sandbox"})

    def test_marked_db_with_foreign_table_is_refused(self):
        with pytest.raises(IsolationRefused, match="_leftover_probe"):
            evaluate_ownership({OWNERSHIP_TABLE, "portfolio", "_leftover_probe"})


# ============================================================================
# prepare_test_database：环境强制与目标选择
# ============================================================================

class TestPrepareTestDatabase:
    def test_default_is_per_session_temp_dir(self, isolated_env):
        first = isolated_env()
        assert os.environ["DATABASE_URL"] == first.url
        assert first.tmp is not None and Path(first.tmp.name).is_dir()

        second = isolated_env()

        # 并发互不污染：两个会话各持不同目录、不同文件
        assert second.tmp.name != first.tmp.name
        assert second.url != first.url
        assert Path(first.tmp.name).is_dir(), "会话结束前 runner 必须仍持有自己的目录"

    def test_ignores_inherited_database_url(self, isolated_env, tmp_path):
        """验收反例：注入外部数据库地址后，既不连它、也不改它。"""
        business_db = tmp_path / "business.db"
        _make_business_db(business_db)

        result = isolated_env(DATABASE_URL=f"sqlite:///{business_db}")

        assert not result.url.endswith("business.db")
        assert result.tmp is not None
        assert os.environ["DATABASE_URL"] == result.url
        assert _sqlite_tables(business_db) == {"portfolio"}, "外部库被测试会话改写即失败"

    def test_explicit_sqlite_channel_is_respected(self, isolated_env, tmp_path):
        fresh = tmp_path / "declared.db"

        result = isolated_env(TEST_DB_URL=f"sqlite:///{fresh}")

        assert result.url == f"sqlite:///{fresh}"
        assert result.tmp is None, "显式声明的库不由 runner 回收"

    def test_explicit_channel_on_foreign_db_is_refused(self, isolated_env, tmp_path):
        business_db = tmp_path / "business.db"
        _make_business_db(business_db)

        with pytest.raises(IsolationRefused, match="拒绝 drop_all"):
            isolated_env(TEST_DB_URL=f"sqlite:///{business_db}")

        assert _sqlite_tables(business_db) == {"portfolio"}

    def test_probe_failure_is_not_treated_as_empty(self, isolated_env):
        """连不上 ≠ 空库：不可达地址必须响亮失败，且不外泄口令。"""
        secret = "super-secret-password"

        with pytest.raises(IsolationRefused, match="无法探测测试库") as excinfo:
            isolated_env(TEST_DB_URL=f"mysql+pymysql://ir_test:{secret}@127.0.0.1:1/ir_test")

        assert secret not in str(excinfo.value)

    def test_scheduler_is_forced_off(self, isolated_env):
        isolated_env(SCHEDULER_ENABLED="true")

        assert os.environ["SCHEDULER_ENABLED"] == "false"


# ============================================================================
# 临时目录生命周期：只能在外层进程里验
# ============================================================================

class TestRunnerTempDirLifecycle:
    """会话没用到 `test_engine` 时，自建目录同样不得留在磁盘上。

    判据是「子进程退出后 TMPDIR 下还有没有 `investring-pytest-*`」，而在同一进程内
    跑用例时，finalizer 何时执行与外层 pytest 的生命周期缠在一起，测不出东西。
    真实泄漏正是这么发生的：回收一度只挂在 `test_engine` 的 teardown 上，
    一个只收集到不用该 fixture 的用例的会话就把目录留了下来。
    """

    def test_unused_temp_dir_is_reclaimed(self, tmp_path):
        if db_isolation.ENV_TEST_FILE.exists() or os.environ.get("TEST_DB_URL"):
            pytest.skip("显式测试通道已开启，本次运行不走缺省临时目录，守卫无从判定")

        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider",
                # 只挑一个不碰 test_engine 的用例：整个会话的 fixture 生命周期里
                # 没有任何一次显式 cleanup，回收全靠兜底机制
                "tests/unit/test_db_isolation.py::TestDescribeUrl",
            ],
            cwd=db_isolation.BACKEND_ROOT,
            # 注入外部地址顺带证明缺省路径被选中（WARN 会回显本次真正的库）
            env={**os.environ, "TMPDIR": str(tmp_path),
                 "DATABASE_URL": f"sqlite:///{tmp_path / 'injected.db'}"},
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = proc.stdout + proc.stderr

        assert proc.returncode == 0, output
        assert "本次为 sqlite://" in output and "investring-pytest-" in output, (
            f"子会话未走缺省临时目录，守卫成了空跑：{output}"
        )
        assert not list(tmp_path.glob("investring-pytest-*")), (
            "缺省临时目录泄漏：回收不能只挂在 test_engine 的 teardown 上"
        )


# ============================================================================
# 归属标记：必须活得过 drop_all，也不进应用 schema
# ============================================================================

class TestOwnershipMarker:
    def test_marker_survives_drop_all(self, tmp_path):
        from app.models.base import Base

        engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
        try:
            Base.metadata.create_all(bind=engine)
            write_ownership_marker(engine)
            Base.metadata.drop_all(bind=engine)

            assert OWNERSHIP_TABLE in set(inspect(engine).get_table_names()), (
                "标记被 drop_all 连删 → 第二次跑测必被自己的闸门拒绝"
            )
            assert OWNERSHIP_TABLE not in Base.metadata.tables, "标记不得属于应用 schema"

            tokens = _marker_tokens(engine)
            write_ownership_marker(engine)
            assert _marker_tokens(engine) == tokens, "重写应幂等，不刷 token"
        finally:
            engine.dispose()

    def test_marker_satisfies_the_pure_criterion(self, tmp_path):
        """首轮放行 → 写标记 → 次轮凭标记放行（重跑不被拒）。"""
        from app.models.base import Base

        engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
        try:
            Base.metadata.create_all(bind=engine)
            write_ownership_marker(engine)
            evaluate_ownership(set(inspect(engine).get_table_names()))
        finally:
            engine.dispose()


# ============================================================================
# 地址脱敏：日志/异常里不得出现凭据
# ============================================================================

class TestDescribeUrl:
    def test_redacts_credentials(self):
        described = describe_url("mysql+pymysql://user:s3cret@db.internal:3306/ir_test")

        assert "s3cret" not in described and "db.internal" not in described
        assert described == "mysql+pymysql://<redacted>"

    def test_keeps_sqlite_path_readable(self):
        assert describe_url("sqlite:////tmp/x/test.db") == "sqlite:////tmp/x/test.db"


# ============================================================================
# conftest 接线（结构性守卫：#539 的缺陷正是 setdefault 继承 + 判定次序）
# ============================================================================

class TestConftestWiring:
    @pytest.fixture(scope="class")
    def source(self):
        return CONFTEST.read_text(encoding="utf-8")

    def test_no_inherited_database_url(self, source):
        assert 'os.environ.setdefault("DATABASE_URL"' not in source
        assert 'os.environ["DATABASE_URL"]' not in source

    def test_ownership_gate_runs_before_app_import(self, source):
        assert source.index("prepare_test_database()") < source.index("from app.main import app"), (
            "app.main 模块期会 create_all，归属判定必须排在它之前"
        )

    def test_shared_fixed_db_path_is_gone(self, source):
        """固定共享文件是并发污染的来源，不允许回到 conftest。"""
        assert "test_investring.db" not in source
