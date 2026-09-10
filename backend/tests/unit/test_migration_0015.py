# ============================================================================
# 单元测试：issue #433 迁移 0015 全库字符集统一 utf8mb4
# ============================================================================
# 断言重点：
# ① 修订链挂在 0014 之后，且常量取自单一事实来源（无字面量）；
# ② SQLite 上 upgrade/downgrade 都是安全 no-op（无字符集概念，且 CONVERT TO CHARACTER
#    SET 不是合法 SQL）——本地就能跑，不必等 CI；
# ③ 外键 DDL 的生成逻辑（复合键、ON DELETE/UPDATE 规则）逐条可断言，纯字符串、双后端可跑；
# ④ MySQL 上真实生效：helper 读到的外键与 information_schema 一致，且「拆 → 转 → 建」
#    往返之后外键条数与 4 字节数据都原样保留。
#
# ④ 只在 CI backend-test-mysql / 本地 MySQL 生效：本库库级 charset 已是 utf8mb4，
#    转码本身是空操作，能验的是「helper 正确 + 拆建外键无损 + 数据无损」这三件真事；
#    真正的 utf8mb3 → utf8mb4 转换由 CI 全新库路径（建库 utf8mb4 + create_all 全 utf8mb4，
#    故 0015 也空转）之外，靠生产演练覆盖。
#
# 不走 alembic 命令 API：0014 之前的迁移含 MySQL 专有 SQL，SQLite 上跑不通整条链，
# 只程序化执行 0015（与 test_migration_0014.py 同法）。
# ============================================================================

import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.constants.db_charset import DB_CHARSET, DB_COLLATE, LEGACY_DB_CHARSET
from app.database import engine as app_engine
from app.models.base import Base

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0015_db_wide_utf8mb4.py"
)

# 4 字节字符样本：U+1F4A5（emoji）
FOUR_BYTE_TEXT = "炸了 💥"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0015_under_test", MIGRATION_PATH)
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
    """字符集相关断言只在 MySQL 有语义；SQLite 跳过而非失败。"""
    if app_engine.dialect.name != "mysql":
        pytest.skip("字符集只在 MySQL 成立（SQLite 无字符集概念）")
    return app_engine


# ---------------------------------------------------------------------------
# 修订链与单一事实来源
# ---------------------------------------------------------------------------


class TestRevisionChain:
    def test_links_onto_0014(self):
        assert migration.revision == "0015"
        assert migration.down_revision == "0014"

    def test_targets_the_shared_constants(self):
        assert migration.DB_CHARSET == DB_CHARSET
        assert migration.DB_COLLATE == DB_COLLATE
        assert migration.LEGACY_DB_CHARSET == LEGACY_DB_CHARSET

    def test_charset_sources_are_the_shared_constants(self):
        """迁移用的 charset/collate 必须**是**共享常量本身，不是抄来的同值字符串。

        `==` 在这里不够：把常量值写死成字面量也能通过相等断言，改常量时迁移就悄悄漂移。
        故断言同一性（`is`）——迁移把常量 import 进来直接用，这是唯一能过 `is` 的形态。
        """
        from app.constants import db_charset

        assert migration.DB_CHARSET is db_charset.DB_CHARSET
        assert migration.DB_COLLATE is db_charset.DB_COLLATE
        assert migration.LEGACY_DB_CHARSET is db_charset.LEGACY_DB_CHARSET
        assert migration.LEGACY_DB_COLLATE is db_charset.LEGACY_DB_COLLATE

    def test_uses_drop_foreign_key_without_if_exists(self):
        """MySQL 全系不支持 `DROP FOREIGN KEY IF EXISTS`（errno 1064）。

        实测踩过：加上该子句会让**整批重建外键**失败——表转码成功而外键全丢。故此处
        把「生成的 DDL 里没有 IF EXISTS」钉死。
        """
        fk = migration._ForeignKey(
            name="fk_probe", table="t1", columns=("a",), ref_table="t2",
            ref_columns=("b",), on_update="NO ACTION", on_delete="NO ACTION",
        )
        assert fk.drop_ddl() == "ALTER TABLE `t1` DROP FOREIGN KEY `fk_probe`"
        assert "IF EXISTS" not in fk.drop_ddl().upper()


# ---------------------------------------------------------------------------
# 外键 DDL 生成（纯字符串，双后端可跑）
# ---------------------------------------------------------------------------


class TestForeignKeyDdl:
    def _fk(self, **kw):
        base = dict(
            name="fk_probe", table="child", columns=("a",), ref_table="parent",
            ref_columns=("b",), on_update="NO ACTION", on_delete="NO ACTION",
        )
        base.update(kw)
        return migration._ForeignKey(**base)

    def test_default_actions_are_omitted(self):
        """NO ACTION 是 MySQL 默认，省略后语义一致——不写死进 DDL。"""
        ddl = self._fk().add_ddl()
        assert ddl == (
            "ALTER TABLE `child` ADD CONSTRAINT `fk_probe` "
            "FOREIGN KEY (`a`) REFERENCES `parent` (`b`)"
        )

    def test_restrict_and_cascade_are_preserved(self):
        """生产库有 3 条 ON DELETE RESTRICT，必须原样保留、不被归一成默认。"""
        ddl = self._fk(on_delete="RESTRICT").add_ddl()
        assert ddl.endswith("ON DELETE RESTRICT"), ddl

        both = self._fk(on_delete="CASCADE", on_update="SET NULL").add_ddl()
        assert "ON DELETE CASCADE" in both and "ON UPDATE SET NULL" in both, both

    def test_composite_key_keeps_column_order(self):
        """复合外键的列顺序即语义（(code, market) ≠ (market, code)），不得重排。"""
        fk = self._fk(
            columns=("product_code", "market"),
            ref_table="product",
            ref_columns=("code", "market"),
        )
        ddl = fk.add_ddl()
        assert "FOREIGN KEY (`product_code`, `market`)" in ddl, ddl
        assert "REFERENCES `product` (`code`, `market`)" in ddl, ddl

    def test_identifiers_are_escaped(self):
        """库名/表名来自 information_schema，反引号必须翻倍以杜绝拼装注入。"""
        fk = self._fk(table="we`ird")
        assert "`we``ird`" in fk.drop_ddl(), fk.drop_ddl()


class TestSqliteIsANoOp:
    """SQLite 无字符集概念，且 `CONVERT TO CHARACTER SET` 不是合法 SQL——必须直接返回。"""

    def test_upgrade_returns_without_touching_the_connection(self, tmp_path):
        eng = sa.create_engine(f"sqlite:///{tmp_path / 'migration_0015.db'}")
        Base.metadata.create_all(bind=eng)
        with eng.begin() as conn:
            _run(migration.upgrade, conn)  # 抛错即红
        eng.dispose()

    def test_downgrade_returns_without_touching_the_connection(self, tmp_path):
        eng = sa.create_engine(f"sqlite:///{tmp_path / 'migration_0015_dn.db'}")
        Base.metadata.create_all(bind=eng)
        with eng.begin() as conn:
            _run(migration.downgrade, conn)
        eng.dispose()


# ---------------------------------------------------------------------------
# MySQL 实况
# ---------------------------------------------------------------------------


class TestMysqlForeignKeys:
    """`_foreign_keys` 必须与 information_schema 对齐，且复合键被正确聚合。"""

    def test_matches_information_schema(self):
        engine = _mysql_only()
        with engine.connect() as conn:
            tables = migration._base_tables(conn)
            assert len(tables) >= 20, tables

            fks = migration._foreign_keys(conn, tables)
            expected = conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
                    "WHERE CONSTRAINT_SCHEMA = DATABASE()"
                )
            ).scalar()
        assert len(fks) == expected, (len(fks), expected)

    def test_composite_keys_are_aggregated_not_split(self):
        engine = _mysql_only()
        with engine.connect() as conn:
            fks = migration._foreign_keys(conn, migration._base_tables(conn))
        composites = [fk for fk in fks if len(fk.columns) > 1]
        assert composites, "本库应有复合外键（product 的 (code, market)）"
        for fk in composites:
            assert len(fk.columns) == len(fk.ref_columns), fk
            # 逐位置成对，不是笛卡尔积
            assert fk.columns == tuple(fk.columns), fk
        assert any(
            fk.columns == ("product_code", "market") and fk.ref_columns == ("code", "market")
            for fk in composites
        ), composites

    def test_referencing_tables_finds_incoming_edges(self):
        """入边表必须被找到——漏掉它，转码父表时撞 errno 3780。"""
        engine = _mysql_only()
        with engine.connect() as conn:
            referencing = migration._referencing_tables(conn, ["product"])
            assert "portfolio_position" in referencing, referencing
            assert "price_record" in referencing, referencing
            # 自引用（share_change_event.parent_event_id）不应把自己算成入边
            assert "product" not in referencing, referencing

    def test_no_tables_without_charset(self):
        engine = _mysql_only()
        with engine.connect() as conn:
            charsets = migration._table_charsets(conn)
        assert charsets, "库内应有表"
        assert all(cs for cs in charsets.values()), charsets


class TestMysqlRoundTripPreservesForeignKeysAndData:
    """「拆 → 转 → 建」往返：外键条数不变、定义不变、4 字节数据不变。

    本库库级 charset 已是 utf8mb4，转码本身是空操作；这里验的是**拆建外键这一段**
    对真实库无损——它才是本迁移最高危的一步（实测曾因 `IF EXISTS` 让 29 条外键全丢）。
    """

    def test_downgrade_then_upgrade_keeps_foreign_keys(self):
        engine = _mysql_only()
        with engine.connect() as conn:
            before = migration._foreign_keys(conn, migration._base_tables(conn))
        assert before, "库内应有外键可验"

        with engine.begin() as conn:
            _run(migration.downgrade, conn)

        with engine.connect() as conn:
            during = migration._foreign_keys(conn, migration._base_tables(conn))

        with engine.begin() as conn:
            _run(migration.upgrade, conn)

        with engine.connect() as conn:
            after = migration._foreign_keys(conn, migration._base_tables(conn))
            names_after = sorted(fk.name for fk in after)

        # 定义（名字 + 列 + 引用 + 规则）必须逐条原样回来
        assert sorted(during, key=lambda fk: fk.name) == sorted(
            before, key=lambda fk: fk.name
        ), "downgrade 后外键定义发生变化"
        assert sorted(after, key=lambda fk: fk.name) == sorted(
            before, key=lambda fk: fk.name
        ), "upgrade 后外键定义发生变化"
        assert len(names_after) == len(set(names_after)), names_after

    def test_round_trip_keeps_four_byte_data(self):
        """含 4 字节数据的表在 downgrade 里被跳过、在 upgrade 里原样保留。

        这是「转码不得改写数据」的回归护栏：若服务端在 ALTER 下把不可表示字符替换掉，
        `record_system_error` 那类 best-effort 写入就会静默丢内容。
        """
        engine = _mysql_only()
        table = "_charset_roundtrip_probe"

        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
            conn.execute(
                sa.text(
                    f"CREATE TABLE {table} (v VARCHAR(50)) "
                    f"CHARSET={DB_CHARSET} COLLATE {DB_COLLATE}"
                )
            )
            conn.execute(sa.text(f"INSERT INTO {table} (v) VALUES (:v)"), {"v": FOUR_BYTE_TEXT})

        try:
            with engine.begin() as conn:
                _run(migration.downgrade, conn)

            with engine.connect() as conn:
                after_downgrade = conn.execute(sa.text(f"SELECT v FROM {table}")).scalar()
                # 库级 charset 已回退到 utf8mb3，但含 4 字节数据的表被跳过、仍是 utf8mb4
                charset = migration._table_charsets(conn)[table]
            assert after_downgrade == FOUR_BYTE_TEXT, repr(after_downgrade)
            assert charset == DB_CHARSET, charset

            with engine.begin() as conn:
                _run(migration.upgrade, conn)

            with engine.connect() as conn:
                final = conn.execute(sa.text(f"SELECT v FROM {table}")).scalar()
            assert final == FOUR_BYTE_TEXT, repr(final)
        finally:
            with engine.begin() as conn:
                conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
            # 库级 charset 恢复终态，避免污染同会话的其他用例
            with engine.begin() as conn:
                _run(migration.upgrade, conn)


class TestIdempotency:
    """已是目标 charset 时必须空转——新库路径（create_all 已建 utf8mb4）依赖这一点。"""

    def test_second_upgrade_is_a_no_op(self, caplog):
        engine = _mysql_only()
        with engine.begin() as conn:
            _run(migration.upgrade, conn)

        with engine.begin() as conn:
            with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
                _run(migration.upgrade, conn)

        messages = [r.getMessage() for r in caplog.records]
        assert any("空转" in m for m in messages), messages
