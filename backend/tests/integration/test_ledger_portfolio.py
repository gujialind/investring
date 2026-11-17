# ============================================================================
# 集成测试：台账型组合创建与手动激活（#905 PR1）
# ============================================================================

from tests.factories import create_portfolio, create_investor
from app.models.portfolio import Portfolio
from app.models.subscription import Subscription


class TestLedgerCreate:
    def test_create_ledger_with_owner(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_L")
        resp = client.post(
            "/api/portfolios",
            json={"code": "LED1", "name": "台账", "kind": "ledger", "owner_investor_code": "INV_L"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        d = resp.json()
        assert d["kind"] == "ledger"
        assert d["owner_investor_code"] == "INV_L"
        assert d["status"] == "draft"

    def test_create_ledger_without_owner_rejected(self, client, admin_headers, test_db):
        resp = client.post(
            "/api/portfolios",
            json={"code": "LED2", "name": "台账", "kind": "ledger"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_create_ledger_unknown_owner_404(self, client, admin_headers, test_db):
        resp = client.post(
            "/api/portfolios",
            json={"code": "LED3", "name": "台账", "kind": "ledger", "owner_investor_code": "NO_INV"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_create_nav_with_owner_rejected(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_N")
        resp = client.post(
            "/api/portfolios",
            json={"code": "NAV1", "name": "净值", "kind": "nav", "owner_investor_code": "INV_N"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_create_invalid_kind_rejected(self, client, admin_headers, test_db):
        resp = client.post(
            "/api/portfolios",
            json={"code": "XK", "name": "x", "kind": "foo"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_create_default_kind_nav(self, client, admin_headers, test_db):
        """净值型零行为变更：不传 kind 仍建 nav、owner 为 None。"""
        resp = client.post(
            "/api/portfolios",
            json={"code": "NAV_D", "name": "默认"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["kind"] == "nav"
        assert resp.json()["owner_investor_code"] is None
        assert resp.json()["status"] == "draft"


class TestLedgerActivate:
    def test_activate_without_subscription(self, client, admin_headers, test_db):
        """验收 1：手动激活即可用，不经任何申购。"""
        create_investor(test_db, code="INV_A")
        client.post(
            "/api/portfolios",
            json={"code": "LED_A", "name": "台账", "kind": "ledger", "owner_investor_code": "INV_A"},
            headers=admin_headers,
        )
        resp = client.post("/api/portfolios/LED_A/activate", headers=admin_headers)
        assert resp.status_code == 200
        assert test_db.query(Subscription).filter(Subscription.portfolio_code == "LED_A").count() == 0
        p = test_db.query(Portfolio).filter(Portfolio.code == "LED_A").first()
        assert p.status == "active"
        assert p.started_at is not None

    def test_activate_nav_rejected(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="NAV_ACT", status="draft")
        resp = client.post("/api/portfolios/NAV_ACT/activate", headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PORTFOLIO_KIND_NOT_ALLOWED"

    def test_activate_already_active_rejected(self, client, admin_headers, test_db):
        create_investor(test_db, code="INV_AA")
        create_portfolio(test_db, code="LED_AA", kind="ledger", owner_investor_code="INV_AA", status="active")
        resp = client.post("/api/portfolios/LED_AA/activate", headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_STATUS"

    def test_activate_nonexistent_404(self, client, admin_headers):
        resp = client.post("/api/portfolios/NO_SUCH/activate", headers=admin_headers)
        assert resp.status_code == 404