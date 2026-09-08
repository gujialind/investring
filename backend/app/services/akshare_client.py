"""AkShare 客户端 — 与 tushare_client.py 平级。

通过 akshare 获取场外基金净值、场内 ETF 行情、香港互认基金净值。
akshare 未安装时，调用会抛 AkshareAPIError，不影响 tushare 主路径。
"""
import time
import logging
from typing import List, Dict, Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

_hk_code_map: Optional[Dict[str, str]] = None


class AkshareAPIError(Exception):
    """AkShare API 调用错误"""
    pass


def _rate_limit_sleep():
    time.sleep(get_settings().akshare_rate_interval)


def _retry(func, error_label: str):
    settings = get_settings()
    max_retries = settings.akshare_max_retries
    delay = 1
    for attempt in range(max_retries):
        _rate_limit_sleep()
        try:
            return func()
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    "%s（第 %d/%d 次尝试）：%s，%ds 后重试",
                    error_label, attempt + 1, max_retries, e, delay,
                )
                time.sleep(delay)
                delay *= 2
                continue
            logger.error(
                "%s（已重试 %d 次）：%s", error_label, max_retries, e, exc_info=True
            )
            raise AkshareAPIError(f"{error_label}: {e}")


def _to_yyyymmdd(d: str) -> str:
    """将 YYYY-MM-DD 转为 YYYYMMDD"""
    return d.replace("-", "")


def get_fund_nav_otc(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    场外基金净值（CN_OTC）。
    symbol: 基金代码（如 '000051' 或 '000051.OF'，取 . 前部分）
    start_date/end_date: YYYYMMDD 格式
    返回: [{trade_date: 'YYYYMMDD', unit_nav, accum_nav}]
    """
    try:
        import akshare as ak
    except ImportError:
        raise AkshareAPIError("akshare 未安装，请 pip install akshare")

    code = symbol.split(".")[0]

    def _fetch():
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        result = []
        for _, row in df.iterrows():
            td = _to_yyyymmdd(str(row["净值日期"]))
            if start_date and td < start_date:
                continue
            if end_date and td > end_date:
                continue
            result.append({
                "trade_date": td,
                "unit_nav": float(row["单位净值"]),
                "accum_nav": None,
            })
        return result

    return _retry(_fetch, "获取场外基金净值失败")


def get_fund_daily_exchange(
    etf_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    场内 ETF 日线（CN_EXCHANGE）。
    etf_code: ETF 代码（如 '510300' 或 '510300.SH'，取 . 前部分）
    start_date/end_date: YYYYMMDD 格式
    返回: [{trade_date: 'YYYYMMDD', close, pre_close, pct_change}]
    """
    try:
        import akshare as ak
    except ImportError:
        raise AkshareAPIError("akshare 未安装，请 pip install akshare")

    code = etf_code.split(".")[0]

    def _fetch():
        sd = start_date or "19900101"
        ed = end_date or "20500101"
        df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=sd, end_date=ed, adjust="")
        result = []
        for _, row in df.iterrows():
            td = _to_yyyymmdd(str(row["日期"]))
            result.append({
                "trade_date": td,
                "close": float(row["收盘"]),
                "pre_close": float(row["开盘"]),
                "pct_change": float(row["涨跌幅"]),
            })
        return result

    return _retry(_fetch, "获取场内 ETF 日线失败")


def _ensure_hk_code_map(ak) -> Dict[str, str]:
    """构建 HK_MUTUAL 6位→10位代码映射（懒加载缓存）"""
    global _hk_code_map
    if _hk_code_map is None:
        df = ak.fund_hk_rank_em()
        _hk_code_map = {}
        for _, row in df.iterrows():
            code6 = str(row["基金代码"]).strip()
            code10 = str(row["香港基金代码"]).strip()
            if code6 and code10:
                _hk_code_map[code6] = code10
    return _hk_code_map


def get_fund_hk_mutual(
    code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    香港互认基金净值（HK_MUTUAL）。
    code: 6位内地销售代码，自动映射为10位香港基金代码
    start_date/end_date: YYYYMMDD 格式
    返回: [{trade_date: 'YYYYMMDD', unit_price, accumulated_nav}]
    """
    try:
        import akshare as ak
    except ImportError:
        raise AkshareAPIError("akshare 未安装，请 pip install akshare")

    code6 = code.split(".")[0]

    def _fetch():
        code_map = _ensure_hk_code_map(ak)
        hk10 = code_map.get(code6)
        if not hk10:
            if len(code6) == 10:
                hk10 = code6
            else:
                raise AkshareAPIError(f"未找到 6 位代码 {code6} 对应的香港基金代码")

        df = ak.fund_hk_fund_hist_em(code=hk10, symbol="历史净值明细")
        result = []
        for _, row in df.iterrows():
            td = _to_yyyymmdd(str(row["净值日期"]))
            if start_date and td < start_date:
                continue
            if end_date and td > end_date:
                continue
            result.append({
                "trade_date": td,
                "unit_price": float(row["单位净值"]),
                "accumulated_nav": None,
            })
        return result

    return _retry(_fetch, "获取香港互认基金净值失败")
