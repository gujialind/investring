"""product.is_active：产品停用标记（#856）

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-10

产品清盘/暂停后无「停用」手段，仍可新建买入/卖出。本迁移加 NOT NULL 布尔列
is_active（server_default 1），存量行由 ADD COLUMN 的默认值回填为 true。
停用只挡 create_trade（PRODUCT_INACTIVE），存量待确认记录、份额事件、快照估值
与净值同步不受影响；虚拟产品（CASH/IN_TRANSIT）禁止停用（product_service 守卫）。

可逆：downgrade 直接删列。列是生命周期标记（无外键引用、无账务下游），删列即回到
「全部 active」的旧行为，无数据被销毁。

幂等：pytest 会话以 Base.metadata create_all 建库（已含本列），_has_column 守卫跳过
（仿 0021）。自包含：不 import app 代码（#128 教训）。
"""
from alembic import op
import sqlalchemy as sa


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _has_column(bind) -> bool:
    return "is_active" in {
        col["name"] for col in sa.inspect(bind).get_columns("product")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(
            "product",
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")
            ),
        )


def downgrade() -> None:
    op.drop_column("product", "is_active")
