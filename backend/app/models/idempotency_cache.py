from sqlalchemy import Column, String, Text, DateTime, func
from app.models.base import Base


class IdempotencyCache(Base):
    __tablename__ = "idempotency_cache"

    key = Column(String(100), primary_key=True)
    portfolio_code = Column(String(20), nullable=False)
    request_hash = Column(String(64), nullable=False)
    response = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)
