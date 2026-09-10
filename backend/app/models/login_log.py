from sqlalchemy import Column, String, Text, DateTime, Integer, func
from app.constants.db_charset import DB_CHARSET, DB_COLLATE
from app.models.base import Base


class LoginLog(Base):
    __tablename__ = "login_log"

    # 显式 utf8mb4（#427 引入，#433 起全库同构）：本表写入**不是** best-effort
    # （record_login_log 直接 commit），4 字节字符（投资人 code 或 User-Agent 带 emoji）
    # 在 utf8mb3 列上撞 errno 1366 会外抛 500、连带登录本身失败——不只是日志缺口。
    # 理由见 app/constants/db_charset.py。
    __table_args__ = {"mysql_charset": DB_CHARSET, "mysql_collate": DB_COLLATE}

    id = Column(Integer, primary_key=True, autoincrement=True)
    investor_code = Column(String(20), nullable=False)
    action = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    ip_address = Column(String(50))
    user_agent = Column(String(500))
    failure_reason = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
