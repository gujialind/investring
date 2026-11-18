# ============================================================================
# 集成测试：台账市值台账 CRUD（#905 PR2，验收 5：非交易日/稀疏/改某日不重算/留痕）
# ============================================================================

import pytest

from datetime import date
from decimal import Decimal

from tests.factories import create_portfolio, create_investor
from app.models.ledger_entry import LedgerEntry
from app.models.audit_log import AuditLog


@pytest.fixture
def ledger_port(test_db):
    create_investor(test_db, code="INV_LE")
    return create_portfolio(test_db, code="LE_PORT", kind="ledger",
                            owner_investor_code="INV_LE", status="active")


def _create_entry(client, headers, code="LE_PORT", value_date="2026-01-05", mv="10000.0000"):
    return client.post(
        f"/api/portfolios/{code}/ledger/entries",
        json={"platform_code": "MYCF", "value_date": value_date,
              "market_value": mv, "asset_kind": "non_cash"},
        headers=headers,
    )


class TestLedgerEntryCRUD:
    def test_create_entry(self, client, admin_headers, test_db, ledger_port):
        resp = _create_entry(client, admin_headers)
        assert resp.status_code == 200
        d = resp.json()
        assert d["market_value"] == 10000.0
        assert d["asset_kind"] == "non_cash"
        assert d["platform_code"] == "MYCF"

    def test_non_trading_day_allowed(self, client, admin_headers, test_db, ledger_port):
        # 2026-01-03 是周六（非交易日），台账 D3 允许任意自然日
        resp = _create_entry(client, admin_headers, value_date="2026-01-03")
        assert resp.status_code == 200

    def test_duplicate_entry_rejected(self, client, admin_headers, test_db, ledger_port):
        assert _create_entry(client, admin_headers).status_code == 200
        resp = _create_entry(client, admin_headers)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "ALREADY_EXISTS"

    def test_sparse_dates_independent(self, client, admin_headers, test_db, ledger_port):
        assert _create_entry(client, admin_headers, value_date="2026-01-05").status_code == 200
        assert _create_entry(client, admin_headers, value_date="2026-02-10").status_code == 200
        entries = test_db.query(LedgerEntry).filter_by(portfolio_code="LE_PORT").all()
        assert len(entries) == 2

    def test_update_entry(self, client, admin_headers, test_db, ledger_port):
        eid = _create_entry(client, admin_headers).json()["id"]
        resp = client.put(f"/api/portfolios/LE_PORT/ledger/entries/{eid}",
                          json={"market_value": "11000.0000"}, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["market_value"] == 11000.0

    def test_update_no_recalc_other_day(self, client, admin_headers, test_db, ledger_port):
        e1 = _create_entry(client, admin_headers, value_date="2026-01-05", mv="10000").json()["id"]
        _create_entry(client, admin_headers, value_date="2026-01-06", mv="20000")
        client.put(f"/api/portfolios/LE_PORT/ledger/entries/{e1}",
                   json={"market_value": "12345"}, headers=admin_headers)
        # 改 D1 不触发 D2 重算：D2 存储行原值不变
        d2 = test_db.query(LedgerEntry).filter_by(
            portfolio_code="LE_PORT", value_date="2026-01-06").first()
        assert float(d2.market_value) == 20000.0

    def test_delete_entry(self, client, admin_headers, test_db, ledger_port):
        eid = _create_entry(client, admin_headers).json()["id"]
        resp = client.delete(f"/api/portfolios/LE_PORT/ledger/entries/{eid}", headers=admin_headers)
        assert resp.status_code == 200
        assert test_db.query(LedgerEntry).filter_by(id=eid).first() is None

    def test_audit_trail(self, client, admin_headers, test_db, ledger_port):
        eid = _create_entry(client, admin_headers).json()["id"]
        client.put(f"/api/portfolios/LE_PORT/ledger/entries/{eid}",
                   json={"market_value": "9999"}, headers=admin_headers)
        client.delete(f"/api/portfolios/LE_PORT/ledger/entries/{eid}", headers=admin_headers)
        actions = [a.action for a in test_db.query(AuditLog).filter_by(
            resource_type="ledger_entry").all()]
        assert "create" in actions and "update" in actions and "delete" in actions

    def test_draft_rejected(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_LD")
        create_portfolio(test_db, code="LE_DRAFT", kind="ledger",
                         owner_investor_code="INV_LD", status="draft")
        resp = _create_entry(client, admin_headers, code="LE_DRAFT")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PORTFOLIO_NOT_ACTIVE"

    def test_nav_rejected(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="NAV_LE", status="active")
        resp = _create_entry(client, admin_headers, code="NAV_LE")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PORTFOLIO_KIND_NOT_ALLOWED"

    def test_negative_market_value_rejected(self, client, admin_headers, test_db, ledger_port):
        resp = _create_entry(client, admin_headers, mv="-1")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_AMOUNT"


class TestLedgerFlowCRUD:
    def test_create_flow_and_list(self, client, admin_headers, test_db, ledger_port):
        resp = client.post("/api/portfolios/LE_PORT/ledger/flows",
                           json={"platform_code": "MYCF", "flow_date": "2026-01-05",
                                 "direction": "inflow", "amount": "10000.00"},
                           headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["amount"] == 10000.0
        assert resp.json()["warnings"] == []
        lst = client.get("/api/portfolios/LE_PORT/ledger/flows", headers=admin_headers)
        assert len(lst.json()) == 1

    def test_same_day_multiple_flows(self, client, admin_headers, test_db, ledger_port):
        for _ in range(2):
            resp = client.post("/api/portfolios/LE_PORT/ledger/flows",
                               json={"platform_code": "MYCF", "flow_date": "2026-01-05",
                                     "direction": "inflow", "amount": "100"},
                               headers=admin_headers)
            assert resp.status_code == 200

    def test_outflow_negative_net_warns(self, client, admin_headers, test_db, ledger_port):
        client.post("/api/portfolios/LE_PORT/ledger/flows",
                    json={"platform_code": "MYCF", "flow_date": "2026-01-05",
                          "direction": "inflow", "amount": "100"}, headers=admin_headers)
        resp = client.post("/api/portfolios/LE_PORT/ledger/flows",
                           json={"platform_code": "MYCF", "flow_date": "2026-01-06",
                                 "direction": "outflow", "amount": "200"}, headers=admin_headers)
        assert resp.status_code == 200
        assert any("累计撤资已超过累计注资" in w for w in resp.json()["warnings"])

    def test_zero_amount_rejected(self, client, admin_headers, test_db, ledger_port):
        resp = client.post("/api/portfolios/LE_PORT/ledger/flows",
                           json={"platform_code": "MYCF", "flow_date": "2026-01-05",
                                 "direction": "inflow", "amount": "0"}, headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_AMOUNT"

    def test_invalid_direction_rejected(self, client, admin_headers, test_db, ledger_port):
        resp = client.post("/api/portfolios/LE_PORT/ledger/flows",
                           json={"platform_code": "MYCF", "flow_date": "2026-01-05",
                                 "direction": "foo", "amount": "100"}, headers=admin_headers)
        assert resp.status_code == 422

    def test_update_and_delete_flow(self, client, admin_headers, test_db, ledger_port):
        fid = client.post("/api/portfolios/LE_PORT/ledger/flows",
                          json={"platform_code": "MYCF", "flow_date": "2026-01-05",
                                "direction": "inflow", "amount": "100"}, headers=admin_headers).json()["id"]
        up = client.put(f"/api/portfolios/LE_PORT/ledger/flows/{fid}",
                        json={"amount": "200"}, headers=admin_headers)
        assert up.status_code == 200 and up.json()["amount"] == 200.0
        de = client.delete(f"/api/portfolios/LE_PORT/ledger/flows/{fid}", headers=admin_headers)
        assert de.status_code == 200


class TestLedgerPortfolioDelete:
    """带台账行的组合删除被拒（台账是账务事实，不随组合壳删除），三态台账行零删除。

    补强 #905 验收断言面，对齐 #820 计数口径（_count_portfolio_dependents 元数据派生
    含 ledger_entry/ledger_flow）。守门名单见 test_portfolio_delete_dependents.py。
    """

    def _add_ledger_row(self, db, code):
        # 直接造行以测删除判据（draft 态写侧闸门要求 active，此处绕过仅测删除语义）
        db.add(LedgerEntry(portfolio_code=code, platform_code="MYCF",
                           value_date=date(2026, 1, 5), market_value=Decimal("100"),
                           asset_kind="non_cash"))
        db.flush()

    def test_active_with_ledger_rejected(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_DEL_A")
        create_portfolio(test_db, code="DEL_ACT", kind="ledger",
                         owner_investor_code="INV_DEL_A", status="active")
        self._add_ledger_row(test_db, "DEL_ACT")
        resp = client.delete("/api/portfolios/DEL_ACT", headers=admin_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "PORTFOLIO_NOT_DELETABLE"
        assert test_db.query(LedgerEntry).filter_by(portfolio_code="DEL_ACT").count() == 1

    def test_draft_with_ledger_rejected(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_DEL_D")
        create_portfolio(test_db, code="DEL_DRA", kind="ledger",
                         owner_investor_code="INV_DEL_D", status="draft")
        self._add_ledger_row(test_db, "DEL_DRA")
        resp = client.delete("/api/portfolios/DEL_DRA", headers=admin_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "PORTFOLIO_HAS_DEPENDENT_DATA"
        assert "ledger_entry" in resp.json()["detail"]["details"]["dependencies"]
        assert test_db.query(LedgerEntry).filter_by(portfolio_code="DEL_DRA").count() == 1

    def test_closed_with_ledger_rejected(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_DEL_C")
        create_portfolio(test_db, code="DEL_CLO", kind="ledger",
                         owner_investor_code="INV_DEL_C", status="closed")
        self._add_ledger_row(test_db, "DEL_CLO")
        resp = client.delete("/api/portfolios/DEL_CLO", headers=admin_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "PORTFOLIO_HAS_DEPENDENT_DATA"
        assert test_db.query(LedgerEntry).filter_by(portfolio_code="DEL_CLO").count() == 1

    def test_empty_ledger_portfolio_deletable(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_DEL_E")
        create_portfolio(test_db, code="DEL_EMP", kind="ledger",
                         owner_investor_code="INV_DEL_E", status="draft")
        resp = client.delete("/api/portfolios/DEL_EMP", headers=admin_headers)
        assert resp.status_code == 200  # 无台账行的空壳可删