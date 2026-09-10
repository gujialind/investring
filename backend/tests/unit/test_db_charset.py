# ============================================================================
# 单元测试：issue #433 全库字符集统一 utf8mb4 —— 建表层（`create_all` 路径）
# ============================================================================
# 缺陷形态：库级 utf8mb3 与连接侧 utf8mb4 不对称，4 字节 UTF-8 字符（emoji、CJK 扩展 B）
# 撞 utf8mb3 列 → errno 1366 → **整条**记录写不进去。迁移 0015 负责存量表，本文件守的是
# 另一条同等重要的路径：`main.py` import 期的 `Base.metadata.create_all` 建出的**新表**
# 必须自带 utf8mb4，否则新环境/新模型会静默重新引入缺陷。
#
# 为什么断言编译出的 DDL 而不是真去 MySQL 建库：建库语句在迁移之前执行，且 `CreateTable
# (…).compile()` 与 `create_all` 走同一套方言渲染——在**任何**后端上都能断言，本地
# SQLite 即可跑，不必等 CI。真实建表另由 `test_migration_0015.py` 的 MySQL 用例覆盖。
# ============================================================================

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import declarative_base
from sqlalchemy.schema import CreateTable

from app.constants.db_charset import (
    DB_CHARSET,
    DB_COLLATE,
    LEGACY_DB_CHARSET,
    LEGACY_DB_COLLATE,
)
from app.models.base import Base

# 必须 import 全部模型，`Base.metadata` 才是全量
import app.models  # noqa: F401  (import 顺序无关，只为触发注册)


MODEL_TABLES = sorted(Base.metadata.tables)
_MYSQL_DIALECT = mysql.dialect()


def _create_ddl(table_name: str) -> str:
    """单行化的建表 DDL——MySQL 方言渲染，与 `create_all` 同源。"""
    return str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=_MYSQL_DIALECT)).replace("\n", "")


class TestModelTablesDeclareCharset:
    """**每一张**模型表的建表 DDL 都要带 utf8mb4——漏一张就是一处静默的 utf8mb3 面。"""

    def test_metadata_is_not_empty(self):
        """防「模型没被 import、断言空集恒真」的假绿。"""
        assert len(MODEL_TABLES) >= 20, MODEL_TABLES

    @pytest.mark.parametrize("table_name", MODEL_TABLES)
    def test_table_ddl_carries_utf8mb4(self, table_name):
        ddl = _create_ddl(table_name)
        assert f"CHARSET={DB_CHARSET}" in ddl, ddl
        assert f"COLLATE {DB_COLLATE}" in ddl, ddl

    def test_no_table_still_declares_legacy_charset(self):
        """全库统一后不应有任何表还钉在 utf8mb3（0015 之后它只会出现在 downgrade 里）。"""
        legacy = [
            name for name in MODEL_TABLES
            if f"CHARSET={LEGACY_DB_CHARSET}" in _create_ddl(name)
        ]
        assert legacy == [], legacy


class TestCharsetHookComposition:
    """钩子必须**补默认**而不是**覆盖**，且不得破坏 `__table_args__` 的约束元组形态。"""

    def test_existing_dict_form_is_preserved(self):
        """dict 形态（五张日志表）原本就写了 charset，结果不变。"""
        from app.models.audit_log import AuditLog

        args = AuditLog.__table_args__
        assert args["mysql_charset"] == DB_CHARSET
        assert args["mysql_collate"] == DB_COLLATE

    def test_explicit_value_wins_over_default(self):
        """子类显式声明的 charset 优先——钩子只 setdefault，不做归一。"""
        md = sa.MetaData()

        from app.models.base import _CharsetBase

        override = declarative_base(cls=_CharsetBase, metadata=md)

        class _Explicit(override):
            __tablename__ = "_charset_hook_probe"
            id = sa.Column(sa.Integer, primary_key=True)
            __table_args__ = {"mysql_charset": "latin1", "mysql_collate": "latin1_swedish_ci"}

        assert _Explicit.__table_args__["mysql_charset"] == "latin1"
        assert _Explicit.__table_args__["mysql_collate"] == "latin1_swedish_ci"
        assert "CHARSET=latin1" in str(
            CreateTable(_Explicit.__table__).compile(dialect=_MYSQL_DIALECT)
        )

    def test_tuple_form_keeps_constraints(self):
        """tuple 形态（约束元组）不得被钩子吃掉——这是最容易写错的一条分支。"""
        md = sa.MetaData()

        from app.models.base import _CharsetBase

        probe_base = declarative_base(cls=_CharsetBase, metadata=md)

        class _TupleForm(probe_base):
            __tablename__ = "_charset_hook_tuple_probe"
            id = sa.Column(sa.Integer, primary_key=True)
            code = sa.Column(sa.String(10), nullable=False)
            __table_args__ = (
                sa.UniqueConstraint("code", name="uq_charset_probe_code"),
                sa.CheckConstraint("id > 0", name="ck_charset_probe_id"),
            )

        args = _TupleForm.__table_args__
        assert isinstance(args, tuple), args
        assert args[-1]["mysql_charset"] == DB_CHARSET, args
        names = {getattr(c, "name", None) for c in args[:-1]}
        assert names == {"uq_charset_probe_code", "ck_charset_probe_id"}, names

        ddl = str(CreateTable(_TupleForm.__table__).compile(dialect=_MYSQL_DIALECT))
        assert "uq_charset_probe_code" in ddl
        assert "ck_charset_probe_id" in ddl
        assert f"CHARSET={DB_CHARSET}" in ddl

    def test_business_constraints_survive_on_real_models(self):
        """真实模型的约束/索引在钩子介入后仍完整（抽查带 UniqueConstraint 的表）。"""
        expected = {
            "portfolio_position": {"uix_position_snapshot", "check_nav_or_non_nav"},
            "price_record": {"uix_price_record"},
            "trade": {"uq_trade_transfer_group"},
        }
        for table_name, wanted in expected.items():
            table = Base.metadata.tables[table_name]
            names = {c.name for c in table.constraints if c.name}
            missing = wanted - names
            assert not missing, f"{table_name} 少了 {missing}"


class TestLogCharsetConstantsStayConsistent:
    """0014 仍在用 `log_charset` 的常量，两者不得漂移。"""

    def test_log_charset_matches_db_charset(self):
        from app.constants.log_charset import LOG_TABLE_CHARSET, LOG_TABLE_COLLATE

        assert LOG_TABLE_CHARSET == DB_CHARSET
        assert LOG_TABLE_COLLATE == DB_COLLATE

    def test_legacy_constants_are_the_old_db_level_values(self):
        """downgrade 依赖这两个值就是当年的库级设置（写错则回滚到错误的 charset）。"""
        assert LEGACY_DB_CHARSET == "utf8mb3"
        assert LEGACY_DB_COLLATE == "utf8mb3_general_ci"
