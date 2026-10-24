"""#777：在 pytest 自有库验证迁移 0023 的双日历往返、幂等与目标态契约。

形态判据走运行时反射（与迁移同源），不钉方言内部实现；MySQL DDL 隐式提交，
fixture 结束时无条件把表恢复为目标态并清理探针行（仿 0022 的恢复策略）。
"""

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.trading_calendar import TradingCalendar

PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/0023_trading_calendar_exchange_unique.py"
)
spec = importlib.util.spec_from_file_location("migration_0023", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

TABLE = "trading_calendar"
COMPOSITE = "uix_trading_calendar_date_exchange"
LEGACY = "ix_trading_calendar_calendar_date"
PROBE_DAY = date(2035, 1, 4)  # 远离种子窗口（today+1年）的孤立探针日


def _run(fn, conn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _unique_names_on(conn, columns):
    insp = sa.inspect(conn)
    names = {
        ix["name"] for ix in insp.get_indexes(TABLE)
        if ix.get("unique") and list(ix["column_names"]) == columns
    }
    names |= {
        uc["name"] for uc in insp.get_unique_constraints(TABLE)
        if list(uc["column_names"]) == columns
    }
    return names


def _exchange_nullable(conn):
    for col in sa.inspect(conn).get_columns(TABLE):
        if col["name"] == "exchange":
            return col.get("nullable", True)
    raise AssertionError("exchange 列不存在")


def _delete_probes(conn):
    conn.execute(
        sa.delete(TradingCalendar.__table__).where(
            TradingCalendar.__table__.c.calendar_date == PROBE_DAY
        )
    )


@pytest.fixture
def migration_conn(test_engine):
    with test_engine.connect() as conn:
        yield conn
        conn.rollback()
    # MySQL DDL 隐式提交撤不回：无条件恢复目标态（联合唯一 + NOT NULL）并清理探针
    with test_engine.begin() as conn:
        _delete_probes(conn)
        if not _unique_names_on(conn, ["calendar_date", "exchange"]):
            _run(migration.upgrade, conn)


def test_revision_and_model_contract():
    assert migration.revision == "0023"
    assert migration.down_revision == "0022"
    # 模型侧目标态：联合唯一 + exchange NOT NULL + calendar_date 不再单列唯一
    t = TradingCalendar.__table__
    assert not t.c.calendar_date.unique
    assert not t.c.exchange.nullable
    uq = {c.name for c in t.constraints if isinstance(c, sa.UniqueConstraint)}
    assert COMPOSITE in uq
    source = PATH.read_text()
    assert "from app.models" not in source
    assert "import app.models" not in source


def test_upgrade_idempotent_on_target_state(migration_conn):
    """create_all 新库已是目标态：upgrade 空转可重复执行，形态不变。"""
    _run(migration.upgrade, migration_conn)
    _run(migration.upgrade, migration_conn)
    assert _unique_names_on(migration_conn, ["calendar_date", "exchange"])
    assert not _unique_names_on(migration_conn, ["calendar_date"])
    assert not _exchange_nullable(migration_conn)


def test_downgrade_restores_legacy_shape(migration_conn):
    """downgrade：删 HKEX 行、恢复单列唯一、exchange 回到可空。"""
    t = TradingCalendar.__table__
    migration_conn.execute(
        t.insert().values(
            [
                {"calendar_date": PROBE_DAY, "is_open": True, "exchange": "SSE"},
                {"calendar_date": PROBE_DAY, "is_open": False, "exchange": "HKEX"},
            ]
        )
    )
    migration_conn.commit()

    _run(migration.downgrade, migration_conn)

    rows = migration_conn.execute(
        sa.select(t.c.exchange).where(t.c.calendar_date == PROBE_DAY)
    ).fetchall()
    assert [r[0] for r in rows] == ["SSE"]  # HKEX 行已删，SSE 行保留
    assert _unique_names_on(migration_conn, ["calendar_date"])
    assert not _unique_names_on(migration_conn, ["calendar_date", "exchange"])
    assert _exchange_nullable(migration_conn)


def test_full_roundtrip_preserves_sse_rows(migration_conn):
    """downgrade → upgrade 往返：SSE 数据全程不丢，终态回到联合唯一 + NOT NULL。"""
    t = TradingCalendar.__table__
    before = migration_conn.execute(
        sa.select(sa.func.count()).select_from(t).where(t.c.exchange == "SSE")
    ).scalar()

    _run(migration.downgrade, migration_conn)
    _run(migration.upgrade, migration_conn)

    after = migration_conn.execute(
        sa.select(sa.func.count()).select_from(t).where(t.c.exchange == "SSE")
    ).scalar()
    assert after == before
    assert _unique_names_on(migration_conn, ["calendar_date", "exchange"])
    assert not _unique_names_on(migration_conn, ["calendar_date"])
    assert not _exchange_nullable(migration_conn)


def test_downgrade_idempotent_on_legacy_shape(migration_conn):
    """已是旧形态时 downgrade 空转（CI 往返与重复执行安全）。"""
    _run(migration.downgrade, migration_conn)
    _run(migration.downgrade, migration_conn)  # 第二次必须无副作用、不抛
    assert _unique_names_on(migration_conn, ["calendar_date"])
    _run(migration.upgrade, migration_conn)  # 交还目标态（fixture 兜底同判据）
    assert _unique_names_on(migration_conn, ["calendar_date", "exchange"])
