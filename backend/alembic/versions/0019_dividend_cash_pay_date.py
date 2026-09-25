"""现金分红到账日与分红在途产品（#522）。

列和种子分别判存在，兼容 create_all 目标态；SQLite/MySQL 共用。
降级先检查日期数据与实际外键引用，全部通过后才删除种子和列。
"""

from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TABLE = "share_change_event"
COLUMN = "cash_pay_date"
PRODUCT_CODE = "IN_TRANSIT_DIVIDEND"


def _has_column(bind):
    return any(col["name"] == COLUMN for col in sa.inspect(bind).get_columns(TABLE))


def _product_table(bind):
    return sa.Table("product", sa.MetaData(), autoload_with=bind, resolve_fks=False)


def _seed_key(product):
    return sa.and_(product.c.code == PRODUCT_CODE, product.c.market == "")


def upgrade():
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(TABLE, sa.Column(COLUMN, sa.Date(), nullable=True))
    product = _product_table(bind)
    if bind.execute(sa.select(product.c.code).where(_seed_key(product))).first() is None:
        bind.execute(product.insert().values(
            code=PRODUCT_CODE, market="", name="分红在途资金",
            product_type="IN_TRANSIT", confirm_days=0, nav_lag_days=0,
            is_qdii=False, data_source="tushare", data_source_status="pending",
        ))


def _referencing_tables(bind, product):
    inspector = sa.inspect(bind)
    references = set()
    for table_name in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table_name):
            if fk["referred_table"] != product.name or fk["referred_schema"] not in (
                None, inspector.default_schema_name,
            ):
                continue
            child = sa.table(table_name, *(
                sa.column(name) for name in fk["constrained_columns"]
            ))
            matching_key = sa.and_(*(
                child.c[local] == product.c[remote]
                for local, remote in zip(fk["constrained_columns"], fk["referred_columns"])
            ))
            query = (
                sa.select(sa.literal(1))
                .select_from(child.join(product, matching_key))
                .where(_seed_key(product)).limit(1)
            )
            if bind.execute(query).first() is not None:
                references.add(table_name)
    return sorted(references)


def downgrade():
    bind = op.get_bind()
    has_column = _has_column(bind)
    if has_column:
        events = sa.table(TABLE, sa.column(COLUMN))
        if bind.execute(
            sa.select(sa.literal(1)).select_from(events)
            .where(events.c[COLUMN].is_not(None)).limit(1)
        ).first() is not None:
            raise RuntimeError("拒绝降级：存在非空 cash_pay_date；未修改任何数据或表结构")

    product = _product_table(bind)
    references = _referencing_tables(bind, product)
    if references:
        raise RuntimeError(
            f"拒绝降级：{PRODUCT_CODE} 被外键引用（{', '.join(references)}）；"
            "未修改任何数据或表结构"
        )

    bind.execute(product.delete().where(_seed_key(product)))
    if has_column:
        op.drop_column(TABLE, COLUMN)
