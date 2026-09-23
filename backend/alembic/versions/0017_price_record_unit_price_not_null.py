"""price_record.unit_price 收口为 NOT NULL（issue #580）

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-23

**缺陷**：`price_record.unit_price` 自建表起就可空，而写入侧把数据源缺值直写成 NULL
（`market_data_service._bulk_upsert_prices` 原样透传 `_normalize_raw` 的 `r.get(...)`）。
NULL 单价不是「这一天的价格未知」的中性记录，它是一颗雷：

- 读取侧 `market_data.py` 的 `float(r.unit_price)` 对 None 抛 TypeError，被 catch-all
  吞成 500（#553 前连日志都没有，事后只剩一句「查询价格数据失败」）；
- 快照取价把 NULL 行当成「这一天有价」，`nav_coverage` 据此误计为已覆盖——比缺一行
  更糟：缺行会被「盘点不一致」抓出来，NULL 行不会，它让基于它的计算**算错**。

**修复两处**：写入侧滤掉无价行（`_usable_price`）为第一道防线，本迁移清存量 NULL +
列改 NOT NULL 为第二道——只有两者齐备，「单价可空」这件事才真正成为历史。

**为什么是 DELETE 而不是 UPDATE 成 0 / 某个兜底值**：给未知价格填一个数字等于把「不知道」
伪装成「知道」，快照会据此算出错误的净值且在临床/UAT 上完全看不出来。这一行本来就没
有可用信息，删掉它让缺口显性——`nav_coverage` 会如实报这天没价。

**不可逆**：已删的行无从恢复（`downgrade` 只把列改回可空）。**回滚前必须有备份**；
本迁移因此先打印将被删除的行数与涉及的组合，留下可核对的凭据。

Update Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLE = "price_record"
COLUMN = "unit_price"


def upgrade():
    conn = op.get_bind()

    # 先清点，给这次不可逆删除留一份可执行前核对的记录
    affected = conn.execute(
        sa.text(
            f"SELECT product_code, market, COUNT(*) FROM {TABLE} "
            f"WHERE {COLUMN} IS NULL GROUP BY product_code, market"
        )
    ).fetchall()
    total = sum(row[2] for row in affected)
    print(f"[0017] 将删除 {total} 行无单价的 {TABLE} 记录")
    for product_code, market, count in affected:
        print(f"[0017]   {product_code} / {market}: {count} 行")

    # 必须先删再改列：MySQL 在 strict mode 关闭时碰到现有 NULL 会把它们静默转成 0，
    # 那正是本迁移要消灭的形态（未知的伪装成已知的）。
    conn.execute(sa.text(f"DELETE FROM {TABLE} WHERE {COLUMN} IS NULL"))

    if conn.dialect.name == "mysql":
        # MySQL 原生支持 MODIFY，无需重建表
        conn.execute(
            sa.text(f"ALTER TABLE {TABLE} MODIFY {COLUMN} NUMERIC(10, 4) NOT NULL")
        )
    else:
        # SQLite 不支持 ALTER COLUMN，走 Alembic 的建新表-搬数据-重命名
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column(COLUMN, existing_type=sa.Numeric(10, 4), nullable=False)


def downgrade():
    conn = op.get_bind()

    if conn.dialect.name == "mysql":
        conn.execute(sa.text(f"ALTER TABLE {TABLE} MODIFY {COLUMN} NUMERIC(10, 4) NULL"))
    else:
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column(COLUMN, existing_type=sa.Numeric(10, 4), nullable=True)

    # 被删除的 NULL 行无法从数据库本身恢复——只能从备份捞回来。
    print("[0017] 已删除的无单价行不可由本迁移恢复，如需还原请从备份中取回")
