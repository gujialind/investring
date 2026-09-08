"""请求上下文中间件（issue #404）：request_id 贯穿 + 每请求一条访问日志。

**必须是纯 ASGI 中间件，不能用 `BaseHTTPMiddleware`**：后者把下游丢进独立 anyio task
执行，contextvar 不保证传播到 endpoint / service，同一请求的多条 service 日志会拿到
空的 request_id。纯 ASGI 在同一 task 内绑定，anyio 的 `to_thread.run_sync` 又会
`copy_context()` 把上下文带进同步 endpoint 的工作线程，service 层因此读得到同一个
request_id（已实测）。
"""

import logging
import re
import time
import uuid
from typing import Optional

from starlette.datastructures import MutableHeaders

from app.context import RequestContext, request_context_var

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Starlette 把 Exception/500 handler 装在 ServerErrorMiddleware——中间件链的**最外层**，
# 它执行时本中间件的 finally 已解绑上下文，故 request_id 另存一份在 scope 上，
# 供未预期异常 handler 回写响应头与日志字段。
SCOPE_REQUEST_ID_KEY = "investring_request_id"

# 探活每 N 秒一次，记进访问日志会淹没真实流量
_SKIP_PATHS = frozenset({"/health"})

# 入站 X-Request-ID 会原样回写响应头并进日志，只接受安全字符集（防响应头注入）
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _inbound_request_id(scope) -> Optional[str]:
    """沿用客户端传入的 X-Request-ID（便于跨服务串联）；缺失或不合法则返回 None。"""
    for key, value in scope["headers"]:
        if key == b"x-request-id":
            candidate = value.decode("latin-1").strip()
            return candidate if _REQUEST_ID_RE.match(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope) or uuid.uuid4().hex
        scope[SCOPE_REQUEST_ID_KEY] = request_id
        token = request_context_var.set(RequestContext(request_id=request_id))

        started = time.perf_counter()
        status_code: Optional[int] = None

        async def send_wrapper(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "HTTP 请求",
                extra={
                    "method": scope["method"],
                    # 只记 path 不记 query：查询串可能带凭据
                    "path": scope["path"],
                    # 没见到 response.start = 异常穿透到 ServerErrorMiddleware，客户端收到 500
                    "status_code": 500 if status_code is None else status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_context_var.reset(token)
