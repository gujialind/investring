"""issue #405 日志表纳入 alembic 管理（四张：audit_log / system_error_log / login_log / task_execution_log）

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-08

四张日志表此前仅靠 `main.py` 的 `Base.metadata.create_all` 建表，alembic versions
0001-0012 中无对应 `create_table`——模型改列后生产库静默不跟随（schema drift）。
本迁移将其纳入 alembic 管理，一次治净。

幂等设计：生产库中这些表已由 create_all 建出，故每张表先
`sa.inspect(op.get_bind()).has_table(name)` 判断，已存在则跳过（打 warning 日志），
不存在才 `op.create_table`。列定义严格对齐 `app/models/` 中各模型（String 长度、
nullable、server_default）。downgrade 逆序 drop 四张表（可逆，CI 往返验证）。
"""
import logging

from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLES = ['audit_log', 'system_error_log', 'login_log', 'task_execution_log']


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('audit_log'):
        op.create_table(
            'audit_log',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('investor_code', sa.String(20), nullable=False),
            sa.Column('action', sa.String(20), nullable=False),
            sa.Column('resource_type', sa.String(50), nullable=False),
            sa.Column('resource_id', sa.String(50), nullable=True),
            sa.Column('resource_name', sa.String(100), nullable=True),
            sa.Column('old_value', sa.Text(), nullable=True),
            sa.Column('new_value', sa.Text(), nullable=True),
            sa.Column('ip_address', sa.String(50), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    else:
        logger.warning("0013 跳过 audit_log 建表（已存在）")

    if not inspector.has_table('system_error_log'):
        op.create_table(
            'system_error_log',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('error_type', sa.String(50), nullable=False),
            sa.Column('error_code', sa.String(50), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=False),
            sa.Column('error_stack', sa.Text(), nullable=True),
            sa.Column('request_path', sa.String(200), nullable=True),
            sa.Column('request_method', sa.String(10), nullable=True),
            sa.Column('request_params', sa.Text(), nullable=True),
            sa.Column('investor_code', sa.String(20), nullable=True),
            sa.Column('ip_address', sa.String(50), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    else:
        logger.warning("0013 跳过 system_error_log 建表（已存在）")

    if not inspector.has_table('login_log'):
        op.create_table(
            'login_log',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('investor_code', sa.String(20), nullable=False),
            sa.Column('action', sa.String(20), nullable=False),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('ip_address', sa.String(50), nullable=True),
            sa.Column('user_agent', sa.String(500), nullable=True),
            sa.Column('failure_reason', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    else:
        logger.warning("0013 跳过 login_log 建表（已存在）")

    if not inspector.has_table('task_execution_log'):
        op.create_table(
            'task_execution_log',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('task_code', sa.String(50), nullable=False),
            sa.Column('trigger_type', sa.String(20), nullable=False),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.Column('records_total', sa.Integer(), nullable=True),
            sa.Column('records_success', sa.Integer(), nullable=True),
            sa.Column('records_failed', sa.Integer(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('error_stack', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
    else:
        logger.warning("0013 跳过 task_execution_log 建表（已存在）")


def downgrade():
    for table in reversed(_TABLES):
        op.drop_table(table)
