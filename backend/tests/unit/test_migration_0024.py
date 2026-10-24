"""#775：0024 只改标准检测器候选的类型；拒绝须发生在任何 DML/DDL 之前。

通用用例随 test_engine 在 SQLite / MySQL 双跑；纯数据迁移无需 DDL 恢复，
用例只在 pytest 自有库内造行，结束回滚，绝不清理共享种子或提交业务数据。
"""

import importlib.util
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.audit_log import AuditLog
from app.models.platform import Platform
from app.models.portfolio import Portfolio
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.product import Product
from app.models.share_change_event import ShareChangeEvent

PATH = Path(__file__).resolve().parents[2] / "alembic/versions/0024_pending_dividend_method.py"
spec = importlib.util.spec_from_file_location("migration_0024", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

EVENT = ShareChangeEvent.__table__
SNAPSHOT = PortfolioValueSnapshot.__table__
AUDIT = AuditLog.__table__
ENT = date(2026, 10, 16)
EX = date(2026, 10, 19)
CREATED = datetime(2026, 10, 15, 9, 10, 11)
WRITE_VERBS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "DROP", "CREATE", "TRUNCATE"}


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    with test_engine.connect() as conn:
        try:
            yield conn
        finally:
            conn.rollback()


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


@contextmanager
def _statements(conn):
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().split()[0].upper())

    sa.event.listen(conn, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        sa.event.remove(conn, "before_cursor_execute", capture)


def _state(conn):
    return {
        table.name: [dict(row) for row in conn.execute(
            sa.select(table).order_by(table.c.id)
        ).mappings()]
        for table in (EVENT, SNAPSHOT, AUDIT)
    }


def _event(conn, **overrides):
    values = {
        "portfolio_code": "E2E_PORT", "product_code": "000300.OF", "market": "CN_OTC",
        "event_type": "reinvest_dividend", "event_source": "tushare", "status": "pending",
        "ex_date": EX, "entitlement_date": ENT, "platform_code": "MYCF",
        "div_cash": Decimal("0.0039"), "created_at": CREATED,
        "notes": "自动检测：tushare fund_div 000300.OF",
    }
    values.update(overrides)
    return conn.execute(EVENT.insert().values(**values)).inserted_primary_key[0]


def _snapshot(conn, day, portfolio_code="E2E_PORT"):
    conn.execute(SNAPSHOT.insert().values(
        portfolio_code=portfolio_code, snapshot_date=day, total_value=1000,
        total_shares=1000, unit_price=1, frozen_shares=0, in_transit_total=0,
    ))


def _audit(conn, key, action, *, resource_type="share_change_event", old=None, new=None):
    conn.execute(AUDIT.insert().values(
        investor_code="SYSTEM", action=action, resource_type=resource_type,
        resource_id=str(key),
        old_value=json.dumps(old, ensure_ascii=False) if old is not None else None,
        new_value=json.dumps(new, ensure_ascii=False) if new is not None else None,
    ))


def _assert_only_types_changed(conn, before, keys):
    expected = deepcopy(before)
    for row in expected[EVENT.name]:
        if row["id"] in keys:
            row["event_type"] = "dividend_pending"
    assert _state(conn) == expected


def _assert_refused_without_writes(conn, fn, *fragments):
    before = _state(conn)
    with _statements(conn) as statements:
        with pytest.raises(RuntimeError) as exc:
            _run(fn, conn)
    for fragment in fragments:
        assert fragment in str(exc.value)
    # 不先 rollback：否则「写了一半再抛错」会被回滚掩盖。
    assert not set(statements) & WRITE_VERBS
    assert _state(conn) == before
    return str(exc.value)


def test_revision_and_frozen_schema_contract():
    assert migration.revision == "0024"
    assert migration.down_revision == "0023"
    assert migration.TABLE == EVENT.name
    assert set(migration.EVENTS.c.keys()) <= set(EVENT.c.keys())
    assert set(migration.SNAPSHOTS.c.keys()) <= set(SNAPSHOT.c.keys())
    assert set(migration.AUDIT.c.keys()) <= set(AUDIT.c.keys())
    assert isinstance(EVENT.c.event_type.type, sa.String)
    assert len("dividend_pending") <= EVENT.c.event_type.type.length
    source = PATH.read_text(encoding="utf-8")
    assert "from app" not in source
    assert "import app" not in source


def test_empty_tables_upgrade_and_downgrade_are_noop(migration_conn):
    conn = migration_conn
    before = _state(conn)
    assert not before[EVENT.name]
    with _statements(conn) as statements:
        _run(migration.upgrade, conn)
        _run(migration.upgrade, conn)
        _run(migration.downgrade, conn)
        _run(migration.downgrade, conn)
    assert not set(statements) & WRITE_VERBS
    assert _state(conn) == before


def test_standard_candidates_change_only_type_and_upgrade_is_idempotent(migration_conn):
    conn = migration_conn
    keys = [_event(conn), _event(conn, platform_code="HBZQ")]
    before = _state(conn)

    _run(migration.upgrade, conn)
    _assert_only_types_changed(conn, before, keys)
    after = _state(conn)
    with _statements(conn) as statements:
        _run(migration.upgrade, conn)
    assert not set(statements) & WRITE_VERBS
    assert _state(conn) == after
    _assert_refused_without_writes(conn, migration.downgrade, f"ID: {keys}")


@pytest.mark.parametrize("div_cash", [None, Decimal("0"), Decimal("-0.0039"), Decimal("0.0001")])
def test_legacy_amount_and_announcement_fields_are_preserved(migration_conn, div_cash):
    """金额坏也不修、不拒；公告标识、人工备注、身份与时间均不能重新生成。"""
    conn = migration_conn
    key = _event(
        conn, div_cash=div_cash, tushare_event_id="announcement-0024",
        notes="已核对公告，金额待补录；保留原备注",
    )
    before = _state(conn)
    _run(migration.upgrade, conn)
    _assert_only_types_changed(conn, before, [key])


@pytest.mark.parametrize("field,value", [
    ("confirmed_at", datetime(2026, 10, 16, 18, 0)),
    ("entitlement_shares", Decimal("0")),
    ("shares_before", Decimal("0")),
    ("shares_change", Decimal("0")),
    ("shares_after", Decimal("0")),
    ("cash_change", Decimal("0")),
    ("reinvest_nav", Decimal("0")),
    ("reinvest_nav", Decimal("1.2345")),
    ("cash_pay_date", EX),
    ("share_confirm_date", EX + timedelta(days=1)),
    ("ratio", Decimal("0")),
    ("cash_product_code", ""),
    ("cash_product_code", "CASH"),
])
def test_non_detector_fields_refuse_even_zero_or_empty(migration_conn, field, value):
    conn = migration_conn
    key = _event(conn, **{field: value})
    _assert_refused_without_writes(
        conn, migration.upgrade, "0024 拒绝升级", f"{key} (", field,
    )


@pytest.mark.parametrize("platform", [None, "", "   "])
def test_unclear_platform_refuses(migration_conn, platform):
    conn = migration_conn
    if platform is not None:
        # 保持外键有效，直接造出存量异常形态，不关闭约束。
        conn.execute(Platform.__table__.insert().values(code=platform, name="0024 空平台探针"))
    key = _event(conn, platform_code=platform)
    _assert_refused_without_writes(conn, migration.upgrade, f"{key} (", "platform_code 不明确")


@pytest.mark.parametrize("market", ["CN_EXCHANGE", "HK_MUTUAL"])
def test_non_detector_market_refuses(migration_conn, market):
    conn = migration_conn
    conn.execute(Product.__table__.insert().values(
        code="M0024_OTHER", market=market, name="0024 非检测器市场", product_type="FUND",
    ))
    key = _event(conn, product_code="M0024_OTHER", market=market)
    _assert_refused_without_writes(conn, migration.upgrade, f"{key} (", "market 非原检测器")


@pytest.mark.parametrize("entitlement_date", [EX, EX + timedelta(days=1)])
def test_non_detector_date_order_refuses(migration_conn, entitlement_date):
    conn = migration_conn
    key = _event(conn, entitlement_date=entitlement_date)
    _assert_refused_without_writes(conn, migration.upgrade, f"{key} (", "非原检测器日期顺序")


def test_child_candidate_refuses(migration_conn):
    conn = migration_conn
    parent = _event(conn, event_type="share_split", event_source="manual", platform_code=None)
    key = _event(conn, parent_event_id=parent)
    _assert_refused_without_writes(conn, migration.upgrade, f"{key} (", "parent_event_id 非空")


@pytest.mark.parametrize("child_status", ["pending", "confirmed", "cancelled"])
def test_candidate_with_any_child_refuses(migration_conn, child_status):
    conn = migration_conn
    parent = _event(conn)
    _event(conn, parent_event_id=parent, event_source="manual", status=child_status)
    _assert_refused_without_writes(conn, migration.upgrade, f"{parent} (", "存在子记录")


@pytest.mark.parametrize("snapshot_date", [EX, EX + timedelta(days=30)])
def test_snapshot_on_or_after_ex_date_refuses(migration_conn, snapshot_date):
    conn = migration_conn
    key = _event(conn)
    _snapshot(conn, snapshot_date)
    _assert_refused_without_writes(
        conn, migration.upgrade, f"{key} (", "除息日起存在 portfolio_value_snapshot",
    )


def test_earlier_snapshot_and_other_portfolio_do_not_block(migration_conn):
    conn = migration_conn
    key = _event(conn)
    _snapshot(conn, ENT)
    conn.execute(Portfolio.__table__.insert().values(code="M0024_OTHER", name="0024 其他组合"))
    _snapshot(conn, EX + timedelta(days=30), "M0024_OTHER")
    before = _state(conn)
    _run(migration.upgrade, conn)
    _assert_only_types_changed(conn, before, [key])


@pytest.mark.parametrize("action", ["confirm", "unconfirm", "cascade_unconfirm"])
def test_confirmation_audit_action_alone_proves_history(migration_conn, action):
    """审计 action 本身已证明发生过确认，不能依赖 JSON 载荷是否齐全或最新状态。"""
    conn = migration_conn
    key = _event(conn)
    _audit(conn, key, action)
    _assert_refused_without_writes(conn, migration.upgrade, f"{key} (", "audit_log 存在确认/回退历史")


@pytest.mark.parametrize("action", ["unconfirm", "cascade_unconfirm"])
def test_confirm_then_unconfirm_with_cleared_fields_still_refuses(migration_conn, action):
    """冻结普通/级联回退的真实审计形态；旧版未回写或后来清空净值时仍能取证。"""
    conn = migration_conn
    key = _event(conn, status="confirmed", confirmed_at=CREATED,
                 entitlement_shares=1000, shares_before=1000, shares_change=4,
                 shares_after=1004, cash_change=0)
    confirmed = {
        "status": "confirmed", "entitlement_shares": "1000.00", "shares_before": "1000.00",
        "shares_change": "4.00", "shares_after": "1004.00", "cash_change": "0.0000",
    }
    _audit(conn, key, "confirm", old={"status": "pending"}, new=confirmed)
    conn.execute(EVENT.update().where(EVENT.c.id == key).values(
        status="pending", confirmed_at=None, entitlement_shares=None,
        shares_before=None, shares_change=None, shares_after=None, cash_change=None,
    ))
    if action == "unconfirm":
        old, new = {"status": "confirmed"}, {"status": "pending"}
    else:
        old, new = confirmed, {
            "status": "pending", "reason": "snapshot_deleted",
            "snapshot_date": ENT.isoformat(), "deleted_children": 0,
        }
    _audit(conn, key, action, old=old, new=new)
    _assert_refused_without_writes(conn, migration.upgrade, f"{key} (", "audit_log 存在确认/回退历史")


def test_unrelated_audit_and_public_field_edits_do_not_block(migration_conn):
    conn = migration_conn
    key = _event(conn, notes="只修改了备注")
    _audit(conn, key, "confirm", resource_type="trade")
    _audit(conn, f"{key}-other", "confirm")
    _audit(conn, key, "create", new={"status": "pending"})
    _audit(conn, key, "update", old={"notes": "原备注"}, new={"notes": "只修改了备注"})
    before = _state(conn)
    _run(migration.upgrade, conn)
    _assert_only_types_changed(conn, before, [key])


def test_mixed_candidates_refuse_all_before_any_write_and_list_all_bad_ids(migration_conn):
    conn = migration_conn
    good_first = _event(conn)
    bad_value = _event(conn, cash_change=0)
    bad_audit = _event(conn)
    good_last = _event(conn)
    _audit(conn, bad_audit, "unconfirm", old={"status": "confirmed"}, new={"status": "pending"})
    message = _assert_refused_without_writes(
        conn, migration.upgrade, f"{bad_value} (", "cash_change",
        f"{bad_audit} (", "audit_log 存在确认/回退历史",
    )
    assert f"{good_first} (" not in message
    assert f"{good_last} (" not in message


def test_unrelated_rows_are_not_changed_or_preflighted(migration_conn):
    conn = migration_conn
    key = _event(conn)
    unrelated = [
        {"event_source": "manual"},
        {"event_source": "akshare"},
        {"status": "confirmed"},
        {"status": "cancelled"},
        *({"event_type": event_type} for event_type in (
            "cash_dividend", "share_split", "share_merge", "bonus_share",
            "forced_adjustment", "dividend_pending",
        )),
    ]
    for overrides in unrelated:
        # 故意带非标准字段 + 确认审计，证明候选以外连预检都不应阻断升级。
        other = _event(conn, reinvest_nav=2, cash_change=0, **overrides)
        _audit(conn, other, "confirm")
    before = _state(conn)
    _run(migration.upgrade, conn)
    _assert_only_types_changed(conn, before, [key])


def test_downgrade_without_pending_type_is_noop(migration_conn):
    conn = migration_conn
    _event(conn)
    _event(conn, event_type="cash_dividend", status="confirmed", event_source="manual")
    before = _state(conn)
    with _statements(conn) as statements:
        _run(migration.downgrade, conn)
        _run(migration.downgrade, conn)
    assert not set(statements) & WRITE_VERBS
    assert _state(conn) == before


@pytest.mark.parametrize("source", ["tushare", "manual"])
@pytest.mark.parametrize("status", ["pending", "confirmed", "cancelled"])
def test_downgrade_refuses_pending_type_for_every_status_and_source(migration_conn, source, status):
    conn = migration_conn
    _event(conn)  # 不能为回退新类型顺带改动旧事件
    key = _event(conn, event_type="dividend_pending", event_source=source, status=status)
    _assert_refused_without_writes(
        conn, migration.downgrade, "0024 拒绝降级", f"ID: [{key}]", "未知分红方式不能猜成再投资",
    )


def test_audit_resource_id_matches_exactly_not_numerically(migration_conn):
    """不同 resource_id 不能被当成同一事件——比较必须在 Python 侧做字符串相等。

    曾在 SQL 里写 `resource_id = CAST(id AS CHAR)`：MySQL 8.4 会因连接排序规则
    （utf8mb4_0900_ai_ci）与列的 utf8mb4_general_ci 不同报 1267，而去掉 CAST 改隐式
    转整数又会把 `0123` / `123-other` 误认成 123。SQLite 两种写法都不报错，
    故这条形态守卫在任何方言上都跑（不再挂 MySQL 标记）。
    """
    conn = migration_conn
    key = _event(conn)
    _audit(conn, f"{key}-other", "confirm")
    _audit(conn, f"0{key}", "unconfirm")
    before = _state(conn)
    _run(migration.upgrade, conn)
    _assert_only_types_changed(conn, before, [key])


@pytest.mark.dialect
class TestMysqlMigration:
    def test_upgrade_has_no_implicit_ddl_commit(self, migration_conn):
        if migration_conn.dialect.name != "mysql":
            pytest.skip("MySQL 纯 DML 迁移可事务回滚验证")
        conn = migration_conn
        key = _event(conn)
        before = _state(conn)
        savepoint = conn.begin_nested()
        with _statements(conn) as statements:
            _run(migration.upgrade, conn)
        _assert_only_types_changed(conn, before, [key])
        assert not set(statements) & (WRITE_VERBS - {"UPDATE"})
        savepoint.rollback()
        assert _state(conn) == before
