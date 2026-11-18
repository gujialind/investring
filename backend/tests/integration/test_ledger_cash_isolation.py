# ============================================================================
# 集成测试：D1 隔离断言（#905 PR2）——台账现金类记录不进可用现金/在途口径
# ============================================================================

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.factories import create_portfolio, create_investor, create_platform
from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.services.position_service import calculate_available_cash


@pytest.fixture
def ledger_port(test_db):
    create_investor(test_db, code="INV_ISO")
    create_platform(test_db, code="PLAT_A")
    return create_portfolio(test_db, code="P_ISO", kind="ledger",
                            owner_investor_code="INV_ISO", status="active")


def _cash_entry(db, mv="10000"):
    db.add(LedgerEntry(portfolio_code="P_ISO", platform_code="PLAT_A",
                       value_date=date(2026, 1, 5), market_value=Decimal(mv), asset_kind="cash"))
    db.flush()


class TestCashIsolation:
    def test_ledger_cash_not_in_available_cash(self, test_db, ledger_port):
        _cash_entry(test_db)
        test_db.add(LedgerFlow(portfolio_code="P_ISO", platform_code="PLAT_A",
                               flow_date=date(2026, 1, 5), direction="inflow", amount=Decimal("10000")))
        test_db.flush()
        # 台账组合无 CASH 持仓行 → 可用现金 0（台账现金类不进可用现金口径）
        assert calculate_available_cash(test_db, "P_ISO", as_of_date=date(2026, 1, 5)) == 0

    def test_nav_available_cash_unaffected(self, test_db, ledger_port):
        create_portfolio(test_db, code="NAV_ISO", status="active")
        before = calculate_available_cash(test_db, "NAV_ISO", as_of_date=date(2026, 1, 5))
        _cash_entry(test_db, mv="99999")
        after = calculate_available_cash(test_db, "NAV_ISO", as_of_date=date(2026, 1, 5))
        assert before == after

    def test_structural_isolation(self):
        # 现金/在途/快照口径源码不引用台账表（D1 结构断言）
        base = Path(__file__).resolve().parents[2] / "app" / "services"
        for fname in ("position_service.py", "snapshot_service.py", "cash_flow_service.py"):
            src = (base / fname).read_text()
            for token in ("LedgerEntry", "LedgerFlow", "ledger_entry", "ledger_flow"):
                assert token not in src, f"{fname} 不应引用台账表 {token}"