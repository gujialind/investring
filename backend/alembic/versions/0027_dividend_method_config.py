"""分红方式配置表 dividend_method_config + CN_OTC 存量 default 回填（#887，SPEC §3.3）。

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-16

append-only 生效区间表，键 (组合, 产品, 市场, 平台, effective_from) 唯一。upgrade 为各组合
最新快照日 portfolio_position 中 shares>0、platform_code 非 NULL、market='CN_OTC' 的三元组
回填 source='default'、值 reinvest_dividend、effective_from=迁移执行日（R2/D6）。default 行
仅预选、零账务影响（A9）；HK_MUTUAL 不回填、CN_EXCHANGE 排除（R10/A14，market 过滤天然排除
虚拟产品）。

可逆（条件性拒绝降级，零写入整体中止，0019/0022/0025 同族）：downgrade 先探测是否存在任何
source='explicit' 行——用户显式声明是记账事实，命中即在任何 DELETE/DROP 之前 RuntimeError；
仅 default 行（或空表）→ 删行 drop 表，完整可逆。事件升降级均不触碰（A10）；配置期 typed
事件在旧代码下仍是合法事件，故不加「存在配置期自动事件即拒绝」守卫（过严会永久关闭回退路径）。

幂等：_has_table 守卫建表；回填按唯一键预去重，重复执行不新增行。双方言（SQLite/MySQL）：
回填用 Core select + bulk_insert，无方言分支。自包含：不 import app 代码（#128 教训）；
字符集与市场白名单硬编码并注释与服务常量同步（db_charset.DB_CHARSET / DB_COLLATE、
dividend_config_service.ALLOWED_CONFIG_MARKETS）。
"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

TABLE = "dividend_method_config"
# 与 app/constants/db_charset.py 同步（迁移自包含，不 import app 代码）。
CHARSET = "utf8mb4"
COLLATE = "utf8mb4_general_ci"
# 与 dividend_config_service.ALLOWED_CONFIG_MARKETS 同步：配置只覆盖 CN_OTC（R10/A14）。
BACKFILL_MARKET = "CN_OTC"
DEFAULT_METHOD = "reinvest_dividend"

CONFIG = sa.table(
    TABLE,
    sa.column("id", sa.Integer()),
    sa.column("portfolio_code", sa.String(20)),
    sa.column("product_code", sa.String(20)),
    sa.column("market", sa.String(20)),
    sa.column("platform_code", sa.String(20)),
    sa.column("dividend_method", sa.String(20)),
    sa.column("effective_from", sa.Date()),
    sa.column("source", sa.String(10)),
)
POSITION = sa.table(
    "portfolio_position",
    sa.column("portfolio_code", sa.String(20)),
    sa.column("product_code", sa.String(20)),
    sa.column("market", sa.String(20)),
    sa.column("platform_code", sa.String(20)),
    sa.column("shares", sa.Numeric(15, 2)),
    sa.column("snapshot_date", sa.Date()),
)


def _has_table(bind) -> bool:
    return sa.inspect(bind).has_table(TABLE)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("portfolio_code", sa.String(20), nullable=False),
            sa.Column("product_code", sa.String(20), nullable=False),
            sa.Column("market", sa.String(20), nullable=False),
            sa.Column("platform_code", sa.String(20), nullable=False),
            sa.Column("dividend_method", sa.String(20), nullable=False),
            sa.Column("effective_from", sa.Date(), nullable=False),
            sa.Column("source", sa.String(10), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["portfolio_code"], ["portfolio.code"],
                name="fk_dividend_config_portfolio", ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["platform_code"], ["platform.code"],
                name="fk_dividend_config_platform", ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["product_code", "market"], ["product.code", "product.market"],
                name="fk_dividend_config_product", ondelete="RESTRICT",
            ),
            sa.UniqueConstraint(
                "portfolio_code", "product_code", "market", "platform_code", "effective_from",
                name="uq_dividend_config_key_from",
            ),
            mysql_charset=CHARSET, mysql_collate=COLLATE,
        )

    # 回填存量 default 行：各组合最新快照日的 CN_OTC 有效持仓三元组（R2/D6/A9）。
    # 业务日按 Asia/Shanghai（UTC+8）取当日，与服务侧 business_today() 同口径；
    # 进程容器为 UTC，date.today() 会在 CST 00:00–08:00 早一天（S3）。迁移自包含，
    # 不 import app 代码，故此处显式按 UTC+8 计算。
    effective_from = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    latest = (
        sa.select(
            POSITION.c.portfolio_code,
            sa.func.max(POSITION.c.snapshot_date).label("max_date"),
        )
        .group_by(POSITION.c.portfolio_code)
        .subquery()
    )
    candidates = bind.execute(
        sa.select(
            POSITION.c.portfolio_code, POSITION.c.product_code,
            POSITION.c.market, POSITION.c.platform_code,
        )
        .join(
            latest,
            sa.and_(
                POSITION.c.portfolio_code == latest.c.portfolio_code,
                POSITION.c.snapshot_date == latest.c.max_date,
            ),
        )
        .where(
            POSITION.c.shares > 0,
            POSITION.c.platform_code.is_not(None),
            POSITION.c.market == BACKFILL_MARKET,
        )
        .distinct()
    ).all()
    existing = {
        (row[0], row[1], row[2], row[3])
        for row in bind.execute(
            sa.select(
                CONFIG.c.portfolio_code, CONFIG.c.product_code,
                CONFIG.c.market, CONFIG.c.platform_code,
            )
        )
    }
    rows = [
        {
            "portfolio_code": r[0], "product_code": r[1], "market": r[2],
            "platform_code": r[3], "dividend_method": DEFAULT_METHOD,
            "effective_from": effective_from, "source": "default",
        }
        for r in candidates
        if (r[0], r[1], r[2], r[3]) not in existing
    ]
    if rows:
        op.bulk_insert(CONFIG, rows)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    # 探测先行、零写入整体中止：用户显式声明不可销毁（R3，0019/0022/0025 同族）。
    if bind.execute(
        sa.select(sa.literal(1)).select_from(CONFIG)
        .where(CONFIG.c.source == "explicit").limit(1)
    ).first() is not None:
        raise RuntimeError(
            "0027 拒绝降级：存在 source='explicit' 的分红方式配置行；用户显式声明是记账事实，"
            "不能销毁；本迁移未修改任何数据或表结构，请前滚或另获授权处置"
        )
    # 仅 default 行（或空表）：删行 drop 表，完整可逆；不触碰任何事件（A10）。
    bind.execute(CONFIG.delete())
    op.drop_table(TABLE)