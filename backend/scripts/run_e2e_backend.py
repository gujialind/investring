# E2E 测试后端启动器：复用 conftest 做法，空 lifespan 跳过 alembic
# （MySQL 专有 SQL 在 SQLite 上报错，故 E2E 用 SQLite 临时库时必须跳过迁移）
# 用法：python backend/scripts/run_e2e_backend.py（监听 127.0.0.1:8000）
# 可用 E2E_DB_PATH / E2E_PORT 覆盖库路径与端口（并行会话/隔离栈复用）。
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
E2E_DB_PATH = os.environ.get("E2E_DB_PATH", "/tmp/ir_e2e.db")
E2E_PORT = int(os.environ.get("E2E_PORT", "8000"))

os.environ.update(
    DATABASE_URL=f"sqlite:///{E2E_DB_PATH}",
    SECRET_KEY="test-secret-key-e2e",
    SCHEDULER_ENABLED="false",
    DEBUG="true",
)
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

# 每次启动重建临时库，避免上一版本 schema 残留（同 conftest 的 drop+create 策略）
if os.path.exists(E2E_DB_PATH):
    os.remove(E2E_DB_PATH)

from app.main import app  # noqa: E402  （import 期完成 create_all）
from app.database import SessionLocal  # noqa: E402
from tests.seed_base import seed_base_data, seed_e2e_active  # noqa: E402

db = SessionLocal()
try:
    seed_base_data(db)
    seed_e2e_active(db)
finally:
    db.close()


@asynccontextmanager
async def _noop_lifespan(app):
    yield


app.router.lifespan_context = _noop_lifespan

import uvicorn  # noqa: E402

# log_config=None：跳过 uvicorn.Config 内置的 dictConfig——它会把 uvicorn 默认明文
# handler 装回、propagate 置 False，覆盖 import app.main 期已执行的 setup_logging()；
# 跳过后 uvicorn 自身日志（含 ASGI 异常）冒泡 root，与应用日志同为单行 JSON（#417）
uvicorn.run(app, host="127.0.0.1", port=E2E_PORT, log_level="warning", log_config=None)
