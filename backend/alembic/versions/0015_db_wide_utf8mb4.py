"""issue #433 全库字符集统一 utf8mb4（库级默认 + 全部存量表）

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-11

**为什么还要这一步**：0014 只在 utf8mb3 库内把五张自由文本表（日志/同步明细）单独提到
utf8mb4，是一条「例外清单」路线——清单必须永远正确，而**任何新建的表**都会静默继承库级
utf8mb3、重新引入 errno 1366（4 字节 UTF-8 撞 utf8mb3 列，整条记录写不进去）。本迁移把
库级默认与其余全部存量表一次转净：全库同构后不再需要维护「哪些表要 utf8mb4」。

**为什么不只在控制台跑脚本**：控制台脚本只改数据库、不改代码，测试库/新环境/CI 仍是
utf8mb3，每次搭环境都要记得手工跑一遍，`alembic_version` 也停在 0014——代码无法再现
数据库的真实状态，正是 0013（#405）治过的 schema drift 的翻版。迁移文件才是可复现、
可在 CI 验证的单一事实来源。

**为什么必须「先拆外键 → 转码 → 再建外键」**：MySQL 拒绝在表被外键引用时 `ALTER TABLE
… CONVERT TO CHARACTER SET`（errno 3780 `Referencing column … are incompatible`）。
实测：生产库 30 条外键、涉及 16 张表，逐表直接转全部失败；只有把相关外键全拆掉再
逐表转、最后重建才成立。**约束名逐运行时从 `information_schema` 读**，不写死在迁移里——
生产库的外键名是 `fk_rule_class` / `fk_product_region_code` 这类描述性名字，而测试库/全新
库由 `create_all` 生成 `*_ibfk_N`，硬编码必然在其中一边失败。

**不要用 `DROP FOREIGN KEY IF EXISTS`**：MySQL（含 8.4）不支持该子句，写了是语法错误
（errno 1064）。更糟的是它会让**整批重建外键**失败——实测踩过：表转码成功而 30 条外键
全部丢失。故这里只拆「确实存在」的外键（列表来自 information_schema），不做防御性写法。

**幂等与自愈**：整库已是目标 charset 时直接返回（新库路径：`create_all` 已按模型建出
utf8mb4 → 本迁移空转）。非空转时**总是**重放一遍「拆 → 转 → 建」：拆哪些外键由运行时
判断，重建用的是拆之前抓到的同一份定义，故重复执行收敛到同一状态，且中途失败留下
「表已转、外键待建」的半成品时，重跑会补齐而不是跳过。

**downgrade 的语义**：反向转 utf8mb3 在表内已有 4 字节字符时必然失败（数据无法表示），
会让 `docs/runbooks/deploy-rollback.md` 的 `alembic downgrade` 半途而废。故逐列探测
（与 0014 同一套 `CONVERT(col USING utf8mb3) <> BINARY col` 判据）并**跳过**含 4 字节
数据的表、打 WARNING，最后汇总一条结论——不假装成功、不吞数据。空库/无 4 字节数据的库
因此仍完整可逆，CI 的 `downgrade -1` + `upgrade head` 往返不需要豁免。
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from alembic import op
import sqlalchemy as sa

from app.constants.db_charset import (
    DB_CHARSET,
    DB_COLLATE,
    LEGACY_DB_CHARSET,
    LEGACY_DB_COLLATE,
)

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# 标识符引用：库名/表名/列名来自 information_schema，走反引号而非参数绑定（DDL 不支持
# 绑定参数），并把内嵌反引号翻倍以杜绝注入式拼装。
_QUOTE = "`{0}`".format


def _q(identifier: str) -> str:
    return _QUOTE(identifier.replace("`", "``"))


# 4 字节 UTF-8 字符探测（判据与 0014 一致，理由见 0014 的注释）：把列值按 utf8mb3 重新
# 解释后与原值做**二进制**比较——utf8mb3 表示不了的字符转换后必然与原值不同。
#
# 刻意不用 `LENGTH(col) - 3 * CHAR_LENGTH(col) > 0`：那套写法依赖「CHAR_LENGTH 返回字符
# 数」这一函数语义，一旦某版本/配置让它按字节计数，差值恒为 0、守卫静默失效。
_FOUR_BYTE_PROBE = (
    "SELECT COUNT(*) FROM {table} WHERE CONVERT({column} USING utf8mb3) <> BINARY {column}"
)


@dataclass(frozen=True)
class _ForeignKey:
    """一条外键的完整定义（拆掉之后据此原样重建）。"""

    name: str
    table: str
    columns: Tuple[str, ...]
    ref_table: str
    ref_columns: Tuple[str, ...]
    on_update: str
    on_delete: str

    def drop_ddl(self) -> str:
        return f"ALTER TABLE {_q(self.table)} DROP FOREIGN KEY {_q(self.name)}"

    def add_ddl(self) -> str:
        cols = ", ".join(_q(c) for c in self.columns)
        ref_cols = ", ".join(_q(c) for c in self.ref_columns)
        # 只在行为不是 MySQL 默认值时显式写出：`NO ACTION` 是默认，省略后重建出的语义
        # 完全一致；而生产库里另有 3 条 `ON DELETE RESTRICT`（与 NO ACTION 相近但不同，
        # 尤其在被引用表有 BEFORE DELETE 触发器时），必须原样保留、不能被本迁移归一。
        clauses = []
        if self.on_delete != "NO ACTION":
            clauses.append(f"ON DELETE {self.on_delete}")
        if self.on_update != "NO ACTION":
            clauses.append(f"ON UPDATE {self.on_update}")
        suffix = (" " + " ".join(clauses)) if clauses else ""
        return (
            f"ALTER TABLE {_q(self.table)} ADD CONSTRAINT {_q(self.name)} "
            f"FOREIGN KEY ({cols}) REFERENCES {_q(self.ref_table)} ({ref_cols}){suffix}"
        )


def _base_tables(bind) -> List[str]:
    """当前库的全部基表（含 alembic_version / apscheduler_jobs 这类非模型表）。

    刻意按 `TABLE_SCHEMA = DATABASE()` 取全库而不是取模型清单：本迁移的目标是**库级**
    统一，任何漏掉的表都会成为下一处 errno 1366 的面；`DATABASE()` 让语句跨环境自洽
    （CI 用 ir_migration、本地/生产用各自库名）。
    """
    rows = bind.execute(
        sa.text(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME"
        )
    ).scalars()
    return list(rows)


def _table_charsets(bind) -> Dict[str, Optional[str]]:
    rows = bind.execute(
        sa.text(
            "SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
        )
    ).all()
    return {name: (collation or "").split("_")[0] or None for name, collation in rows}


def _db_charset(bind) -> Optional[str]:
    return bind.execute(
        sa.text(
            "SELECT DEFAULT_CHARACTER_SET_NAME FROM information_schema.SCHEMATA "
            "WHERE SCHEMA_NAME = DATABASE()"
        )
    ).scalar()


def _foreign_keys(bind, tables) -> List[_ForeignKey]:
    """这些表**作为子表**声明的全部外键（复合键按 ORDINAL_POSITION 聚合）。"""
    if not tables:
        return []
    rows = bind.execute(
        sa.text(
            "SELECT rc.CONSTRAINT_NAME, rc.TABLE_NAME, rc.REFERENCED_TABLE_NAME, "
            "       rc.UPDATE_RULE, rc.DELETE_RULE, "
            "       GROUP_CONCAT(k.COLUMN_NAME ORDER BY k.ORDINAL_POSITION) cols, "
            "       GROUP_CONCAT(k.REFERENCED_COLUMN_NAME ORDER BY k.ORDINAL_POSITION) ref_cols "
            "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
            "JOIN information_schema.KEY_COLUMN_USAGE k "
            "  ON k.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA "
            " AND k.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
            " AND k.TABLE_NAME = rc.TABLE_NAME "
            "WHERE rc.CONSTRAINT_SCHEMA = DATABASE() "
            "  AND rc.TABLE_NAME IN :tables "
            "GROUP BY rc.CONSTRAINT_NAME, rc.TABLE_NAME, rc.REFERENCED_TABLE_NAME, "
            "         rc.UPDATE_RULE, rc.DELETE_RULE "
            "ORDER BY rc.TABLE_NAME, rc.CONSTRAINT_NAME"
        ).bindparams(sa.bindparam("tables", expanding=True)),
        {"tables": list(tables)},
    ).all()
    return [
        _ForeignKey(
            name=name,
            table=table,
            columns=tuple(cols.split(",")),
            ref_table=ref_table,
            ref_columns=tuple(ref_cols.split(",")),
            on_update=on_update,
            on_delete=on_delete,
        )
        for name, table, ref_table, on_update, on_delete, cols, ref_cols in rows
    ]


def _referencing_tables(bind, tables) -> List[str]:
    """把这些表**作为父表**引用的子表（这些子表的外键也必须先拆，否则转码撞 errno 3780）。"""
    if not tables:
        return []
    rows = bind.execute(
        sa.text(
            "SELECT DISTINCT TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() "
            "  AND REFERENCED_TABLE_NAME IN :tables "
            "  AND TABLE_NAME <> REFERENCED_TABLE_NAME"
        ).bindparams(sa.bindparam("tables", expanding=True)),
        {"tables": list(tables)},
    ).scalars()
    return list(rows)


def _convert(bind, table: str, charset: str, collate: str) -> None:
    bind.execute(sa.text(f"ALTER TABLE {_q(table)} CONVERT TO CHARACTER SET {charset} COLLATE {collate}"))


def _four_byte_columns(bind, table: str) -> List[str]:
    """该表文本列中实际出现 4 字节字符的列名（无则空）。判据同 0014。

    探测语句本身抛错时按「无法证明可安全回退」处理——记 WARNING 并交由调用方跳过，
    绝不假设它能无损转回 utf8mb3。
    """
    inspector = sa.inspect(bind)
    hits = []
    for column in inspector.get_columns(table):
        if isinstance(column["type"], (sa.Text, sa.String)):
            probe = _FOUR_BYTE_PROBE.format(table=_q(table), column=_q(column["name"]))
            try:
                count = bind.execute(sa.text(probe)).scalar()
            except sa.exc.SQLAlchemyError as exc:
                logger.warning(
                    "0015 探测 %s.%s 失败（%s），按「不可安全回退」处理",
                    table,
                    column["name"],
                    exc.__class__.__name__,
                )
                hits.append(column["name"])
                continue
            if (count or 0) > 0:
                hits.append(column["name"])
    return hits


def _recreate_foreign_keys(bind, foreign_keys: List[_ForeignKey]) -> None:
    for fk in foreign_keys:
        bind.execute(sa.text(fk.add_ddl()))
    logger.info("0015 已重建 %d 条外键", len(foreign_keys))


def _convert_database(
    bind,
    target_charset: str,
    target_collate: str,
    skip_four_byte: bool,
) -> List[str]:
    """把全库收敛到目标 charset：库级默认 + 所有非目标表（含其外键拆建）。

    返回被跳过的表名（仅 `skip_four_byte=True` 时可能非空）。
    """
    current = _table_charsets(bind)
    pending = [t for t, cs in sorted(current.items()) if cs != target_charset]
    db_charset = _db_charset(bind)

    if not pending and db_charset == target_charset:
        logger.info("0015 已是 %s，空转", target_charset)
        return []

    if db_charset != target_charset:
        # 只影响**将来**建表的默认值；已存在的表必须逐表 CONVERT 才动
        bind.execute(
            sa.text(f"ALTER DATABASE {_q(bind.engine.url.database)} "
                    f"CHARACTER SET {target_charset} COLLATE {target_collate}")
        )
        logger.info("0015 库级 charset 已改为 %s / %s", target_charset, target_collate)

    if not pending:
        return []

    skipped: List[str] = []
    if skip_four_byte:
        for table in list(pending):
            hits = _four_byte_columns(bind, table)
            if hits:
                logger.warning(
                    "0015 downgrade 跳过 %s：列 %s 已含 4 字节字符，无法转回 %s",
                    table,
                    ", ".join(hits),
                    target_charset,
                )
                skipped.append(table)
        pending = [t for t in pending if t not in skipped]

    # 需要拆外键的范围 = 待转表的**出边**（待转表自己声明的外键）+ **入边**（其它表引用
    # 待转表的外键）。两者都必须拆：MySQL 不允许被外键引用的表转码，也不允许转码后让
    # 外键两端跨越不同 charset。
    child_tables = sorted(set(pending) | set(_referencing_tables(bind, pending)))
    foreign_keys = _foreign_keys(bind, child_tables)

    for fk in foreign_keys:
        bind.execute(sa.text(fk.drop_ddl()))

    for table in pending:
        _convert(bind, table, target_charset, target_collate)
    logger.info("0015 已把 %d 张表转为 %s（拆建外键 %d 条）",
                len(pending), target_charset, len(foreign_keys))

    _recreate_foreign_keys(bind, foreign_keys)

    still = [t for t, cs in _table_charsets(bind).items() if t in pending and cs != target_charset]
    if still:
        raise RuntimeError(
            f"0015 转码后仍有表未达到 {target_charset}：{still}；"
            "请检查是否存在跨 charset 的外键或权限不足"
        )
    return skipped


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        # SQLite 无字符集概念，且 CONVERT TO CHARACTER SET 不是合法 SQL
        return
    _convert_database(bind, DB_CHARSET, DB_COLLATE, skip_four_byte=False)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    skipped = _convert_database(
        bind, LEGACY_DB_CHARSET, LEGACY_DB_COLLATE, skip_four_byte=True
    )
    if skipped:
        logger.warning(
            "0015 downgrade 完成但未完全可逆：%d 张表仍为 %s（含 4 字节数据，无法表示）——%s",
            len(skipped),
            DB_CHARSET,
            ", ".join(skipped),
        )
