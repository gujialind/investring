"""#887：分红方式配置 API（/api/dividend-configs）CRUD、市场收口、resolve、鉴权与审计。"""

from datetime import date

import pytest

from app.models.audit_log import AuditLog
from app.models.dividend_method_config import DividendMethodConfig
from tests.factories import create_portfolio, create_product, create_platform

PORT = "CFG_API"
PRODUCT = "000300.OF"
PLATFORM = "MYCF"
HIST = date(2020, 1, 1)
FUTURE = date(2099, 1, 1)
KEY = dict(portfolio_code=PORT, product_code=PRODUCT, market="CN_OTC", platform_code=PLATFORM)


@pytest.fixture
def env(test_db):
    create_portfolio(test_db, code=PORT)
    create_product(test_db, code=PRODUCT, market="CN_OTC")
    create_platform(test_db, code=PLATFORM)
    return test_db


def _body(**overrides):
    body = dict(portfolio_code=PORT, product_code=PRODUCT, market="CN_OTC",
                platform_code=PLATFORM, dividend_method="cash_dividend",
                effective_from=FUTURE.isoformat())
    body.update(overrides)
    return body


def _row(db, *, source="explicit", method="reinvest_dividend", eff=FUTURE):
    r = DividendMethodConfig(
        portfolio_code=PORT, product_code=PRODUCT, market="CN_OTC", platform_code=PLATFORM,
        dividend_method=method, effective_from=eff, source=source,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


class TestListAndResolve:
    def test_list_empty(self, client, admin_headers, env):
        resp = client.get("/api/dividend-configs", headers=admin_headers, params=KEY)
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        assert resp.json()["total"] == 0

    def test_resolve_no_config_returns_null(self, client, admin_headers, env):
        resp = client.get("/api/dividend-configs/resolve", headers=admin_headers,
                          params={**KEY, "entitlement_date": "2026-11-16"})
        assert resp.status_code == 200
        assert resp.json() is None

    def test_resolve_default_hit(self, client, admin_headers, env):
        _row(env, source="default", eff=HIST)
        resp = client.get("/api/dividend-configs/resolve", headers=admin_headers,
                          params={**KEY, "entitlement_date": "2026-11-16"})
        assert resp.status_code == 200
        assert resp.json()["source"] == "default"
        assert resp.json()["dividend_method"] == "reinvest_dividend"

    def test_resolve_before_effective_returns_null(self, client, admin_headers, env):
        _row(env, eff=FUTURE)
        resp = client.get("/api/dividend-configs/resolve", headers=admin_headers,
                          params={**KEY, "entitlement_date": "2026-11-16"})
        assert resp.json() is None

    def test_list_after_create(self, client, admin_headers, env):
        client.post("/api/dividend-configs", headers=admin_headers, json=_body())
        resp = client.get("/api/dividend-configs", headers=admin_headers, params=KEY)
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["source"] == "explicit"


class TestCreate:
    def test_create_explicit(self, client, admin_headers, env):
        resp = client.post("/api/dividend-configs", headers=admin_headers, json=_body())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"] == "explicit"
        assert data["market"] == "CN_OTC"
        assert data["dividend_method"] == "cash_dividend"

    def test_create_market_omitted_autofill(self, client, admin_headers, env):
        body = _body()
        del body["market"]
        resp = client.post("/api/dividend-configs", headers=admin_headers, json=body)
        assert resp.status_code == 200
        assert resp.json()["market"] == "CN_OTC"

    def test_viewer_cannot_create(self, client, viewer_headers, env):
        resp = client.post("/api/dividend-configs", headers=viewer_headers, json=_body())
        assert resp.status_code == 403

    def test_hk_market_rejected(self, client, admin_headers, env):
        resp = client.post("/api/dividend-configs", headers=admin_headers,
                           json=_body(market="HK_MUTUAL"))
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_exchange_market_rejected(self, client, admin_headers, env):
        resp = client.post("/api/dividend-configs", headers=admin_headers,
                           json=_body(market="CN_EXCHANGE"))
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_invalid_method_rejected(self, client, admin_headers, env):
        resp = client.post("/api/dividend-configs", headers=admin_headers,
                           json=_body(dividend_method="dividend_pending"))
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_explicit_null_required_field_rejected(self, client, admin_headers, env):
        resp = client.post("/api/dividend-configs", headers=admin_headers,
                           json=_body(dividend_method=None))
        assert resp.status_code == 422

    def test_missing_portfolio_rejected(self, client, admin_headers, env):
        resp = client.post("/api/dividend-configs", headers=admin_headers,
                           json=_body(portfolio_code="NO_SUCH"))
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "NOT_FOUND"

    def test_promote_default_via_api(self, client, admin_headers, env):
        _row(env, source="default", method="reinvest_dividend", eff=FUTURE)
        resp = client.post("/api/dividend-configs", headers=admin_headers,
                           json=_body(dividend_method="cash_dividend"))
        assert resp.status_code == 200
        assert resp.json()["source"] == "explicit"
        assert resp.json()["dividend_method"] == "cash_dividend"
        assert env.query(DividendMethodConfig).count() == 1

    def test_create_writes_audit(self, client, admin_headers, env):
        client.post("/api/dividend-configs", headers=admin_headers, json=_body())
        audits = env.query(AuditLog).filter_by(resource_type="dividend_config").all()
        assert len(audits) == 1
        assert audits[0].action == "create"


class TestDelete:
    def test_delete_future_ok(self, client, admin_headers, env):
        row = _row(env, eff=FUTURE)
        resp = client.delete(f"/api/dividend-configs/{row.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "分红方式配置删除成功"
        assert env.query(DividendMethodConfig).count() == 0

    def test_delete_historical_rejected(self, client, admin_headers, env):
        row = _row(env, eff=HIST)
        resp = client.delete(f"/api/dividend-configs/{row.id}", headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        assert env.query(DividendMethodConfig).count() == 1

    def test_viewer_cannot_delete(self, client, viewer_headers, env):
        row = _row(env, eff=FUTURE)
        resp = client.delete(f"/api/dividend-configs/{row.id}", headers=viewer_headers)
        assert resp.status_code == 403

    def test_delete_not_found(self, client, admin_headers, env):
        resp = client.delete("/api/dividend-configs/999999", headers=admin_headers)
        assert resp.status_code == 404