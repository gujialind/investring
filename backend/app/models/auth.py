from sqlalchemy import Column, String, DateTime, ForeignKey, JSON
from app.models.base import Base


class AuthSession(Base):
    __tablename__ = "auth_session"

    id = Column(String(32), primary_key=True)
    investor_code = Column(String(20), ForeignKey(
        "investor.code", name="fk_auth_session_investor", ondelete="RESTRICT",
    ), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)


class ApiKey(Base):
    __tablename__ = "api_key"

    id = Column(String(32), primary_key=True)
    investor_code = Column(String(20), ForeignKey(
        "investor.code", name="fk_api_key_investor", ondelete="RESTRICT",
    ), nullable=False, index=True)
    label = Column(String(100), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    scopes = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime)
    revoked_at = Column(DateTime)
