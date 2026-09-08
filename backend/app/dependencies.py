from datetime import datetime
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app import context
from app.database import get_db
from app.models.investor import Investor
from app.models.login_log import LoginLog
from app.utils.security import decode_token, is_token_blacklisted, is_account_locked

security = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    """获取客户端 IP 地址"""
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    """获取用户代理"""
    return request.headers.get("User-Agent", "")


def record_login_log(
    db: Session,
    investor_code: str,
    action: str,
    status: str,
    ip_address: str,
    user_agent: str,
    failure_reason: Optional[str] = None,
) -> None:
    log = LoginLog(
        investor_code=investor_code,
        action=action,
        status=status,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if failure_reason:
        log.failure_reason = failure_reason
    db.add(log)
    db.commit()


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Investor:
    """
    获取当前登录用户。
    验证 Token 有效性、黑名单、以及账户锁定状态。
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # 检查 Token 是否在黑名单中
    if is_token_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    investor_code: Optional[str] = payload.get("sub")
    if not investor_code:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查账户是否被锁定
    locked, locked_until = is_account_locked(investor_code)
    if locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "ACCOUNT_LOCKED",
                "message": f"Account is locked until {locked_until.isoformat()}",
                "locked_until": locked_until.isoformat(),
            },
        )

    investor = db.query(Investor).filter(Investor.code == investor_code).first()
    if not investor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 改中间件绑定的可变上下文对象（不能在此 set 新 ContextVar：同步依赖跑在自己的
    # threadpool context 拷贝里，set 只作用于该拷贝，endpoint/service 读不到）
    context.set_actor(investor.code)
    context.set_client_ip(get_client_ip(request))

    return investor


def get_current_admin(
    current_user: Investor = Depends(get_current_user),
) -> Investor:
    """
    获取当前管理员用户。
    要求用户角色必须为 admin。
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "FORBIDDEN",
                "message": "Admin privileges required",
            },
        )
    return current_user


def require_auth():
    """
    权限校验装饰器：要求用户已登录。
    在路由函数中使用 Depends(require_auth()) 即可。
    """
    return Depends(get_current_user)


def require_admin():
    """
    权限校验装饰器：要求用户为管理员。
    在路由函数中使用 Depends(require_admin()) 即可。
    """
    return Depends(get_current_admin)
