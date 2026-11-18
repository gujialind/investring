# ============================================================================
# 集成测试：台账收益口径（#905 PR2，D2：收益=当前市值−净入金，step-forward）
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from tests.factories import create_portfolio, create_investor, create_platform
from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.services.ledger_profit_service import compute_ledger_totals


@pytest.fixture
def port(test_db):
    create_investor(test_db, code="INV_P")
    create_platform(test_db, code="PLAT_A")
    create_platform(test_db, code="PLAT_B")
    return create_portfolio(test_db, code="P_LED", kind="ledger",
                            owner_investor_code="INV_P", status="active")


def _entry(db, platform, d, mv, code="P_LED"):
    db.add(LedgerEntry(portfolio_code=code, platform_code=platform, value_date=d,
                       market_value=Decimal(mv), asset_kind="non_cash"))
    db.flush()


def _flow(db, platform, d, direction, amt, code="P_LED"):
    db.add(LedgerFlow(portfolio_code=code, platform_code=platform, flow_date=d,
                      direction=direction, amount=Decimal(amt)))
    db.flush()


class TestLedgerProfit:
    def test_profit_deducts_net_inflow(self, test_db, port):
        # 验收 3：注资 100 + 市值 100 → 收益 0；市值改 110 → 收益 10（≠市值全额）
        _flow(test_db, "PLAT_A", date(2026, 1, 5), "inflow", "100")
        _entry(test_db, "PLAT_A", date(2026, 1, 5), "100")
        t1 = compute_ledger_totals(test_db, "P_LED", as_of_date=date(2026, 1, 5))
        assert t1["total_value"] == 100.0
        assert t1["net_inflow"] == 100.0
        assert t1["total_profit"] == 0.0
        _entry(test_db, "PLAT_A", date(2026, 1, 6), "110")
        t2 = compute_ledger_totals(test_db, "P_LED", as_of_date=date(2026, 1, 6))
        assert t2["total_value"] == 110.0
        assert t2["total_profit"] == 10.0

    def test_step_forward_multi_platform(self, test_db, port):
        # 每平台取 ≤D 最近一条，跨平台求和
        _entry(test_db, "PLAT_A", date(2026, 1, 5), "100")
        _entry(test_db, "PLAT_A", date(2026, 1, 10), "200")
        _entry(test_db, "PLAT_B", date(2026, 1, 8), "50")
        t = compute_ledger_totals(test_db, "P_LED", as_of_date=date(2026, 1, 10))
        assert t["total_value"] == 250.0  # PLAT_A 200（最近）+ PLAT_B 50

    def test_step_forward_as_of(self, test_db, port):
        _entry(test_db, "PLAT_A", date(2026, 1, 5), "100")
        _entry(test_db, "PLAT_A", date(2026, 1, 10), "200")
        t = compute_ledger_totals(test_db, "P_LED", as_of_date=date(2026, 1, 7))
        assert t["total_value"] == 100.0  # ≤1/7 最近一条是 1/5 的 100

    def test_outflow_reduces_net_inflow(self, test_db, port):
        _flow(test_db, "PLAT_A", date(2026, 1, 5), "inflow", "100")
        _flow(test_db, "PLAT_A", date(2026, 1, 6), "outflow", "30")
        _entry(test_db, "PLAT_A", date(2026, 1, 6), "80")
        t = compute_ledger_totals(test_db, "P_LED", as_of_date=date(2026, 1, 6))
        assert t["net_inflow"] == 70.0
        assert t["total_profit"] == 10.0  # 80 − 70

    def test_negative_net_inflow_warns(self, test_db, port):
        _flow(test_db, "PLAT_A", date(2026, 1, 5), "inflow", "100")
        _flow(test_db, "PLAT_A", date(2026, 1, 6), "outflow", "200")
        _entry(test_db, "PLAT_A", date(2026, 1, 6), "50")
        t = compute_ledger_totals(test_db, "P_LED", as_of_date=date(2026, 1, 6))
        assert t["net_inflow"] == -100.0
        assert t["total_profit"] == 150.0  # 50 − (−100)，如实计算不静默
        assert any("累计撤资已超过累计注资" in w for w in t["warnings"])

    def test_active_no_records_zero(self, test_db, port):
        t = compute_ledger_totals(test_db, "P_LED", as_of_date=date(2026, 1, 5))
        assert t["total_value"] == 0.0
        assert t["total_profit"] == 0.0

    def test_draft_none(self, test_db):
        create_investor(test_db, code="INV_PD")
        create_portfolio(test_db, code="P_LED_D", kind="ledger",
                         owner_investor_code="INV_PD", status="draft")
        t = compute_ledger_totals(test_db, "P_LED_D")
        assert t["total_value"] is None
        assert t["total_profit"] is None