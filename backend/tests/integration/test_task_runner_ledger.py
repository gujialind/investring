# ============================================================================
# 集成测试：自动快照不处理台账型组合（#905 PR1，验收 9）
# ============================================================================

from datetime import date

from tests.factories import create_portfolio, create_investor
from app.services import task_runner
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.trading_calendar import TradingCalendar


def _open_day(db):
    row = (
        db.query(TradingCalendar.calendar_date)
        .filter(
            TradingCalendar.exchange == "SSE",
            TradingCalendar.is_open.is_(True),
            TradingCalendar.calendar_date >= date(2026, 1, 1),
        )
        .order_by(TradingCalendar.calendar_date.asc())
        .first()
    )
    return row[0]


def test_auto_snapshot_skips_ledger(test_db):
    """台账型即使 active + auto_snapshot_enabled=True 也不进自动快照轮。"""
    create_investor(test_db, code="INV_T")
    create_portfolio(
        test_db, code="LED_T", kind="ledger", owner_investor_code="INV_T",
        status="active", auto_snapshot_enabled=True,
    )
    outcome = task_runner._generate_snapshots_for_date(test_db, _open_day(test_db))
    assert outcome["portfolios_processed"] == 0
    assert test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code="LED_T").count() == 0