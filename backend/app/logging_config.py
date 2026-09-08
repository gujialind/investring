"""集中日志配置（issue #404）：stdout 单行 JSON。

只输出到 stdout、不写文件——轮转交给 docker `json-file` driver（生产已配 10m×3）。
`setup_logging()` 幂等：uvicorn、pytest、`scripts/run_e2e_backend.py` 是多入口，
重复调用不得产生重复日志行。
"""

import json
import logging
import logging.config
from datetime import datetime, timezone

from app.config import get_settings
from app.context import get_actor, get_request_id

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# LogRecord 自带属性 + Formatter 注入属性；经 extra= 传入的其余键原样进 JSON
_RESERVED_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
}

# uvicorn 经 extra= 附带 color_message（含 ANSI 转义与未渲染的 %d 占位），
# 渲染后的文本已在 message 里，这个字段对日志消费方纯属噪声
_SKIPPED_EXTRA = frozenset({"color_message"})


class JsonFormatter(logging.Formatter):
    """单行 JSON：中文不转义，异常折叠进 `exception` 字段而非拼进 message。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED_ATTRS and key not in _SKIPPED_EXTRA:
                payload[key] = value
        # extra= 显式传过的（如未预期异常 handler 从 scope 取回的 request_id）优先
        if "request_id" not in payload:
            request_id = get_request_id()
            if request_id:
                payload["request_id"] = request_id
        if "actor" not in payload:
            actor = get_actor()
            if actor:
                payload["actor"] = actor
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def resolve_log_level() -> str:
    """`LOG_LEVEL`（→ `settings.log_level`）优先，否则由 `settings.debug` 推导。"""
    settings = get_settings()
    configured = (settings.log_level or "").strip().upper()
    if configured in _VALID_LEVELS:
        return configured
    return "DEBUG" if settings.debug else "INFO"


def _build_config(level: str) -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            # 访问日志由 RequestContextMiddleware 单点产出，uvicorn 自己的关掉，
            # 否则每个请求出两行
            "uvicorn.access": {"handlers": [], "propagate": False},
            # 启动/关闭/ASGI 异常等 uvicorn 自身日志改走 root，与应用日志同为 JSON
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            # 刻意不覆盖 sqlalchemy.engine：SQL 详略由 database.py 的
            # echo=settings.debug 控制，这里再钉级别会与之争用
        },
    }


def setup_logging() -> None:
    """配置 root logger 输出 JSON 到 stdout。幂等。"""
    if _is_configured():
        return
    logging.config.dictConfig(_build_config(resolve_log_level()))


def _is_configured() -> bool:
    root = logging.getLogger()
    return any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
