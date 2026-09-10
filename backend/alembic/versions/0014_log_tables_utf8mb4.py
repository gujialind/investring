"""issue #427 四张日志表字符集 utf8mb3 → utf8mb4（4 字节字符致 errno 1366 整条记录静默丢失）

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-10

**为什么**：生产 RDS 与 CI 建库都用 utf8mb3（`utf8mb3_general_ci`，刻意对齐生产 RDS），
而连接侧字符集是 utf8mb4（`app/config.py` 连接串 `charset=utf8mb4`）。四张日志表
（`audit_log` / `system_error_log` / `login_log` / `task_execution_log`）建表时未指定表级
charset，继承库级 utf8mb3——一旦自由文本列出现 4 字节 UTF-8 字符（emoji、CJK 扩展 B
汉字等，典型来源是用户输入被异常文案回显），MySQL 严格模式即拒绝写入（errno 1366
`Incorrect string value`）。`record_system_error` 是 best-effort（写失败只记 stdout），
errno 1366 被吸收后**整条错误记录静默丢失**；`login_log` / `task_execution_log` 的写入
不是 best-effort，更是外抛 500、连带登录/任务执行本身失败。

**为什么改表而不改库级 charset**：库级 utf8mb3 是对齐生产 RDS 的刻意约定（`ci.yml`
三处建库语句的注释），本迁移**不动库级设置**，只在库内把这四张日志表单独提到 utf8mb4。
刻意排除的另一条路是「写入侧对 4 字节字符做转义/替换」——那需要维护「哪些列要转义」
的清单，遗漏面不可见，且改变取证文本原貌。

**为什么还要模型显式声明**：本迁移在 MySQL 上把存量表转码，但 SQLite 不支持
`ALTER TABLE ... CONVERT TO CHARACTER SET`，且任何**新建库**（新环境、CI 重建）都由
`create_all` 先建表——只靠迁移的话新库会重新生成 utf8mb3 表、缺陷原样复发。故四个模型
的 `__table_args__` 同时带 `mysql_charset` / `mysql_collate`（取自
`app.constants.log_charset`），使 create_all 建出的表不依赖库级默认。单一事实来源是
`app/constants/log_charset.py`，由 `tests/unit/test_migration_0014.py` 守门。

**幂等设计**：已是 utf8mb4 的表再执行 `CONVERT` 是无操作（新库路径：create_all 已按模型
建出 utf8mb4 → 本迁移空转），重复执行无副作用。SQLite 直接 return——无字符集概念，且
`CONVERT TO CHARACTER SET` 不是合法 SQL（本地 SQLite 测不出该缺陷，CI MySQL job 才是
真实验收面）。

**downgrade 语义（为什么不是无脑反向 CONVERT）**：反向转 utf8mb3 在表内已存在 4 字节
字符时必然失败（数据无法表示），会让 `docs/runbooks/deploy-rollback.md` 的
`alembic downgrade` 这条真实运维路径半途而废。故先逐表探测是否存在 4 字节字符
（`LENGTH(col) - 3 * CHAR_LENGTH(col) > 0` 在 utf8mb3/utf8mb4 表下都是精确探测：4 字节
字符贡献 +1，3 字节中文贡献 0），有则**跳过该表并打 WARNING**——不假装成功、不吞数据、
也不阻断回滚；无则正常反向 CONVERT。CI 的 `alembic downgrade -1` 往返在空库上因此必然
可逆（`SKIP_DOWNGRADE` 无需豁免）。

**不纳入本迁移**：`nav_sync_detail`（同库 utf8mb3、`error_message` String(500) 同样接收
外部接口原文）不在 issue #427 的四表范围内，其失败形态也不同（外抛而非静默丢日志）——
按 `backend/AGENTS.md` §2 影响面约定另行评估，见 #427 讨论。
"""
import logging

from alembic import op
import sqlalchemy as sa

from app.constants.log_charset import LOG_TABLE_CHARSET, LOG_TABLE_COLLATE, LOG_TABLES

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# 4 字节 UTF-8 字符探测：utf8mb3/utf8mb4 表下均精确（4 字节字符 LENGTH-3*CHAR_LENGTH=+1，
# 3 字节中文与 ASCII 均为 0）。不用 information_schema——那要求当前库名与权限，且 CHAR_LENGTH
# 一类的歧义（NULL/空串）还要另作处理。
_FOUR_BYTE_PROBE = (
    "SELECT COUNT(*) FROM {table} WHERE LENGTH({column}) - 3 * CHAR_LENGTH({column}) > 0"
)


def _four_byte_columns(bind, table_name: str):
    """该表文本列中实际出现 4 字节字符的列名列表（无则空）。"""
    inspector = sa.inspect(bind)
    hits = []
    for column in inspector.get_columns(table_name):
        if isinstance(column["type"], (sa.Text, sa.String)):
            probe = _FOUR_BYTE_PROBE.format(table=table_name, column=column["name"])
            if bind.execute(sa.text(probe)).scalar():
                hits.append(column["name"])
    return hits


def _convert(bind, table_name: str, charset: str, collate: str) -> None:
    bind.execute(
        sa.text(f"ALTER TABLE {table_name} CONVERT TO CHARACTER SET {charset} COLLATE {collate}")
    )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        # SQLite 无字符集概念，且 CONVERT TO CHARACTER SET 不是合法 SQL
        return

    for table_name in LOG_TABLES:
        _convert(bind, table_name, LOG_TABLE_CHARSET, LOG_TABLE_COLLATE)
        logger.info("0014 已把 %s 转为 %s", table_name, LOG_TABLE_CHARSET)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    for table_name in LOG_TABLES:
        hits = _four_byte_columns(bind, table_name)
        if hits:
            # 数据已含 4 字节字符，转回 utf8mb3 必失败（errno 1366，数据无法表示）。
            # 跳过而非半途而废：deploy-rollback 的 downgrade 是真实运维路径。
            logger.warning(
                "0014 downgrade 跳过 %s：列 %s 已含 4 字节字符，无法转回 %s",
                table_name,
                ", ".join(hits),
                "utf8mb3",
            )
            continue
        _convert(bind, table_name, "utf8mb3", "utf8mb3_general_ci")
        logger.info("0014 downgrade 已把 %s 转回 utf8mb3", table_name)
