from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import bcrypt
from jose import JWTError, jwt
from app.config import get_settings

settings = get_settings()

login_failure_tracker: dict[str, dict] = {}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")


def create_access_token(
    data: dict, expires_delta: Optional[timedelta] = None, *,
    expires_at: Optional[datetime] = None,
):
    now = datetime.now(timezone.utc)
    expire = expires_at or now + (
        expires_delta if expires_delta is not None else timedelta(days=settings.token_expire_days)
    )
    to_encode = {**data, "exp": expire, "iat": now, "jti": uuid4().hex}
    return jwt.encode(to_encode, settings.secret_key, algorithm="HS256")


def decode_token(token: str):
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"], options={
            "require_exp": True, "require_iat": True, "require_jti": True,
        })
        return payload
    except JWTError:
        return None


def record_login_failure(investor_code: str) -> tuple[bool, Optional[datetime]]:
    now = datetime.utcnow()
    tracker = login_failure_tracker.get(investor_code)

    if tracker is None:
        login_failure_tracker[investor_code] = {"count": 1, "locked_until": None}
        return False, None

    locked_until = tracker.get("locked_until")
    if locked_until and now < locked_until:
        return True, locked_until

    if locked_until and now >= locked_until:
        tracker["count"] = 1
        tracker["locked_until"] = None
        return False, None

    tracker["count"] += 1

    if tracker["count"] >= 5:
        lock_until = now + timedelta(minutes=15)
        tracker["locked_until"] = lock_until
        return True, lock_until

    return False, None


def clear_login_failure(investor_code: str) -> None:
    if investor_code in login_failure_tracker:
        del login_failure_tracker[investor_code]


def is_account_locked(investor_code: str) -> tuple[bool, Optional[datetime]]:
    tracker = login_failure_tracker.get(investor_code)
    if not tracker:
        return False, None

    locked_until = tracker.get("locked_until")
    if locked_until and datetime.utcnow() < locked_until:
        return True, locked_until

    if locked_until and datetime.utcnow() >= locked_until:
        del login_failure_tracker[investor_code]
        return False, None

    return False, None
