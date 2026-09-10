from sqlalchemy import Column, String, Text, Boolean, DateTime, Integer, func
from app.models.base import Base


class ScheduledTask(Base):
    __tablename__ = "scheduled_task"

    code = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    cron_expr = Column(String(100))
    is_enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime)
    last_run_status = Column(String(20))
    next_run_at = Column(DateTime)
    timeout_seconds = Column(Integer, default=300)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
