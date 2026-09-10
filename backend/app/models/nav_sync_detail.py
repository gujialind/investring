from sqlalchemy import Column, String, Numeric, DateTime, Integer, ForeignKey, func
from app.constants.log_charset import LOG_TABLE_CHARSET, LOG_TABLE_COLLATE
from app.database import Base


class NavSyncDetail(Base):
    __tablename__ = "nav_sync_detail"

    # #427：显式 utf8mb4。error_message 接收外部数据源原文，4 字节字符在 utf8mb3 列上撞
    # errno 1366；本表写入直接 commit，故形态是外抛 500、中断净值同步——与四张日志表同型
    # （静默丢失那档）。理由与常量来源见 app/constants/log_charset.py。
    __table_args__ = {"mysql_charset": LOG_TABLE_CHARSET, "mysql_collate": LOG_TABLE_COLLATE}

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_log_id = Column(Integer, ForeignKey("task_execution_log.id"))
    job_id = Column(Integer, ForeignKey("sync_job.id"), nullable=True, index=True)
    product_code = Column(String(20), nullable=False)
    market = Column(String(20), nullable=False)
    nav_date = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    nav_value = Column(Numeric(10, 4))
    synced_count = Column(Integer, default=0)
    source = Column(String(20))
    error_message = Column(String(500))
    created_at = Column(DateTime, server_default=func.now())
