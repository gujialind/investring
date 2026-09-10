from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.config import get_settings

settings = get_settings()

# Database engine with connection pool configuration
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# SQLite WAL mode configuration (for backward compatibility)
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 声明式 `Base` 现在定义在 `app/models/base.py`（连同全库建表字符集钩子，issue #433）：
# 模型从包内导入它，避免 `app.database` → `app.models.base` → `app.models/__init__`
# → 某模型 → `app.database` 这条循环。`from app.database import Base` 不再可用，
# 需要 Base 的非模型代码请改 `from app.models.base import Base`。
# 为什么字符集钩子必须挂在 Base 的类体里（而不是 MetaData 参数或 before_create 事件），
# 理由见该模块 docstring。


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
