"""nav_sync_detail.job_id 外键去重收敛（issue #434）

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-10

**缺陷**：生产库 `nav_sync_detail.job_id` 上并挂着两条指向 `sync_job(id)` 的外键——
迁移 0001 显式命名的 `fk_nav_sync_detail_job_id`，与 `create_all` 时代 SQLAlchemy 未命名、
由 MySQL 自动命名的 `nav_sync_detail_ibfk_2`。MySQL 不做「同列对重复外键」去重，两条
语义完全相同（同为 `NO ACTION`/`NO ACTION`）的约束会同时生效。

**为什么必须治**：功能上冗余约束无害，真正的代价是**后续迁移的静默前提失真**——任何
按显式名 `DROP FOREIGN KEY` 的迁移只删掉一条，另一条继续强制外键语义，迁移作者会在
「外键已解除」的错误前提下改列/改表（典型翻车：认为可以自由改 `job_id` 的类型或灌入
无对应 `sync_job` 的历史行）。此外 `0001.downgrade()` 按显式名 drop，留 `_ibfk_N` 的库
回滚到 base 会直接失败。

**根因（为什么两条都留下来了）**：生产库是「先有表（`create_all`，未命名 FK → `_ibfk_2`）、
后跑迁移（0001 显式名建第二条）」的历史顺序，两条路径互不知道对方存在。0001 里那句
`op.create_foreign_key(...)` 套着 `try/except: pass`（为兼容 `create_all` 已建表的新库），
于是「同语义约束已存在」这件事被吞掉、没有被当成错误暴露。

**仓库侧已不再复现**（#433）：`app/models/nav_sync_detail.py` 的 `job_id` 改为
`ForeignKey("sync_job.id", name="fk_nav_sync_detail_job_id")`，全新库两条路径落到**同一个
约束名**——`create_all` 先建出它，0001 再建撞 `errno 1826`（Duplicate foreign key
constraint name）被 `try/except` 吞掉，结果恰好一条。故本迁移对全新库是 no-op，只对
「已经处于重复态」的存量库（生产）生效——与 0013 同为**采纳型**迁移。

**不改 0001**：它已在所有环境执行过，改它会让「跑过旧 0001 的库」与「跑过新 0001 的库」
落到不同状态（历史不可对齐）；存量库的收敛由本迁移负责。

**不做通用去重**：只认 `nav_sync_detail.job_id` 这一个精确的列对与目标（`sync_job.id`），
其余表只**扫一遍并打 WARNING**、绝不自动删——「同列对重复」本身不足以判定该保留哪条
（两条的 `ON DELETE`/`ON UPDATE` 规则可能不同），误删一条外键 = 静默丢掉一层约束语义。

**目标态（收敛，而非仅删冗余）**：`nav_sync_detail.job_id` 上**恰好一条**外键，且名为
`fk_nav_sync_detail_job_id`。三条分支：
  ① 已是目标态 → 空转（全新库、CI、跑过 #433 之后的测试库走这条）；
  ② 有显式名那条（生产）→ **只多删**冗余的其余条，好的那条全程不碰；
  ③ 只有自动名 `*_ibfk_N`（#433 之前由 `create_all` 建的旧库）→ 拆掉重建为显式名，
     避免各环境外键名长期不一致（也让 `0001.downgrade()` 在这些库上可用）。
`TARGET_FK` 与模型 `ForeignKey(name=...)` 的一致性由 `tests/unit/test_migration_0016.py`
绑死——本迁移刻意不 import 模型：已执行的迁移必须冻结，模型后续演进不得改变它的行为。
"""
import logging
from dataclasses import dataclass
from typing import List, Tuple

from alembic import op
import sqlalchemy as sa


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# 目标列对与前缀名（与 app/models/nav_sync_detail.py 的 ForeignKey(name=...) 必须一致）
TABLE = "nav_sync_detail"
COLUMN = "job_id"
REF_TABLE = "sync_job"
REF_COLUMN = "id"
TARGET_FK = "fk_nav_sync_detail_job_id"

# 标识符引用：名字来自 information_schema，走反引号而非参数绑定（DDL 不支持绑定参数），
# 并把内嵌反引号翻倍以杜绝注入式拼装（同 0015）。
_QUOTE = "`{0}`".format


def _q(identifier: str) -> str:
    return _QUOTE(identifier.replace("`", "``"))


@dataclass(frozen=True)
class _ForeignKey:
    name: str
    key_columns: Tuple[str, ...]
    ref_table: str
    ref_columns: Tuple[str, ...]
    on_update: str
    on_delete: str

    def drop_ddl(self) -> str:
        return f"ALTER TABLE {_q(TABLE)} DROP FOREIGN KEY {_q(self.name)}"

    def add_ddl(self, name: str) -> str:
        """按 `name` 重建（等于本条的定义）——用于把自动名提升为显式名。"""
        cols = ", ".join(_q(c) for c in self.key_columns)
        ref_cols = ", ".join(_q(c) for c in self.ref_columns)
        # `NO ACTION` 是 MySQL 默认值，省略后重建出的语义完全一致（同 0015 的取舍）
        clauses = []
        if self.on_delete != "NO ACTION":
            clauses.append(f"ON DELETE {self.on_delete}")
        if self.on_update != "NO ACTION":
            clauses.append(f"ON UPDATE {self.on_update}")
        suffix = (" " + " ".join(clauses)) if clauses else ""
        return (
            f"ALTER TABLE {_q(TABLE)} ADD CONSTRAINT {_q(name)} "
            f"FOREIGN KEY ({cols}) REFERENCES {_q(self.ref_table)} ({ref_cols}){suffix}"
        )


def _job_id_foreign_keys(bind) -> List[_ForeignKey]:
    """`nav_sync_detail.job_id` 上的全部外键（不分目标表——便于识别意外状态）。"""
    rows = bind.execute(
        sa.text(
            "SELECT rc.CONSTRAINT_NAME, rc.REFERENCED_TABLE_NAME, "
            "       rc.UPDATE_RULE, rc.DELETE_RULE, "
            "       GROUP_CONCAT(k.COLUMN_NAME ORDER BY k.ORDINAL_POSITION) cols, "
            "       GROUP_CONCAT(k.REFERENCED_COLUMN_NAME ORDER BY k.ORDINAL_POSITION) ref_cols "
            "FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
            "JOIN information_schema.KEY_COLUMN_USAGE k "
            "  ON k.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA "
            " AND k.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
            " AND k.TABLE_NAME = rc.TABLE_NAME "
            "WHERE rc.CONSTRAINT_SCHEMA = DATABASE() "
            "  AND rc.TABLE_NAME = :table "
            "GROUP BY rc.CONSTRAINT_NAME, rc.REFERENCED_TABLE_NAME, "
            "         rc.UPDATE_RULE, rc.DELETE_RULE "
            "ORDER BY rc.CONSTRAINT_NAME"
        ),
        {"table": TABLE},
    ).all()
    return [
        _ForeignKey(
            name=name,
            key_columns=tuple(cols.split(",")),
            ref_table=ref_table,
            ref_columns=tuple(ref_cols.split(",")),
            on_update=on_update,
            on_delete=on_delete,
        )
        for name, ref_table, on_update, on_delete, cols, ref_cols in rows
        if tuple(cols.split(",")) == (COLUMN,)
    ]


def _duplicate_fk_groups(bind) -> List[Tuple[str, str, str, str, int]]:
    """全库「同表同列对指向同一目标」的重复外键分组（只读，供告警）。

    本 issue 已逐表核对为**仅** `nav_sync_detail.job_id` 一处；这里留一个每次执行本迁移
    都会跑的哨兵，让同类问题在新的库上现身时至少能在部署日志里被看见。已由本迁移处理的
    那一个列对不计入（它马上就会被收敛掉，报出来只会误导）。

    计数粒度必须是**约束条数**、不能是 JOIN 后的行数：一条复合外键在 JOIN 结果里占多行，
    直接 `COUNT(*)` 会把单条复合外键误判成重复（生产库有 4 条复合外键）。故内层先按约束名
    把复合键聚合成「一条约束一行」，外层的 `COUNT(*)` 才是条数。

    分两层也不是风格：外层要按「聚合后的列清单」分组，而 MySQL 不允许 `GROUP BY` 一个聚合
    别名（errno 1056 `Can't group on 'cols'`，实测踩过）；内层聚合之后，外层拿到的 `cols` /
    `ref_cols` 已经是派生表的普通列。
    """
    rows = bind.execute(
        sa.text(
            "SELECT t, rt, cols, ref_cols, COUNT(*) n FROM ("
            "  SELECT rc.TABLE_NAME t, rc.REFERENCED_TABLE_NAME rt, "
            "         GROUP_CONCAT(k.COLUMN_NAME ORDER BY k.ORDINAL_POSITION) cols, "
            "         GROUP_CONCAT(k.REFERENCED_COLUMN_NAME ORDER BY k.ORDINAL_POSITION) ref_cols "
            "  FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
            "  JOIN information_schema.KEY_COLUMN_USAGE k "
            "    ON k.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA "
            "   AND k.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
            "   AND k.TABLE_NAME = rc.TABLE_NAME "
            "  WHERE rc.CONSTRAINT_SCHEMA = DATABASE() "
            "  GROUP BY rc.CONSTRAINT_NAME, rc.TABLE_NAME, rc.REFERENCED_TABLE_NAME, "
            "           rc.UPDATE_RULE, rc.DELETE_RULE "
            ") fk_defs "
            "GROUP BY t, rt, cols, ref_cols "
            "HAVING COUNT(*) > 1 "
            "ORDER BY t"
        )
    ).all()
    return [
        (t, rt, cols, ref_cols, n)
        for t, rt, cols, ref_cols, n in rows
        if not (t == TABLE and cols == COLUMN)
    ]


def _converge_job_id_foreign_key(bind) -> None:
    """把 `nav_sync_detail.job_id` 收敛到「恰好一条、名为 TARGET_FK」的确定态。"""
    foreign_keys = _job_id_foreign_keys(bind)

    mismatched = [
        fk for fk in foreign_keys
        if fk.ref_table != REF_TABLE or fk.ref_columns != (REF_COLUMN,)
    ]
    if mismatched:
        # 同列指向别的目标属于完全意料之外的状态，不猜、不动，交给人处理
        raise RuntimeError(
            f"0016：{TABLE}.{COLUMN} 上存在指向非 {REF_TABLE}({REF_COLUMN}) 的外键 "
            f"{[(fk.name, fk.ref_table, fk.ref_columns) for fk in mismatched]}，中止"
        )

    rules = {(fk.on_delete, fk.on_update) for fk in foreign_keys}
    if len(rules) > 1:
        # 两条规则不同时无法判断该保留哪条语义 ⇒ 响亮失败，绝不静默挑一条
        raise RuntimeError(
            f"0016：{TABLE}.{COLUMN} 上多条外键的 ON DELETE/ON UPDATE 规则不一致 "
            f"{sorted(rules)}，需人工确认保留哪条，中止"
        )

    if len(foreign_keys) == 1 and foreign_keys[0].name == TARGET_FK:
        logger.info("0016 空转：%s.%s 上已是唯一且名为 %s 的外键", TABLE, COLUMN, TARGET_FK)
        return

    if not foreign_keys:
        # 外键语义整体缺失（比重复更严重）：按模型声明补建，孤儿行会让它响亮失败
        logger.warning(
            "0016：%s.%s 上没有任何外键，按模型声明补建 %s", TABLE, COLUMN, TARGET_FK
        )
        bind.execute(sa.text(
            f"ALTER TABLE {_q(TABLE)} ADD CONSTRAINT {_q(TARGET_FK)} "
            f"FOREIGN KEY ({_q(COLUMN)}) REFERENCES {_q(REF_TABLE)} ({_q(REF_COLUMN)})"
        ))
        return

    keeper = next((fk for fk in foreign_keys if fk.name == TARGET_FK), foreign_keys[0])
    dropped = [fk.name for fk in foreign_keys if fk.name != keeper.name]
    # 提升自动名（*_ibfk_N）到显式名需重建，此时连 keeper 一起拆——但只在**名字不对**时
    # 才这么做；名字已对（生产）时 keeper 全程不碰，把改动面压到最小。
    rebuild = keeper.name != TARGET_FK
    if rebuild:
        dropped.append(keeper.name)

    for name in dropped:
        bind.execute(sa.text(f"ALTER TABLE {_q(TABLE)} DROP FOREIGN KEY {_q(name)}"))
    logger.info("0016 已拆除 %s.%s 上的外键 %s", TABLE, COLUMN, dropped)

    if rebuild:
        bind.execute(sa.text(keeper.add_ddl(TARGET_FK)))
        logger.info("0016 已按显式名 %s 重建 %s.%s 的外键", TARGET_FK, TABLE, COLUMN)

    remaining = _job_id_foreign_keys(bind)
    if len(remaining) != 1 or remaining[0].name != TARGET_FK:
        raise RuntimeError(
            f"0016 收敛失败：{TABLE}.{COLUMN} 上现存外键 "
            f"{[fk.name for fk in remaining]}，期望恰好一条 {TARGET_FK}"
        )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        # SQLite 无 information_schema，也没有「同列对重复外键」这一现象（本地可直接跑）
        return

    for table, ref_table, cols, ref_cols, count in _duplicate_fk_groups(bind):
        logger.warning(
            "0016 哨兵：%s(%s) -> %s(%s) 上有 %d 条同列对外键（重复）。本迁移只收敛 "
            "%s.%s；此处刻意不自动处置——同列对重复不足以判定该保留哪条（两条的 "
            "ON DELETE/ON UPDATE 规则可能不同），误删即静默丢约束，需人工确认",
            table, cols, ref_table, ref_cols, count, TABLE, COLUMN,
        )

    _converge_job_id_foreign_key(bind)


def downgrade():
    # 刻意 no-op，别「顺手把冗余外键加回来」：
    # ① 本迁移是**缺陷修复**（#434），不是 schema 演进——那条冗余外键是历史事故的残留，
    #    与保留的那条语义完全相同（同列对、同规则），加回来不恢复任何功能，只会把
    #    「按名 drop 只删掉一条、另一条继续强制外键语义」这个陷阱重新埋回库里。
    # ② 与 0013 同型（采纳型迁移）：增量库里本迁移对**已存在**的库生效，全新库路径
    #    （`create_all` 已按模型显式名建出唯一一条）本就是 no-op，逆操作同样无物可还原。
    # ③ `ci.yml` 的 `alembic downgrade -1` 是真实执行路径，不能 raise——0006/0008 那种
    #    NotImplementedError 会直接把 CI 的迁移链验证弄红。
    pass
