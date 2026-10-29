"""#797 持久化会话、代理凭证与审计身份；已使用时拒绝销毁撤销记录的降级。"""
from alembic import op
import sqlalchemy as sa

from app.constants.db_charset import DB_CHARSET, DB_COLLATE

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("auth_session"):
        op.create_table(
            "auth_session",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("investor_code", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["investor_code"], ["investor.code"],
                                    name="fk_auth_session_investor", ondelete="RESTRICT"),
            mysql_charset=DB_CHARSET, mysql_collate=DB_COLLATE,
        )
        op.create_index("ix_auth_session_investor_code", "auth_session", ["investor_code"])
    if not inspector.has_table("api_key"):
        op.create_table(
            "api_key",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("investor_code", sa.String(20), nullable=False),
            sa.Column("label", sa.String(100), nullable=False),
            sa.Column("secret_hash", sa.String(64), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["investor_code"], ["investor.code"],
                                    name="fk_api_key_investor", ondelete="RESTRICT"),
            mysql_charset=DB_CHARSET, mysql_collate=DB_COLLATE,
        )
        op.create_index("ix_api_key_investor_code", "api_key", ["investor_code"])
    columns = {col["name"] for col in inspector.get_columns("audit_log")}
    for name, length in (("credential_id", 32), ("credential_label", 100)):
        if name not in columns:
            op.add_column("audit_log", sa.Column(name, sa.String(length), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    present = [name for name in ("auth_session", "api_key") if inspector.has_table(name)]
    for name in present:
        table = sa.table(name, sa.column("id"))
        if bind.execute(sa.select(table.c.id).limit(1)).first():
            raise RuntimeError("0025 拒绝降级：凭证已被使用，不能销毁会话、密钥或撤销记录；请前滚修复")
    columns = {col["name"] for col in inspector.get_columns("audit_log")}
    audit_fields = [name for name in ("credential_id", "credential_label") if name in columns]
    if audit_fields:
        audit = sa.table("audit_log", *(sa.column(name) for name in audit_fields))
        if bind.execute(sa.select(sa.literal(1)).select_from(audit).where(
            sa.or_(*(audit.c[name].is_not(None) for name in audit_fields))
        ).limit(1)).first():
            raise RuntimeError("0025 拒绝降级：审计已有凭证身份，不能销毁历史；请前滚修复")
    for name in audit_fields:
        op.drop_column("audit_log", name)
    for name in reversed(present):
        op.drop_table(name)
