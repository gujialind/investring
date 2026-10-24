"""将未涉及历史账务的自动分红整批改为方式待定，拒绝猜测方式的降级。"""

from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TABLE = "share_change_event"
PENDING_TYPE = "dividend_pending"
# 原 detector 不写这些字段；0 / 空串同样不是 NULL，不能当成「未处理」。
NULL_FIELDS = (
    "confirmed_at", "entitlement_shares", "shares_before", "shares_change",
    "shares_after", "cash_change", "reinvest_nav", "cash_pay_date",
    "share_confirm_date", "ratio", "cash_product_code",
)
EVENTS = sa.table(
    TABLE,
    sa.column("id", sa.Integer()),
    sa.column("portfolio_code", sa.String(20)),
    sa.column("market", sa.String(20)),
    sa.column("event_type", sa.String(30)),
    sa.column("event_source", sa.String(20)),
    sa.column("status", sa.String(20)),
    sa.column("ex_date", sa.Date()),
    sa.column("entitlement_date", sa.Date()),
    sa.column("platform_code", sa.String(20)),
    sa.column("parent_event_id", sa.Integer()),
    *(sa.column(name) for name in NULL_FIELDS),
)
SNAPSHOTS = sa.table(
    "portfolio_value_snapshot",
    sa.column("portfolio_code", sa.String(20)),
    sa.column("snapshot_date", sa.Date()),
)
AUDIT = sa.table(
    "audit_log",
    sa.column("resource_type", sa.String(50)),
    sa.column("resource_id", sa.String(50)),
    sa.column("action", sa.String(20)),
)
CONFIRM_ACTIONS = ("confirm", "unconfirm", "cascade_unconfirm")


def _confirmation_audit_ids(bind):
    """已留确认/回退痕迹的事件 ID（字符串形态，与 audit_log.resource_id 同型）。

    一次性取回再在 Python 侧精确比对，不在 SQL 里 CAST 整数：`audit_log.resource_id`
    是 VARCHAR，而 MySQL 8.4 的连接排序规则（utf8mb4_0900_ai_ci）与库/列的
    utf8mb4_general_ci 不同，`resource_id = CAST(id AS CHAR)` 会撞 1267
    Illegal mix of collations；隐式转整数又会把 `0123`、`123-x` 这类不同资源 ID
    误认成同一条。SQLite 两侧都不报错，只有真实 MySQL 能暴露，故此处不留 SQL 比较。
    """
    return {
        row.resource_id
        for row in bind.execute(
            sa.select(AUDIT.c.resource_id).where(
                AUDIT.c.resource_type == TABLE,
                AUDIT.c.action.in_(CONFIRM_ACTIONS),
            )
        )
    }


def _candidate_filter():
    return sa.and_(
        EVENTS.c.event_source == "tushare",
        EVENTS.c.status == "pending",
        EVENTS.c.event_type == "reinvest_dividend",
    )


def upgrade():
    bind = op.get_bind()
    children = EVENTS.alias("child")
    # unconfirm 与快照 cascade_unconfirm 会清空 confirmed_at 和计算值，但不清净值。
    # 即使净值后来被清空，已留存的这三个审计 action 仍是确认历史证据。
    # 审计是 best-effort 且可清理：无日志不证明从未确认，仍须检查所有形态和依赖。
    candidates = bind.execute(
        sa.select(
            EVENTS,
            sa.exists().where(
                children.c.parent_event_id == EVENTS.c.id
            ).label("has_children"),
            sa.exists().where(
                SNAPSHOTS.c.portfolio_code == EVENTS.c.portfolio_code,
                SNAPSHOTS.c.snapshot_date >= EVENTS.c.ex_date,
            ).label("has_snapshots"),
        ).where(_candidate_filter()).order_by(EVENTS.c.id)
    ).mappings().all()

    audited = _confirmation_audit_ids(bind) if candidates else set()

    rejected = []
    for row in candidates:
        reasons = []
        if not (row["platform_code"] or "").strip():
            reasons.append("platform_code 不明确")
        if row["market"] != "CN_OTC":
            reasons.append("market 非原检测器 CN_OTC")
        if (
            row["ex_date"] is None or row["entitlement_date"] is None
            or row["ex_date"] <= row["entitlement_date"]
        ):
            reasons.append("非原检测器日期顺序 ex_date > entitlement_date")
        if row["parent_event_id"] is not None:
            reasons.append("parent_event_id 非空")
        if row["has_children"]:
            reasons.append("存在子记录")
        non_null = [name for name in NULL_FIELDS if row[name] is not None]
        if non_null:
            reasons.append("非检测器字段非空: " + ", ".join(non_null))
        if str(row["id"]) in audited:
            reasons.append("audit_log 存在确认/回退历史")
        if row["has_snapshots"]:
            reasons.append("除息日起存在 portfolio_value_snapshot")
        if reasons:
            rejected.append(f"{row['id']} ({'; '.join(reasons)})")

    if rejected:
        raise RuntimeError(
            "0024 拒绝升级：候选事件需人工核实，ID: " + "; ".join(rejected)
            + "；本迁移未修改任何数据或表结构；请另获授权逐笔处理后重跑"
        )
    if candidates:
        # 只更新本次已审查的 ID；先整体检查，不能边检查边改或跳过坏行继续。
        bind.execute(
            sa.update(EVENTS).where(
                _candidate_filter(),
                EVENTS.c.id.in_([row["id"] for row in candidates]),
            ).values(event_type=PENDING_TYPE)
        )


def downgrade():
    bind = op.get_bind()
    ids = bind.execute(
        sa.select(EVENTS.c.id).where(
            EVENTS.c.event_type == PENDING_TYPE
        ).order_by(EVENTS.c.id)
    ).scalars().all()
    if ids:
        raise RuntimeError(
            f"0024 拒绝降级：存在 dividend_pending，ID: {ids}（含所有来源和状态）；"
            "未知分红方式不能猜成再投资；本迁移未修改任何数据或表结构，请前滚或另获授权处置"
        )
    # 不存在新类型才可安全 no-op；没有标记能区分迁移行和新建行，也不反向猜方式。
