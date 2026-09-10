from sqlalchemy import Column, String, Text, JSON, DateTime, Integer, func
from app.models.base import Base


class SyncJob(Base):
    __tablename__ = "sync_job"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    params = Column(JSON, nullable=True)
    total = Column(Integer, default=0)
    done = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    error_message = Column(Text)
    triggered_by = Column(String(20), default="manual")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
