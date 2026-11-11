"""持久化会话、独立代理 Key 与权限边界（#797）。"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.auth import ApiKey, AuthSession
from app.models.audit_log import AuditLog
from app.services import auth_service, investor_service
from app.models.investor import Investor
from app.utils.security import create_access_token, decode_token
from tests.factories import create_investor


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def issue_key(client, admin_headers, **overrides):
    response = client.post("/api/auth/keys", headers=admin_headers, json={"label": "Analyst", **overrides})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_renewal_preserves_family_and_logout_revokes_all_tokens(client, test_db, admin_token):
    original = decode_token(admin_token)
    renewed = client.post("/api/auth/renew", headers=headers(admin_token))
    assert renewed.status_code == 200
    fresh = renewed.json()["token"]
    claims = decode_token(fresh)
    assert claims["sid"] == original["sid"]
    assert claims["jti"] != original["jti"]
    assert test_db.get(AuthSession, claims["sid"]).revoked_at is None
    other = auth_service.create_session(test_db, test_db.get(Investor, "ADMIN"))["token"]
    for token in (admin_token, fresh):
        assert client.get("/api/auth/status", headers=headers(token)).status_code == 200
    assert client.post("/api/auth/logout", headers=headers(fresh)).status_code == 200
    for token in (admin_token, fresh):
        assert client.post("/api/auth/renew", headers=headers(token)).status_code == 401
    assert client.get("/api/auth/status", headers=headers(other)).status_code == 200


def test_rolling_session_has_no_absolute_age_cap(client, test_db, admin_token):
    sid = decode_token(admin_token)["sid"]
    session = test_db.get(AuthSession, sid)
    session.created_at -= timedelta(days=100)
    session.expires_at = auth_service.utcnow() + timedelta(hours=1)
    test_db.flush()
    result = client.post("/api/auth/renew", headers=headers(admin_token))
    assert result.status_code == 200
    test_db.refresh(session)
    assert session.expires_at > auth_service.utcnow() + timedelta(days=6)
    # Extended family lifetime must not resurrect an individually expired token.
    expired = create_access_token({"sub": "ADMIN", "sid": sid}, expires_delta=timedelta(seconds=-5))
    assert client.get("/api/auth/status", headers=headers(expired)).status_code == 401


def test_old_stateless_and_expired_session_rejected(client, test_db, admin_token):
    legacy = create_access_token({"sub": "ADMIN", "role": "admin"})
    assert client.get("/api/auth/status", headers=headers(legacy)).status_code == 401
    session = test_db.get(AuthSession, decode_token(admin_token)["sid"])
    session.expires_at = auth_service.utcnow() - timedelta(seconds=1)
    test_db.flush()
    assert client.post("/api/auth/renew", headers=headers(admin_token)).status_code == 401


@pytest.mark.parametrize("generic_update", [False, True])
def test_password_change_revokes_target_sessions_not_admin_or_key(client, test_db, admin_headers, generic_update):
    owner = create_investor(test_db, code="KEY_OWNER", password="old_pass")
    tokens = [auth_service.create_session(test_db, owner)["token"] for _ in range(2)]
    key = issue_key(client, admin_headers, investor_code=owner.code)
    if generic_update:
        investor_service.update_investor(test_db, code=owner.code, updates={"password": "new_pass"})
        test_db.flush()
    else:
        result = client.put("/api/auth/password", headers=admin_headers, json={"target_code": owner.code, "new_password": "new_pass"})
        assert result.status_code == 200
    for token in tokens:
        assert client.get("/api/auth/status", headers=headers(token)).status_code == 401
    assert client.get("/api/auth/status", headers=admin_headers).status_code == 200
    assert client.get("/api/auth/status", headers=headers(key["token"])).status_code == 200


def test_key_secret_one_time_and_persistent_revocation(client, test_db, admin_headers):
    issued = issue_key(client, admin_headers)
    token = issued["token"]
    info = issued["key"]
    assert info["expires_at"] is None
    row = test_db.get(ApiKey, info["id"])
    assert row.secret_hash not in token
    listing = client.get("/api/auth/keys", headers=admin_headers)
    assert token not in listing.text and row.secret_hash not in listing.text
    status = client.get("/api/auth/status", headers=headers(token)).json()
    assert status["credential_type"] == "api_key" and status["label"] == "Analyst"
    assert status["credential_id"] == info["id"]
    for _ in range(2):
        assert client.post(f'/api/auth/keys/{info["id"]}/revoke', headers=admin_headers).status_code == 200
    assert client.get("/api/portfolios", headers=headers(token)).status_code == 401
    audits = test_db.query(AuditLog).filter(AuditLog.resource_type == "api_key", AuditLog.resource_id == info["id"]).all()
    assert sorted(a.action for a in audits) == ["create", "revoke"]
    assert all(a.credential_id and a.investor_code == "ADMIN" for a in audits)
    assert token not in repr([a.new_value for a in audits])
    assert row.secret_hash not in repr([a.new_value for a in audits])


@pytest.mark.parametrize("body", [
    {"label": " "}, {"label": "bad\nlabel"}, {"label": "x" * 101},
    {"scopes": []}, {"scopes": ["admin"]}, {"unexpected": True},
    {"expires_at": "2020-01-01T00:00:00Z"}, {"expires_at": "2099-01-01T00:00:00"},
])
def test_key_invalid_inputs(client, admin_headers, body):
    response = client.post("/api/auth/keys", headers=admin_headers, json={"label": "test", **body})
    assert response.status_code == 422


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/auth/keys"), ("POST", "/api/auth/keys"),
    ("POST", "/api/auth/keys/nonexistent/revoke"), ("POST", "/api/auth/renew"),
    ("POST", "/api/auth/logout"), ("PUT", "/api/auth/password"),
    ("POST", "/api/investors"), ("GET", "/api/system/data-sources"),
    ("DELETE", "/api/trades/99999"),
])
def test_even_write_keys_cannot_access_auth_admin_or_delete(client, admin_headers, method, path):
    key = issue_key(client, admin_headers, scopes=["ledger:write"])
    response = client.request(method, path, headers=headers(key["token"]), json={})
    assert response.status_code == 403, response.text


def test_read_scope_and_owner_role_remain_restrictive(client, admin_headers, viewer_headers):
    assert client.post("/api/auth/keys", headers=viewer_headers, json={"label": "no"}).status_code == 403
    read_key = issue_key(client, admin_headers)
    assert client.get("/api/portfolios", headers=headers(read_key["token"])).status_code == 200
    assert client.post("/api/trades", headers=headers(read_key["token"]), json={}).status_code == 403
    viewer_key = issue_key(client, admin_headers, investor_code="VIEWER", scopes=["ledger:write"])
    assert client.post("/api/trades", headers=headers(viewer_key["token"]), json={}).status_code == 403


def test_read_preview_has_no_business_writes(client, test_db, admin_headers):
    key = issue_key(client, admin_headers)
    before = test_db.query(AuditLog).count()
    # Missing record still reaches the read-only business route, not the scope guard.
    response = client.get("/api/trades/999999/preview", headers=headers(key["token"]))
    assert response.status_code == 404
    assert test_db.query(AuditLog).count() == before


def test_expired_and_tampered_key_rejected(client, test_db, admin_headers):
    key = issue_key(client, admin_headers, expires_at="2099-01-01T00:00:00+08:00")
    token = key["token"]
    assert client.get("/api/auth/status", headers=headers(token + "x")).status_code == 401
    altered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert client.get("/api/auth/status", headers=headers(altered)).status_code == 401
    row = test_db.get(ApiKey, key["key"]["id"])
    row.expires_at = auth_service.utcnow() - timedelta(seconds=1)
    test_db.flush()
    assert client.get("/api/auth/status", headers=headers(token)).status_code == 401


def test_account_recreation_does_not_revive_credentials(client, test_db, admin_headers):
    owner = create_investor(test_db, code="RECREATED", password="old_pass")
    session = auth_service.create_session(test_db, owner)
    key = issue_key(client, admin_headers, investor_code=owner.code)
    investor_service.delete_investor(test_db, owner.code)
    test_db.flush()
    create_investor(test_db, code="RECREATED", password="new_pass")
    for token in (session["token"], key["token"]):
        assert client.get("/api/auth/status", headers=headers(token)).status_code == 401


def test_key_financial_create_preview_confirm_audit(client, test_db, admin_headers, json_log_capture):
    from app.models.trade import Trade
    from tests.conftest import log_lines
    from tests.factories import (create_portfolio, create_product, create_platform,
                                 create_trade, create_price_record, ensure_trading_day)

    key = issue_key(client, admin_headers, scopes=["ledger:write"])
    auth = headers(key["token"])
    create_portfolio(test_db, code="KEY_PORT", status="active")
    create_product(test_db, code="KEY_FUND", market="CN_OTC", product_type="OEF",
                   asset_class_code="ASSET_STOCK", confirm_days=1)
    create_platform(test_db, code="KEY_PLATFORM")
    for day in (6, 7, 8):
        ensure_trading_day(test_db, date(2025, 10, day), is_open=True)
    create_trade(test_db, "KEY_PORT", "CASH", "", trade_type="buy", amount=50000,
                 price=None, platform_code="KEY_PLATFORM", trade_date=date(2025, 10, 3),
                 confirm_date=date(2025, 10, 3), status="confirmed")
    create_price_record(test_db, "KEY_FUND", "CN_OTC", date(2025, 10, 6), unit_price=1.25)
    created = client.post("/api/trades", headers=auth, json={
        "portfolio_code": "KEY_PORT", "product_code": "KEY_FUND", "market": "CN_OTC",
        "trade_type": "buy", "amount": 10000, "platform_code": "KEY_PLATFORM", "trade_date": "2025-10-06",
    })
    assert created.status_code == 200, created.text
    trade_id = created.json()["id"]
    before = client.get(f"/api/trades/{trade_id}", headers=auth).json()
    rows_before = [{c.name: getattr(t, c.name) for c in Trade.__table__.columns}
                   for t in test_db.query(Trade).filter_by(portfolio_code="KEY_PORT").order_by(Trade.id)]
    audit_count = test_db.query(AuditLog).count()
    preview = client.get(f"/api/trades/{trade_id}/preview", headers=auth)
    assert preview.status_code == 200, preview.text
    assert test_db.query(AuditLog).count() == audit_count
    assert client.get(f"/api/trades/{trade_id}", headers=auth).json() == before
    test_db.expire_all()
    assert rows_before == [{c.name: getattr(t, c.name) for c in Trade.__table__.columns}
                           for t in test_db.query(Trade).filter_by(portfolio_code="KEY_PORT").order_by(Trade.id)]
    confirmed = client.post(f"/api/trades/{trade_id}/confirm", headers=auth)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["trade"]["status"] == "confirmed"
    for field in ("price", "shares", "amount", "actual_amount", "confirm_date"):
        assert confirmed.json()["trade"][field] == preview.json()["preview"][field]
    audits = test_db.query(AuditLog).filter_by(resource_type="trade", resource_id=str(trade_id)).all()
    assert {row.action for row in audits} == {"create", "confirm"}
    assert all(row.investor_code == "ADMIN" and row.credential_id == key["key"]["id"]
               and row.credential_label == "Analyst" for row in audits)
    access = [line for line in log_lines(json_log_capture) if line.get("path") == f"/api/trades/{trade_id}/preview"]
    assert access and all(line["credential_id"] == key["key"]["id"] for line in access)
    assert key["token"] not in repr(log_lines(json_log_capture))


@pytest.mark.parametrize("scope", ["ledger:read", "ledger:write", "snapshot:ops"])
def test_key_route_matrix_defaults_to_deny(client, admin_headers, scope):
    import re
    from app.main import app
    from app.constants.api_key_scopes import (
        READ_PATHS, SNAPSHOT_OPS_OPERATIONS, WRITE_OPERATIONS,
    )

    operations = {(method.upper(), path) for path, methods in app.openapi()["paths"].items()
                  for method in methods if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}}
    assert {("GET", path) for path in READ_PATHS} | WRITE_OPERATIONS | SNAPSHOT_OPS_OPERATIONS <= operations
    overrides = {}
    if scope == "snapshot:ops":
        # 该 scope 创建时强制到期时间（≤90 天）；其余 scope 保持 helper 默认的 None
        overrides["expires_at"] = (
            auth_service.utcnow() + timedelta(days=30)
        ).replace(tzinfo=timezone.utc).isoformat()
    key = issue_key(client, admin_headers, scopes=[scope], **overrides)
    for method, path in sorted(operations):
        if not path.startswith("/api/") or path == "/api/auth/login":
            continue
        # 期望值由三张白名单独立拼装，不调用 key_allows——否则与 get_current_user
        # 同源，误扩 SNAPSHOT_OPS_OPERATIONS 时断言会同步变绿，守门失效。
        allowed = (
            ((method, path) in SNAPSHOT_OPS_OPERATIONS and scope == "snapshot:ops")
            or (method == "GET" and path in READ_PATHS and scope in {"ledger:read", "ledger:write"})
            or (scope == "ledger:write" and (method, path) in WRITE_OPERATIONS)
        )
        concrete = re.sub(r"\{([^}]+)\}", lambda m: "999999999" if m[1] in {"id", "job_id"} else "MISSING_KEY_TEST", path)
        result = client.request(method, concrete, headers=headers(key["token"]), json={})
        if allowed:
            assert result.status_code not in {401, 403, 500}, (method, path, result.text)
        else:
            assert result.status_code == 403, (method, path, result.text)


def test_deleted_session_cannot_renew_after_authentication(test_db):
    from app.services.exceptions import BusinessError

    with pytest.raises(BusinessError) as exc:
        auth_service.renew_session(test_db, "deleted", test_db.get(Investor, "ADMIN"))
    assert exc.value.code == "AUTH_REQUIRED"


def test_credential_revocation_survives_process_restart(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models.base import Base
    from app.config import get_settings

    url = f"sqlite:///{tmp_path / 'credentials.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = Investor(code="PROCESS", name="Process test", role="admin", password_hash="unused")
        db.add(owner)
        db.flush()
        token = auth_service.create_session(db, owner)["token"]
        key, secret = auth_service.create_api_key(db, investor_code=owner.code, label="process-test",
                                                scopes=["ledger:read"], expires_at=None)
        key_id = key.id
        sid = decode_token(token)["sid"]
        db.commit()
    engine.dispose()
    script = '''
import json, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.services.auth_service import authenticate_session, authenticate_api_key
from app.services.exceptions import BusinessError
data = json.load(sys.stdin)
engine = create_engine(data["url"])
with Session(engine) as db:
    result = []
    for fn, token in ((authenticate_session, data["session"]), (authenticate_api_key, data["key"])):
        try:
            fn(db, token)
            result.append(True)
        except BusinessError as exc:
            assert exc.http_status == 401
            result.append(False)
print(json.dumps(result))
engine.dispose()
'''
    environment = {**os.environ, "SECRET_KEY": get_settings().secret_key,
                   "DATABASE_URL": url, "SCHEDULER_ENABLED": "false", "DEBUG": "false"}
    def fresh_process():
        result = subprocess.run([sys.executable, "-c", script],
                                input=json.dumps({"url": url, "session": token, "key": secret}),
                                text=True, capture_output=True, env=environment, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert fresh_process() == [True, True]
    with Session(engine) as db:
        auth_service.revoke_session(db, sid)
        auth_service.revoke_api_key(db, key_id)
        db.commit()
    engine.dispose()
    assert fresh_process() == [False, False]
    assert fresh_process() == [False, False]


# ---------------------------------------------------------------------------
# snapshot:ops 历史补录闭环（#855）
# ---------------------------------------------------------------------------

def _expiry_iso(days: int) -> str:
    return (auth_service.utcnow() + timedelta(days=days)).replace(tzinfo=timezone.utc).isoformat()


def _ops_key(client, admin_headers, **overrides):
    options = {"scopes": ["ledger:write", "snapshot:ops"], "expires_at": _expiry_iso(30)}
    options.update(overrides)
    return issue_key(client, admin_headers, **options)


def _seed_cash_snapshots(test_db, portfolio_code: str, days):
    from tests.factories import create_investor_holding, create_position_snapshot, create_value_snapshot

    for snapshot_date, amount in days:
        create_position_snapshot(
            test_db, portfolio_code, "CASH", "", snapshot_date=snapshot_date,
            cash_amount=amount, unit_price=None, cost_price=None,
            market_value=amount, platform_code="MYCF",
        )
        create_value_snapshot(test_db, portfolio_code, snapshot_date, amount, amount, 1.0)
        create_investor_holding(test_db, portfolio_code, "VIEWER", snapshot_date, shares=amount)


def test_snapshot_ops_key_full_backfill_loop(client, test_db, admin_headers):
    """读写 + snapshot:ops 打通「删窗→预览→重算」闭环，且不借任何白名单外路由。"""
    from app.models import PortfolioValueSnapshot
    from tests.factories import create_portfolio, ensure_trading_day

    key = _ops_key(client, admin_headers)
    auth = headers(key["token"])
    start, end = date(2025, 5, 6), date(2025, 5, 7)
    for day in (start, end):
        ensure_trading_day(test_db, day, is_open=True)
    create_portfolio(test_db, code="OPS_PORT", status="active")
    _seed_cash_snapshots(test_db, "OPS_PORT", [(start, 10000.0), (end, 12000.0)])

    def snapshot_count() -> int:
        return test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code="OPS_PORT").count()

    dry_run = client.delete(f"/api/snapshots/OPS_PORT/bulk/{end.isoformat()}?dry_run=true", headers=auth)
    assert dry_run.status_code == 200, dry_run.text
    assert snapshot_count() == 2
    deleted = client.delete(f"/api/snapshots/OPS_PORT/bulk/{end.isoformat()}?confirm=true", headers=auth)
    assert deleted.status_code == 200, deleted.text
    assert snapshot_count() == 1

    body = {"portfolio_code": "OPS_PORT", "start_date": end.isoformat(), "end_date": end.isoformat()}
    preview = client.post("/api/snapshots/recalculate", headers=auth, json=body)
    assert preview.status_code == 422
    detail = preview.json()["detail"]
    assert detail["error"] == "CONFIRM_REQUIRED"
    assert detail["details"]["total_trading_days"] == 1
    assert detail["details"]["portfolios"][0]["effective_end_date"] == end.isoformat()

    done = client.post("/api/snapshots/recalculate", headers=auth, params={"confirm": True}, json=body)
    assert done.status_code == 200, done.text
    assert done.json()["results"][0]["processed_dates"] == [end.isoformat()]
    test_db.expire_all()
    assert snapshot_count() == 2


def test_snapshot_ops_key_requires_expiry_and_window_cap(client, admin_headers):
    missing = client.post(
        "/api/auth/keys", headers=admin_headers,
        json={"label": "ops", "scopes": ["ledger:write", "snapshot:ops"]},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["error"] == "INVALID_PARAM"

    over = client.post(
        "/api/auth/keys", headers=admin_headers,
        json={"label": "ops", "scopes": ["ledger:write", "snapshot:ops"], "expires_at": _expiry_iso(91)},
    )
    assert over.status_code == 422
    assert over.json()["detail"]["error"] == "INVALID_PARAM"

    # 89 天在窗口内；三 scope 组合合法
    issued = issue_key(
        client, admin_headers,
        scopes=["ledger:read", "ledger:write", "snapshot:ops"],
        expires_at=_expiry_iso(89),
    )
    assert issued["key"]["expires_at"] is not None


def test_snapshot_ops_key_accepts_exact_90_day_window(test_db, monkeypatch):
    """90 天边界在 service 层用冻结时钟验证（HTTP 层有毫秒级误差，不做等值断言）。"""
    from app.services.exceptions import BusinessError

    fixed = datetime(2026, 1, 1, 0, 0, 0)
    monkeypatch.setattr(auth_service, "utcnow", lambda: fixed)
    edge = (fixed + timedelta(days=90)).replace(tzinfo=timezone.utc)
    key, _ = auth_service.create_api_key(
        test_db, investor_code="ADMIN", label="edge", scopes=["snapshot:ops"], expires_at=edge,
    )
    assert key.expires_at == fixed + timedelta(days=90)

    with pytest.raises(BusinessError) as exc:
        auth_service.create_api_key(
            test_db, investor_code="ADMIN", label="over", scopes=["snapshot:ops"],
            expires_at=(fixed + timedelta(days=91)).replace(tzinfo=timezone.utc),
        )
    assert exc.value.code == "INVALID_PARAM"


def test_snapshot_ops_key_slides_idle_window(client, test_db, admin_headers):
    key = _ops_key(client, admin_headers, expires_at=_expiry_iso(1))
    row = test_db.get(ApiKey, key["key"]["id"])
    before = row.expires_at
    assert client.get("/api/auth/status", headers=headers(key["token"])).status_code == 200
    test_db.refresh(row)
    assert row.expires_at > before
    assert row.expires_at >= auth_service.utcnow() + timedelta(days=89)


def test_snapshot_ops_key_slide_is_throttled_and_expired_key_not_refreshed(client, test_db, admin_headers):
    key = _ops_key(client, admin_headers, expires_at=_expiry_iso(1))
    row = test_db.get(ApiKey, key["key"]["id"])
    assert client.get("/api/auth/status", headers=headers(key["token"])).status_code == 200
    test_db.refresh(row)
    first = row.expires_at
    assert client.get("/api/auth/status", headers=headers(key["token"])).status_code == 200
    test_db.refresh(row)
    assert row.expires_at == first, "24h 内二次认证不得重复顺延"

    expired_at = auth_service.utcnow() - timedelta(minutes=1)
    row.expires_at = expired_at
    test_db.flush()
    # MySQL DATETIME(0) 落库截断微秒、SQLite 保留，故以回读值为基线做等值断言，
    # 不拿内存中含微秒的 expired_at 比较（否则只在 MySQL CI 失败）。
    test_db.refresh(row)
    expired_stored = row.expires_at
    assert client.get("/api/auth/status", headers=headers(key["token"])).status_code == 401
    test_db.refresh(row)
    assert row.expires_at == expired_stored, "过期 Key 不得被顺延复活"


def test_plain_key_never_slides(client, test_db, admin_headers):
    key = issue_key(client, admin_headers, expires_at=_expiry_iso(1))
    row = test_db.get(ApiKey, key["key"]["id"])
    before = row.expires_at
    assert client.get("/api/auth/status", headers=headers(key["token"])).status_code == 200
    test_db.refresh(row)
    assert row.expires_at == before, "无 snapshot:ops 的 Key 不做滑动顺延"


def test_recalculate_proxy_key_requires_portfolio_code(client, admin_headers):
    key = _ops_key(client, admin_headers)
    for payload in (
        {"start_date": "2025-05-06", "end_date": "2025-05-07"},
        {"portfolio_code": None, "start_date": "2025-05-06", "end_date": "2025-05-07"},
        {"portfolio_code": "", "start_date": "2025-05-06", "end_date": "2025-05-07"},
        {"portfolio_code": "   ", "start_date": "2025-05-06", "end_date": "2025-05-07"},
    ):
        resp = client.post(
            "/api/snapshots/recalculate", headers=headers(key["token"]),
            params={"confirm": True}, json=payload,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"


def test_recalculate_snapshot_ops_key_requires_admin_role(client, admin_headers):
    key = _ops_key(client, admin_headers, investor_code="VIEWER")
    resp = client.post(
        "/api/snapshots/recalculate", headers=headers(key["token"]), params={"confirm": True},
        json={"portfolio_code": "ANY", "start_date": "2025-05-06", "end_date": "2025-05-06"},
    )
    assert resp.status_code == 403


def test_recalculate_without_confirm_returns_preview_and_never_executes(client, test_db, admin_headers, monkeypatch):
    from unittest.mock import MagicMock
    from tests.factories import create_portfolio, ensure_trading_day

    day = date(2025, 5, 6)
    ensure_trading_day(test_db, day, is_open=True)
    create_portfolio(test_db, code="PREVIEW_PORT", status="active")
    execute = MagicMock(side_effect=AssertionError("confirm 未通过时不得执行重算"))
    monkeypatch.setattr("app.routers.snapshots.recalculate_snapshots", execute)

    resp = client.post(
        "/api/snapshots/recalculate", headers=admin_headers,
        json={"portfolio_code": "PREVIEW_PORT", "start_date": day.isoformat(), "end_date": day.isoformat()},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "CONFIRM_REQUIRED"
    assert "1 个交易日" in detail["message"] and "1 个组合" in detail["message"]
    assert detail["details"] == {
        "portfolios": [{
            "portfolio_code": "PREVIEW_PORT",
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "effective_end_date": day.isoformat(),
            "trading_days": 1,
        }],
        "total_trading_days": 1,
    }
    execute.assert_not_called()


def test_recalculate_admin_session_empty_portfolio_code_means_all_active(client, test_db, admin_headers):
    """管理员会话传空串仍按全部 active 处理（守 `if portfolio_code:` 真值判断）。"""
    from tests.factories import create_portfolio, ensure_trading_day

    day = date(2025, 5, 6)
    ensure_trading_day(test_db, day, is_open=True)
    create_portfolio(test_db, code="ADMIN_ALL", status="active")
    for portfolio_code in ("", None):
        resp = client.post(
            "/api/snapshots/recalculate", headers=admin_headers, params={"confirm": True},
            json={"portfolio_code": portfolio_code, "start_date": day.isoformat(), "end_date": day.isoformat()},
        )
        assert resp.status_code == 200, resp.text
        assert [item["portfolio_code"] for item in resp.json()["results"]] == ["ADMIN_ALL"]
