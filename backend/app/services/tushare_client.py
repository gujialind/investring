import math
import os
import time
from datetime import date
from pathlib import Path
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
import tushare as ts

from app.config import get_settings

_ENV_LOADED = False


def _ensure_env_loaded():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_paths = [
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path(".env"),
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(dotenv_path=str(env_path))
            _ENV_LOADED = True
            return
    _ENV_LOADED = True


def _get_tushare_pro():
    """获取 Tushare Pro 实例，带 Token 校验"""
    _ensure_env_loaded()
    settings = get_settings()
    token = settings.tushare_token or os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise TushareNotConfiguredError("Tushare token 未配置，请在 .env 文件中设置 TUSHARE_TOKEN")
    return ts.pro_api(token)


class TushareNotConfiguredError(Exception):
    """Tushare 未配置错误"""
    pass


class TushareAPIError(Exception):
    """Tushare API 调用错误"""
    pass


def _rate_limit_sleep():
    """每次 pro.xxx() 调用前 sleep，per-API-call 粒度（sleep 时长由 settings.tushare_rate_interval 控制，满足 Tushare 官方限频）"""
    time.sleep(get_settings().tushare_rate_interval)


def _is_rate_limit_error(exc) -> bool:
    """检测 Tushare 频率限制错误"""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("频率", "rate", "每分钟", "limit", "too many"))


def _get_backoff_delays():
    """从 config 获取限频退避延迟列表 [10, 30, 60]"""
    raw = get_settings().tushare_rate_limit_backoff
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _retry_with_rate_limit(fetch_func, error_label: str):
    """带限流 sleep + 限频退避 + 网络退避的重试包装"""
    settings = get_settings()
    max_retries = settings.tushare_max_retries
    backoff_delays = _get_backoff_delays()
    retry_delay = 1

    for attempt in range(max_retries):
        _rate_limit_sleep()
        try:
            return fetch_func()
        except Exception as e:
            if attempt < max_retries - 1:
                if _is_rate_limit_error(e):
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    time.sleep(delay)
                else:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                continue
            raise TushareAPIError(f"{error_label}: {str(e)}")


def get_trade_calendar(year: int, exchange: str = "SSE") -> List[Dict[str, Any]]:
    """
    获取指定年份的交易日历

    Args:
        year: 年份，如 2026
        exchange: 交易所代码，SSE(上交所) 或 SZSE(深交所)，默认 SSE

    Returns:
        交易日历列表，每项包含 date(YYYY-MM-DD) 和 is_open(bool)
        例如: [{"date": "2026-01-05", "is_open": True}, ...]

    Raises:
        TushareNotConfiguredError: Tushare token 未配置
        TushareAPIError: API 调用失败
    """
    pro = _get_tushare_pro()

    start_date = f"{year}0101"
    end_date = f"{year}1231"

    df = _retry_with_rate_limit(
        lambda: pro.trade_cal(
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            fields="cal_date,is_open",
        ),
        "获取交易日历失败",
    )

    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        cal_date = str(row["cal_date"])
        # 将 YYYYMMDD 转换为 YYYY-MM-DD
        formatted_date = f"{cal_date[:4]}-{cal_date[4:6]}-{cal_date[6:]}"
        is_open = bool(row["is_open"] == 1)
        result.append({
            "date": formatted_date,
            "is_open": is_open,
        })

    return result


def get_trade_calendar_years(years: List[int], exchange: str = "SSE") -> List[Dict[str, Any]]:
    """
    批量获取多个年份的交易日历

    Args:
        years: 年份列表
        exchange: 交易所代码

    Returns:
        合并后的交易日历列表
    """
    all_dates = []
    for year in years:
        dates = get_trade_calendar(year, exchange)
        all_dates.extend(dates)
    return all_dates


def get_hk_trade_calendar(year: int) -> List[Dict[str, Any]]:
    """
    获取指定年份的港股（HKEX）交易日历（issue #777）

    走 Tushare 专用接口 hk_tradecal（2000 积分档，现有 token 已实测可调）；
    通用 trade_cal 的 exchange 参数只覆盖内地交易所，不支持 HKEX。
    单次上限 2000 行，按年拉取（365/366 行）不触限。

    Args:
        year: 年份，如 2026

    Returns:
        交易日历列表，结构与 get_trade_calendar 对齐：
        [{"date": "2026-10-19", "is_open": False}, ...]

    Raises:
        TushareNotConfiguredError: Tushare token 未配置
        TushareAPIError: API 调用失败（含积分不足）
    """
    pro = _get_tushare_pro()

    start_date = f"{year}0101"
    end_date = f"{year}1231"

    df = _retry_with_rate_limit(
        lambda: pro.hk_tradecal(
            start_date=start_date,
            end_date=end_date,
            fields="cal_date,is_open",
        ),
        "获取港股交易日历失败",
    )

    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        cal_date = str(row["cal_date"])
        formatted_date = f"{cal_date[:4]}-{cal_date[4:6]}-{cal_date[6:]}"
        is_open = bool(row["is_open"] == 1)
        result.append({
            "date": formatted_date,
            "is_open": is_open,
        })

    return result


def get_fund_daily(
    ts_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    获取场内基金日线行情（收盘价）

    Args:
        ts_code: 基金代码（如 510300.SH）
        start_date: 开始日期（YYYYMMDD），不传则返回从有数据以来的全部记录
        end_date: 结束日期（YYYYMMDD），不传则到最新日期

    Returns:
        行情数据列表，包含 trade_date, close, pre_close, pct_chg 等
    """
    pro = _get_tushare_pro()

    kwargs = {"ts_code": ts_code}
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date

    df = _retry_with_rate_limit(
        lambda: pro.fund_daily(**kwargs),
        "获取基金日线行情失败",
    )

    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        # `is not None` 而非 truthiness（#692）：平盘日 pct_chg 恰为 0.0，被 truthiness
        # 折成 None 后前端读成「无数据」而非 0%。NaN 由写侧 `_usable_price` 兜住，本层不归一。
        result.append({
            "trade_date": str(row["trade_date"]),
            "close": float(row["close"]) if row.get("close") is not None else None,
            "pre_close": float(row["pre_close"]) if row.get("pre_close") is not None else None,
            "pct_chg": float(row["pct_chg"]) if row.get("pct_chg") is not None else None,
        })

    return result


def get_fund_nav(
    ts_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    获取场外基金净值数据

    Args:
        ts_code: 基金代码（如 000001.OF）
        start_date: 开始日期（YYYYMMDD），不传则返回从有数据以来的全部记录
        end_date: 结束日期（YYYYMMDD），不传则到最新日期

    Returns:
        净值数据列表，包含 trade_date, unit_nav, accum_nav 等
    """
    pro = _get_tushare_pro()

    kwargs = {"ts_code": ts_code}
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date

    df = _retry_with_rate_limit(
        lambda: pro.fund_nav(**kwargs),
        "获取基金净值失败",
    )

    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        # `unit_nav`/`accum_nav` 判 `is not None` 而非 truthiness（#701，与上方
        # get_fund_daily 的 close 同写法）：这一层曾把 `0.0` 静默折成缺值，是全路径上
        # 唯一挡住 0 价的地方。现在净值列的正负由写入判据 `market_data_service._usable_nav`
        # 收口，坏值在那里落成「跳过 + WARNING + 计 0 条」，比在这里无声消失可查——
        # 取数层只负责「上游给没给值」，不负责判断这个值像不像净值。
        result.append({
            "trade_date": str(row["nav_date"]) if row.get("nav_date") else None,
            "unit_nav": float(row["unit_nav"]) if row.get("unit_nav") is not None else None,
            "accum_nav": float(row["accum_nav"]) if row.get("accum_nav") is not None else None,
        })

    return result


def _div_cash_or_none(raw: Any) -> Optional[float]:
    """`fund_div.div_cash` 原值 → float；缺值与非有限值（NaN/Inf）一律归一为 None。

    不能只判 `is not None`：pandas 的缺失值是 `float('nan')` 而非 None，会原样透传下去。
    下游三道出口的判据都是 `Decimal(str(x))` 的有序比较（服务层单点
    `dividend_div_cash_invalid`、检测口复用它、读侧 `reinvest_receivable` 的 `amount <= 0`），
    而 NaN 让这条比较抛 `InvalidOperation`（不是返回 False）——检测口抛出的
    异常会被产品级 `except Exception` 兜住并 `db.rollback()`，把该产品本轮已建的有效事件一起
    撤掉，且只要这行公告还在就每轮重演（正是 #771 要消除的反复失败形态）。净值列的同一形态
    （`get_fund_nav` 的 NaN）不改这里，因为写侧 `_usable_nav` 已有 `isfinite` 判据兜住；
    `div_cash` 没有对应的写侧闸门，故在单一来源归一，让下游自然走既有 NULL 口径。

    本层不判正负：0/负值是「上游给了一个坏值」，留给检测口留痕跳过、服务层拒绝。
    """
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def get_fund_div(
    ts_code: str,
) -> List[Dict[str, Any]]:
    """
    获取基金分红信息

    Args:
        ts_code: 基金代码（如 000001.OF）

    Returns:
        分红记录列表，包含 ex_date(YYYYMMDD), record_date(YYYYMMDD), div_cash, div_proc
    """
    pro = _get_tushare_pro()

    df = _retry_with_rate_limit(
        lambda: pro.fund_div(ts_code=ts_code),
        "获取基金分红信息失败",
    )

    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        result.append({
            "ex_date": str(row.get("ex_date", "")),
            "record_date": str(row.get("record_date", "")),
            "div_cash": _div_cash_or_none(row.get("div_cash")),
            "div_proc": str(row.get("div_proc", "")),
        })

    return result
