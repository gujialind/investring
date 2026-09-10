from sqlalchemy import Column, String, Text, DateTime, Integer, func
from app.constants.db_charset import DB_CHARSET, DB_COLLATE
from app.models.base import Base


class SystemErrorLog(Base):
    __tablename__ = "system_error_log"

    # 显式 utf8mb4（#427 引入，#433 起全库同构）：错误文案常回显用户输入（路径参数、
    # 请求体），含 4 字节字符时 utf8mb3 列撞 errno 1366，被 record_system_error 的
    # best-effort except 吸收后**整条**记录静默丢失。理由见 app/constants/db_charset.py。
    __table_args__ = {"mysql_charset": DB_CHARSET, "mysql_collate": DB_COLLATE}

    id = Column(Integer, primary_key=True, autoincrement=True)
    error_type = Column(String(50), nullable=False)
    error_code = Column(String(50))
    error_message = Column(Text, nullable=False)
    error_stack = Column(Text)
    request_path = Column(String(200))
    request_method = Column(String(10))
    request_params = Column(Text)
    investor_code = Column(String(20))
    ip_address = Column(String(50))
    created_at = Column(DateTime, server_default=func.now())
