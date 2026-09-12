# ============================================================================
# InvestRing 测试全局配置 (conftest.py)
# ============================================================================
# 提供所有测试共享的 fixtures：
# - 测试数据库（SQLite 文件或 CI 中的 MySQL）
# - FastAPI TestClient（带依赖注入覆写）
# - 认证 Token（admin / viewer）
# - 基础数据初始化（资产分类、平台、产品、交易日历）
# ============================================================================

import os
import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Generator

# ---------------------------------------------------------------------------
# 关键：在所有 app 模块导入之前设置测试数据库 URL，
# 这样 app.config.Settings 和 app.database.engine 将使用测试数据库
#
# 优先级：环境变量 TEST_DB_URL（CI 显式指定）
#        > backend/.env.test（gitignored，按需配置本地/远程 MySQL）
#        > 本地 SQLite 文件（两者都不可用时的降级）
# ---------------------------------------------------------------------------
def _load_test_db_url() -> str:
    if os.environ.get("TEST_DB_URL"):
        return os.environ["TEST_DB_URL"]
    env_test = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.test")
    if os.path.exists(env_test):
        with open(env_test) as f:
            for line in f:
                if line.startswith("TEST_DB_URL="):
                    return line.strip().split("=", 1)[1]
    return "sqlite:///./test_investring.db"


os.environ.setdefault("DATABASE_URL", _load_test_db_url())
# 确保 DEBUG 不会因 .env 文件干扰测试
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import io
import json
import logging
import logging.config
from contextlib import asynccontextmanager, redirect_stdout
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.base import Base
from app.logging_config import _build_config
from app.main import app
from app.models import (
    Investor, Portfolio, Product, Platform,
    TradingCalendar, PriceRecord,
    PortfolioPosition, PortfolioValueSnapshot, InvestorHolding,
    Subscription, Trade, ShareChangeEvent,
    SyncJob, ManualMarketValue, Notification, IdempotencyCache,
)
from app.utils.security import create_access_token


# ============================================================================
# 数据库引擎（session-scoped）
# ============================================================================

TEST_DB_URL = os.environ["DATABASE_URL"]
IS_SQLITE = TEST_DB_URL.startswith("sqlite")


@pytest.fixture(scope="session")
def test_engine():
    """
    创建测试数据库引擎（整个测试会话共享）。
    - SQLite: 使用文件数据库 + WAL 模式
    - MySQL: 本地经 .env.test 配置（gitignored），CI 用环境变量配置

    清理策略：会话【开始】时 drop_all + create_all 保证干净起跑，
    会话结束不清理——保留 _seed_base_data 的基准数据（ADMIN/产品/日历），
    跑完测试可直接登录本地前端浏览。
    """
    if IS_SQLITE:
        engine = create_engine(
            TEST_DB_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,  # 内存数据库需要 StaticPool
            echo=False,
        )
        # SQLite WAL 模式配置
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    else:
        engine = create_engine(
            TEST_DB_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            echo=False,
        )

    # 会话开始：先删后建，清掉上一轮测试/手工种子的残留数据
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield engine

    # 会话结束：保留表与种子数据，供测试后浏览使用
    engine.dispose()


@pytest.fixture(scope="session")
def _seed_base_data(test_engine):
    """
    初始化测试基础数据（session-scoped，仅执行一次）。
    种子体在 tests/seed_base.py（与 CI E2E 种子同源，issue #222）：
    资产分类、平台、示例产品、交易日历、draft 组合、管理员和测试用户。
    """
    from tests.seed_base import seed_base_data

    SessionFactory = sessionmaker(bind=test_engine)
    db = SessionFactory()
    try:
        seed_base_data(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================================
# 认证全局状态隔离（autouse）
# ============================================================================

@pytest.fixture(autouse=True)
def _clear_auth_global_state():
    """每个测试前清空进程级认证全局状态，避免跨测试污染。

    token_blacklist 为模块级内存集合：改密/登出测试会将当前 token 拉黑，
    而同一秒内生成的同 sub/role token 字节完全一致，导致后续无关测试
    随机 401（Token has been revoked）。login_failure_tracker 同理。
    """
    from app.utils.security import token_blacklist, login_failure_tracker
    token_blacklist.clear()
    login_failure_tracker.clear()
    yield


# ============================================================================
# 数据库会话（function-scoped，使用事务隔离）
# ============================================================================

@pytest.fixture
def test_db(test_engine, _seed_base_data) -> Generator[Session, None, None]:
    """
    每个测试函数获得独立的数据库会话。
    使用事务 + SAVEPOINT 实现测试间隔离：
    - 测试开始时开启外层事务
    - 使用 SAVEPOINT 嵌套事务
    - 测试结束后 rollback 到 SAVEPOINT，再 rollback 外层事务
    这样每个测试的数据变更都不会影响其他测试。
    """
    connection = test_engine.connect()
    transaction = connection.begin()

    TestingSession = sessionmaker(bind=connection, expire_on_commit=False)
    db = TestingSession()

    # SQLite 需要开启嵌套事务支持
    if IS_SQLITE:
        connection.execute(text("PRAGMA foreign_keys=ON"))

    nested = connection.begin_nested()

    @event.listens_for(db, "after_transaction_end")
    def _restart_savepoint(session, transaction):
        if transaction.nested and not transaction._parent.nested:
            session.begin_nested()

    yield db

    db.close()
    transaction.rollback()
    connection.close()


# ============================================================================
# FastAPI TestClient（function-scoped，带依赖注入覆写）
# ============================================================================

@pytest.fixture
def client(test_db: Session) -> Generator[TestClient, None, None]:
    """
    提供配置好的 TestClient，已覆写 get_db 依赖注入，
    使所有 API 请求使用测试数据库。

    同时跳过 lifespan 中的 alembic upgrade（避免 MySQL 专有 SQL 在 SQLite 上报错）。
    """
    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db

    # 用空 lifespan 替代原始 lifespan，避免执行 alembic migration
    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original_lifespan
        app.dependency_overrides.clear()


# ============================================================================
# 认证 Token Fixtures
# ============================================================================

@pytest.fixture
def admin_token(test_db: Session) -> str:
    """生成管理员用户的 JWT Token"""
    return create_access_token({"sub": "ADMIN", "role": "admin"})


@pytest.fixture
def viewer_token(test_db: Session) -> str:
    """生成普通用户（viewer）的 JWT Token"""
    return create_access_token({"sub": "VIEWER", "role": "viewer"})


@pytest.fixture
def admin_headers(admin_token: str) -> dict:
    """管理员认证请求头"""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def viewer_headers(viewer_token: str) -> dict:
    """普通用户认证请求头"""
    return {"Authorization": f"Bearer {viewer_token}"}


# ============================================================================
# 测试数据工厂 Fixtures
# ============================================================================

@pytest.fixture
def sample_trading_day(test_db: Session) -> date:
    """返回一个确认为交易日的日期（2025-01-06 是周一）"""
    d = date(2025, 1, 6)
    # 确保该日期存在于交易日历中
    existing = test_db.query(TradingCalendar).filter(TradingCalendar.calendar_date == d).first()
    if not existing:
        test_db.add(TradingCalendar(calendar_date=d, is_open=True, exchange="SSE"))
        test_db.commit()
    return d


@pytest.fixture
def sample_non_trading_day(test_db: Session) -> date:
    """返回一个确认为非交易日的日期（2025-01-04 是周六）"""
    d = date(2025, 1, 4)
    existing = test_db.query(TradingCalendar).filter(TradingCalendar.calendar_date == d).first()
    if not existing:
        test_db.add(TradingCalendar(calendar_date=d, is_open=False, exchange="SSE"))
        test_db.commit()
    return d


@pytest.fixture
def sample_portfolio(test_db: Session) -> Portfolio:
    """创建一个测试用投资组合（draft 状态）"""
    code = "TEST_PORT"
    existing = test_db.query(Portfolio).filter(Portfolio.code == code).first()
    if existing:
        return existing
    port = Portfolio(code=code, name="测试组合", description="测试用", status="draft")
    test_db.add(port)
    test_db.commit()
    test_db.refresh(port)
    return port


@pytest.fixture
def active_portfolio(test_db: Session) -> Portfolio:
    """创建一个已激活的测试组合"""
    code = "ACTIVE_PORT"
    existing = test_db.query(Portfolio).filter(Portfolio.code == code).first()
    if existing:
        return existing
    port = Portfolio(code=code, name="活跃组合", description="测试用", status="active")
    test_db.add(port)
    test_db.commit()
    test_db.refresh(port)
    return port


@pytest.fixture
def sample_etf_product(test_db: Session) -> Product:
    """返回一个场内 ETF 产品"""
    return test_db.query(Product).filter(
        Product.code == "510300.SH", Product.market == "CN_EXCHANGE"
    ).first()


@pytest.fixture
def sample_otc_product(test_db: Session) -> Product:
    """返回一个场外 OEF 产品"""
    return test_db.query(Product).filter(
        Product.code == "000300.OF", Product.market == "CN_OTC"
    ).first()


@pytest.fixture
def sample_platform(test_db: Session) -> Platform:
    """返回一个测试用平台"""
    return test_db.query(Platform).filter(Platform.code == "MYCF").first()


@pytest.fixture
def sample_investor(test_db: Session) -> Investor:
    """返回 viewer 测试用户"""
    return test_db.query(Investor).filter(Investor.code == "VIEWER").first()


@pytest.fixture
def sample_admin(test_db: Session) -> Investor:
    """返回 admin 测试用户"""
    return test_db.query(Investor).filter(Investor.code == "ADMIN").first()


# ============================================================================
# 日志捕获（issue #404）
# ============================================================================

@pytest.fixture
def json_log_capture() -> Generator[io.StringIO, None, None]:
    """把生产日志配置重新应用到本测试专属的 stdout 缓冲，返回该缓冲。

    不用 capsys/capfd：`app.main` 在 import 期已执行 `setup_logging()`，dictConfig 的
    `ext://sys.stdout` 绑死的是那一刻的 pytest 全局捕获对象，per-test 捕获换不掉它。
    这里在 redirect_stdout 状态下重新应用生产配置（handler 因而绑到本缓冲），
    结束后还原 root logger，避免影响其他测试。
    """
    buffer = io.StringIO()
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    with redirect_stdout(buffer):
        logging.config.dictConfig(_build_config("INFO"))

    try:
        yield buffer
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def log_lines(buffer: io.StringIO) -> list[dict]:
    """解析缓冲里的每一行 stdout；任一行不是合法 JSON 即测试失败。"""
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def only_log_line(buffer: io.StringIO, **fields) -> dict:
    """断言恰好有一行匹配给定字段，并返回它。"""
    lines = log_lines(buffer)
    matched = [line for line in lines if all(line.get(k) == v for k, v in fields.items())]
    assert len(matched) == 1, f"期望恰好 1 行匹配 {fields}，实际 {len(matched)}；全部行：{lines}"
    return matched[0]


# ============================================================================
# 测试分层 marker：按目录自动打标（issue #469）
# ============================================================================
# marker 在 pyproject.toml [tool.pytest.ini_options] 登记（--strict-markers 要求）。
# 分层取目录事实而非逐文件声明，新增测试文件天然带标、不会漏。
# dialect 不在此推断——它表达「仅 MySQL 方言有效」，与目录正交，由用例经
# _mysql_only() 门控并显式 pytest.mark.dialect 声明。
_LAYER_MARKERS = {"unit", "integration", "e2e"}


def pytest_collection_modifyitems(items):
    """按 tests/<layer>/ 路径自动追加 unit / integration / e2e marker。"""
    tests_dir = Path(__file__).resolve().parent
    for item in items:
        try:
            layer = item.path.resolve().relative_to(tests_dir).parts[0]
        except (AttributeError, ValueError, IndexError):
            continue  # 非本目录收集项（如跨目录跑 pytest）不参与分层
        if layer in _LAYER_MARKERS:
            item.add_marker(getattr(pytest.mark, layer))
