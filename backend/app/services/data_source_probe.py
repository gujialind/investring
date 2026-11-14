"""数据源连通检测单点（issue #880，SPEC v1.1 V3 / Block 2）。

添加产品 / 切换数据源前的只读前置检测：对目标源真的取一条净值，取不到即阻断保存。

- **不写 price_record、不改产品行**（只读检测，探测成功 ≠ 同步成功）；
- **预算独立于同步链路**：最多 2 次尝试 + 1s 快速重试（同步是 3 次 + 10/30/60s 退避，
  最坏分钟级，HTTP 保存不可接受）；限频 sleep 沿用各源 rate_interval，不绕过限频；
- **token 预检先于探测**（g）：目标 tushare 且无 token → INVALID_DATA_SOURCE，不发起上游调用；
- 失败抛 BusinessError：DATA_SOURCE_PROBE_FAILED（reason=unreachable|no_data|auth_error）。

create 必探、update 仅 data_source 实际变化时探（调用方判定）；REST/CLI/批量共用本单点。
调用方须先经 product_service.validate_data_source（枚举/市场兼容/虚拟产品），本函数假定
data_source ∈ {tushare, akshare} 且 market 与源兼容（港互认+tushare 已被校验器拒绝）。
"""
import logging
import time
from datetime import timedelta

from app.config import get_settings
from app.services.exceptions import BusinessError
from app.utils.business_time import business_today

logger = logging.getLogger(__name__)

# 探测预算（独立于同步重试参数，SPEC v1.1 V3）：最多 2 次尝试，重试间隔 1s。
PROBE_MAX_ATTEMPTS = 2
PROBE_RETRY_DELAY_SECONDS = 1.0
# 窄窗自然日：场内/港互认按此窗口取数；场外 akshare 全量下载取最新行（窗口仅作语义说明）。
PROBE_WINDOW_DAYS = 10


def _usable_values(values):
    """复用写入侧净值合格判据（#580 可用性 + #701 必须为正），不另立一套。"""
    from app.services.market_data_service import _usable_nav

    return [v for v in values if _usable_nav(v)]


def _fetch_tushare_values(market, code, start_str, end_str):
    """tushare 窄窗净值/收盘价原值列表。token 缺失由 _get_tushare_pro 抛 TushareNotConfiguredError。"""
    from app.services.tushare_client import _get_tushare_pro

    pro = _get_tushare_pro()
    if market == "CN_OTC":
        df = pro.fund_nav(ts_code=code, start_date=start_str, end_date=end_str)
        key = "unit_nav"
    elif market == "CN_EXCHANGE":
        df = pro.fund_daily(ts_code=code, start_date=start_str, end_date=end_str)
        key = "close"
    else:
        raise ValueError(f"tushare 不支持的市场: {market}")
    if df is None or df.empty:
        return []
    return [row.get(key) for row in df.to_dict("records")]


def _fetch_akshare_values(market, code, start_str, end_str):
    """akshare 净值/收盘价原值列表。场外全量下载取最新行；场内/港互认窄窗。"""
    import akshare as ak

    from app.services.akshare_client import _hk_nav_request_page, _resolve_hk10

    symbol = code.split(".")[0]
    if market == "CN_OTC":
        df = ak.fund_open_fund_info_em(symbol=symbol, indicator="单位净值走势")
        if df is None or df.empty:
            return []
        return [row.get("单位净值") for row in df.to_dict("records")]
    if market == "CN_EXCHANGE":
        df = ak.fund_etf_hist_em(
            symbol=symbol, period="daily", start_date=start_str, end_date=end_str, adjust=""
        )
        if df is None or df.empty:
            return []
        return [row.get("收盘") for row in df.to_dict("records")]
    if market == "HK_MUTUAL":
        hk10 = _resolve_hk10(code)
        d1 = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:]}"
        d2 = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:]}"
        payload = _hk_nav_request_page(hk10, 0, d1, d2)
        rows = payload.get("Data") or []
        return [row.get("NAV") for row in rows]
    raise ValueError(f"akshare 不支持的市场: {market}")


def _classify_probe_error(exc):
    msg = str(exc).lower()
    if any(k in msg for k in ("token", "权限", "积分", "auth", "permission")):
        return "auth_error", "数据源鉴权失败，请检查 Tushare token 是否有效"
    return "unreachable", "数据源暂时不可达，请稍后重试"


def probe_data_source(code, market, data_source):
    """连通检测单点。通过返回 None；失败抛 BusinessError（阻断保存，零写入）。"""
    settings = get_settings()
    end = business_today()
    start = end - timedelta(days=PROBE_WINDOW_DAYS)
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    # token 预检先于探测（g）：tushare 无 token 直接拒绝，不发起上游调用
    if data_source == "tushare":
        from app.services.tushare_client import TushareNotConfiguredError, _get_tushare_pro

        try:
            _get_tushare_pro()
        except TushareNotConfiguredError:
            raise BusinessError(
                "INVALID_DATA_SOURCE",
                "Tushare token 未配置，请先在系统设置配置 TUSHARE_TOKEN",
                details={"reason": "token_missing", "data_source": data_source},
            )
        fetch = lambda: _fetch_tushare_values(market, code, start_str, end_str)
        rate_interval = settings.tushare_rate_interval
    elif data_source == "akshare":
        fetch = lambda: _fetch_akshare_values(market, code, start_str, end_str)
        rate_interval = settings.akshare_rate_interval
    else:
        raise BusinessError(
            "INVALID_DATA_SOURCE",
            f"非法数据源：{data_source}",
            details={"data_source": data_source},
        )

    last_exc = None
    for attempt in range(PROBE_MAX_ATTEMPTS):
        try:
            time.sleep(rate_interval)
            values = fetch()
            if _usable_values(values):
                return None
            # 上游可达但无合格净值 → no_data（重试也是同样结果，不重试）
            raise BusinessError(
                "DATA_SOURCE_PROBE_FAILED",
                "目标数据源未返回该产品的净值，请核对代码与市场",
                details={
                    "reason": "no_data",
                    "data_source": data_source,
                    "market": market,
                    "product_code": code,
                },
            )
        except BusinessError:
            raise
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "数据源连通检测第 %d/%d 次失败：%s", attempt + 1, PROBE_MAX_ATTEMPTS, exc
            )
            if attempt < PROBE_MAX_ATTEMPTS - 1:
                time.sleep(PROBE_RETRY_DELAY_SECONDS)

    reason, message = _classify_probe_error(last_exc)
    raise BusinessError(
        "DATA_SOURCE_PROBE_FAILED",
        message,
        details={
            "reason": reason,
            "data_source": data_source,
            "market": market,
            "product_code": code,
            "error": str(last_exc)[:200],
        },
    )
