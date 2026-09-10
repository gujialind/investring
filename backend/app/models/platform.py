from sqlalchemy import Column, String, DateTime, func
from app.models.base import Base


class Platform(Base):
    __tablename__ = "platform"

    code = Column(String(20), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    platform_type = Column(String(50))
    created_at = Column(DateTime, server_default=func.now())
