"""product.hk_sale_code：港互认 6 位内地销售代码入库（#705）

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-14

`product.code` 是取数/主键口径（港互认存 10 位香港基金代码），用户在销售渠道
认知的是 6 位内地销售代码（968050）。展示码若运行时派生，读路径就要引东财
`fund_hk_rank_em` 的 190 只清单（覆盖率不保证、读侧带外部调用），故 #705 选定
**入库**：本迁移加可空列并回填存量，读侧 `display_code()` 只读列、映射不可得
回退原码。

存量回填用**静态映射**而非迁移期调 `_ensure_hk_code_map`：迁移必须离线可跑
（CI 空库往返、部署回滚都不许带网络依赖），且映射来源是 2026-10-13 实测
`fund_hk_rank_em`（190 只双向唯一、无碰撞；本库两只港基均可解析）。清单外的
存量产品保持 NULL，由读侧回退原码——与「映射不可得不报错不空码」同口径。
新建/纠错路径的 6 位↔10 位解析走运行时映射（product_service），不走本迁移。

可逆：downgrade 直接删列。列是纯展示派生源（无账务下游、无外键引用），
丢的值可由重跑本迁移或录入路径恢复。

自包含：不 import app 代码（#128 教训，同 0007 头注释惯例）。
"""
import logging

from alembic import op
import sqlalchemy as sa


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# 10 位香港基金代码 → 6 位内地销售代码（2026-10-13 实测 fund_hk_rank_em）
HK_SALE_CODE_BY_HK_CODE = {
    "1001767344": "968050",
    "1001767346": "968052",
}


def _has_column(bind) -> bool:
    # pytest 会话以 Base.metadata create_all 建库（已含本列），迁移须幂等跳过
    # （0019 同型守卫）
    return "hk_sale_code" in {
        col["name"] for col in sa.inspect(bind).get_columns("product")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(
            "product",
            sa.Column("hk_sale_code", sa.String(length=10), nullable=True),
        )
    for hk_code, sale_code in HK_SALE_CODE_BY_HK_CODE.items():
        # bind.execute（非 op.execute）才拿得到 CursorResult，rowcount 才有值可记
        result = bind.execute(
            sa.text(
                "UPDATE product SET hk_sale_code = :sale "
                "WHERE code = :code AND market = 'HK_MUTUAL'"
            ).bindparams(sale=sale_code, code=hk_code)
        )
        logger.info(
            "0021 backfill hk_sale_code=%s for %s: rowcount=%s",
            sale_code, hk_code, result.rowcount,
        )


def downgrade() -> None:
    op.drop_column("product", "hk_sale_code")
