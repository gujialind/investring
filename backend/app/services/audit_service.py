"""审计日志与系统错误日志写入服务（issue #405）。

本模块是 `audit_log` 与 `system_error_log` 两张表的**唯一写入路径**。

设计要点：
- **savepoint 隔离 + Core INSERT**：先把业务改动 flush 进外层事务，再用连接级
  savepoint 只包审计行本身；审计行走 Core INSERT 而非 ORM `add()`+flush——后者一旦
  flush 失败就把 Session 置为 pending-rollback（此后任何会话操作都抛
  PendingRollbackError，savepoint 回滚救不回来，业务事务等于被审计拖垮），
  Core execute 失败只污染该条语句，回滚 savepoint 后会话照常可用；
- **不 commit**（§1.1 service 约定）：审计行随业务事务由 router 提交/回滚——
  业务回滚则审计不落，只记真正发生的事实；
- **SYSTEM 哨兵**：后台执行体（调度器、线程池）无请求上下文时 actor 落 "SYSTEM"；
- **失败响亮**：审计写入失败记 stdout ERROR + 落 `system_error_log`，使缺口可追溯，
  审计永不拖垮核心记账；守护范围含 savepoint **之前**的 `db.flush()` 与建 savepoint
  本身（#422a，flush 干的是调用方的活、撞约束即抛），savepoint 回滚失败也不再静默
  （#422b，另记一条带堆栈的 stdout ERROR，并把「业务事务状态不可信」写进日志与
  `system_error_log` 文案——回滚失败几乎必然意味着连接已死）；
- **system_error_log 走独立 session**：`record_system_error` 的两个调用点（审计 flush
  失败、`main.py` 全局未预期异常 handler）所处的事务都已不可信，且 best-effort——
  写不进去只记 stdout，绝不外抛掩盖原始错误。
"""

import json
import logging
import traceback
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app import context
from app.constants.audit_actions import SYSTEM_ACTOR
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

# system_error_log.request_path 的列宽（String(200)）。HTTP 路径长度无界（h11 请求行
# 上限 16 KB），不截断则 MySQL 严格模式下 errno 1406 → 整条错误记录被 best-effort
# except 吞掉（#422c）。宽度与模型列的一致性由 tests/unit/test_audit_service.py 守门；
# 完整 path 仍由调用方的 stdout 日志保留（main.py 未预期异常 handler），二者互补。
SYSTEM_ERROR_PATH_MAX = 200


def _serialize_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _is_same(old_val: Any, new_val: Any) -> bool:
    """数值统一转 Decimal 再比：Decimal 与 float 的比较是**精确**的，
    `Decimal("1234.5600") != 1234.56` 恒真（1234.56 的二进制浮点并不精确等于该十进制值）。
    DB Numeric 列读出 Decimal，而 update schema 的数值字段是 `Optional[float]`
    （如 `ShareChangeEventUpdate.cash_change`），直接比会把用户原样重提交的字段判为
    「已变更」，写出根本没发生的审计变更。经 `str()` 转 Decimal 后按数值比较，
    标度差不再误判（`Decimal("1.1") == Decimal("1.1000")`）。

    只归一**比较**、不归一调用方 setattr 的值，故落库数据不变。
    """
    if isinstance(old_val, bool) or isinstance(new_val, bool):
        return old_val == new_val
    if isinstance(old_val, (int, float, Decimal)) and isinstance(
        new_val, (int, float, Decimal)
    ):
        return Decimal(str(old_val)) == Decimal(str(new_val))
    return old_val == new_val


def _diff_fields(obj: Any, updates: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """捕获 setattr 前后的变化字段，返回 (old_dict, new_dict)。仅含实际变化的键。"""
    old: Dict[str, Any] = {}
    new: Dict[str, Any] = {}
    for key, new_val in updates.items():
        old_val = getattr(obj, key, None)
        if not _is_same(old_val, new_val):
            old[key] = old_val
            new[key] = new_val
    return old, new


def record_system_error(
    *,
    error_type: str,
    error_message: str,
    error_stack: Optional[str] = None,
    request_path: Optional[str] = None,
    request_method: Optional[str] = None,
    investor_code: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """落一条 system_error_log。独立 session + best-effort：写失败只记 stdout，绝不外抛。

    必须用独立 session——两个调用点的业务会话都不可信：审计 flush 已失败（事务可能已
    进入 aborted 态），全局异常 handler 里的会话则随时会被上层关闭/回滚，复用即连带丢失。

    `request_path` 按 `SYSTEM_ERROR_PATH_MAX` 截断（#422c）：HTTP 路径长度无界，超列宽
    在 MySQL 严格模式下会让整条记录写不进去；完整 path 由调用方的 stdout 日志保留。
    """
    from app.database import SessionLocal
    from app.models.system_error_log import SystemErrorLog

    err_session = SessionLocal()
    try:
        err_session.add(SystemErrorLog(
            error_type=error_type,
            error_message=error_message,
            error_stack=error_stack,
            request_path=request_path[:SYSTEM_ERROR_PATH_MAX] if request_path else None,
            request_method=request_method,
            investor_code=investor_code,
            ip_address=ip_address,
        ))
        err_session.commit()
    except Exception:
        logger.error("system_error_log 写入失败（错误不可追溯）", exc_info=True)
    finally:
        err_session.close()


def record_audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    old_value: Any = None,
    new_value: Any = None,
) -> None:
    """写入一条审计记录。savepoint 隔离，不 commit，失败不阻断业务。

    失败边界（#422a/b）：flush 业务改动、建 savepoint、Core INSERT、savepoint 回滚
    四个环节任一抛异常都**响亮记录后返回、绝不外抛**——审计不拖垮核心记账，也不留
    静默缺口（回滚失败另记一条带堆栈的 ERROR，因为它意味着业务事务状态已不可信）。

    Args:
        db: 业务会话（审计行随该事务提交/回滚）。
        action: 操作动词，取 `audit_actions.ACTION_*` 常量（≤20 字符）。
        resource_type: 资源类型，取 `audit_actions.RESOURCE_*` 常量（≤50 字符）。
        resource_id: 资源标识（字符串化主键或业务键）。
        resource_name: 资源可读名（可选）。
        old_value: 变更前值（dict → JSON，str → 原样，None → 空）。
        new_value: 变更后值（同上）。
    """
    actor = context.get_actor() or SYSTEM_ACTOR
    ip_address = context.get_client_ip()

    values = {
        "investor_code": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "old_value": _serialize_value(old_value),
        "new_value": _serialize_value(new_value),
        "ip_address": ip_address,
    }

    # 先把调用方尚未 flush 的业务改动落进外层事务：生产 SessionLocal 是 autoflush=False、
    # 测试会话是 autoflush=True，显式 flush 让 savepoint 在两种环境下都只包住审计行本身。
    # flush 不能删（不先 flush，ROLLBACK TO SAVEPOINT 会连带撤销调用方的业务改动，而调用方
    # 收到成功响应），但它干的是调用方的活、撞约束即抛，故必须与建 savepoint 一起纳入守护
    # （#422a）：否则 recalculate 收尾埋点的 flush 失败会逃出 service，把 router 承诺的
    # 200 + results[].errors 变成 500 RECALCULATION_FAILED。
    sp = None
    try:
        db.flush()
        sp = db.connection().begin_nested()
        # 用 Core INSERT 而非 ORM add+flush：flush 失败会把 Session 置为 pending-rollback
        # （此后任何会话操作都抛 PendingRollbackError），连接级 savepoint 回滚救不回来，
        # 业务事务等于被审计拖垮；Core execute 失败只污染该条语句，回滚 savepoint 后
        # 外层事务与会话仍可用（已实测）。
        db.execute(insert(AuditLog).values(**values))
        sp.commit()
    except Exception as exc:
        rollback_error: Optional[Exception] = None
        if sp is not None:
            try:
                sp.rollback()
            except Exception as exc_rollback:
                # 回滚失败几乎必然意味着连接已死或事务状态已废——比「审计没写进去」更高一级
                # 的信号，此前被裸 pass 吞掉，调用方还会照着「业务事务不受影响」继续 commit
                rollback_error = exc_rollback
                logger.error(
                    "审计 savepoint 回滚失败（连接或事务状态可能已废，业务事务未必干净）",
                    exc_info=True,
                    extra={
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "error": str(exc_rollback),
                    },
                )

        if sp is None:
            outcome = "savepoint 未建立：业务改动 flush 阶段即失败，业务事务状态不可信"
        elif rollback_error is None:
            outcome = "savepoint 已回滚，业务事务不受影响"
        else:
            outcome = f"savepoint 回滚亦失败（{rollback_error}），业务事务状态不可信"

        logger.error(
            f"审计写入失败（{outcome}）",
            extra={
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "error": str(exc),
            },
        )
        record_system_error(
            error_type="AuditWriteFailure",
            error_message=(
                f"审计写入失败: action={action}, resource_type={resource_type}, "
                f"resource_id={resource_id}: {exc}（{outcome}）"
            ),
            error_stack=traceback.format_exc(),
            investor_code=context.get_actor(),
            ip_address=context.get_client_ip(),
        )
