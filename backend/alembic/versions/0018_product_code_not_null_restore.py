"""issue #537 迁移 0018：恢复 6 张表 product_code 的 NOT NULL（修复 0006 漂移）

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-25

背景（真库实测发现，非推测）：0006 对 product_code 执行
`alter_column(type_=String(20))` 时未传 `nullable`，alembic 在 MySQL 上生成
`MODIFY col VARCHAR(20) NULL`——MODIFY 整体替换列定义，create_all 建出的
NOT NULL 被静默剥离。自 0006 上线（2026-08）起，每个经「create_all → upgrade」
初始化的 MySQL 库（含生产）这 6 列都可空。旧的隐式初始化不做列级比对，漂移
长期未被发现；#537 显式 bootstrap 按模型（nullable=False）比对后如实报
schema_incomplete，prepare 拒绝把漂移状态当作已准备。

修复方式（计划授权口径：确需新增迁移时先明确原因与范围）：
- 不重写历史迁移 0006：已运行它的库无法重放，且重写会改变迁移内容指纹；
- 前滚恢复：对 6 列 `MODIFY ... VARCHAR(20) NOT NULL`（类型不变，仅收紧约束，
  索引/外键/utf8mb4 表默认字符集均不受影响）；
- **NULL 行不静默删除、不回填**：product_code 是业务键成员与复合外键引用列，
  出现 NULL 属数据异常，迁移直接 RuntimeError 保留现场，交人工处置后重跑；
- SQLite 不运行 MySQL 迁移（#537 显式初始化对 SQLite 只建模型表）；测试程序化
  执行场景走 batch 重建分支，失败容忍，与 0004/0006 同策略。

范围钉死为 6 列（product.code 为主键本已 NOT NULL 且 0006 已显式传参；
share_change_event.cash_product_code 模型可空，不在恢复范围）：
  portfolio_position.product_code      trade.product_code
  price_record.product_code            manual_market_value.product_code
  nav_sync_detail.product_code         share_change_event.product_code

downgrade：恢复可空（放宽约束，无损）。
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


PRODUCT_CODE_COLUMNS = [
    ('portfolio_position', 'product_code'),
    ('trade', 'product_code'),
    ('price_record', 'product_code'),
    ('manual_market_value', 'product_code'),
    ('nav_sync_detail', 'product_code'),
    ('share_change_event', 'product_code'),
]


def _sqlite_batch(nullable):
    for table, col in PRODUCT_CODE_COLUMNS:
        try:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(col, existing_type=sa.String(20), nullable=nullable)
        except Exception:
            print(f"WARN: {table}.{col} nullable={nullable} 在 SQLite 上跳过（不强制列约束重建）")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'mysql':
        _sqlite_batch(False)
        return
    # 两段式：先全量核查 NULL（任一违规则零 DDL 改动、保留现场），再统一收紧
    violations = []
    for table, col in PRODUCT_CODE_COLUMNS:
        # 表/列名来自本文件白名单常量，无注入面
        nulls = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` IS NULL")).scalar()
        if nulls:
            violations.append(f"{table}.{col}={nulls} 行")
    if violations:
        raise RuntimeError(
            f"存在 NULL product_code（{'；'.join(violations)}）；该列为业务键/外键成员，"
            "拒绝自动回填或删除。请人工核对来源并清理后重跑迁移（本次未做任何 DDL）")
    for table, col in PRODUCT_CODE_COLUMNS:
        bind.execute(sa.text(f"ALTER TABLE `{table}` MODIFY `{col}` VARCHAR(20) NOT NULL"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'mysql':
        _sqlite_batch(True)
        return
    for table, col in PRODUCT_CODE_COLUMNS:
        bind.execute(sa.text(f"ALTER TABLE `{table}` MODIFY `{col}` VARCHAR(20) NULL"))
