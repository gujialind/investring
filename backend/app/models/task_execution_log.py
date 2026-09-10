from sqlalchemy import Column, String, Text, DateTime, Integer, func
from app.constants.db_charset import DB_CHARSET, DB_COLLATE
from app.models.base import Base


class TaskExecutionLog(Base):
    __tablename__ = "task_execution_log"

    # 显式 utf8mb4（#427 引入，#433 起全库同构）：error_message / error_stack 是自由文本、
    # 文案可能回显外部数据，4 字节字符在 utf8mb3 列上撞 errno 1366 会让整条任务记录写不
    # 进去（且 routers/tasks.py 的写入不是 best-effort，会外抛）。理由见
    # app/constants/db_charset.py。
    __table_args__ = {"mysql_charset": DB_CHARSET, "mysql_collate": DB_COLLATE}

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_code = Column(String(50), nullable=False)
    trigger_type = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    duration_ms = Column(Integer)
    records_total = Column(Integer)
    records_success = Column(Integer)
    records_failed = Column(Integer)
    error_message = Column(Text)
    error_stack = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
