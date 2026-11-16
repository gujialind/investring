"""#887：dividend_config_service 解析单点、写语义（SPEC §1.2）、审计与 key scope。"""

import json
from datetime import date

import pytest

from app.constants.api_key_scopes import key_allows
from app.models.audit_log import AuditLog
from app.models.dividend_method_config import DividendMethodConfig
from app.services import dividend_config_service as svc
from app.services.exceptions import BusinessError, NotFoundError
from tests.factories import create_portfolio, create_product, create_platform

TODAY = date(2026, 11, 16)
HIST = date(2026, 11, 15)
FUTURE = date(2026, 11, 20)
PORT = "CFG_PORT"
PRODUCT = "000300.OF"
PLATFORM = "MYCF"


@pytest.fixture
def env(test_db, monkeypatch):
    monkeypatch.setattr(svc, "business_today", lambda: TODAY)
    create_portfolio(test_db, code=PORT)
    create_product(test_db, code=PRODUCT, market="CN_OTC")
    create_platform(test_db, code=PLATFORM)
    return test_db


def _row(db, *, source="explicit", method="reinvest_dividend", eff=TODAY,
         market="CN_OTC", product=PRODUCT, platform=PLATFORM, port=PORT):
    r = DividendMethodConfig(
        portfolio_code=port, product_code=product, market=market, platform_code=platform,
        dividend_method=method, effective_from=eff, source=source,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _create(db, *, method="cash_dividend", eff=TODAY, market="CN_OTC",
            product=PRODUCT, platform=PLATFORM, port=PORT):
    return svc.create_config(
        db, portfolio_code=port, product_code=product, market=market,
        platform_code=platform, dividend_method=method, effective_from=eff,
    )


def _audits(db):
    return db.query(AuditLog).filter(
        AuditLog.resource_type == "dividend_config"
    ).order_by(AuditLog.id).all()


def _payload(value):
    return json.loads(value) if isinstance(value, str) else value


class TestResolve:
    def test_latest_effective_interval(self, env):
        _row(env, method="cash_dividend", eff=date(2026, 11, 10))
        _row(env, method="reinvest_dividend", eff=date(2026, 11, 14))
        assert svc.resolve_dividend_method(
            env, PORT, PRODUCT, "CN_OTC", PLATFORM, TODAY).dividend_method == "reinvest_dividend"
        assert svc.resolve_dividend_method(
            env, PORT, PRODUCT, "CN_OTC", PLATFORM, date(2026, 11, 12)).dividend_method == "cash_dividend"
        assert svc.resolve_dividend_method(
            env, PORT, PRODUCT, "CN_OTC", PLATFORM, date(2026, 11, 9)) is None

    def test_source_filter_explicit_hides_default(self, env):
        _row(env, source="default", eff=date(2026, 11, 10))
        assert svc.resolve_dividend_method(
            env, PORT, PRODUCT, "CN_OTC", PLATFORM, TODAY, source_filter="explicit") is None
        assert svc.resolve_dividend_method(
            env, PORT, PRODUCT, "CN_OTC", PLATFORM, TODAY).source == "default"

    def test_future_row_only_effective_on_its_date(self, env):
        _row(env, eff=FUTURE)
        assert svc.resolve_dividend_method(env, PORT, PRODUCT, "CN_OTC", PLATFORM, TODAY) is None
        assert svc.resolve_dividend_method(env, PORT, PRODUCT, "CN_OTC", PLATFORM, FUTURE) is not None

    def test_resolve_endpoint_no_source_filter(self, env):
        _row(env, source="default", eff=date(2026, 11, 10))
        row = svc.resolve_config(
            env, portfolio_code=PORT, product_code=PRODUCT, market=None,
            platform_code=PLATFORM, entitlement_date=TODAY)
        assert row is not None and row.source == "default"


class TestCreate:
    def test_append_explicit_with_audit(self, env):
        row = _create(env, method="cash_dividend")
        assert row.source == "explicit"
        assert row.dividend_method == "cash_dividend"
        audits = _audits(env)
        assert len(audits) == 1
        assert audits[0].action == "create"
        assert _payload(audits[0].new_value)["source"] == "explicit"

    def test_promote_default_regardless_of_date(self, env):
        existing = _row(env, source="default", method="reinvest_dividend", eff=HIST)
        row = _create(env, method="cash_dividend", eff=HIST)
        assert row.id == existing.id
        assert row.source == "explicit"
        assert row.dividend_method == "cash_dividend"
        audits = _audits(env)
        assert len(audits) == 1
        assert audits[0].action == "update"
        assert _payload(audits[0].old_value)["source"] == "default"
        assert _payload(audits[0].new_value)["source"] == "explicit"

    def test_replace_today_explicit(self, env):
        _row(env, method="reinvest_dividend", eff=TODAY)
        row = _create(env, method="cash_dividend", eff=TODAY)
        assert row.dividend_method == "cash_dividend"
        assert _audits(env)[0].action == "update"

    def test_replace_future_explicit(self, env):
        _row(env, method="reinvest_dividend", eff=FUTURE)
        row = _create(env, method="cash_dividend", eff=FUTURE)
        assert row.dividend_method == "cash_dividend"

    def test_historical_explicit_different_method_rejected(self, env):
        _row(env, method="reinvest_dividend", eff=HIST)
        with pytest.raises(BusinessError) as exc:
            _create(env, method="cash_dividend", eff=HIST)
        assert exc.value.code == "INVALID_PARAM"
        env.expire_all()
        assert env.query(DividendMethodConfig).one().dividend_method == "reinvest_dividend"
        assert _audits(env) == []

    def test_same_method_idempotent_no_audit(self, env):
        _row(env, method="reinvest_dividend", eff=HIST)
        row = _create(env, method="reinvest_dividend", eff=HIST)
        assert row.dividend_method == "reinvest_dividend"
        assert _audits(env) == []

    def test_market_guard_hk_rejected(self, env):
        with pytest.raises(BusinessError) as exc:
            _create(env, market="HK_MUTUAL")
        assert exc.value.code == "INVALID_PARAM"
        assert "不支持分红方式配置" in exc.value.message

    def test_market_guard_exchange_rejected(self, env):
        with pytest.raises(BusinessError) as exc:
            _create(env, market="CN_EXCHANGE")
        assert exc.value.code == "INVALID_PARAM"

    def test_invalid_method_rejected(self, env):
        with pytest.raises(BusinessError) as exc:
            _create(env, method="dividend_pending")
        assert exc.value.code == "INVALID_PARAM"

    def test_market_omitted_autofill(self, env):
        row = _create(env, market=None)
        assert row.market == "CN_OTC"

    def test_portfolio_not_found(self, env):
        with pytest.raises(NotFoundError) as exc:
            _create(env, port="NO_SUCH_PORT")
        assert exc.value.code == "NOT_FOUND"

    def test_platform_not_found(self, env):
        with pytest.raises(NotFoundError) as exc:
            _create(env, platform="NO_SUCH_PLAT")
        assert exc.value.code == "PLATFORM_NOT_FOUND"

    def test_product_not_found_explicit_market(self, env):
        """B1：显式 market + 不存在产品 → PRODUCT_NOT_FOUND（杜绝复合外键违约 500）。"""
        with pytest.raises(NotFoundError) as exc:
            _create(env, product="NO_SUCH.OF", market="CN_OTC")
        assert exc.value.code == "PRODUCT_NOT_FOUND"
        assert env.query(DividendMethodConfig).count() == 0


class TestDelete:
    def test_delete_today_ok_with_audit(self, env):
        row = _row(env, eff=TODAY)
        svc.delete_config(env, row.id)
        env.expire_all()
        assert env.query(DividendMethodConfig).count() == 0
        assert _audits(env)[0].action == "delete"

    def test_delete_future_ok(self, env):
        row = _row(env, eff=FUTURE)
        svc.delete_config(env, row.id)
        assert env.query(DividendMethodConfig).count() == 0

    def test_delete_historical_rejected(self, env):
        row = _row(env, eff=HIST)
        with pytest.raises(BusinessError) as exc:
            svc.delete_config(env, row.id)
        assert exc.value.code == "INVALID_PARAM"
        assert env.query(DividendMethodConfig).count() == 1

    def test_delete_default_today_ok(self, env):
        row = _row(env, source="default", eff=TODAY)
        svc.delete_config(env, row.id)
        assert env.query(DividendMethodConfig).count() == 0

    def test_delete_not_found(self, env):
        with pytest.raises(NotFoundError):
            svc.delete_config(env, 999999)


class TestApiKeyScopes:
    def test_get_requires_ledger_read(self):
        assert key_allows(["ledger:read"], "GET", "/api/dividend-configs")
        assert key_allows(["ledger:read"], "GET", "/api/dividend-configs/resolve")
        assert not key_allows([], "GET", "/api/dividend-configs")

    def test_write_requires_ledger_write(self):
        assert key_allows(["ledger:write"], "POST", "/api/dividend-configs")
        assert key_allows(["ledger:write"], "DELETE", "/api/dividend-configs/{config_id}")
        assert not key_allows(["ledger:read"], "POST", "/api/dividend-configs")
        assert not key_allows(["ledger:read"], "DELETE", "/api/dividend-configs/{config_id}")