"""issue #427 六张日志/同步明细表字符集 utf8mb3 → utf8mb4（4 字节字符致 errno 1366 整条记录写不进去）

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-10

**为什么**：生产 RDS 与 CI 建库都用 utf8mb3（`utf8mb3_general_ci`，刻意对齐生产 RDS），
而连接侧字符集是 utf8mb4（`app/config.py` 连接串 `charset=utf8mb4`）。这六张表
（`audit_log` / `system_error_log` / `login_log` / `task_execution_log` / `nav_sync_detail`）
建表时未指定表级 charset，继承库级 utf8mb3——一旦自由文本列出现 4 字节 UTF-8 字符
（emoji、CJK 扩展 B 汉字等，典型来源是用户输入或外部数据源原文被回显），MySQL 严格模式
即拒绝写入（errno 1366 `Incorrect string value`）。失效形态分两档：`record_system_error`
是 best-effort（写失败只记 stdout），errno 1366 被吸收后**整条错误记录静默丢失**；
`login_log` / `task_execution_log` / `nav_sync_detail` 的写入不是 best-effort，更是外抛
500、连带登录、任务执行与净值同步本身失败。

**为什么改表而不改库级 charset**：库级 utf8mb3 是对齐生产 RDS 的刻意约定（`ci.yml`
三处建库语句的注释），本迁移**不动库级设置**，只在库内把这些表单独提到 utf8mb4。
刻意排除的另一条路是「写入侧对 4 字节字符做转义/替换」——那需要维护「哪些列要转义」
的清单，遗漏面不可见，且改变取证文本原貌。

**为什么还要模型显式声明**：本迁移在 MySQL 上把存量表转码，但 SQLite 不支持
`ALTER TABLE ... CONVERT TO CHARACTER SET`，且任何**新建库**（新环境、CI 重建）都由
`create_all` 先建表——只靠迁移的话新库会重新生成 utf8mb3 表、缺陷原样复发。故五个模型的
`__table_args__` 同时带 `mysql_charset` / `mysql_collate`（取自
`app.constants.log_charset`），使 create_all 建出的表不依赖库级默认。纳管清单与常量是
`CHARSET_TABLES` / `LOG_TABLE_CHARSET` / `LOG_TABLE_COLLATE`，由
`tests/unit/test_migration_0014.py` 守三方一致。

**幂等设计**：已是 utf8mb4 的表再执行 `CONVERT` 是无操作（新库路径：create_all 已按模型
建出 utf8mb4 → 本迁移空转），重复执行无副作用。SQLite 直接 return——无字符集概念，且
`CONVERT TO CHARACTER SET` 不是合法 SQL（本地 SQLite 测不出该缺陷，CI MySQL job 才是
真实验收面）。逐表带 `has_table()` 守卫：`nav_sync_detail` 在 alembic 链里只被增量改列
（0001/0006），其建表同样靠 `create_all`，理论上缺失时不应让整条迁移链失败。

**downgrade 语义（为什么不是无脑反向 CONVERT）**：反向转 utf8mb3 在表内已存在 4 字节
字符时必然失败（数据无法表示），会让 `docs/runbooks/deploy-rollback.md` 的
`alembic downgrade` 这条真实运维路径半途而废。故先逐表探测是否存在 4 字节字符
（`CONVERT(col USING utf8mb3) <> BINARY col`：utf8mb3 表示不了的字符转换后必然与原值不同），
有则**跳过该表并打 WARNING**——不假装成功、不吞数据、
也不阻断回滚；无则正常反向 CONVERT。CI 的 `alembic downgrade -1` 往返在空库上因此必然
可逆（`SKIP_DOWNGRADE` 无需豁免）。
"""
import logging

from alembic import op
import sqlalchemy as sa

from app.constants.log_charset import CHARSET_TABLES, LOG_TABLE_CHARSET, LOG_TABLE_COLLATE

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# 4 字节 UTF-8 字符探测：把列值按 utf8mb3 重新解释，与原值做**二进制**比较——只要出现任何
# utf8mb3 表示不了的字符（即 4 字节字符），转换结果就与原值不同。
#
# 为什么不用 `LENGTH(col) - 3 * CHAR_LENGTH(col) > 0`：那套写法依赖「CHAR_LENGTH 返回字符数」
# 这一函数语义，一旦某个 MySQL 版本/配置让它按字节计数，差值恒为 0、探测静默失效（守卫形同
# 虚设，且没有别的用例看得出来）。`CONVERT(... USING utf8mb3)` + BINARY 比较只依赖
# 「转换保留可表示字符、其余变替代符」这一稳定行为，不依赖长度函数口径。
#
# BINARY 关键字是必须的：MySQL 字符串比较默认按 collation 做，替代符 `?` 在 *_ci 下仍可能
# 被判等，加 BINARY 退化为逐字节比较。
_FOUR_BYTE_PROBE = (
    "SELECT COUNT(*) FROM {table} WHERE CONVERT({column} USING utf8mb3) <> BINARY {column}"
)


def _four_byte_columns(bind, table_name: str):
    """该表文本列中实际出现 4 字节字符的列名列表（无则空）。

    `COUNT(*)` 返回 0 是合法结果（该列没有 4 字节字符），故判据写 `(count or 0) > 0`
    而非真值判断——后者会把 0 与 NULL 都当假，语义上不表达「计数为正」。

    探测语句（`CONVERT(... USING utf8mb3)`）**不保证**在被探测内容不可表示时返回替代符：
    若服务端选择抛错（errno 1366），本函数按「无法证明可安全回退」处理——记一条 WARNING
    并把该表交由调用方跳过，绝不假设它能无损转回 utf8mb3。
    """
    inspector = sa.inspect(bind)
    hits = []
    for column in inspector.get_columns(table_name):
        if isinstance(column["type"], (sa.Text, sa.String)):
            probe = _FOUR_BYTE_PROBE.format(table=table_name, column=column["name"])
            try:
                count = bind.execute(sa.text(probe)).scalar()
            except sa.exc.SQLAlchemyError as exc:
                logger.warning(
                    "0014 探测 %s.%s 失败（%s），按「不可安全回退」处理",
                    table_name,
                    column["name"],
                    exc.__class__.__name__,
                )
                hits.append(column["name"])
                continue
            if (count or 0) > 0:
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

    inspector = sa.inspect(bind)
    for table_name in CHARSET_TABLES:
        if not inspector.has_table(table_name):
            # 这些表的建表都靠 create_all，理论上必然存在；缺失时响亮告警而非静默跳过，
            # 因为漏转一张就留一处 errno 1366 面（正常路径不会走到这里）
            logger.warning("0014 跳过 %s：表不存在（建表依赖 create_all）", table_name)
            continue
        _convert(bind, table_name, LOG_TABLE_CHARSET, LOG_TABLE_COLLATE)
        logger.info("0014 已把 %s 转为 %s", table_name, LOG_TABLE_CHARSET)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    inspector = sa.inspect(bind)
    for table_name in CHARSET_TABLES:
        if not inspector.has_table(table_name):
            logger.warning("0014 downgrade 跳过 %s：表不存在", table_name)
            continue
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
