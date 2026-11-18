"""台账型组合市值台账与本金进出表（#905 PR2）。

新增 ledger_entry（平台市值台账，每平台每日一行绝对市值，唯一键 portfolio+platform+value_date）
与 ledger_flow（本金进出流水，同平台同日可多笔）。两表与净值型三表完全隔离（D1），
不被现金/在途/快照口径消费。可 update/delete（D5），无 ORM 禁改 hook。

幂等设计：create_table 前 _has_table 守卫（兼容 create_all 目标态，同 0027）。
降级条件拒绝（仿 0019/0022/0028）：任一表有行即 RuntimeError（台账是账务数据，
删表销毁记账事实），拒绝发生在任何 DROP 之前；两表均空则 drop_table 完整可逆。
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return name in sa.inspect(bind).get_table_names()


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "ledger_entry"):
        op.create_table(
            "ledger_entry",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("portfolio_code", sa.String(20), nullable=False),
            sa.Column("platform_code", sa.String(20), nullable=False),
            sa.Column("value_date", sa.Date(), nullable=False),
            sa.Column("market_value", sa.Numeric(15, 4), nullable=False),
            sa.Column("asset_kind", sa.String(20), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(50), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["portfolio_code"], ["portfolio.code"], name="fk_ledger_entry_portfolio"),
            sa.ForeignKeyConstraint(["platform_code"], ["platform.code"], name="fk_ledger_entry_platform"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "portfolio_code", "platform_code", "value_date",
                name="uq_ledger_entry_portfolio_platform_date",
            ),
        )
    if not _has_table(bind, "ledger_flow"):
        op.create_table(
            "ledger_flow",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("portfolio_code", sa.String(20), nullable=False),
            sa.Column("platform_code", sa.String(20), nullable=False),
            sa.Column("flow_date", sa.Date(), nullable=False),
            sa.Column("direction", sa.String(10), nullable=False),
            sa.Column("amount", sa.Numeric(15, 2), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(50), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["portfolio_code"], ["portfolio.code"], name="fk_ledger_flow_portfolio"),
            sa.ForeignKeyConstraint(["platform_code"], ["platform.code"], name="fk_ledger_flow_platform"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    bind = op.get_bind()
    entry_exists = _has_table(bind, "ledger_entry")
    flow_exists = _has_table(bind, "ledger_flow")
    if not entry_exists and not flow_exists:
        return
    for name, exists in (("ledger_entry", entry_exists), ("ledger_flow", flow_exists)):
        if exists:
            tbl = sa.table(name, sa.column("id"))
            if bind.execute(sa.select(sa.literal(1)).select_from(tbl).limit(1)).first() is not None:
                raise RuntimeError(f"拒绝降级：{name} 存在台账记录（账务数据）；未修改任何数据或表结构")
    if flow_exists:
        op.drop_table("ledger_flow")
    if entry_exists:
        op.drop_table("ledger_entry")