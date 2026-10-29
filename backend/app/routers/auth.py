from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.investor import Investor
from app.models.auth import ApiKey
from app.schemas.auth import (
    LoginRequest, LoginResponse, ChangePasswordRequest, AuthMessage, AuthStatus,
    ApiKeyCreate, ApiKeyInfo, ApiKeyCreated,
)
from app.services import auth_service
from app.utils.security import (
    verify_password,
    get_password_hash,
    record_login_failure,
    clear_login_failure,
    is_account_locked,
)
from app.dependencies import (
    get_client_ip,
    get_user_agent,
    record_login_log,
    get_current_user,
    get_current_admin,
)
from datetime import datetime, timezone

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(request: Request, response: Response, body: LoginRequest, db: Session = Depends(get_db)):
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)

    # 1. 查找用户
    investor = db.query(Investor).filter(Investor.code == body.code).with_for_update().first()

    # 2. 检查账户是否已被锁定
    locked, locked_until = is_account_locked(body.code)
    if locked:
        record_login_log(db, body.code, "login_failed", "failed", ip_address, user_agent)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "ACCOUNT_LOCKED",
                "message": f"账户已锁定，请 {locked_until.isoformat()} 后再试",
                "locked_until": locked_until.isoformat(),
            },
        )

    # 3. 验证密码
    if not investor or not verify_password(body.password, investor.password_hash):
        is_now_locked, new_locked_until = record_login_failure(body.code)
        record_login_log(db, body.code, "login_failed", "failed", ip_address, user_agent)

        if is_now_locked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "ACCOUNT_LOCKED",
                    "message": f"连续登录失败次数过多，账户已锁定至 {new_locked_until.isoformat()}",
                    "locked_until": new_locked_until.isoformat(),
                },
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "INVALID_CREDENTIALS",
                "message": "用户名或密码错误",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. 登录成功：清除失败记录、更新最后登录时间、记录日志
    clear_login_failure(body.code)
    investor.last_login_at = auth_service.utcnow()
    result = auth_service.create_session(db, investor)
    record_login_log(db, body.code, "login", "success", ip_address, user_agent)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/logout", response_model=AuthMessage)
def logout(
    request: Request,
    current_user: Investor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤销当前密码登录会话（含所有续期 token），并记录登出日志。"""
    auth_service.revoke_session(db, request.state.credential.id)

    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    record_login_log(db, current_user.code, "logout", "success", ip_address, user_agent)

    return {"message": "登出成功"}


@router.put("/password", response_model=AuthMessage)
def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current_user: Investor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    修改密码。
    - admin 可以修改任意用户密码（通过 target_code）
    - viewer 只能修改自己的密码，且必须提供旧密码验证
    """
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)

    target_code = body.target_code or current_user.code

    # 权限检查：非 admin 只能修改自己
    if current_user.role != "admin" and target_code != current_user.code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "FORBIDDEN",
                "message": "无权修改其他用户密码",
            },
        )

    investor = db.query(Investor).filter(Investor.code == target_code).populate_existing().with_for_update().first()
    if not investor:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 非 admin 修改自己密码时，必须验证旧密码
    if current_user.role != "admin" or target_code == current_user.code:
        if not body.old_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "OLD_PASSWORD_REQUIRED",
                    "message": "修改密码需要提供旧密码",
                },
            )
        if not verify_password(body.old_password, investor.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "INVALID_OLD_PASSWORD",
                    "message": "旧密码错误",
                },
            )

    investor.password_hash = get_password_hash(body.new_password)
    auth_service.revoke_user_sessions(db, target_code)
    record_login_log(db, target_code, "password_changed", "success", ip_address, user_agent)

    return {"message": "密码修改成功，请重新登录"}


@router.post("/renew", response_model=LoginResponse)
def renew(
    request: Request, response: Response,
    current_user: Investor = Depends(get_current_user), db: Session = Depends(get_db),
):
    result = auth_service.renew_session(db, request.state.credential.id, current_user)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request, current_user: Investor = Depends(get_current_user)):
    credential = request.state.credential
    is_key = request.state.credential_type == "api_key"
    expires = credential.expires_at if is_key else datetime.fromtimestamp(request.state.token_expires_at, tz=timezone.utc)
    return {
        "user": auth_service.user_info(current_user),
        "credential_type": request.state.credential_type,
        "credential_id": credential.id,
        "label": credential.label if is_key else None,
        "expires_at": expires.replace(tzinfo=timezone.utc).isoformat() if expires else None,
        "scopes": credential.scopes if is_key else [],
    }


@router.post("/keys", response_model=ApiKeyCreated)
def create_key(
    body: ApiKeyCreate, response: Response,
    current_user: Investor = Depends(get_current_admin), db: Session = Depends(get_db),
):
    key, token = auth_service.create_api_key(
        db, investor_code=body.investor_code or current_user.code,
        label=body.label, scopes=body.scopes, expires_at=body.expires_at,
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"key": key, "token": token}


@router.get("/keys", response_model=list[ApiKeyInfo])
def list_keys(current_user: Investor = Depends(get_current_admin), db: Session = Depends(get_db)):
    return db.query(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id).all()


@router.post("/keys/{key_id}/revoke", response_model=ApiKeyInfo)
def revoke_key(
    key_id: str, current_user: Investor = Depends(get_current_admin), db: Session = Depends(get_db),
):
    key = auth_service.revoke_api_key(db, key_id)
    db.commit()
    return key
