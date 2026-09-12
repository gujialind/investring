# ============================================================================
# 单元测试：issue #434 迁移 0016 nav_sync_detail.job_id 外键去重收敛
# ============================================================================
# 断言重点：
# ① 修订链挂在 0015 之后；迁移的目标列对/约束名与**模型声明**绑死（迁移动辄冻结，
#    不 import 模型，故用测试把两者钉在一起）；
# ② DDL 生成逻辑逐条可断言（纯字符串、双后端可跑）；
# ③ SQLite 上 upgrade/downgrade 都是安全 no-op（无 information_schema）；
# ④ MySQL 真库上三种起始态都收敛到「恰好一条 fk_nav_sync_detail_job_id」：
#    已是目标态 → 空转；两条并存（生产现状）→ 只多删冗余那条；只有自动名 → 拆掉重建。
#    并验证收敛后外键语义仍在（插入越界行被拒）。
#
# ④ 只在 CI backend-test-mysql / 本地 MySQL 生效。测试库每次会话
# `drop_all + create_all` 重建，故起始态天然是「恰好一条、且是显式名」——这正是 #433
# 给模型加 `ForeignKey(name=...)` 想要的结果，本文件顺带把它钉成回归护栏。
#
# 不走 alembic 命令 API：0014 之前的迁移含 MySQL 专有 SQL，SQLite 上跑不通整条链，
# 只程序化执行 0016（与 test_migration_0014/0015.py 同法）。
# ============================================================================

import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import engine as app_engine
from app.models.nav_sync_detail import NavSyncDetail

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0016_dedupe_nav_sync_detail_job_id_fk.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0016_under_test", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _run(fn, connection):
    """在给定连接上执行程序化迁移（alembic.op 代理需 Operations.context 安装）。"""
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        fn()


def _mysql_only():
    """去重只在 MySQL 有语义（SQLite 无 information_schema 也无此现象）。"""
    if app_engine.dialect.name != "mysql":
        pytest.skip("同列对重复外键只在 MySQL 成立")
    return app_engine


def _job_id_fk_names(conn):
    """直接查 information_schema（不借助迁移的 helper），供断言与收尾用。"""
    return list(
        conn.execute(
            sa.text(
                "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE CONSTRAINT_SCHEMA = DATABASE() "
                "  AND TABLE_NAME = 'nav_sync_detail' "
                "  AND COLUMN_NAME = 'job_id' "
                "  AND REFERENCED_TABLE_NAME IS NOT NULL "
                "ORDER BY CONSTRAINT_NAME"
            )
        ).scalars()
    )


def _add_job_id_fk(conn, name, on_delete=None):
    """手工造出一条同列对外键（模拟历史事故留下的重复约束）。"""
    suffix = f" ON DELETE {on_delete}" if on_delete else ""
    conn.execute(
        sa.text(
            f"ALTER TABLE `nav_sync_detail` ADD CONSTRAINT `{name}` "
            f"FOREIGN KEY (`job_id`) REFERENCES `sync_job` (`id`){suffix}"
        )
    )


def _drop_job_id_fk(conn, name):
    conn.execute(sa.text(f"ALTER TABLE `nav_sync_detail` DROP FOREIGN KEY `{name}`"))


def _restore_canonical_state(conn):
    """把 `nav_sync_detail.job_id` 恢复成目标态——**刻意不复用被测代码**。

    直接建外键前先清掉越界行（正常路径下不该有；真有则先删，避免收尾本身失败把
    真实断言结果掩盖掉）。
    """
    for name in _job_id_fk_names(conn):
        _drop_job_id_fk(conn, name)
    conn.execute(
        sa.text(
            "DELETE FROM nav_sync_detail WHERE job_id IS NOT NULL "
            "AND job_id NOT IN (SELECT id FROM sync_job)"
        )
    )
    _add_job_id_fk(conn, migration.TARGET_FK)


# ---------------------------------------------------------------------------
# 修订链与模型绑定（双后端可跑）
# ---------------------------------------------------------------------------


class TestRevisionChain:
    def test_links_onto_0015(self):
        assert migration.revision == "0016"
        assert migration.down_revision == "0015"

    def test_target_column_pair_matches_the_model(self):
        """迁移认定的列对就是模型声明的那一对——两边漂移会让收敛方向反掉。"""
        assert migration.TABLE == NavSyncDetail.__tablename__
        fks = [fk for fk in NavSyncDetail.__table__.foreign_keys if fk.parent.name == "job_id"]
        assert len(fks) == 1, fks
        assert migration.COLUMN == fks[0].parent.name
        assert migration.REF_TABLE == fks[0].column.table.name
        assert migration.REF_COLUMN == fks[0].column.name

    def test_target_fk_name_matches_the_model(self):
        """`TARGET_FK` 必须等于模型 `ForeignKey(name=...)`（#433 加的那个显式名）。

        这是本迁移最要紧的一根钉子：迁移刻意不 import 模型（已执行的迁移必须冻结，
        模型后续演进不得改变它的行为），于是「两边一致」只能由测试守住。模型改名而
        迁移没跟上 ⇒ 生产收敛到旧名、全新库建出新名，各环境外键名再次分叉。
        """
        fks = [fk for fk in NavSyncDetail.__table__.foreign_keys if fk.parent.name == "job_id"]
        assert migration.TARGET_FK == fks[0].name, fks[0].name

    def test_migration_does_not_import_models(self):
        """迁移只带冻结的字面量，不 import `app.models` / `app.constants`。

        import 模型等于把「已执行迁移的行为」交给后续模型演进决定：模型一改，这条
        早已跑完的迁移在全新库上的行为就跟着变（0013 的纳管清单用 `log_charset.py`
        冻结常量也是同一考虑）。
        """
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "from app.models" not in source
        assert "import app.models" not in source
        assert "from app.constants" not in source


# ---------------------------------------------------------------------------
# 外键 DDL 生成（纯字符串，双后端可跑）
# ---------------------------------------------------------------------------


class TestForeignKeyDdl:
    def _fk(self, **kw):
        base = dict(
            name="nav_sync_detail_ibfk_2",
            key_columns=("job_id",),
            ref_table="sync_job",
            ref_columns=("id",),
            on_update="NO ACTION",
            on_delete="NO ACTION",
        )
        base.update(kw)
        return migration._ForeignKey(**base)

    def test_drop_ddl(self):
        assert (
            self._fk().drop_ddl()
            == "ALTER TABLE `nav_sync_detail` DROP FOREIGN KEY `nav_sync_detail_ibfk_2`"
        )

    def test_drop_ddl_has_no_if_exists(self):
        """MySQL 全系不支持 `DROP FOREIGN KEY IF EXISTS`（errno 1064）。

        0015 实测踩过：加上该子句会让整批重建外键失败（表转码成功而外键全丢）。这里
        把同一条禁令钉死。
        """
        assert "IF EXISTS" not in self._fk().drop_ddl().upper()

    def test_add_ddl_omits_no_action(self):
        """`NO ACTION` 是 MySQL 默认值，省略后重建出的语义完全一致。"""
        assert self._fk().add_ddl(migration.TARGET_FK) == (
            "ALTER TABLE `nav_sync_detail` ADD CONSTRAINT `fk_nav_sync_detail_job_id` "
            "FOREIGN KEY (`job_id`) REFERENCES `sync_job` (`id`)"
        )

    def test_add_ddl_preserves_non_default_rules(self):
        """非默认规则必须原样带上——丢了就静默改掉删除行为。"""
        ddl = self._fk(on_delete="CASCADE", on_update="RESTRICT").add_ddl("fk_probe")
        assert ddl.endswith("ON DELETE CASCADE ON UPDATE RESTRICT"), ddl


# ---------------------------------------------------------------------------
# SQLite：安全 no-op
# ---------------------------------------------------------------------------


class TestSqliteIsANoOp:
    def test_upgrade_returns_without_touching_anything(self):
        if app_engine.dialect.name != "sqlite":
            pytest.skip("反向断言：本用例只在 SQLite 上有意义")
        with app_engine.begin() as conn:
            _run(migration.upgrade, conn)

    def test_downgrade_does_not_raise(self):
        """`ci.yml` 的 `alembic downgrade -1` 是真实执行路径，no-op 不等于 raise。"""
        with app_engine.begin() as conn:
            _run(migration.downgrade, conn)


# ---------------------------------------------------------------------------
# MySQL：三种起始态都收敛到唯一显式名
# ---------------------------------------------------------------------------


@pytest.mark.dialect
class TestMysqlCanonicalState:
    def test_fresh_schema_already_has_the_explicit_name(self):
        """#433 的模型改动是否已足够：全新库（create_all）建出的就是显式名，只此一条。"""
        engine = _mysql_only()
        with engine.connect() as conn:
            names = _job_id_fk_names(conn)
        assert names == [migration.TARGET_FK], names

    def test_helper_matches_information_schema(self):
        engine = _mysql_only()
        with engine.connect() as conn:
            fks = migration._job_id_foreign_keys(conn)
            expected = len(_job_id_fk_names(conn))
        assert len(fks) == expected, (len(fks), expected)

    def test_helper_ignores_other_columns_of_the_same_table(self):
        """`nav_sync_detail` 另有 task_log_id 外键，绝不能被卷进来。"""
        engine = _mysql_only()
        with engine.connect() as conn:
            fks = migration._job_id_foreign_keys(conn)
        assert all(fk.key_columns == ("job_id",) for fk in fks), fks

    def test_sentinel_reports_no_other_duplicates(self):
        """全库哨兵：仓库侧（模型 + 0001）不应再产出任何同列对重复外键。"""
        engine = _mysql_only()
        with engine.connect() as conn:
            assert migration._duplicate_fk_groups(conn) == []

    def test_upgrade_is_a_no_op_when_already_canonical(self, caplog):
        engine = _mysql_only()
        with engine.begin() as conn:
            with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
                _run(migration.upgrade, conn)
            names = _job_id_fk_names(conn)
        assert names == [migration.TARGET_FK], names
        assert any("空转" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_downgrade_keeps_the_single_fk(self):
        engine = _mysql_only()
        with engine.begin() as conn:
            _run(migration.downgrade, conn)
            names = _job_id_fk_names(conn)
        assert names == [migration.TARGET_FK], names


@pytest.mark.dialect
class TestMysqlConvergesFromDuplicatedState:
    """生产现状：`fk_nav_sync_detail_job_id` + `nav_sync_detail_ibfk_2` 并存。"""

    # 收尾一律用**新连接**：MySQL 的 DDL 各自隐式提交，写在同一个 `engine.begin()`
    # 事务里的恢复语句会被随后的回滚一起带走，断言一失败就再也回不到干净态。
    def test_drops_only_the_redundant_one(self):
        engine = _mysql_only()
        try:
            with engine.begin() as conn:
                _add_job_id_fk(conn, "nav_sync_detail_ibfk_dup")
                assert len(_job_id_fk_names(conn)) == 2

                _run(migration.upgrade, conn)

                assert _job_id_fk_names(conn) == [migration.TARGET_FK]
        finally:
            with engine.begin() as conn:
                _restore_canonical_state(conn)

    def test_is_idempotent_after_deduping(self, caplog):
        engine = _mysql_only()
        try:
            with engine.begin() as conn:
                _add_job_id_fk(conn, "nav_sync_detail_ibfk_dup")
                _run(migration.upgrade, conn)
                caplog.clear()
                with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
                    _run(migration.upgrade, conn)
                assert _job_id_fk_names(conn) == [migration.TARGET_FK]
        finally:
            with engine.begin() as conn:
                _restore_canonical_state(conn)
        assert any("空转" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_fk_enforcement_survives_the_dedup(self):
        """去重后外键语义必须还在——只删冗余，不是把约束删没了。"""
        engine = _mysql_only()
        try:
            with engine.begin() as conn:
                _add_job_id_fk(conn, "nav_sync_detail_ibfk_dup")
                _run(migration.upgrade, conn)
                assert _job_id_fk_names(conn) == [migration.TARGET_FK]

            # 越界插入单独一段：`engine.connect()` 退出即回滚，不落任何行
            with engine.connect() as conn:
                with pytest.raises(sa.exc.IntegrityError):
                    conn.execute(
                        sa.text(
                            "INSERT INTO nav_sync_detail "
                            "(job_id, product_code, market, nav_date, status) "
                            "VALUES (987654321, 'X', 'CN_OTC', '2026-01-01', 'failed')"
                        )
                    )
        finally:
            with engine.begin() as conn:
                _restore_canonical_state(conn)

    def test_refuses_to_pick_when_rules_differ(self):
        """两条规则不同 ⇒ 无法判断该保留哪条语义 ⇒ 响亮失败，绝不静默挑一条。"""
        engine = _mysql_only()
        try:
            with engine.begin() as conn:
                _add_job_id_fk(conn, "nav_sync_detail_ibfk_cascade", on_delete="CASCADE")
                assert len(_job_id_fk_names(conn)) == 2

                with pytest.raises(RuntimeError, match="规则不一致"):
                    _run(migration.upgrade, conn)

                # 失败必须是「什么都没动」：两条都还在
                assert len(_job_id_fk_names(conn)) == 2
        finally:
            with engine.begin() as conn:
                _restore_canonical_state(conn)


@pytest.mark.dialect
class TestMysqlPromotesAutomaticallyNamedFk:
    """#433 之前由 `create_all` 建的旧库：只有 `*_ibfk_N`，名字要提升为显式名。"""

    def test_renames_to_the_explicit_name(self):
        engine = _mysql_only()
        try:
            with engine.begin() as conn:
                _drop_job_id_fk(conn, migration.TARGET_FK)
                _add_job_id_fk(conn, "nav_sync_detail_ibfk_legacy")
                assert _job_id_fk_names(conn) == ["nav_sync_detail_ibfk_legacy"]

                _run(migration.upgrade, conn)

                assert _job_id_fk_names(conn) == [migration.TARGET_FK]
        finally:
            with engine.begin() as conn:
                _restore_canonical_state(conn)

    def test_creates_the_fk_when_none_exists(self):
        """外键语义整体缺失（比重复更严重）：按模型声明补建。"""
        engine = _mysql_only()
        try:
            with engine.begin() as conn:
                for name in _job_id_fk_names(conn):
                    _drop_job_id_fk(conn, name)
                assert _job_id_fk_names(conn) == []

                _run(migration.upgrade, conn)

                assert _job_id_fk_names(conn) == [migration.TARGET_FK]
        finally:
            with engine.begin() as conn:
                _restore_canonical_state(conn)
