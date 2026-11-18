# ============================================================================
# 集成测试：组合类型/归属创建后不可改（#905 PR1，验收 8）
# ============================================================================

from tests.factories import create_portfolio, create_investor
from app.models.portfolio import Portfolio


class TestKindImmutable:
    def test_kind_cannot_change(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_I")
        create_portfolio(test_db, code="LED_I", kind="ledger", owner_investor_code="INV_I", status="active")
        resp = client.put("/api/portfolios/LED_I", json={"kind": "nav"}, headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        p = test_db.query(Portfolio).filter(Portfolio.code == "LED_I").first()
        assert p.kind == "ledger"

    def test_owner_cannot_change(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_I2")
        create_investor(test_db, code="INV_I3")
        create_portfolio(test_db, code="LED_I2", kind="ledger", owner_investor_code="INV_I2", status="active")
        resp = client.put("/api/portfolios/LED_I2", json={"owner_investor_code": "INV_I3"}, headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        p = test_db.query(Portfolio).filter(Portfolio.code == "LED_I2").first()
        assert p.owner_investor_code == "INV_I2"

    def test_nav_kind_cannot_change_to_ledger(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="NAV_I", status="active")
        resp = client.put("/api/portfolios/NAV_I", json={"kind": "ledger"}, headers=admin_headers)
        assert resp.status_code == 422
        p = test_db.query(Portfolio).filter(Portfolio.code == "NAV_I").first()
        assert p.kind == "nav"

    def test_normal_update_still_works(self, client, admin_headers, test_db):
        """净值型零行为变更：不含 kind/owner 的正常 PUT 仍成功。"""
        create_portfolio(test_db, code="NAV_U", status="active")
        resp = client.put("/api/portfolios/NAV_U", json={"name": "改名"}, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "改名"