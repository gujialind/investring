"""AkShare 客户端 — 与 tushare_client.py 平级。

通过 akshare 获取场外基金净值、场内 ETF 行情；香港互认基金净值直连东财海外接口
（#651：akshare 的 `fund_hk_fund_hist_em` 按固定位置重命名上游 JSON 列，键序一变
即静默错位，故港互认不再消费 akshare 的返回结构）。
akshare 未安装时，调用会抛 AkshareAPIError，不影响 tushare 主路径。
"""
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

_hk_code_map: Optional[Dict[str, str]] = None

# 东财海外基金接口（港互认历史净值明细）。分页与参数形态与 akshare 同源，
# 但按 TotalCount 取净而非恒取第一页。
_HK_NAV_URL = "https://overseas.1234567.com.cn/overseasapi/OpenApiHander.ashx"
_HK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    )
}
_HK_PAGE_SIZE = 1000
_HK_MAX_PAGES = 10
_HK_TIMEOUT = 15
_HK_SKIPPED_SAMPLE_CAP = 10

_DASHED_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_RE = re.compile(r"^\d{8}$")


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


def _compact_date(raw: Any) -> Optional[str]:
    """上游日期原值 → YYYYMMDD；不是这两种形态就返回 None（不猜、不放宽）。

    #651 的主判据：接口键序错位时，日期列会先塌成 `"元"` 这类非日期串，而 pandas
    对坏值常给 NaT（`str()` 后是 "NaT"，且 `"NaT" > "20260929"` 为真）。让这种值
    流过日期过滤器，就是「整段静默空集」；返回 None 才能把它交给响亮判据。
    """
    text = str(raw).strip()
    if _DASHED_DATE_RE.match(text):
        return _to_yyyymmdd(text)
    if _COMPACT_DATE_RE.match(text):
        return text
    return None


def _finite_number(raw: Any) -> Optional[float]:
    """上游价格原值 → float；None/空串/NaN/Inf/非数字返回 None。

    与写入侧 `market_data_service._usable_price`（#580）判的是同一族坏值，但这里
    还兼作「这一行是否真的是净值」的分类输入，故刻意各留一处：把 DB 层 service
    引进叶子数据源模块会反转分层（且依赖对方恰好函数体内 import）。两处改动需同步。
    """
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _screen_nav_rows(
    pairs: List[Tuple[Any, Any]],
    *,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Tuple[List[Tuple[str, float]], List[str]]:
    """把 (日期原值, 价格原值) 序列筛成 (YYYYMMDD, price)，并回报无法解析的样本。

    日期先于区间判定：坏日期无法比较区间，把它记成 malformed 而不是默默丢掉，
    「上游给了行却零可解析」这条判据才有输入。落在区间外是正常剔除，不算 malformed。
    """
    usable: List[Tuple[str, float]] = []
    malformed: List[str] = []
    for index, (raw_date, raw_price) in enumerate(pairs):
        td = _compact_date(raw_date)
        if td is None:
            malformed.append(f"{index}:date={raw_date!r}")
            continue
        price = _finite_number(raw_price)
        if price is None:
            malformed.append(f"{index}:price={raw_price!r}")
            continue
        if start_date and td < start_date:
            continue
        if end_date and td > end_date:
            continue
        usable.append((td, price))
    return usable, malformed


def _finalize_nav_rows(
    usable: List[Tuple[str, float]],
    malformed: List[str],
    *,
    upstream_rows: int,
    product_code: str,
    market: str,
    operation: str,
    start_date: Optional[str],
    unwindowed_empty_is_error: bool,
) -> None:
    """#651 的响亮判据，三条取数路径共用：什么情况静默、什么情况报错。

    - 有可解析行：正常；若同时有坏行，局部漂移记 WARNING 后照常返回，不为几行坏值
      丢掉整段可用历史。
    - 上游给了行、却没有一行可解析：结构变动，响亮抛错，绝不回落成「空集」。
    - 上游 0 行：带区间的请求合法地可能为空（周末、停市日、净值尚未发布）。实测东财
      对无效代码返回 `Code:"1" Data:[]`，与周末窗口逐字节同形，故只在**不带下界的
      全历史请求**上把它当错误——那不可能良性。一律判失败会让每个周一都假报警。
    """
    if usable:
        if malformed:
            logger.warning(
                "跳过无法解析的净值行（#651）",
                extra={
                    "operation": operation,
                    "product_code": product_code,
                    "market": market,
                    "upstream_rows": upstream_rows,
                    "parsed": len(usable),
                    "skipped": malformed[:_HK_SKIPPED_SAMPLE_CAP],
                },
            )
        return

    if malformed:
        logger.error(
            "数据源返回行但无一行可解析（#651，疑似上游接口结构变动）",
            extra={
                "operation": operation,
                "product_code": product_code,
                "market": market,
                "upstream_rows": upstream_rows,
                "parsed": 0,
                "skipped": malformed[:_HK_SKIPPED_SAMPLE_CAP],
            },
        )
        raise AkshareAPIError(
            f"{market} {product_code} 上游返回 {upstream_rows} 行但无一行可解析"
            f"（区间起点 {start_date}），疑似接口结构变动"
        )

    if unwindowed_empty_is_error and upstream_rows == 0:
        logger.error(
            "全历史区间未返回任何净值行（#651）",
            extra={
                "operation": operation,
                "product_code": product_code,
                "market": market,
                "upstream_rows": 0,
                "parsed": 0,
            },
        )
        raise AkshareAPIError(
            f"{market} {product_code} 全历史区间未返回任何净值行"
            "（无效代码或接口语义变动）"
        )


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
        # akshare 在此按位置重命名 4 列，与港互认同族；但它重命名前已引用 temp_df["x"]，
        # 键被改名会先抛 KeyError（响亮）。位置错位若只调换尾部列，本路径不受影响。
        pairs = [(row["净值日期"], row["单位净值"]) for _, row in df.iterrows()]
        usable, malformed = _screen_nav_rows(
            pairs, start_date=start_date, end_date=end_date
        )
        return usable, malformed, len(pairs)

    usable, malformed, upstream_rows = _retry(_fetch, "获取场外基金净值失败")
    _finalize_nav_rows(
        usable, malformed,
        upstream_rows=upstream_rows, product_code=code, market="CN_OTC",
        operation="fetch_fund_nav_otc", start_date=start_date,
        unwindowed_empty_is_error=start_date is None,
    )
    return [
        {"trade_date": td, "unit_nav": price, "accum_nav": None}
        for td, price in usable
    ]


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
        # 上游是逗号分隔 kline 串，列语义由我们自行声明的 fields2=f51..f61 钉住，
        # 不存在港互认那种键序漂移；无 k 线时 akshare 显式返回空 DF，那里的「空」是
        # 设计好的合法答复，不得当作失败。故只按行校验，不套用零行判据。
        upstream_rows = len(df)
        usable: List[Tuple[str, float, float, float]] = []
        malformed: List[str] = []
        for index, (_, row) in enumerate(df.iterrows()):
            td = _compact_date(row["日期"])
            if td is None:
                malformed.append(f"{index}:date={row['日期']!r}")
                continue
            close = _finite_number(row["收盘"])
            if close is None:
                malformed.append(f"{index}:price={row['收盘']!r}")
                continue
            if start_date and td < start_date:
                continue
            if end_date and td > end_date:
                continue
            # 开盘/涨跌幅非本次判据范围，维持原有取法：把它们坏值折成 0.0 是新的静默
            # 写脏，而 #651 要消除的正是「看起来成功」的坏数据。缺值交给写入侧 #580。
            usable.append((td, close, float(row["开盘"]), float(row["涨跌幅"])))
        return usable, malformed, upstream_rows

    usable, malformed, upstream_rows = _retry(_fetch, "获取场内 ETF 日线失败")
    _finalize_nav_rows(
        usable, malformed,
        upstream_rows=upstream_rows, product_code=code, market="CN_EXCHANGE",
        operation="fetch_fund_daily_exchange", start_date=start_date,
        unwindowed_empty_is_error=start_date is None,
    )
    return [
        {"trade_date": td, "close": close, "pre_close": pre_close, "pct_change": pct_change}
        for td, close, pre_close, pct_change in usable
    ]


def _ensure_hk_code_map(ak) -> Dict[str, str]:
    """构建 6位内地销售代码→10位香港基金代码 映射（懒加载缓存，进程内不失效）。

    键是排名表 `基金代码`（968050 这类 6 位码）。库内 `product.code` 存的是 10 位
    `香港基金代码`，故港互认主路径不经此处（见 `_resolve_hk10`）。
    """
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


def _resolve_hk10(code: str) -> str:
    """港互认取数键：10 位香港基金代码直接用，6 位内地销售代码才查排名表映射。

    #651 前这段查表跑在取数闭包最前面：每次同步白跑一整页 `fund_hk_rank_em`，且它
    一旦抛错会把本来可用的 10 位码路径一起打死。挪出来短路，港互认就只打一次上游。
    """
    code6 = code.split(".")[0]
    if len(code6) == 10:
        return code6

    try:
        import akshare as ak
    except ImportError:
        raise AkshareAPIError("akshare 未安装，请 pip install akshare")

    hk10 = _ensure_hk_code_map(ak).get(code6)
    if not hk10:
        raise AkshareAPIError(f"未找到 6 位代码 {code6} 对应的香港基金代码")
    return hk10


def _dashed_bound(value: Optional[str], field: str) -> str:
    """YYYYMMDD → 接口要的 YYYY-MM-DD；空值返回空串（= 该侧不设界）。

    严格拒绝其他形态而不是原样透传：实测把紧凑格式当 date1 传上去，接口回的是
    `Code:"-1" Message:"103"` 错误信封。若在此静默放过，就会以另一种形态复刻
    「整段拿不到数据」。
    """
    if not value:
        return ""
    if not _COMPACT_DATE_RE.match(value):
        raise AkshareAPIError(f"{field} 需为 YYYYMMDD，实得 {value!r}")
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _hk_nav_request_page(hkfcode: str, page_index: int, date1: str, date2: str) -> Dict[str, Any]:
    """取一页港互认净值并校验信封。

    信封判据先于取数：`Code != "1"` 时响应**根本不带 `Data` 键**。写成
    `payload.get("Data") or []` 就会把上游错误读成合法空集，正是 #651 的故障形态。
    """
    params = {
        "api": "HKFDApi",
        "m": "MethodJZ",
        "hkfcode": hkfcode,
        "action": "2",
        "pageindex": str(page_index),
        "pagesize": str(_HK_PAGE_SIZE),
        "date1": date1,
        "date2": date2,
    }
    resp = requests.get(_HK_NAV_URL, params=params, headers=_HK_HEADERS, timeout=_HK_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()

    if str(payload.get("Code")) != "1":
        raise AkshareAPIError(
            "东财港互认接口返回错误信封 "
            f"Code={payload.get('Code')} Message={payload.get('Message')} "
            f"ErrorCode={payload.get('ErrorCode')}"
        )
    rows = payload.get("Data")
    if not isinstance(rows, list):
        raise AkshareAPIError(
            f"东财港互认接口响应缺少 Data 列表（上游结构变动）：keys={sorted(payload)}"
        )
    return payload


def _fetch_hk_nav_rows(
    hkfcode: str,
    date1: str,
    date2: str,
) -> List[Dict[str, Any]]:
    """按 TotalCount 分页取净区间内的原始行，不含任何业务解释（供 `_retry` 包裹）。

    akshare 恒取 `pageindex=0`，实测某港互认基金 `TotalCount=1768` 而一页 1000 行，
    即最早的历史被静默截掉。这里取到短页为止；触上限仍没取净时响亮留 WARNING，
    绝不静默少给。
    """
    collected: List[Dict[str, Any]] = []
    page_index = 0
    while True:
        payload = _hk_nav_request_page(hkfcode, page_index, date1, date2)
        page_rows = payload["Data"]
        collected.extend(page_rows)

        if len(page_rows) < _HK_PAGE_SIZE:
            break
        total = payload.get("TotalCount")
        if isinstance(total, int) and len(collected) >= total:
            break
        if page_index + 1 >= _HK_MAX_PAGES:
            logger.warning(
                "港互认净值分页触上限，历史可能不全（#651）",
                extra={
                    "operation": "fetch_hk_mutual_nav",
                    "product_code": hkfcode,
                    "market": "HK_MUTUAL",
                    "collected": len(collected),
                    "total_count": total,
                    "truncated": True,
                },
            )
            break
        page_index += 1
    return collected


def get_fund_hk_mutual(
    code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    香港互认基金净值（HK_MUTUAL）。
    code: 库内 product.code，即 10 位香港基金代码；6 位内地销售代码经排名表映射
    start_date/end_date: YYYYMMDD 格式
    返回: [{trade_date: 'YYYYMMDD', unit_price, accumulated_nav}]

    直连东财海外接口并按 `PDATE`/`NAV` **键名**取数，不再用 akshare 的
    `fund_hk_fund_hist_em`：它把上游 JSON 按固定位置重命名成中文列，2026-09 键序
    一变即把日期列错位成币种串（#651）。原键名在 akshare 里已被整体替换 columns
    标签销毁，事后无法从 DataFrame 找回，故只能自带请求。
    """
    hk10 = _resolve_hk10(code)
    # 入参校验也留在 _retry 之外：格式不对是调用方契约错误，重试三次只会白打上游。
    date1 = _dashed_bound(start_date, "start_date")
    date2 = _dashed_bound(end_date, "end_date")
    # 解析刻意留在 _retry 之外：键序变动是确定性的，重试只会三倍打上游，而 _retry 的
    # 「第 N/M 次尝试」文案是为瞬时故障写的，会把契约变动误报成网络抖动。
    rows = _retry(
        lambda: _fetch_hk_nav_rows(hk10, date1, date2),
        "获取香港互认基金净值失败",
    )
    pairs = [(row.get("PDATE"), row.get("NAV")) for row in rows]
    usable, malformed = _screen_nav_rows(
        pairs, start_date=start_date, end_date=end_date
    )
    _finalize_nav_rows(
        usable, malformed,
        upstream_rows=len(rows), product_code=hk10, market="HK_MUTUAL",
        operation="fetch_hk_mutual_nav", start_date=start_date,
        unwindowed_empty_is_error=start_date is None,
    )
    # accumulated_nav 恒为 None：上游 ACCNAV 实测为空串，且改这里会连带改变 #595
    # 净值曲线的取数形态，属另一决策（见 #651 follow-up）。
    return [
        {"trade_date": td, "unit_price": price, "accumulated_nav": None}
        for td, price in usable
    ]
