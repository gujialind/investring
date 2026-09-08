"""请求级上下文（issue #404）：request_id / actor / client_ip。

写入方与消费方：
- `request_context.py` 中间件在请求进入时创建 `RequestContext` 并绑定，出站时解绑；
- `dependencies.py::get_current_user` 解析出投资人后写 actor / client_ip；
- `logging_config.py` 的 JSON formatter 读 request_id / actor 附到每条日志。

后台执行体（调度器触发体、价格同步线程池）不在请求上下文中，读到 None——
子 issue B #405 据此落 `SYSTEM` 哨兵。
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass
class RequestContext:
    """单次请求的可变上下文。

    **必须是可变对象、且由中间件在派发前创建**：FastAPI 把同步依赖与同步 endpoint
    分别派发到 threadpool，anyio 每次派发都 `copy_context()` 出一份独立拷贝
    （`anyio/_backends/_asyncio.py` 的 `run_sync_in_worker_thread`）。依赖里直接
    `some_var.set(...)` 只作用于依赖那份拷贝，endpoint / service 读到的仍是 None
    （已实测）。改同一个对象的字段则各拷贝共享，service 层可见。
    """

    request_id: Optional[str] = None
    actor: Optional[str] = None
    client_ip: Optional[str] = None


request_context_var: ContextVar[Optional[RequestContext]] = ContextVar(
    "request_context", default=None
)


def get_request_context() -> Optional[RequestContext]:
    return request_context_var.get()


def get_request_id() -> Optional[str]:
    ctx = request_context_var.get()
    return ctx.request_id if ctx else None


def get_actor() -> Optional[str]:
    ctx = request_context_var.get()
    return ctx.actor if ctx else None


def get_client_ip() -> Optional[str]:
    ctx = request_context_var.get()
    return ctx.client_ip if ctx else None


def set_actor(actor: str) -> None:
    """无请求上下文（后台线程）时静默跳过——没有可归属的请求。"""
    ctx = request_context_var.get()
    if ctx is not None:
        ctx.actor = actor


def set_client_ip(client_ip: str) -> None:
    ctx = request_context_var.get()
    if ctx is not None:
        ctx.client_ip = client_ip
