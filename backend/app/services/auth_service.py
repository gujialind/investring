"""持久化会话与代理凭证。不提交事务，不缓存撤销状态。"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.auth import AuthSession, ApiKey
from app.models.investor import Investor
from app.services.exceptions import BusinessError, NotFoundError
from app.services.audit_service import record_audit
from app.constants.audit_actions import ACTION_CREATE, ACTION_REVOKE, RESOURCE_API_KEY
from app.utils.security import create_access_token, decode_token


API_KEY_PREFIX = "ir_key_"


def utcnow() -> datetime:
    # DB DateTime 为 naive UTC；仅在存取边界剥离/附加时区，不使用业务日时钟。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def user_info(investor: Investor) -> dict:
    return {"code": investor.code, "name": investor.name, "role": investor.role}


def session_token(session: AuthSession, investor: Investor) -> dict:
    expires = session.expires_at.replace(tzinfo=timezone.utc)
    return {
        "token": create_access_token(
            {"sub": investor.code, "role": investor.role, "sid": session.id},
            expires_at=expires,
        ),
        "expires_at": expires.isoformat(),
        "user": user_info(investor),
    }


def create_session(db: Session, investor: Investor) -> dict:
    now = utcnow().replace(microsecond=0)
    session = AuthSession(
        id=uuid4().hex, investor_code=investor.code, created_at=now,
        expires_at=now + timedelta(days=get_settings().token_expire_days),
    )
    db.add(session)
    db.flush()
    return session_token(session, investor)


def authenticate_session(db: Session, token: str) -> tuple[AuthSession, dict]:
    payload = decode_token(token)
    if not payload or not isinstance(payload.get("sid"), str):
        raise BusinessError("AUTH_REQUIRED", "登录凭证无效或已过期，请重新登录", http_status=401)
    session = db.get(AuthSession, payload["sid"], populate_existing=True)
    if (not session or session.investor_code != payload.get("sub")
            or session.revoked_at is not None or session.expires_at <= utcnow()):
        raise BusinessError("AUTH_REQUIRED", "登录状态已失效，请重新登录", http_status=401)
    return session, payload


def renew_session(db: Session, session_id: str, investor: Investor) -> dict:
    # 锁定后重新读取：并发登出/改密不能被旧 ORM 状态或续期覆盖。
    session = db.query(AuthSession).filter(AuthSession.id == session_id).populate_existing().with_for_update().one_or_none()
    now = utcnow().replace(microsecond=0)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise BusinessError("AUTH_REQUIRED", "登录状态已失效，请重新登录", http_status=401)
    session.expires_at = max(session.expires_at, now + timedelta(days=get_settings().token_expire_days))
    db.flush()
    return session_token(session, investor)


def revoke_session(db: Session, session_id: str) -> None:
    db.query(AuthSession).filter(
        AuthSession.id == session_id, AuthSession.revoked_at.is_(None),
    ).update({AuthSession.revoked_at: utcnow()}, synchronize_session=False)


def revoke_user_sessions(db: Session, investor_code: str) -> None:
    db.query(AuthSession).filter(
        AuthSession.investor_code == investor_code, AuthSession.revoked_at.is_(None),
    ).update({AuthSession.revoked_at: utcnow()}, synchronize_session=False)


def create_api_key(db: Session, *, investor_code: str, label: str, scopes: list[str], expires_at: datetime | None) -> tuple[ApiKey, str]:
    owner = db.query(Investor).filter(Investor.code == investor_code).with_for_update().first()
    if owner is None:
        raise NotFoundError("NOT_FOUND", f"投资人 {investor_code} 不存在")
    if expires_at is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if expires_at <= utcnow():
            raise BusinessError("INVALID_PARAM", "凭证到期时间必须晚于当前时间")
    key_id = uuid4().hex
    secret = secrets.token_urlsafe(32)
    key = ApiKey(
        id=key_id, investor_code=investor_code, label=label,
        secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
        scopes=sorted(set(scopes)), created_at=utcnow(), expires_at=expires_at,
    )
    db.add(key)
    db.flush()
    record_audit(db, action=ACTION_CREATE, resource_type=RESOURCE_API_KEY,
                 resource_id=key.id, resource_name=key.label,
                 new_value={"investor_code": key.investor_code, "scopes": key.scopes, "expires_at": key.expires_at})
    return key, f"{API_KEY_PREFIX}{key_id}.{secret}"


def authenticate_api_key(db: Session, token: str) -> ApiKey:
    value = token.removeprefix(API_KEY_PREFIX)
    key_id, separator, secret = value.partition(".")
    key = db.get(ApiKey, key_id, populate_existing=True) if separator and len(key_id) == 32 and len(secret) == 43 else None
    if (key is None or not hmac.compare_digest(key.secret_hash, hashlib.sha256(secret.encode()).hexdigest())
            or key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= utcnow())):
        raise BusinessError("AUTH_REQUIRED", "代理凭证无效、已过期或已撤销，请联系管理员", http_status=401)
    return key


def revoke_api_key(db: Session, key_id: str) -> ApiKey:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).populate_existing().with_for_update().first()
    if key is None:
        raise NotFoundError("NOT_FOUND", "代理凭证不存在")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        record_audit(db, action=ACTION_REVOKE, resource_type=RESOURCE_API_KEY,
                     resource_id=key.id, resource_name=key.label,
                     new_value={"revoked_at": key.revoked_at})
    return key
