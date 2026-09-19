"""
投资人管理服务

投资人 CRUD 与删除保护（INVESTOR_HAS_SHARES），从路由层提取供 REST 与 CLI 共用。
service 层只抛领域异常，不 commit。role 参数默认 viewer：REST 不传（恒为 viewer），
CLI 可显式传入以创建管理员。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.investor import Investor
from app.models.investor_holding import InvestorHolding
from app.utils.security import get_password_hash
from app.services.exceptions import BusinessError, NotFoundError
from app.services.null_guard import reject_explicit_nulls


def create_investor(
    db: Session,
    *,
    code: str,
    name: str,
    password: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    role: str = "viewer",
) -> Investor:
    """创建投资人。不 commit。"""
    if db.query(Investor).filter(Investor.code == code).first():
        raise BusinessError("ALREADY_EXISTS", f"投资人 {code} 已存在", http_status=400)
    investor = Investor(
        code=code, name=name, role=role, phone=phone, email=email,
        password_hash=get_password_hash(password),
    )
    db.add(investor)
    return investor


def update_investor(db: Session, *, code: str, updates: dict) -> Investor:
    """更新投资人信息（password 字段自动转 password_hash）。不 commit。

    显式 null 收口（issue #573）：role/name 落 NULL 会让 InvestorResponse 校验 500
    且此后该行 GET 恒 500；phone/email 列可空且响应 Optional，null = 清除。
    """
    investor = db.query(Investor).filter(Investor.code == code).first()
    if not investor:
        raise NotFoundError("NOT_FOUND", f"投资人 {code} 不存在")

    updates = dict(updates)
    reject_explicit_nulls(updates, allow={"phone", "email"})
    if "password" in updates:
        updates["password_hash"] = get_password_hash(updates.pop("password"))

    for field, value in updates.items():
        setattr(investor, field, value)
    return investor


def delete_investor(db: Session, code: str) -> None:
    """删除投资人（仍持有份额则拒绝）。不 commit。"""
    investor = db.query(Investor).filter(Investor.code == code).first()
    if not investor:
        raise NotFoundError("NOT_FOUND", f"投资人 {code} 不存在")

    holding = db.query(InvestorHolding).filter(
        InvestorHolding.investor_code == code
    ).order_by(InvestorHolding.snapshot_date.desc()).first()
    if holding and holding.shares and holding.shares > 0:
        raise BusinessError("INVESTOR_HAS_SHARES", "投资人仍持有份额，需先全部赎回")

    db.delete(investor)
