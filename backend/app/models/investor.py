from sqlalchemy import Column, String, DateTime, func
from app.models.base import Base


class Investor(Base):
    __tablename__ = "investor"

    code = Column(String(20), primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    role = Column(String(20), default="viewer")  # admin/viewer
    phone = Column(String(20))
    email = Column(String(100))
    password_hash = Column(String(255), nullable=False)
    last_login_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
