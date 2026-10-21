"""份额变动事件的再投资份额确认日（#743）。

允许「权益登记日 = 除息日」的场外分红：新增可空 `share_confirm_date`，
份额生效日由 `ShareChangeEvent.share_effective_date` 单点派生
（reinvest 取 coalesce(share_confirm_date, ex_date)，其余取 ex_date）。

列判存在，兼容 create_all 目标态；SQLite/MySQL 共用，无数据回填。
降级先检查是否已有非空值，命中即在任何 DDL 之前 RuntimeError（仿 0019）：
该列一旦被使用，删列等于销毁「份额在哪一天到账」的记账事实，走前滚或快照。
"""

from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TABLE = "share_change_event"
COLUMN = "share_confirm_date"


def _has_column(bind):
    return any(col["name"] == COLUMN for col in sa.inspect(bind).get_columns(TABLE))


def upgrade():
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(TABLE, sa.Column(COLUMN, sa.Date(), nullable=True))


def downgrade():
    bind = op.get_bind()
    if not _has_column(bind):
        return
    events = sa.table(TABLE, sa.column(COLUMN))
    if bind.execute(
        sa.select(sa.literal(1)).select_from(events)
        .where(events.c[COLUMN].is_not(None)).limit(1)
    ).first() is not None:
        raise RuntimeError("拒绝降级：存在非空 share_confirm_date；未修改任何数据或表结构")
    op.drop_column(TABLE, COLUMN)
