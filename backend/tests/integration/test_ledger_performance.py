# ============================================================================
# 集成测试：台账绩效口径 A（#905 PR2，§5.1：mwr XIRR + 区间收益 Modified Dietz）
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from tests.factories import create_portfolio, create_investor, create_platform
from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.services import ledger_profit_service as lps
from app.services.ledger_profit_service import _modified_dietz, compute_ledger_performance


@pytest.fixture
def port(test_db):
    create_investor(test_db, code="INV_PERF")
    create_platform(test_db, code="PLAT_A")
    return create_portfolio(test_db, code="P_PERF", kind="ledger",
                            owner_investor_code="INV_PERF", status="active")


def _entry(db, d, mv):
    db.add(LedgerEntry(portfolio_code="P_PERF", platform_code="PLAT_A", value_date=d,
                       market_value=Decimal(mv), asset_kind="non_cash"))
    db.flush()


def _flow(db, d, direction, amt):
    db.add(LedgerFlow(portfolio_code="P_PERF", platform_code="PLAT_A", flow_date=d,
                      direction=direction, amount=Decimal(amt)))
    db.flush()


class TestModifiedDietz:
    def test_hand_calc_with_flow(self, test_db, port):
        # 稀疏数据下区间收益与手算一致：BMV=1000 EMV=1150 CF=100 T=30 t_i=15 w=0.5
        # denom=1000+50=1050, R=(1150-1000-100)/1050=4.7619%
        _entry(test_db, date(2025, 12, 31), "1000")
        _entry(test_db, date(2026, 1, 31), "1150")
        _flow(test_db, date(2026, 1, 16), "inflow", "100")
        r = _modified_dietz(test_db, "P_PERF", date(2026, 1, 1), date(2026, 1, 31))
        assert r == Decimal("4.7619")

    def test_no_flow_simple(self, test_db, port):
        _entry(test_db, date(2025, 12, 31), "1000")
        _entry(test_db, date(2026, 1, 31), "1200")
        r = _modified_dietz(test_db, "P_PERF", date(2026, 1, 1), date(2026, 1, 31))
        assert r == Decimal("20.0000")

    def test_bmv_zero_none(self, test_db, port):
        _entry(test_db, date(2026, 1, 10), "1000")  # 首条晚于 start → 不足窗口
        assert _modified_dietz(test_db, "P_PERF", date(2026, 1, 1), date(2026, 1, 31)) is None

    def test_emv_zero_full_loss(self, test_db, port):
        _entry(test_db, date(2025, 12, 31), "1000")
        _entry(test_db, date(2026, 1, 31), "0")
        r = _modified_dietz(test_db, "P_PERF", date(2026, 1, 1), date(2026, 1, 31))
        assert r == Decimal("-100.0000")


class TestPerformanceKoujingA:
    def test_nav_fields_none_and_mwr_present(self, test_db, port, monkeypatch):
        monkeypatch.setattr(lps, "business_today", lambda: date(2026, 1, 31))
        _entry(test_db, date(2025, 12, 31), "1000")
        _entry(test_db, date(2026, 1, 31), "1150")
        _flow(test_db, date(2025, 12, 31), "inflow", "1000")
        _flow(test_db, date(2026, 1, 16), "inflow", "100")
        perf = compute_ledger_performance(test_db, "P_PERF")
        for k in ("twr", "twr_chained", "annualized_twr", "initial_nav", "current_nav",
                  "nav_series_consistent", "max_drawdown", "max_drawdown_peak_date",
                  "max_drawdown_trough_date", "annualized_volatility"):
            assert perf[k] is None, f"{k} 口径A 应恒 None"
        assert perf["mwr"] is not None
        assert perf["return_1m"] is not None
        assert perf["cash_flow_count"] == 2
        assert "data_sparse" not in perf  # 口径A 不新增字段

    def test_draft_empty(self, test_db):
        create_investor(test_db, code="INV_PERFD")
        create_portfolio(test_db, code="P_PERF_D", kind="ledger",
                         owner_investor_code="INV_PERFD", status="draft")
        perf = compute_ledger_performance(test_db, "P_PERF_D")
        assert perf["mwr"] is None
        assert perf["return_1m"] is None