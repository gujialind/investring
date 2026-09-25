"""
命令层共享工具

- build_body / resolve_body: 请求体构造（过滤 None、支持 --json 直传）
- validate_body: 本地日期/枚举预校验（不发请求即可拦截格式错误）
- project_fields: 输出字段裁剪（降低 AI agent 上下文消耗）
- run_list: 列表命令统一执行（--all 全量翻页 + --fields 裁剪 + 默认摘要字段/--full）
- SUMMARY_FIELDS: 各资源列表默认输出的摘要字段集
"""
import json
from datetime import date
from typing import Any, Optional

from ir_cli.client import APIClient
from ir_cli.output import error, success

# 后端枚举取值（与 AGENTS.md 附录 A 一致）
ENUMS = {
    "trade_type": ("buy", "sell"),
    "sub_type": ("subscribe", "redeem"),
    "event_type": (
        "cash_dividend", "reinvest_dividend", "share_split",
        "share_merge", "bonus_share", "forced_adjustment",
    ),
    "market": ("CN_EXCHANGE", "CN_OTC", "HK_MUTUAL"),
    "product_type": ("ETF", "OEF", "LOF", "CASH"),
    "role": ("admin", "viewer"),
}

# 日期类字段名（后缀匹配）
DATE_FIELDS = ("_date",)

# 列表默认输出的摘要字段集（对照后端 schemas）；--full 输出全字段，--fields 显式指定优先级最高
SUMMARY_FIELDS = {
    "trade": "id,portfolio_code,product_code,market,platform_code,trade_type,trade_date,confirm_date,status,amount,shares,price,cash_platform_code,cash_confirm_date",
    "subscription": "id,portfolio_code,investor_code,platform_code,sub_type,apply_date,confirm_date,status,amount,shares,unit_price",
    "position": "id,portfolio_code,product_code,market,platform_code,shares,cash_amount,market_value,unit_price,snapshot_date",
    "log_login": "id,investor_code,action,status,ip_address,failure_reason,created_at",
    "log_audit": "id,investor_code,action,resource_type,resource_id,resource_name,created_at",
    "log_error": "id,error_type,error_code,error_message,request_path,created_at",
    "asset_classification": "code,dimension,name,sort_order,is_active,applicable_asset_classes",
}


def validate_body(body: dict) -> None:
    """
    本地预校验请求体：日期格式（YYYY-MM-DD）与枚举取值。
    不合法时直接报 VALIDATION_ERROR，避免一次往返后才发现格式错误。
    """
    for key, value in body.items():
        if value is None:
            continue
        if isinstance(value, str) and any(key.endswith(suf) for suf in DATE_FIELDS):
            try:
                # fromisoformat 也接受紧凑日期/周日期；CLI 契约只接受 YYYY-MM-DD。
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError:
                error("VALIDATION_ERROR", f"字段 {key} 日期格式非法: '{value}'，需为 YYYY-MM-DD")
        allowed = ENUMS.get(key)
        if allowed and value not in allowed:
            error(
                "VALIDATION_ERROR",
                f"字段 {key} 取值非法: '{value}'，允许值: {', '.join(allowed)}",
            )


def build_body(**kwargs) -> dict:
    """过滤 None 值，构造请求体"""
    return {k: v for k, v in kwargs.items() if v is not None}


def resolve_body(json_body: Optional[str], required: tuple = (), **kwargs) -> dict:
    """
    构造请求体：--json 直传优先，否则由逐项参数构造。

    Args:
        json_body: --json 选项传入的完整 JSON 请求体字符串
        required: 必填字段名列表（无论来源，缺失即报 VALIDATION_ERROR）
        **kwargs: 逐项参数（None 值被过滤）
    """
    if json_body is not None:
        try:
            body = json.loads(json_body)
        except json.JSONDecodeError as e:
            error("INVALID_JSON", f"--json 参数不是合法 JSON: {e}")
        if not isinstance(body, dict):
            error("INVALID_JSON", "--json 参数必须是 JSON 对象")
    else:
        body = build_body(**kwargs)
    missing = [k for k in required if body.get(k) is None]
    if missing:
        error("VALIDATION_ERROR", f"缺少必填字段: {', '.join(missing)}")
    validate_body(body)
    return body


def project_fields(data: Any, fields: Optional[str]) -> Any:
    """按逗号分隔的字段列表裁剪 dict / list[dict] 输出"""
    if not fields:
        return data
    keys = [f.strip() for f in fields.split(",") if f.strip()]
    if not keys:
        return data
    if isinstance(data, list):
        return [{k: it.get(k) for k in keys} if isinstance(it, dict) else it for it in data]
    if isinstance(data, dict):
        return {k: data.get(k) for k in keys}
    return data


def run_list(
    client: APIClient,
    path: str,
    params: Optional[dict] = None,
    page: int = 1,
    page_size: int = 20,
    all_pages: bool = False,
    fields: Optional[str] = None,
    default_fields: Optional[str] = None,
    full: bool = False,
) -> None:
    """执行列表查询并输出。

    字段裁剪优先级：显式 --fields > --full（全字段）> default_fields（摘要字段）。
    --all 时自动翻完所有页。
    """
    params = dict(params or {})
    if all_pages:
        result = client.get_all(path, params=params)
    else:
        params["page"] = page
        params["page_size"] = page_size
        result = client.get(path, params=params)
    effective_fields = fields if fields else (None if full else default_fields)
    success(data=project_fields(result["data"], effective_fields), meta=result.get("meta"))
