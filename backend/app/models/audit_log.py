from sqlalchemy import Column, String, Text, DateTime, Integer, func
from app.constants.log_charset import LOG_TABLE_CHARSET, LOG_TABLE_COLLATE
from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    # #427：显式 utf8mb4。库级 charset 是 utf8mb3（对齐生产 RDS 的刻意约定），不声明则
    # 本表继承库级设置，4 字节字符（emoji、CJK 扩展 B 等）会让整条记录撞 errno 1366
    # 写不进去——审计/错误日志最不该有的失效模式。理由与常量来源见
    # app/constants/log_charset.py。
    __table_args__ = {"mysql_charset": LOG_TABLE_CHARSET, "mysql_collate": LOG_TABLE_COLLATE}

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
