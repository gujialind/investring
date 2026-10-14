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

# 东财场外「单位净值走势」的日涨跌列（akshare 已 to_numeric，百分数形态）。#692：
# 缺列只降级成 WARNING，不参与「是否净值行」判据，理由见 _screen_nav_rows。
_OTC_PCT_COLUMN = "日增长率"
# 东财场外「累计净值走势」的净值列（#704）。同一接口函数换 indicator 即返回，此前
# 本地从未请求，导致 akshare 场外产品的 accumulated_nav 恒 NULL。
_OTC_ACCUM_COLUMN = "累计净值"
_OTC_ACCUM_INDICATOR = "累计净值走势"
# 东财港互认响应里的日涨跌率键（百分数形态，如 -0.4762 = -0.4762%）。
_HK_PCT_KEY = "NAVCHGRT"
# 东财港互认响应里的累计净值键（#703）。实测**逐基金分档**：968024 有值、968050 为
# 空串，故它是列级可选字段而非恒缺字段——此前按「恒为空串」硬编码 None，把有值的那
# 批基金也一并丢成了 NULL。
_HK_ACCUM_KEY = "ACCNAV"


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

    **本层不判正负**，且不能判：同一个 helper 既喂价格也喂 `extras`（场外「日增长率」
    / 港互认 `NAVCHGRT`，见 `_screen_nav_rows`），平盘的 `0.0` 必须原样留下。净值列
    「必须为正」的判据在写入侧 `market_data_service._usable_nav`（#701），它只覆盖
    `unit_price` 与 `accumulated_nav`，不覆盖日涨跌——所以三层各判各的：本层判「是不是
    一个有限数」，`_usable_price` 判「上游给没给值」，`_usable_nav` 在此之上再判「像不像
    净值」。放宽其中任一侧都要同时复核另两侧。
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
    extras: Optional[List[Any]] = None,
    extras2: Optional[List[Any]] = None,
) -> Tuple[List[Tuple[str, float, Optional[float], Optional[float]]], List[str]]:
    """把 (日期原值, 价格原值) 序列筛成 (YYYYMMDD, price, extra, extra2)，并回报无法解析的样本。

    日期先于区间判定：坏日期无法比较区间，把它记成 malformed 而不是默默丢掉，
    「上游给了行却零可解析」这条判据才有输入。落在区间外是正常剔除，不算 malformed。

    `extras`（#692）/ `extras2`（#703）是与 `pairs` 等长的附加列原值——`extras` 是日
    涨跌（场外「日增长率」/ 港互认 `NAVCHGRT`），`extras2` 是港互认累计净值 `ACCNAV`
    ——各按 `_finite_number` 归一成 float 或 None，缺省整列为 None。两者**刻意都不计入
    malformed**：「这一行是不是净值行」的判据仍只看日期 + 价格。日涨跌与累计净值都是
    展示列（累计净值另有写入侧 `_usable_nav` 的列级判据兜底），把它们的坏值算进去等于
    让 #651 的响亮判据由展示列驱动——一列漂移就会打死整段可用历史。
    """
    usable: List[Tuple[str, float, Optional[float], Optional[float]]] = []
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
        extra = _finite_number(extras[index]) if extras is not None else None
        extra2 = _finite_number(extras2[index]) if extras2 is not None else None
        usable.append((td, price, extra, extra2))
    return usable, malformed


def _finalize_nav_rows(
    usable: List[Tuple[str, float, Optional[float], Optional[float]]],
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


def _fetch_otc_accum_map(code: str) -> Tuple[Dict[str, float], bool]:
    """东财场外「累计净值走势」→ `({YYYYMMDD: 累计净值}, degraded)`（#704）。

    同一接口函数换 `indicator` 即返回累计净值（实测 000001 有 6020 行），此前本地只请
    求「单位净值走势」并把 `accum_nav` 写死 None，等于把可取得的数据永久丢在读侧外面。

    合并靠日期映射天然对齐，不做位置对齐：两条序列各自独立取回，缺日/坏值就是查不到
    键，落 NULL 而**不拿单位净值顶替**——列级判据由写入侧 `_bulk_upsert_prices` 承接。

    整段失效降级为空映射 + 一条 WARNING，但**必须与合法空序列分开**（`degraded`）：
    累计净值是展示列，一次上游抖动不该把已经取到手的单位净值历史一起打死（与
    `get_fund_nav_otc` 里日涨跌列的既有立场同族）；可若只返回 `{}`，写入侧就分不清
    「本次没取到」与「该产品本来没有累计净值」，重叠区间/全历史同步会把库里已经正确的
    `accumulated_nav` 覆盖成 NULL——增量窗只回看本地最大起最近十个交易日（#698），
    窗口滑过仍未修复的降级（长时故障）不再被回访，先覆盖就抹掉了最后的好值。
    `degraded=True` 的三种形态：请求重试耗尽、缺「累计净值」列、有列但零条
    日期可解析；写入侧据此在冲突行保留既有值。上游 0 行（货币基金等品种本就没有累计
    净值序列）是合法空序列，`degraded=False` 且不告警。
    """
    try:
        import akshare as ak
    except ImportError:
        raise AkshareAPIError("akshare 未安装，请 pip install akshare")

    def _fetch():
        df = ak.fund_open_fund_info_em(symbol=code, indicator=_OTC_ACCUM_INDICATOR)
        pairs = [
            (row.get("净值日期"), row.get(_OTC_ACCUM_COLUMN))
            for _, row in df.iterrows()
        ]
        return pairs, _OTC_ACCUM_COLUMN in df.columns

    def _degraded(message: str, *args, **fields) -> Tuple[Dict[str, float], bool]:
        logger.warning(
            message, *args,
            extra={
                "operation": "fetch_fund_nav_otc",
                "product_code": code,
                "market": "CN_OTC",
                "accum_degraded": True,
                **fields,
            },
        )
        return {}, True

    try:
        pairs, has_accum_column = _retry(_fetch, "获取场外基金累计净值失败")
    except AkshareAPIError as e:
        return _degraded(
            "东财场外累计净值请求失败，本批 accumulated_nav 全为空（#704）：%s", e,
        )

    if pairs and not has_accum_column:
        return _degraded(
            "东财场外累计净值响应缺少「%s」列，本批累计净值全为空（#704）",
            _OTC_ACCUM_COLUMN,
            upstream_rows=len(pairs),
            missing_column=_OTC_ACCUM_COLUMN,
            column_drift=True,
        )

    accum_map: Dict[str, float] = {}
    unparsed_dates = 0
    for raw_date, raw_value in pairs:
        td = _compact_date(raw_date)
        if td is None:
            unparsed_dates += 1
            continue
        value = _finite_number(raw_value)
        if value is None:
            # 坏值只让这一天没有累计净值，不计 malformed、不打死整段：
            # 「这一行是不是净值行」的判据仍只由单位净值序列驱动（#651）。
            continue
        accum_map[td] = value

    if unparsed_dates == len(pairs) and pairs:
        # 有「累计净值」列却零条日期可解析 = 日期列整段漂移（akshare 会把不可解析日期
        # 转成 NaT，列名不变）。这与逐日坏值不同：整段失效，按降级处置。
        return _degraded(
            "东财场外累计净值日期整段不可解析，本批 accumulated_nav 全为空（#704）",
            upstream_rows=len(pairs),
            parsed=0,
            accum_date_drift=True,
        )
    return accum_map, False


def get_fund_nav_otc(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    场外基金净值（CN_OTC）。
    symbol: 基金代码（如 '000051' 或 '000051.OF'，取 . 前部分）
    start_date/end_date: YYYYMMDD 格式
    返回: [{trade_date: 'YYYYMMDD', unit_nav, accum_nav, pct_change}]

    `pct_change` 是上游「日增长率」原值（百分数，1.2195 即 1.2195%，#692）：语义为
    单位净值日涨跌、除息日含跳水，与天天基金展示一致。上游缺该列或该行坏值时为 None。
    `accum_nav` 取自同接口的「累计净值走势」指标（#704），按净值日期合并；该序列缺日或
    坏值时为 None，不以单位净值顶替。整段失效（请求重试耗尽 / 缺列 / 日期整段不可解析）
    时每行另带 `accum_degraded: True`——写入侧据此在冲突行保留既有 `accumulated_nav`，
    不让一次上游抖动抹掉已成功落库的历史（见 `_fetch_otc_accum_map`）。
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
        # 用 .get 而不是 row["日增长率"]：缺列时的 KeyError 会被 `_retry` 当成瞬时故障
        # 重试三次，再抛 AkshareAPIError——一个展示列的漂移就把整段可用历史打死。
        extras = [row.get(_OTC_PCT_COLUMN) for _, row in df.iterrows()]
        usable, malformed = _screen_nav_rows(
            pairs, start_date=start_date, end_date=end_date, extras=extras,
        )
        return usable, malformed, len(pairs), _OTC_PCT_COLUMN in df.columns

    usable, malformed, upstream_rows, has_pct_column = _retry(_fetch, "获取场外基金净值失败")
    if upstream_rows and not has_pct_column:
        # 早于 _finalize_nav_rows：整段解析失败时，列漂移正是最可能的成因，不能因为
        # 随后抛错就把这条线索吞掉。
        logger.warning(
            "东财场外净值响应缺少「%s」列，本批日涨跌全为空（#692）",
            _OTC_PCT_COLUMN,
            extra={
                "operation": "fetch_fund_nav_otc",
                "product_code": code,
                "market": "CN_OTC",
                "upstream_rows": upstream_rows,
                "missing_column": _OTC_PCT_COLUMN,
                "column_drift": True,
            },
        )
    _finalize_nav_rows(
        usable, malformed,
        upstream_rows=upstream_rows, product_code=code, market="CN_OTC",
        operation="fetch_fund_nav_otc", start_date=start_date,
        unwindowed_empty_is_error=start_date is None,
    )
    if not usable:
        # 合法空窗（带区间的周末/停市/净值未发布，或上游行全落在窗口外）：没有行要消费
        # 累计净值，第二次请求只是白下载整份 pingzhongdata 并等一次限流，而
        # `run_nav_sync` 按产品串行，空窗日的无效成本会被产品数放大。
        return []
    # 累计净值另打一次上游（#704），放在 _finalize_nav_rows 与空窗短路之后：单位净值整段
    # 解析不出来、或本批根本没有行时，本次同步都不该再白打一次限流请求。
    accum_map, accum_degraded = _fetch_otc_accum_map(code)
    return [
        {
            "trade_date": td,
            "unit_nav": price,
            "accum_nav": accum_map.get(td),
            "pct_change": pct,
            # 只在降级批次上带这个键：它是批级状态而非逐日属性，非降级行多一个恒 False
            # 的键只会让「哪些行受影响」读起来更像逐日事实。
            **({"accum_degraded": True} if accum_degraded else {}),
        }
        # 场外只传 `extras`（日涨跌），四元组的第四位恒为 None：累计净值走
        # `_fetch_otc_accum_map` 的按日映射，不做位置对齐（#704）。
        for td, price, pct, _accum in usable
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
        # kline 是逗号分隔的位置串，akshare 按它自己请求的 fields2 顺序贴列名——顺序
        # 由调用侧锁定，不受东财响应键序带动，故不存在港互认那种键序漂移。这条路径
        # 剩下的判据只是「空怎么读」：带区间的空良性（停市、假日、新基金尚未成交），
        # 不带下界的全历史空判故障——实测有效代码恒有行（510300 全历史 3487 条），
        # 只有无效代码才返回空 DF（999999 为 0 条）。那种空读成成功就是 #651 换了个市场。
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

    取数走 `_retry`（本模块唯一的「上游任何异常 → 归一 AkshareAPIError」单点，#705
    评审）：直调 `fund_hk_rank_em` 会让超时/连接失败/上游结构变化以非领域异常冒出
    service 层，落到全局 handler 变 500，而建档侧承诺的是 422 `HK_CODE_UNMAPPED`。
    """
    global _hk_code_map
    if _hk_code_map is None:
        def _fetch() -> Dict[str, str]:
            df = ak.fund_hk_rank_em()
            mapping: Dict[str, str] = {}
            for _, row in df.iterrows():
                code6 = str(row["基金代码"]).strip()
                code10 = str(row["香港基金代码"]).strip()
                if code6 and code10:
                    mapping[code6] = code10
            return mapping

        _hk_code_map = _retry(_fetch, "获取东财港基排名表失败")
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


def resolve_hk_code_pair(code: str) -> Tuple[str, Optional[str]]:
    """港互认录入归一（#705）：返回 ``(入库 code, hk_sale_code)``。

    6 位内地销售码经排名表解析为 10 位香港基金码（查不到抛 AkshareAPIError，
    由调用方转业务错误——入库码不能靠猜）；10 位码原样入库并**尽力**反查 6 位
    （反查失败不抛：展示码缺失由读侧 `display_code` 回退原码，不值得阻断录入）；
    其他形态原样返回 ``(code, None)``。
    """
    stripped = code.strip()
    if stripped.isdigit() and len(stripped) == 6:
        return _resolve_hk10(stripped), stripped
    if stripped.isdigit() and len(stripped) == 10:
        try:
            import akshare as ak

            reverse = {v: k for k, v in _ensure_hk_code_map(ak).items()}
            return stripped, reverse.get(stripped)
        except Exception:  # 反查是展示码锦上添花：网络/依赖/清单缺失都不阻断录入
            return stripped, None
    return stripped, None


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
        if page_index:
            # `_retry` 只在每次「尝试」前限流一次，分页后一次尝试要打多页；不在页间
            # 补睡，`akshare_rate_interval` 就被稀释成 1/页数。全历史实测 2 页，代价 +1s。
            _rate_limit_sleep()
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
    返回: [{trade_date: 'YYYYMMDD', unit_price, accumulated_nav, pct_change}]

    直连东财海外接口并按 `PDATE`/`NAV` **键名**取数，不再用 akshare 的
    `fund_hk_fund_hist_em`：它把上游 JSON 按固定位置重命名成中文列，2026-09 键序
    一变即把日期列错位成币种串（#651）。原键名在 akshare 里已被整体替换 columns
    标签销毁，事后无法从 DataFrame 找回，故只能自带请求。

    `pct_change` 取上游 `NAVCHGRT`（#692），已是百分数（-0.4762 即 -0.4762%），
    原值透传、不做推导：语义为单位净值日涨跌、除息日含跳水，与上游展示一致。同族的
    `NAVCHG`（涨跌额）不消费——API 不暴露 `pre_close`，无消费方。
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
    extras = [row.get(_HK_PCT_KEY) for row in rows]
    extras2 = [row.get(_HK_ACCUM_KEY) for row in rows]
    usable, malformed = _screen_nav_rows(
        pairs, start_date=start_date, end_date=end_date, extras=extras, extras2=extras2,
    )
    _finalize_nav_rows(
        usable, malformed,
        upstream_rows=len(rows), product_code=hk10, market="HK_MUTUAL",
        operation="fetch_hk_mutual_nav", start_date=start_date,
        unwindowed_empty_is_error=start_date is None,
    )
    # accumulated_nav 按行取上游 ACCNAV（#703）：该键**逐基金分档**——实测 968024 有值、
    # 968050 为空串——故缺值落 NULL 由读侧按 basis 回退单位净值，不在此用单位净值顶替。
    return [
        {"trade_date": td, "unit_price": price, "accumulated_nav": accum, "pct_change": pct}
        for td, price, pct, accum in usable
    ]
