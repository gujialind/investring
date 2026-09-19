"""
JSON 输出协议

统一输出格式：
- 成功: {"ok": true, "data": ..., "meta": ..., "hints": [...]}
- 失败: {"ok": false, "error": {"code": ..., "message": ..., "hints": [...]}}
- hints 为可选字段：错误侧按错误码自动附加补救指引（见 hints.py），
  成功侧由命令在关键节点主动提供下一步建议。

退出码分层：
- 0: 成功
- 1: 业务错误（可换参数重试）
- 2: 认证错误（需 ir auth login）
- 3: 连接/超时错误（可原样重试或检查服务）
- 64: 用法错误（选项/参数不存在，命令本身没跑；取 sysexits.h EX_USAGE，
  不与上面任何一档语义重叠——#520：Click 缺省也返回 2，会被误读成「未登录」）
"""
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any, NoReturn, Optional

from ir_cli.hints import get_hint


class InvestRingEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，处理 Decimal/date/datetime 类型"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            # 字符串化避免 float 二进制误差（如 0.1+0.2 问题），agent 可直接用于金额比对
            return format(obj, "f")
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)


def _output(data: dict) -> None:
    """输出 JSON 到 stdout"""
    print(json.dumps(data, cls=InvestRingEncoder, ensure_ascii=False))


def success(data: Any, meta: Optional[dict] = None, hints: Optional[list] = None) -> None:
    """
    成功输出并退出（exit code 0）

    Args:
        data: 主要数据（dict/list/model dict）
        meta: 分页元数据 {"total", "page", "page_size"}
        hints: 下一步操作建议（供 AI agent 参考）
    """
    result = {"ok": True, "data": data}
    if meta:
        result["meta"] = meta
    if hints:
        result["hints"] = hints
    _output(result)
    sys.exit(0)


# 退出码常量
EXIT_BUSINESS = 1
EXIT_AUTH = 2
EXIT_CONNECTION = 3
EXIT_USAGE = 64  # sysexits.h EX_USAGE；用法错误（#520）


def error(
    code: str,
    message: str,
    details: Optional[dict] = None,
    exit_code: int = EXIT_BUSINESS,
    hints: Optional[list] = None,
) -> NoReturn:
    """
    错误输出并退出

    Args:
        code: 错误码，如 NOT_FOUND, VALIDATION_ERROR
        message: 人类可读错误描述
        details: 额外详情
        exit_code: 退出码（1=业务 2=认证 3=连接 64=用法，见模块 docstring）
        hints: 补救指引；缺省时按 code+details 自动生成（get_hint，issue #86）
    """
    result: dict = {
        "ok": False,
        "error": {"code": code, "message": message},
    }
    if details:
        result["error"]["details"] = details
    if hints is None:
        auto_hint = get_hint(code, details)
        hints = [auto_hint] if auto_hint else None
    if hints:
        result["error"]["hints"] = hints
    _output(result)
    sys.exit(exit_code)
