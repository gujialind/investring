import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.database import engine, Base, SessionLocal
from app.logging_config import setup_logging
from app.request_context import (
    REQUEST_ID_HEADER,
    SCOPE_REQUEST_ID_KEY,
    RequestContextMiddleware,
)
from app.routers import auth, investors, portfolios, products, platforms, trading_calendar, data_sources, market_data, subscriptions, trades, share_change_events, positions, logs, tasks, notifications, snapshots, cash_transfers, sync_jobs, asset_classifications
from app.services.exceptions import BusinessError
from app.init_tasks import init_scheduled_tasks

logger = logging.getLogger(__name__)

# 必须早于下面两处 import 期副作用（建表、初始化调度任务），它们本身可能产日志（issue #404）
setup_logging()

Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    init_scheduled_tasks(db)
finally:
    db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    from alembic.config import Config as AlembicCfg
    from alembic import command as alembic_command

    alembic_ini = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    alembic_cfg = AlembicCfg(alembic_ini)
    alembic_command.upgrade(alembic_cfg, "head")

    from app.services.market_data_service import recover_orphan_jobs
    recover_orphan_jobs()

    from app.services.scheduler_service import init_scheduler, shutdown_scheduler
    init_scheduler()
    yield
    shutdown_scheduler()


def _resolve_version() -> str:
    """项目版本单一来源 = 仓库根 VERSION 文件（#375）；APP_VERSION 环境变量优先，供运行期覆盖。"""
    env_version = os.environ.get("APP_VERSION")
    if env_version:
        return env_version
    for parent in Path(__file__).resolve().parents:
        version_file = parent / "VERSION"
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip()
    return "0.0.0"


app = FastAPI(
    title="InvestRing API",
    description="资产组合管理系统 API",
    version=_resolve_version(),
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求上下文（request_id + 访问日志）。Starlette 的 add_middleware 后注册者更外，
# 故本中间件包在 CORS 之外，预检 OPTIONS 同样留痕（issue #404）
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    """统一领域异常映射：保持 detail.{error,message} 契约不变。"""
    logger.warning(
        "业务拒绝",
        extra={
            "code": exc.code,
            "reason": exc.message,
            "method": request.method,
            "path": request.url.path,
            "status_code": exc.http_status,
        },
    )
    detail = {"error": exc.code, "message": exc.message}
    if exc.details:
        detail["details"] = exc.details
    return JSONResponse(status_code=exc.http_status, content={"detail": detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未预期异常：ERROR 级带完整堆栈 + 500。子 issue B #405 在此追加 SystemErrorLog 落库。

    Starlette 把 Exception handler 装在 ServerErrorMiddleware（中间件链**最外层**），
    执行时 RequestContextMiddleware 的 finally 已解绑上下文，故 request_id 从 scope 取回。
    """
    request_id = request.scope.get(SCOPE_REQUEST_ID_KEY)
    extra = {"method": request.method, "path": request.url.path, "status_code": 500}
    if request_id:
        extra["request_id"] = request_id
    logger.error("未预期异常", exc_info=exc, extra=extra)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={REQUEST_ID_HEADER: request_id} if request_id else None,
    )


# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(investors.router, prefix="/api/investors", tags=["投资人管理"])
app.include_router(portfolios.router, prefix="/api/portfolios", tags=["组合管理"])
app.include_router(products.router, prefix="/api/products", tags=["产品管理"])
app.include_router(asset_classifications.router, prefix="/api/asset-classifications", tags=["资产分类维度字典"])
app.include_router(platforms.router, prefix="/api/platforms", tags=["平台管理"])
app.include_router(trading_calendar.router, prefix="/api/trading-calendar", tags=["交易日历"])
app.include_router(data_sources.router, prefix="/api/system/data-sources", tags=["数据源配置"])
app.include_router(market_data.router, prefix="/api/market-data", tags=["市场数据"])
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["申购赎回"])
app.include_router(trades.router, prefix="/api/trades", tags=["调仓交易"])
app.include_router(share_change_events.router, prefix="/api/share-change-events", tags=["份额变动事件"])
app.include_router(positions.router, prefix="/api/positions", tags=["持仓管理"])
app.include_router(logs.router, prefix="/api/system/logs", tags=["系统日志"])
app.include_router(tasks.router, prefix="/api/system/tasks", tags=["任务管理"])
app.include_router(notifications.router, prefix="/api/system/notifications", tags=["通知"])
app.include_router(snapshots.router, prefix="/api/snapshots", tags=["快照管理"])
app.include_router(cash_transfers.router, prefix="/api/portfolios", tags=["现金转移"])
app.include_router(sync_jobs.router, prefix="/api/sync-jobs", tags=["价格同步任务"])

@app.get("/")
def read_root():
    return {"message": "Welcome to InvestRing API"}

@app.get("/health")
def health_check():
    """健康检查端点，供 Docker 和 CI/CD 使用"""
    return {"status": "healthy"}
