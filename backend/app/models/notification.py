from sqlalchemy import Column, String, Text, DateTime, Integer, func
from app.models.base import Base


class Notification(Base):
    __tablename__ = "notification"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text)
    level = Column(String(20), default="info")
    recipient = Column(String(20))
    channel = Column(String(20), default="in_app")
    status = Column(String(20), default="pending")
    sent_at = Column(DateTime)
    read_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
