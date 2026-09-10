from sqlalchemy import Column, String, Text, DateTime, Integer, func
from app.constants.db_charset import DB_CHARSET, DB_COLLATE
from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    # 显式 utf8mb4（#427 引入，#433 起全库同构）：本表的高危面是「整条记录静默丢失」——
    # 审计文本回显用户输入，4 字节字符（emoji、CJK 扩展 B）在 utf8mb3 列上撞 errno 1366，
    # 而写入是 best-effort、异常被吸收后不留痕。库级默认 + 模型声明双保险，理由见
    # app/constants/db_charset.py。
    __table_args__ = {"mysql_charset": DB_CHARSET, "mysql_collate": DB_COLLATE}

    id = Column(Integer, primary_key=True, autoincrement=True)
    investor_code = Column(String(20), nullable=False)
    action = Column(String(20), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(50))
    resource_name = Column(String(100))
    old_value = Column(Text)
    new_value = Column(Text)
    ip_address = Column(String(50))
    created_at = Column(DateTime, server_default=func.now())
