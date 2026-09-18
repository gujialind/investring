"""测试库隔离（issue #539 第二单元）：破坏性初始化只允许打在 pytest 创建并持有的实例上。

`tests/conftest.py` 必须在 **`import app.main` 之前**调用 `prepare_test_database()`——
`app/main.py` 模块期的 `Base.metadata.create_all` 打的正是那一刻的 `DATABASE_URL`，
所以「强制覆盖环境」与「归属判定」两件都必须排在应用导入之前。

所有权判据**不看库名**（名字里有没有 test 都不构成许可），只认三条事实：

1. 目标为空（空 sqlite 文件 / 空库 / `:memory:`）——无数据可失；
2. 目标由本次会话自建（缺省路径：`prepare_test_database()` 造的 `TemporaryDirectory`）；
3. 目标带 pytest 写入的归属标记表，且除标记表与模型表外没有其他表。

判据 3 的「没有其他表」兜住两类漂移：人工在测试库另建表，以及用例被硬中断后残留的探测表。
两种都宁可拒绝并点名残留，也不悄悄把不是自己造的东西 drop 掉。
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    make_url,
    select,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_TEST_FILE = BACKEND_ROOT / ".env.test"

#: 归属标记表：刻意挂在**另一个 MetaData** 上，因此不进 `Base.metadata`——
#: 会话开始的 `drop_all` 不会连它一起删，`create_all` 也不会替它建。
OWNERSHIP_TABLE = "__ir_pytest_ownership__"
RUNNER_TAG = "pytest"

_ownership_meta = MetaData()
OWNERSHIP_MARKER = Table(
    OWNERSHIP_TABLE,
    _ownership_meta,
    Column("runner", String(20), primary_key=True),
    Column("token", String(64), nullable=False),
    Column("created_at", String(32), nullable=False),
)


class IsolationRefused(RuntimeError):
    """隔离闸门拒绝放行：目标未被证明归 pytest 所有，或压根连不上。"""


class PreparedDatabase(NamedTuple):
    url: str
    #: 仅缺省路径非空。刻意**不做** `mkdtemp`——`TemporaryDirectory` 的 finalizer
    #: 在进程退出时兜底回收，因此「整个会话没有一个用例用到 test_engine」时也不泄漏。
    tmp: tempfile.TemporaryDirectory[str] | None


def describe_url(url: str) -> str:
    """人类可读、不含凭据的地址描述（非 sqlite 连接串里通常带口令）。"""
    try:
        parsed = make_url(url)
    except Exception:
        return "<无法解析的地址>"
    if parsed.drivername.startswith("sqlite"):
        # SQLite 地址结构上没有凭据，原样回显最好定位（自己拼斜杠会把绝对路径拼成相对）
        return url.split("?", 1)[0]
    return f"{parsed.drivername}://<redacted>"


def _explicit_test_url() -> str | None:
    """显式测试通道：env `TEST_DB_URL` > `backend/.env.test`（gitignored）。"""
    value = os.environ.get("TEST_DB_URL", "").strip()
    if value:
        return value
    if ENV_TEST_FILE.exists():
        for line in ENV_TEST_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("TEST_DB_URL="):
                return line.split("=", 1)[1].strip() or None
    return None


def _model_table_names() -> set[str]:
    import app.models  # noqa: F401  确保全部模型都已注册进 Base.metadata
    from app.models.base import Base

    return set(Base.metadata.tables)


def evaluate_ownership(tables: set[str]) -> None:
    """纯判据：只吃「目标实例现有哪些表」，无需真连库即可测全矩阵。"""
    if not tables:
        return
    if OWNERSHIP_TABLE not in tables:
        raise IsolationRefused(
            f"目标库内已有 {len(tables)} 张表却没有归属标记（{OWNERSHIP_TABLE}），"
            "无法证明它是 pytest 创建并持有的实例，拒绝 drop_all。"
            "请改用缺省临时目录（取消 TEST_DB_URL/.env.test），或把 TEST_DB_URL 指向空库。"
        )
    foreign = tables - {OWNERSHIP_TABLE} - _model_table_names()
    if foreign:
        raise IsolationRefused(
            f"目标库带 pytest 归属标记，却另有无归属的表 {sorted(foreign)}"
            "（既非归属标记表亦非模型表）——拒绝把来源不明的表一起 drop。"
            "请确认这些表可以丢弃后重建空库，或另起一个空库。"
        )


def assert_pytest_owned(url: str) -> None:
    """连上目标读表名后交给 `evaluate_ownership`；探测失败同样拒绝，绝不按「空库」放行。"""
    probe = create_engine(url, pool_pre_ping=True, echo=False)
    try:
        tables = set(inspect(probe).get_table_names())
    except Exception as exc:
        detail = str(exc).replace(url, describe_url(url)).splitlines()[0]
        raise IsolationRefused(
            f"无法探测测试库 {describe_url(url)}（{type(exc).__name__}）：{detail}。"
            "探测失败不等于空库，拒绝继续。"
        ) from None
    finally:
        probe.dispose()
    evaluate_ownership(tables)


def write_ownership_marker(engine) -> None:
    """建表并落一行归属凭据；已有则不重复写（标记跨会话存活，是重跑不被拒的依据）。"""
    OWNERSHIP_MARKER.create(bind=engine, checkfirst=True)
    with engine.begin() as conn:
        held = conn.execute(
            select(OWNERSHIP_MARKER.c.runner).where(OWNERSHIP_MARKER.c.runner == RUNNER_TAG)
        ).scalar()
        if held is None:
            conn.execute(
                OWNERSHIP_MARKER.insert().values(
                    runner=RUNNER_TAG,
                    token=uuid.uuid4().hex,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
            )


def prepare_test_database() -> PreparedDatabase:
    """选定并锁定测试库，返回 URL 与（自建时的）临时目录句柄。

    副作用即本函数的目的：无条件覆盖 `DATABASE_URL`、关闭调度，并在应用导入前完成归属判定。
    """
    url = _explicit_test_url()
    tmp: tempfile.TemporaryDirectory[str] | None = None
    if url is None:
        tmp = tempfile.TemporaryDirectory(prefix="investring-pytest-")
        url = f"sqlite:///{Path(tmp.name) / 'test.db'}"

    inherited = os.environ.get("DATABASE_URL", "").strip()
    if inherited and inherited != url:
        print(
            f"[InvestRing][WARN] 忽略外部环境 DATABASE_URL（{describe_url(inherited)}）："
            f"测试库一律由 pytest 选定，本次为 {describe_url(url)}",
            file=sys.stderr,
        )
    os.environ["DATABASE_URL"] = url
    # 调度器在 lifespan 内起真实 job、会写库，测试期恒关（与 #539 首单元的隔离子进程同口径）。
    os.environ["SCHEDULER_ENABLED"] = "false"
    # SECRET_KEY 不强制：app.config 对默认占位值本身拒绝实例化，且有用例自行 patch 环境验证该守卫。
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

    assert_pytest_owned(url)
    return PreparedDatabase(url=url, tmp=tmp)
