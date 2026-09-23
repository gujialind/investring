"""路由兜底的未预期异常观测出口（issue #553）。

背景：`HTTPException` 由 Starlette 的 `ExceptionHandlerMiddleware`（中间件链内侧）
就地渲染成 JSON 响应，**永远冒不到**装着 `Exception` handler 的
`ServerErrorMiddleware`（最外层）。于是 router 里「`except Exception` → 抛
`HTTPException(5xx)`」这一族写法让 `main.py` 的全局 handler 完全失效：原始异常类型
与堆栈被丢掉、stdout 无 ERROR 行、`system_error_log` 不落一行，事后只剩响应体里
`str(e)` 那一句话。CI 里一次 500 因此查不到任何线索（`system_error_log` 是 #405
承诺的排障入口，对这一整类失效是空的）。

本模块是这些兜底分支的**统一出口**：`logger.error` 带堆栈 + 落一条
`system_error_log`，两件事做完即返回，raise 与 rollback 仍由调用点自己负责
（`rollback` 的时机各端点不同，`BusinessError` 透传的边界也各端点不同，硬收进
helper 会一次性改动全部事务语义——那是 #553 方案 C 的活，不在本次范围）。

**只给 `except Exception` 的 catch-all 用**：领域异常（`BusinessError`、参数校验
`ValueError` → 422）是预期内结果，口径是 WARNING（见 `main.py` 的 BusinessError
handler），走这里会把业务拒绝刷成 ERROR 噪音。
"""

import logging
import traceback
from typing import Any

from app.context import get_actor, get_client_ip, get_request_context
from app.services.audit_service import record_system_error

logger = logging.getLogger(__name__)

# 落 system_error_log 时的 error_type 取原始异常类名——若记成 HTTPException，
# 「500 的真因是什么」这个问题就没有答案（#553 的根因正是它被 router 换掉了）。
# `record_system_error` 自身 best-effort（独立 session，写不进去只记 stdout），
# 故这里不额外包 try：落库失败绝不许掩盖原始异常。


def report_unexpected(exc: BaseException, *, operation: str, **fields: Any) -> None:
    """记录一条「被路由就地翻成 5xx 的未预期异常」。

    Args:
        exc: `except ... as e` 拿到的原始异常（**不是**将要抛出的 HTTPException）。
        operation: 人可读的端点/动作名，进 stdout 的 `operation` 字段，便于按端点聚合。
        **fields: 业务键（组合代码、产品代码、快照日等），按需传，同进 stdout。
    """
    ctx = get_request_context()
    path = ctx.path if ctx else None
    method = ctx.method if ctx else None

    # request_id 由 JsonFormatter 从上下文自动附上（logging_config.py），此处不重复取
    extra: dict[str, Any] = {
        "operation": operation,
        "method": method,
        "path": path,
        "status_code": 500,
    }
    extra.update(fields)
    # exc_info=exc 而非 logger.exception：后者读 sys.exc_info()，一旦有人在
    # 非 except 块里误调就静默记成 "NoneType: None"；显式传原始异常则必然是它
    logger.error("路由兜底未预期异常", exc_info=exc, extra=extra)

    record_system_error(
        error_type=type(exc).__name__,
        error_message=str(exc),
        error_stack="".join(traceback.format_exception(exc)),
        request_path=path,
        request_method=method,
        investor_code=get_actor(),
        ip_address=get_client_ip(),
    )
