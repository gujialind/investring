"""组合类型与台账型归属投资人（#905 PR1）。

portfolio 新增两列：
- kind：组合类型 nav（净值型，默认/存量回填）/ ledger（平台市值台账型），NOT NULL。
- owner_investor_code：台账型组合级归属投资人（可空 FK → investor.code）；
  ledger 必填、nav 禁填，应用侧校验见 portfolio_service.create_portfolio。

幂等设计：add_column 前用 sa.inspect 探列存在（兼容 create_all 目标态，同 0022），
外键经 batch_alter_table 显式命名 fk_portfolio_owner_investor_code（#434 规范名，
双方言 SQLite/MySQL 共用）。

降级条件拒绝（仿 0019/0022，零写入整体中止）：kind 一旦出现 'ledger' 行、或
owner_investor_code 出现非空值，即意味着台账型组合已被使用，删列等于销毁类型/归属
事实，故在任何 DDL 之前 RuntimeError；未被使用的库（含 CI 空库往返）完整可逆。
"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

TABLE = "portfolio"
FK_NAME = "fk_portfolio_owner_investor_code"


def _columns(bind):
    return {col["name"] for col in sa.inspect(bind).get_columns(TABLE)}


def upgrade():
    bind = op.get_bind()
    cols = _columns(bind)
    if "kind" in cols and "owner_investor_code" in cols:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "kind" not in cols:
            batch.add_column(
                sa.Column("kind", sa.String(20), nullable=False, server_default="nav")
            )
        if "owner_investor_code" not in cols:
            batch.add_column(sa.Column("owner_investor_code", sa.String(20), nullable=True))
            batch.create_foreign_key(FK_NAME, "investor", ["owner_investor_code"], ["code"])


def downgrade():
    bind = op.get_bind()
    cols = _columns(bind)
    if "kind" not in cols and "owner_investor_code" not in cols:
        return
    portfolio = sa.table(TABLE, sa.column("kind"), sa.column("owner_investor_code"))
    if "kind" in cols:
        used = bind.execute(
            sa.select(sa.literal(1)).select_from(portfolio)
            .where(portfolio.c.kind == "ledger").limit(1)
        ).first()
        if used is not None:
            raise RuntimeError("拒绝降级：存在台账型组合（kind='ledger'）；未修改任何数据或表结构")
    if "owner_investor_code" in cols:
        used = bind.execute(
            sa.select(sa.literal(1)).select_from(portfolio)
            .where(portfolio.c.owner_investor_code.is_not(None)).limit(1)
        ).first()
        if used is not None:
            raise RuntimeError("拒绝降级：存在归属投资人（owner_investor_code 非空）；未修改任何数据或表结构")
    with op.batch_alter_table(TABLE) as batch:
        if "owner_investor_code" in cols:
            batch.drop_constraint(FK_NAME, type_="foreignkey")
            batch.drop_column("owner_investor_code")
        if "kind" in cols:
            batch.drop_column("kind")