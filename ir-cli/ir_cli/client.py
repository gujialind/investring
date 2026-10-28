"""
HTTP 客户端封装

所有命令通过此模块与后端通信，统一处理认证、错误和响应格式。

环境变量：
- IR_TOKEN: 直接指定 token（优先于 ~/.ir/token.json，适合 CI/一次性调用）
- IR_CONNECT_TIMEOUT / IR_HTTP_TIMEOUT: 连接/读取超时
- IR_RETRY: GET 请求失败重试次数（默认 2，仅幂等 GET 重试网络异常/5xx）
- IR_DEBUG: 设为 1 时向 stderr 输出请求耗时与状态码

代理口径：不读取任何代理环境变量（trust_env=False，#798/#661），始终直连 base_url，
见 CLI_MANUAL.md §2.2。
"""
import os
import sys
import time
from typing import Any, Optional

import httpx

from ir_cli import config
from ir_cli.output import EXIT_AUTH, EXIT_CONNECTION, error, success


class ApiError(Exception):
    """HTTP 业务错误（raise_errors=True 时抛出）

    供链式命令（如 create --confirm）捕获后续阶段的错误后，
    在错误输出中携带已创建记录 id 等上下文，而非直接退出。
    """

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details


def _debug_enabled() -> bool:
    return os.environ.get("IR_DEBUG", "") not in ("", "0", "false")


def _debug(msg: str) -> None:
    if _debug_enabled():
        print(f"[debug] {msg}", file=sys.stderr)


class APIClient:
    """HTTP API 客户端"""

    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # 连接超时短（快速发现服务不可达），读超时长（兼容快照重算等长任务）
        timeout = httpx.Timeout(
            connect=float(os.environ.get("IR_CONNECT_TIMEOUT", "5")),
            read=float(os.environ.get("IR_HTTP_TIMEOUT", "300")),
            write=30.0,
            pool=5.0,
        )
        # trust_env=False：不读取代理环境变量（HTTP_PROXY/HTTPS_PROXY/NO_PROXY 及小写形式），
        # 也不读取 .netrc / SSL_CERT_FILE / REQUESTS_CA_BUNDLE，始终直连 base_url。
        # 根因：no_proxy 含带方括号 IPv6（如 [::1]，curl 语义合法）时 httpx 0.28 生成非法
        # pattern `all://*[::1]`，构造期抛 InvalidURL 漏出 raw traceback（#798）。
        # 口径与 #661（CD 探活不继承主机代理环境）一致，见 CLI_MANUAL.md §2.2。
        try:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=timeout,
                trust_env=False,
            )
        except Exception as e:
            # 构造期异常同样必须走 JSON 错误出口（#798 C1），归连通性类 exit 3
            error(
                "CLIENT_INIT_ERROR",
                f"HTTP 客户端初始化失败: {type(e).__name__}: {e}",
                details={"base_url": self.base_url},
                exit_code=EXIT_CONNECTION,
            )

    @classmethod
    def from_config(cls, require_auth: bool = True) -> "APIClient":
        """读取 base_url 和 token（IR_TOKEN 环境变量优先于 token 文件）"""
        base_url = config.get_base_url()
        token = os.environ.get("IR_TOKEN")
        if not token:
            token_data = config.load_token()
            token = token_data["token"] if token_data else None
        if require_auth and not token:
            error("AUTH_REQUIRED", "未登录或 token 已过期，请执行: ir auth login", exit_code=EXIT_AUTH)
        return cls(base_url, token)

    def _handle_response(self, resp: httpx.Response, raise_errors: bool = False) -> dict:
        """统一处理 HTTP 响应（raise_errors=True 时 HTTP >=400 抛 ApiError 而非直接退出）"""
        if resp.status_code >= 400:
            default_code = {
                401: "AUTH_REQUIRED",
                403: "FORBIDDEN",
                404: "NOT_FOUND",
                409: "CONFLICT",
                422: "VALIDATION_ERROR",
            }.get(resp.status_code, "SERVER_ERROR" if resp.status_code >= 500 else "HTTP_ERROR")
            code = self._extract_error_code(resp, default_code)
            detail = self._extract_detail(resp)
            # 合并后端结构化 details，供 get_hint 动态插值就近提示（issue #86）
            merged_details = {**self._extract_details(resp), "http_status": resp.status_code}
            # 后端无结构化 message 时使用友好提示兜底
            if detail.startswith(f"HTTP {resp.status_code}"):
                fallback = {
                    401: "认证失败或 token 已过期，请执行: ir auth login",
                    403: "无权限执行此操作",
                }.get(resp.status_code)
                if fallback:
                    detail = fallback
            if raise_errors:
                raise ApiError(code, detail, merged_details)
            error(
                code,
                detail,
                details=merged_details,
                exit_code=EXIT_AUTH if resp.status_code == 401 else 1,
            )

        # 2xx 成功
        try:
            body = resp.json()
        except Exception:
            return {"data": resp.text}

        # 分页响应: {items: [...], total, page, page_size}
        if isinstance(body, dict) and "items" in body and "total" in body:
            total = body["total"]
            page = body.get("page")
            page_size = body.get("page_size")
            total_pages = body.get("total_pages")
            has_more = None
            if page is not None:
                # 后端给了 total_pages 就以它判（#752 起调仓列表按组边界切页，一页可能
                # 多带同组剩余腿，`page * page_size < total` 会在末页误报还有下一页）；
                # 未给则沿用它之前的行口径公式。
                if total_pages is not None:
                    has_more = page < total_pages
                elif page_size is not None:
                    has_more = page * page_size < total
            meta = {
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_more": has_more,
            }
            # 组口径字段按「后端返回才透传」：目前只有 `trade list` 带（#752），其余列表端点
            # 的响应里没有，不给它们凭空补 null。
            for key in ("total_groups", "total_pages"):
                if key in body:
                    meta[key] = body[key]
            return {"data": body["items"], "meta": meta}
        return {"data": body}

    def _extract_detail(self, resp: httpx.Response) -> str:
        """从错误响应中提取 detail 消息"""
        try:
            body = resp.json()
            detail = body.get("detail", "")
            if isinstance(detail, dict):
                return detail.get("message", str(detail))
            return str(detail) if detail else f"HTTP {resp.status_code}"
        except Exception:
            return f"HTTP {resp.status_code}: {resp.text[:200]}"

    def _extract_details(self, resp: httpx.Response) -> dict:
        """从错误响应中提取后端结构化详情（detail.details），缺失时返回空 dict"""
        try:
            body = resp.json()
            detail = body.get("detail", {})
            if isinstance(detail, dict) and isinstance(detail.get("details"), dict):
                return detail["details"]
        except Exception:
            pass
        return {}

    def _extract_error_code(self, resp: httpx.Response, default: str) -> str:
        """从错误响应中提取后端结构化业务错误码（detail.error），缺失时用默认码"""
        try:
            body = resp.json()
            detail = body.get("detail", {})
            if isinstance(detail, dict):
                return detail.get("error", default)
        except Exception:
            pass
        return default

    def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        retryable: bool = False,
        raise_errors: bool = False,
    ) -> dict:
        """统一请求入口：幂等请求（GET）在网络异常/5xx 时按 IR_RETRY 重试；
        非幂等请求任何网络异常直接进入循环外错误处理（不重试）"""
        max_retries = 0
        if retryable:
            try:
                max_retries = max(0, int(os.environ.get("IR_RETRY", "2")))
            except ValueError:
                max_retries = 2

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                delay = 0.5 * (2 ** (attempt - 1))
                _debug(f"retry {attempt}/{max_retries} after {delay:.1f}s: {method} {path}")
                time.sleep(delay)
            start = time.monotonic()
            try:
                resp = self._client.request(method, path, json=json_data, params=params)
            except httpx.HTTPError as e:
                # 覆盖 RemoteProtocolError/ReadError/WriteError 等全部网络异常，
                # 保证任何失败路径 stdout 都有结构化 JSON（issue #73/#77 根因）
                last_exc = e
                _debug(f"{method} {path} -> {type(e).__name__} ({time.monotonic() - start:.2f}s)")
                continue
            _debug(f"{method} {path} -> {resp.status_code} ({time.monotonic() - start:.2f}s)")
            # 5xx 视为服务端瞬时故障，幂等请求可重试
            if retryable and resp.status_code >= 500 and attempt < max_retries:
                continue
            return self._handle_response(resp, raise_errors=raise_errors)

        if isinstance(last_exc, httpx.TimeoutException):
            error(
                "TIMEOUT_ERROR",
                f"请求超时: {self.base_url}{path}；服务端可能仍在执行，请用对应查询命令（如 ir snapshot status）"
                "回查结果，勿盲目重发；长任务（如区间重算）建议改用 ir snapshot recalculate --async 提交后台任务；"
                "也可用 IR_HTTP_TIMEOUT 环境变量调大（当前默认 300s）",
                exit_code=EXIT_CONNECTION,
            )
        if isinstance(last_exc, httpx.ConnectError):
            error("CONNECTION_ERROR", f"无法连接到 {self.base_url}，请检查 IR_BASE_URL 配置", exit_code=EXIT_CONNECTION)
        error(
            "NETWORK_ERROR",
            f"网络传输中断: {type(last_exc).__name__}: {last_exc}；服务端可能仍在执行，请先回查结果再决定是否重发",
            exit_code=EXIT_CONNECTION,
        )

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        """GET 请求（幂等，自动重试）"""
        return self._request("GET", path, params=params, retryable=True)

    def post(
        self,
        path: str,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        raise_errors: bool = False,
    ) -> dict:
        """POST 请求（非幂等，不重试）"""
        return self._request("POST", path, json_data=json_data, params=params, raise_errors=raise_errors)

    def put(self, path: str, json_data: Optional[dict] = None) -> dict:
        """PUT 请求（非幂等，不重试）"""
        return self._request("PUT", path, json_data=json_data)

    def delete(self, path: str, params: Optional[dict] = None) -> dict:
        """DELETE 请求（非幂等，不重试）"""
        return self._request("DELETE", path, params=params)

    def get_all(self, path: str, params: Optional[dict] = None) -> dict:
        """分页获取所有记录"""
        params = dict(params or {})
        params["page_size"] = 100
        params["page"] = 1

        all_items = []
        while True:
            result = self.get(path, params=params)
            items = result.get("data", [])
            all_items.extend(items)
            meta = result.get("meta", {})
            total = meta.get("total", 0)
            if len(all_items) >= total or not items:
                break
            params["page"] += 1

        return {
            "data": all_items,
            "meta": {"total": len(all_items), "has_more": False},
        }

    def close(self):
        """关闭 HTTP 客户端"""
        self._client.close()
