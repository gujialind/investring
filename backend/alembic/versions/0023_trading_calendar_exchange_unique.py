"""trading_calendar 双日历联合唯一键（#777）。

`calendar_date` 单列唯一 → `(calendar_date, exchange)` 联合唯一，允许同一日期
SSE 与 HKEX 各存一行；`exchange` 补 NOT NULL + server_default 'SSE'。

幂等（仿 0016 三分支）：已是目标态（全新库 create_all 路径）→ 空转；
存量单列唯一 → 归一 exchange 后换约束。约束统一以**唯一索引**形态操作
（MySQL 的 UNIQUE 约束本就落为唯一索引，SQLite 免 batch 重建）。

downgrade 完整可逆：删除非 SSE 行（日历是可重新同步的参考数据，销毁无账务
后果），恢复 exchange 可空并重建单列唯一索引；重建前先探测同日重复行，
命中即 RuntimeError 拒绝（不静默删数据）。
"""

from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TABLE = "trading_calendar"
COMPOSITE_NAME = "uix_trading_calendar_date_exchange"
LEGACY_INDEX_NAME = "ix_trading_calendar_calendar_date"


def _unique_indexes(bind):
    """{索引/约束名: (列名列表, 形态)}，unique 索引与唯一约束合并视图。

    形态 "index" 用 drop_index 拆，"constraint" 用 drop_constraint 拆——
    SQLite 反射可能把表级 UNIQUE 报成约束而非索引，拆法不同。
    """
    insp = sa.inspect(bind)
    result = {}
    for ix in insp.get_indexes(TABLE):
        if ix.get("unique"):
            result[ix["name"]] = (list(ix["column_names"]), "index")
    for uc in insp.get_unique_constraints(TABLE):
        result.setdefault(uc["name"], (list(uc["column_names"]), "constraint"))
    return result


def _find_unique_on(bind, columns):
    """返回 (恰好覆盖 columns（有序）的唯一索引/约束名, 形态)，不存在返回 (None, None)。"""
    for name, (cols, kind) in _unique_indexes(bind).items():
        if cols == list(columns):
            return name, kind
    return None, None


def _drop_unique(bind, name, kind):
    if kind == "constraint":
        # SQLite 不支持直接 ALTER 约束，须经 batch 复制重建；MySQL 下 batch 透传
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_constraint(name, type_="unique")
    else:
        op.drop_index(name, table_name=TABLE)


def _exchange_not_null(bind):
    for col in sa.inspect(bind).get_columns(TABLE):
        if col["name"] == "exchange":
            return not col.get("nullable", True)
    return False


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return

    legacy, legacy_kind = _find_unique_on(bind, ["calendar_date"])
    composite, _ = _find_unique_on(bind, ["calendar_date", "exchange"])
    if legacy is None and composite is not None and _exchange_not_null(bind):
        return  # 已是目标态（create_all 新库路径），空转

    # 1. 存量 exchange 归一为 SSE（历史行由 Python 侧 default 写入，防御 NULL/空串）
    cal = sa.table(TABLE, sa.column("exchange"))
    bind.execute(
        sa.update(cal)
        .where(sa.or_(cal.c.exchange.is_(None), cal.c.exchange == ""))
        .values(exchange="SSE")
    )

    # 2. exchange NOT NULL + server_default（SQLite 经 batch 重建，MySQL 直接 ALTER）
    if not _exchange_not_null(bind):
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.alter_column(
                "exchange",
                existing_type=sa.String(20),
                nullable=False,
                server_default="SSE",
            )

    # 3. 拆单列唯一 → 建联合唯一（唯一索引形态，双方言同路径）
    if legacy is not None:
        _drop_unique(bind, legacy, legacy_kind)
    if _find_unique_on(op.get_bind(), ["calendar_date", "exchange"])[0] is None:
        op.create_index(COMPOSITE_NAME, TABLE, ["calendar_date", "exchange"], unique=True)

    # 4. calendar_date 保留普通索引（模型 index=True；原单列唯一索引兼查询加速，拆掉后补回）
    existing_plain = {
        ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(TABLE)
    }
    if LEGACY_INDEX_NAME not in existing_plain:
        op.create_index(LEGACY_INDEX_NAME, TABLE, ["calendar_date"], unique=False)


def downgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return

    composite, composite_kind = _find_unique_on(bind, ["calendar_date", "exchange"])
    legacy, _ = _find_unique_on(bind, ["calendar_date"])
    if composite is None and legacy is not None:
        return  # 已是旧形态，空转

    # 1. 删除非 SSE 行（可重新同步的参考数据）；同日重复探测先行，命中即拒绝
    cal = sa.table(TABLE, sa.column("calendar_date"), sa.column("exchange"))
    dup = bind.execute(
        sa.select(cal.c.calendar_date)
        .where(cal.c.exchange == "SSE")
        .group_by(cal.c.calendar_date)
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if dup is not None:
        raise RuntimeError(
            f"拒绝降级：SSE 行内存在同日重复 {dup[0]}，恢复单列唯一会静默失败；未修改任何数据"
        )
    bind.execute(sa.delete(cal).where(cal.c.exchange != "SSE"))

    # 2. 拆联合唯一，恢复单列唯一索引
    if composite is not None:
        _drop_unique(bind, composite, composite_kind)
    insp = sa.inspect(op.get_bind())
    plain_names = {ix["name"] for ix in insp.get_indexes(TABLE)}
    unique_names = {
        ix["name"] for ix in insp.get_indexes(TABLE) if ix.get("unique")
    }
    if LEGACY_INDEX_NAME in plain_names and LEGACY_INDEX_NAME not in unique_names:
        # 升级期补回的普通索引与旧唯一索引同名：先拆再以唯一形态重建
        op.drop_index(LEGACY_INDEX_NAME, table_name=TABLE)
    if _find_unique_on(op.get_bind(), ["calendar_date"])[0] is None:
        op.create_index(LEGACY_INDEX_NAME, TABLE, ["calendar_date"], unique=True)

    # 3. exchange 恢复可空、去 server_default
    if _exchange_not_null(op.get_bind()):
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.alter_column(
                "exchange",
                existing_type=sa.String(20),
                nullable=True,
                server_default=None,
            )
