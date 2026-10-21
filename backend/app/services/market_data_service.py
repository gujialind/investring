from datetime import date, datetime, timedelta
from decimal import Decimal
import logging
import math
from typing import List, Dict, Any, Optional, Tuple
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.models.price_record import PriceRecord
from app.models.product import Product
from app.models.trading_calendar import TradingCalendar
from app.services.exceptions import BusinessError, NotFoundError
from app.services.tushare_client import get_fund_daily, get_fund_nav, TushareAPIError
from app.utils.business_time import business_today
from app.utils.quantize import quantize_nav

logger = logging.getLogger(__name__)


def _usable_price(value: Any) -> bool:
    """上游原值是否是「一个值」（#580 可用性判据）。

    数据源缺值时给的是 None/空串，pandas 转换还说不准给 NaN——三者都不能作为
    「这一天的价格」落库。它们一旦进了 price_record，`unit_price` 就从此不再可靠：
    读取侧 `float(None)` 直接 500，快照取价反过来把 NULL 当成说好吧有价。

    **只回答「上游有没有给值」，不回答「给的值像不像净值」**（#701）：净值列还要过
    `_usable_nav` 的正值判据；`pct_change` 则必须停在本题——日涨跌是比率，平盘那天的
    `0.0` 是真实值（#692 专门保住它），判正会把平盘一起判坏。

    取数层 `akshare_client._finite_number` 判的是同一族坏值，刻意各留一处（把 DB 层
    service 引回叶子数据源模块会反转分层）；放宽一侧的口径必须同时复核另一侧。
    """
    if value is None or value == "":
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _usable_nav(value: Any) -> bool:
    """净值型单价是否够格入库（#701：在 #580 的可用性之上再加「必须为正」）。

    适用 `price_record.unit_price`（行级判据：不合格整行不入库）与
    `accumulated_nav`（列级判据：只丢这一列，不连累已合格的价格）。

    `0.0` 与负数是有限值，因而不是 `_usable_price` 的拒绝对象；但它们在业务上不是
    任何一天的净值——真实上游给 0 只可能是脏数据、上游故障或新市场接入。它一旦落库，
    这一行是「有价的一天」而不是缺行：快照把市值算成 0（或负），`nav_coverage` 把这天
    计为已覆盖，缺口从此无法在这里定位——与迁移 0017 描述 NULL 行同一条错法
    （「未知的伪装成已知的」）。负价还多一层：读取侧的 truthiness 判空挡不住它，
    调仓买入会算出负份额落库。

    判正比的是**会落库的那个值**：列标度 `Numeric(10,4)`，`0.00001` 之类过 `float > 0`
    即放行、落库塌成 `0.0000`，正是要消灭的形态，故先经 `quantize_nav` 收口再比——
    「落库列的标度不是收口点」（口径见 `app/utils/quantize.py`），判据也不能比它宽。

    与 `_usable_price` 分表而不是加参数：两类输入的判据集合本就不同，共用一个函数
    参数会把「平盘 0.0 必须留」这条反证变成调用点的口头约定。
    """
    if not _usable_price(value):
        return False
    return quantize_nav(value) > 0


def get_price_records(
    db: Session,
    product_code: str,
    market: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: Optional[int] = None,
) -> List[PriceRecord]:
    """
    查询价格记录

    Args:
        db: 数据库会话
        product_code: 产品代码
        market: 市场类型
        start_date: 开始日期（可选）
        end_date: 结束日期（可选）
        limit: 限制返回数量（可选）

    Returns:
        价格记录列表
    """
    query = db.query(PriceRecord).filter(
        and_(
            PriceRecord.product_code == product_code,
            PriceRecord.market == market,
        )
    )

    if start_date:
        query = query.filter(PriceRecord.price_date >= start_date)
    if end_date:
        query = query.filter(PriceRecord.price_date <= end_date)

    query = query.order_by(PriceRecord.price_date.desc())

    if limit:
        query = query.limit(limit)

    return query.all()


# --- #595 产品详情页净值数据（§5.3）：读侧统计，口径见各函数 docstring ---

# 曲线区间 → 日历月数（曲线画什么，前端区间 Tab 的直观语义）。
_NAV_CURVE_RANGE_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "1y": 12}
# 曲线区间码 → 响应字段名（ProductIntervalReturns 的 m1/m3/m6/y1）
_INTERVAL_FIELD_BY_RANGE = {"1m": "m1", "3m": "m3", "6m": "m6", "1y": "y1"}
# 净值口径 → 取数列（#703）。键即响应字段 `nav_basis` 的取值，前端据此改标题与注记：
# `unit` 在净值源上是单位净值（不含分红再投资）、在场内行情源上是收盘价（市价）。
_NAV_COLUMN_BY_BASIS = {"accumulated": "accumulated_nav", "unit": "unit_price"}


# 六窗区间收益率的窗口锚点：1m/3m 用 30/90 自然日、6m/1y 用日历月/年，与
# performance_service 组合级逐一对齐——产品详情页与组合绩效卡的四个固定窗口
# 因此语义一致，同名数字可互相印证（#637 L2 S2）。#703 起产品级可能回退到单位净值
# 口径（响应 `nav_basis="unit"`），那时这条互证不成立、组合级数字仍是原口径。
# 注意曲线区间（_NAV_CURVE_RANGE_MONTHS）仍按日历月过滤，与同名窗口锚点不必相同
# （评审 Nit1）；ytd 产品级 clip_to_first、组合级不 clip，分叉理由见 get_nav_analysis
# （评审 S3 只落在产品级）——「与组合级一致」仅指 m1/m3/m6/y1 四窗。
def _window_start(latest: date, field: str) -> date:
    if field == "m1":
        return latest - timedelta(days=30)
    if field == "m3":
        return latest - timedelta(days=90)
    if field == "m6":
        return latest - relativedelta(months=6)
    if field == "y1":
        return latest - relativedelta(years=1)
    raise ValueError(f"未知窗口: {field}")


def get_nav_history_page(
    db: Session,
    product_code: str,
    market: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    page: int = 1,
    page_size: int = 5,
) -> Tuple[List[PriceRecord], int]:
    """历史净值分页（日期降序）：单位净值必有，累计净值/日涨跌可空照传。"""
    query = db.query(PriceRecord).filter(
        and_(
            PriceRecord.product_code == product_code,
            PriceRecord.market == market,
        )
    )
    if start_date is not None:
        query = query.filter(PriceRecord.price_date >= start_date)
    if end_date is not None:
        query = query.filter(PriceRecord.price_date <= end_date)

    total = query.count()
    items = (
        query.order_by(PriceRecord.price_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def _nav_interval_return(
    records: List[PriceRecord],
    start: date,
    *,
    column: str,
    clip_to_first: bool = False,
) -> Optional[float]:
    """区间收益率（百分数，4dp）：以 start 当日或之后首条记录为基准，至最新记录。

    `column` 是取值的列名（`accumulated_nav` 或回退口径的 `unit_price`，#703），由调用
    方与曲线共用同一份 `records` 传入，保证「六窗与曲线同 basis」是结构事实而非约定。

    基序算法与 performance_service._period_return 同构（#595 spec §5.3 要求统一
    量化口径）：基准与最新同日、基准净值非正时返回 None，前端显示占位而非
    虚高数字。历史未覆盖 start 时默认返回 None（固定长度窗口不足窗口期会虚高）；
    ``clip_to_first=True`` 供变长窗口（ytd）使用——本年内的收益从本年首个有价日
    起算天经地义，「窗口起点早于首条记录」不是不足而是全部历史（#637 L2 评审
    S3：本年新成立产品的 ytd 不得显示占位）。
    """
    if len(records) < 2:
        return None
    if records[0].price_date > start:
        if not clip_to_first:
            return None
        base = records[0]
    else:
        base = next((r for r in records if r.price_date >= start), None)
    latest = records[-1]
    if base is None or base.price_date >= latest.price_date:
        return None
    base_nav = float(getattr(base, column))
    if base_nav <= 0:
        return None
    return round((float(getattr(latest, column)) / base_nav - 1) * 100, 4)


def _nav_records(db: Session, product_code: str, market: str, column: str) -> List[PriceRecord]:
    """该产品该市场下 `column` 非空的价格记录，日期升序（#703 两 basis 共用取数）。"""
    return (
        db.query(PriceRecord)
        .filter(
            and_(
                PriceRecord.product_code == product_code,
                PriceRecord.market == market,
                getattr(PriceRecord, column).isnot(None),
            )
        )
        .order_by(PriceRecord.price_date.asc())
        .all()
    )


def get_nav_analysis(
    db: Session,
    product_code: str,
    market: str,
    range_code: str,
) -> dict:
    """产品净值分析：所选区间净值曲线 + 六窗区间收益率 + 本批口径 `nav_basis`。

    **两段式取数（#703）**：先取累计净值非空的记录（`nav_basis="accumulated"`，原口
    径）；累计净值覆盖不足单位净值半数时回退到单位净值记录（`nav_basis="unit"`）。此前
    只认累计净值，而港互认缺值基金与场内行情源该列**恒 NULL**，1773 行完好的
    `unit_price` 被整段过滤掉，两张卡永久空白且响应仍是 200（前端失败态不触发）。回退
    判据用「覆盖度过半」而非「整列为空」：写侧改逐行读 ACCNAV 后，存量未回补的增量同步
    与 ACCNAV 稀疏的基金会呈现「零星几行有累计净值」，若仍钉在 accumulated，曲线只剩那
    几行、「成立以来」错按部署日（首个有累计净值的交易日）为基准、五个窗口因历史不足全
    None。用覆盖度而非严格计数相等，是为 #701 非正丢列留容差——OTC 产品偶发个别
    `accumulated_nav` 非正被丢，覆盖度仍近满，个别坏值不应把整支产品切到单位净值口径。
    皆空才返回空曲线与全 None 六窗，此时 `nav_basis` 报 `accumulated`（空态与口径无关，
    不给前端第三种取值）。六窗与曲线取自同一份 `records` 与同一个 `column`，口径一致由
    结构保证。

    回退口径的语义差异（不含分红再投资、场内为市价）由前端按 `nav_basis` 明示，本层
    不做换算：把单位净值「补成」累计净值需要逐日分红数据，本地没有。
    """
    column = _NAV_COLUMN_BY_BASIS["accumulated"]
    records = _nav_records(db, product_code, market, column)
    unit_records = _nav_records(db, product_code, market, _NAV_COLUMN_BY_BASIS["unit"])
    nav_basis = "accumulated"
    # 累计净值非空行数不足单位净值半数 → 序列过于破碎，回退单位净值（#703 L2 Blocker 2）。
    # 进入此分支必有 unit_records 非空（否则 2*len(records) < 0 恒假），故 records 不会变空。
    if len(records) * 2 < len(unit_records):
        column = _NAV_COLUMN_BY_BASIS["unit"]
        records = unit_records
        nav_basis = "unit"

    curve: List[dict] = []
    interval_returns: Dict[str, Optional[float]] = {
        "m1": None, "m3": None, "m6": None,
        "y1": None, "ytd": None, "all": None,
    }
    if not records:
        return {"curve": curve, "interval_returns": interval_returns, "nav_basis": nav_basis}

    latest_date = records[-1].price_date

    months = _NAV_CURVE_RANGE_MONTHS[range_code]
    curve_start = latest_date - relativedelta(months=months)
    curve = [
        {"date": r.price_date, "accumulated_nav": float(getattr(r, column))}
        for r in records
        if r.price_date >= curve_start
    ]

    for field in _INTERVAL_FIELD_BY_RANGE.values():
        interval_returns[field] = _nav_interval_return(
            records, _window_start(latest_date, field), column=column
        )
    # ytd 是变长窗口：历史全部落在本年时从首条记录起算（clip_to_first），
    # 本年新成立产品的「今年以来」== 「成立以来」，不得显示占位（#637 L2 评审 S3）
    interval_returns["ytd"] = _nav_interval_return(
        records, date(latest_date.year, 1, 1), column=column, clip_to_first=True
    )
    # 成立以来 = 首条记录为基准
    interval_returns["all"] = _nav_interval_return(
        records, records[0].price_date, column=column
    )

    return {"curve": curve, "interval_returns": interval_returns, "nav_basis": nav_basis}


def get_latest_price(
    db: Session,
    product_code: str,
    market: str,
    target_date: Optional[date] = None,
) -> Optional[PriceRecord]:
    """
    获取指定日期或之前的最新价格

    Args:
        db: 数据库会话
        product_code: 产品代码
        market: 市场类型
        target_date: 目标日期，如果为None则获取最新一条

    Returns:
        价格记录或None
    """
    query = db.query(PriceRecord).filter(
        and_(
            PriceRecord.product_code == product_code,
            PriceRecord.market == market,
        )
    )

    if target_date:
        query = query.filter(PriceRecord.price_date <= target_date)

    return query.order_by(PriceRecord.price_date.desc()).first()


def get_nav_coverage(
    db: Session,
    product_code: str,
    market: str,
    start_date: date,
    end_date: date,
) -> Dict[str, Any]:
    """
    校验区间内净值同步覆盖情况

    以交易日历（is_open=true）为基准，与 price_record 已有日期做集合差，
    单次聚合查询，不逐日循环。coverage 在区间无交易日时为 None。
    """
    product = db.query(Product).filter(
        and_(Product.code == product_code, Product.market == market)
    ).first()
    if not product:
        raise NotFoundError("NOT_FOUND", f"产品 {product_code} ({market}) 不存在")
    if start_date > end_date:
        raise BusinessError(
            "INVALID_DATE_RANGE",
            f"start_date ({start_date}) 不能晚于 end_date ({end_date})",
            http_status=422,
        )

    trading_days = {
        row[0]
        for row in db.query(TradingCalendar.calendar_date).filter(
            TradingCalendar.calendar_date >= start_date,
            TradingCalendar.calendar_date <= end_date,
            TradingCalendar.is_open.is_(True),
        ).all()
    }
    synced_dates = {
        row[0]
        for row in db.query(PriceRecord.price_date).filter(
            PriceRecord.product_code == product_code,
            PriceRecord.market == market,
            PriceRecord.price_date >= start_date,
            PriceRecord.price_date <= end_date,
        ).all()
    }

    missing = sorted(trading_days - synced_dates)
    total = len(trading_days)
    synced = total - len(missing)
    return {
        "product_code": product_code,
        "market": market,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_trading_days": total,
        "synced_days": synced,
        "coverage": round(synced / total, 4) if total > 0 else None,
        "missing_dates": [d.isoformat() for d in missing],
    }


# 增量同步窗口的回看深度（#698）：下界 = min(MAX+1 自然日, MAX 往前第 N 个交易日)。
# 单位取交易日而非自然日：现场乱序形态是 1 步（2026-09-30 被 10-08 越过），N=10 留 10 倍
# 余量；而交易日步长与假期长度解耦——国庆 8 个自然日、春节 11 个自然日在交易日轴上都只是
# 1 步，按自然日定 K 会把「闭市多长」与「上游迟披露几步」混进同一个参数（#698 根因第 6 条：
# 成洞取决于披露顺序，不取决于假期长度）。代价只是载荷：每产品每次多回捞约 N 行，三条取数
# 路径的请求条数恒为 1/产品/运行（tushare 单次、akshare CN_OTC 本就拉全量、HK_MUTUAL 单页
# 1000 行远不到第 2 页），tushare 限频按 per-call，等待预算不变；写入侧 `_bulk_upsert_prices`
# 是 ON DUPLICATE KEY UPDATE，重放同区间幂等——这是本方案的前提，也是回看段能顺带传播
# 上游对近日净值修订的原因。
# 刻意不做配置项/迁移：运行期改它等于改同步语义，调参属代码审查面。
NAV_LOOKBACK_TRADING_DAYS = 10


def resolve_incremental_start_date(
    db: Session,
    product_code: str,
    market: str,
) -> Optional[date]:
    """增量同步窗口的下界（#698）：min(MAX(price_date)+1 自然日, MAX 往前第 N 个交易日)。

    为什么不能只是 MAX+1：上游乱序披露（某交易日净值晚于其后交易日披露）会让 MAX 被右侧
    日跳推过去，左侧中间空洞从此不在任何一次增量窗内、永不回补；长假后首个交易日的快照
    随即被 MISSING_NAV 阻断。回看 N 个交易日起窗，使「迟披露 ≤N 步」的空洞在下一次运行
    自动回填（写入幂等，重放无害）。

    MAX 查询刻意内聚在本函数：#698 的成因正是 `task_runner.run_nav_sync` 与
    `_run_price_sync_job_impl` 两处各算各的；调用点收敛到这里之后，「两处同口径」由结构
    保证，测试各自断言 `== 本函数返回值`（任一处绕过单点即红，强于两调用点互比）。

    日历不足时的三档降级**都不抛 CALENDAR_NOT_SYNCED**（与取价侧 `_prev_trading_day`
    刻意相反，#591 的分工）：本函数返回值只是「问上游要哪段区间」，不写进任何财务数据；
    抛错会把「日历短」升级成「该产品净值从此不再更新」，而真正的缺净值仍由快照侧响亮报
    MISSING_NAV。降级也不伪造日期——返回的恒是日历里真实存在的开市日，或旧口径 MAX+1。
    不按 exchange 过滤（全仓一致口径；回看只是放宽抓取区间，HK 产品借 SSE 日历无害）。
    """
    latest = db.query(func.max(PriceRecord.price_date)).filter(
        PriceRecord.product_code == product_code,
        PriceRecord.market == market,
    ).scalar()
    if latest is None:
        return None                          # 新产品：不设下界，保持全量历史语义
    floor = latest + timedelta(days=1)       # 旧口径，日历救不到时的兜底
    open_days = db.query(TradingCalendar.calendar_date).filter(
        TradingCalendar.calendar_date < latest,
        TradingCalendar.is_open.is_(True),
    ).order_by(TradingCalendar.calendar_date.desc()).limit(
        NAV_LOOKBACK_TRADING_DAYS
    ).all()
    if not open_days:
        return floor                         # MAX 前无任何开市日：退回旧口径，不抛错
    return min(floor, open_days[-1][0])      # 回看日恒早于 floor；min 把「不窄于旧口径」写成代码事实


# 累计净值整段降级的统一留痕文案（#704）。同步终态（NavSyncDetail.error_message）与
# 单产品同步返回的 message 共用同一句，避免两处措辞漂移。
ACCUM_DEGRADED_NOTE = "累计净值降级：本批未取到累计净值，已有值保留"


def sync_product_prices(
    db: Session,
    product_code: str,
    market: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    单产品价格同步（改造版）。
    数据源路由 + 批量 upsert + 禁止硬编码 data_source + sync_error/source 回写。
    返回 {success, synced_count, message, source}；真正写入价格的那条路径另带
    `accum_degraded`（#704），跳过/失败/无新数据的早退路径没有该键，调用方一律 `.get()`。

    `accum_degraded` 为 True 表示 akshare 场外累计净值整段失效：同步本身成功（单位净值
    已落库、冲突行的既有 accumulated_nav 已保留），但本批没取到累计净值，任务终态据此
    显式标注降级，不与「上游合法地没有累计净值序列」共用一种结果。
    """
    product = db.query(Product).filter(
        and_(Product.code == product_code, Product.market == market)
    ).first()
    if not product:
        raise ValueError(f"产品 {product_code} ({market}) 不存在")

    if not end_date:
        # 取数上界的缺省值属业务日（#746）：调用方显式传值时不受影响
        end_date = business_today()
    start_str = start_date.strftime("%Y%m%d") if start_date else None
    end_str = end_date.strftime("%Y%m%d")

    data_source = product.data_source or "tushare"
    raw_data: List[dict] = []
    try:
        if data_source == "tushare":
            if market == "CN_EXCHANGE":
                raw_data = get_fund_daily(product_code, start_str, end_str)
            elif market == "CN_OTC":
                raw_data = get_fund_nav(product_code, start_str, end_str)
            elif market == "HK_MUTUAL":
                _mark_skipped(db, product, "tushare 不支持 HK_MUTUAL")
                return {"success": True, "synced_count": 0, "message": "跳过：tushare 不支持 HK_MUTUAL", "source": data_source}
            else:
                raise ValueError(f"不支持的 market: {market}")
        elif data_source == "akshare":
            from app.services.akshare_client import (
                get_fund_nav_otc, get_fund_daily_exchange, get_fund_hk_mutual,
                AkshareAPIError,
            )
            if market == "CN_OTC":
                raw_data = get_fund_nav_otc(product_code, start_str, end_str)
            elif market == "CN_EXCHANGE":
                raw_data = get_fund_daily_exchange(product_code, start_str, end_str)
            elif market == "HK_MUTUAL":
                raw_data = get_fund_hk_mutual(product_code, start_str, end_str)
            else:
                _mark_skipped(db, product, f"akshare 不支持 market={market}")
                return {"success": True, "synced_count": 0, "message": "跳过", "source": data_source}
        else:
            _mark_skipped(db, product, f"未实现的数据源: {data_source}")
            return {"success": True, "synced_count": 0, "message": f"跳过：未实现 {data_source}", "source": data_source}
    except (TushareAPIError, Exception) as e:
        _mark_failed(db, product, f"{type(e).__name__}: {e}")
        return {"success": False, "message": str(e), "synced_count": 0, "source": data_source}

    if not raw_data:
        # 空 raw_data 有两种含义，此处只该放行其中一种（#651）：
        #   1. 上游这段区间确实没有数据——无效代码，或该段历史被整段下架清除。实测
        #      东财对无效代码与周末窗口返回逐字节同形，无法在传输层区分，故这里保持
        #      success。若照 #651 原文改判 failed，每次运行都整段空的产品就会天天被
        #      记一次失败，噪音反过来淹没真故障。#698 起增量窗含回看段，已有本地历史
        #      的健康产品通常能从存量区间带回行，周末/停市窗基本不再走到这里。
        #   2. 上游给了行而我们解析不出任何一条（接口结构变动）。这种形态已在
        #      akshare_client 的响亮判据处抛 AkshareAPIError，走上面 except 分支落
        #      failed + sync_error，不会到达这里。
        # 即：判据是「上游有行而零可解析」（取数层看得见），不是「结果为空」（这里看不见）。
        product.data_source_status = "success"
        product.last_sync_at = datetime.utcnow()
        product.sync_error = None
        db.flush()
        return {"success": True, "message": "无新数据", "synced_count": 0, "source": data_source}

    normalized = _normalize_raw(raw_data, market)
    if market == "CN_OTC" and data_source == "tushare":
        # 上游 fund_nav 无涨跌幅字段，只能就地推导；akshare 场外与港互认走上游透传，
        # 口径与限制见 _derive_otc_pct_change（#692）。
        _derive_otc_pct_change(db, product_code, market, normalized)
    synced_count = _bulk_upsert_prices(db, product_code, market, normalized, data_source)
    accum_degraded = any(r.get("accum_degraded") for r in normalized)

    product.data_source_status = "success"
    product.last_sync_at = datetime.utcnow()
    # 降级不写 sync_error：`data_source_status` 仍是 success，只写错误文案会让产品列表
    # 读成「这条源坏了」。降级留痕走任务终态（NavSyncDetail / TaskExecutionLog）与
    # 本函数返回的 message。
    product.sync_error = None
    db.flush()

    message = f"同步 {synced_count} 条"
    if accum_degraded:
        message = f"{message}（{ACCUM_DEGRADED_NOTE}）"
    return {
        "success": True,
        "message": message,
        "synced_count": synced_count,
        "source": data_source,
        "accum_degraded": accum_degraded,
    }


def sync_price_data(
    db: Session,
    product_code: str,
    market: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """向后兼容包装（CLI ir market sync / 现有 router 调用）"""
    return sync_product_prices(db, product_code, market, start_date, end_date)


def _mark_failed(db: Session, product: Product, msg: str):
    # 不 commit（backend/AGENTS.md「分层目录与职责」节）：sync_product_prices 捕获异常后返回 success=False
    # 而非上抛，失败标记经调用方正常路径 commit 持久化
    product.data_source_status = "failed"
    product.sync_error = msg[:1000] if msg else msg
    product.last_sync_at = datetime.utcnow()
    db.flush()


def _mark_skipped(db: Session, product: Product, reason: str):
    product.data_source_status = "skipped"
    product.sync_error = reason
    product.last_sync_at = datetime.utcnow()
    db.flush()


def _normalize_raw(raw_data: List[dict], market: str) -> List[dict]:
    """统一适配 tushare/akshare 返回 → upsert 行（按 trade_date 去重，后到者覆盖）。

    两个分支的键集不同，这是上游能力差异而非遗漏（#692 更正此前声称「五键统一」的失真
    docstring）：
    - CN_EXCHANGE：`{trade_date, unit_price, pre_close, pct_change}`——场内是行情源，
      给收盘价与前收、不给累计净值；
    - 其余（CN_OTC / HK_MUTUAL）：`{trade_date, unit_price, accumulated_nav, pct_change}`
      ——净值源给单位/累计净值与日涨跌、不给前收。

    `pct_change` 是**百分数**（1.2195 即 1.2195%），在产生点经 `quantize_nav` 收口到
    4 位 HALF_UP（#692）。缺值、坏值（含 NaN）与 `0.0` 必须区分：0.0 是平盘这一天的
    真实涨跌，折成 None 会让前端读成「无数据」；反之缺值不得伪造成 0.0。同分支的单位
    净值/累计净值同样改判 `is not None`——本函数只做保真映射，不在这里用 `or` 链把
    `0.0` 与缺值混成一件事。「`0.0` 不是合法净值」由**写入判据独自**处置（#701：
    `_bulk_upsert_prices` 对 `unit_price`/`accumulated_nav` 走 `_usable_nav`，在可用性
    之上再判正；取数层不再折 0，好让坏值响亮地落成跳过 + WARNING 而不是静默消失），
    详见 `test_zero_accumulated_nav_is_not_folded_into_missing`。

    `accum_degraded`（#704）是 akshare 场外专有的批级标记，仅在该批累计净值整段失效时
    出现，透传给 `_bulk_upsert_prices` 决定冲突行是否保留既有 `accumulated_nav`。
    """
    seen: dict[str, dict] = {}
    for r in raw_data:
        td = r.get("trade_date")
        if not td:
            continue
        if market == "CN_EXCHANGE":
            pct = r.get("pct_chg")
            if pct is None:
                pct = r.get("pct_change")
            seen[td] = {
                "trade_date": td,
                "unit_price": r.get("close"),
                "pre_close": r.get("pre_close"),
                "pct_change": _optional_pct(pct),
            }
        else:
            unit = r.get("unit_nav")
            if unit is None:
                unit = r.get("unit_price")
            accum = r.get("accum_nav")
            if accum is None:
                accum = r.get("accumulated_nav")
            row = {
                "trade_date": td,
                "unit_price": unit,
                "accumulated_nav": accum,
                "pct_change": _optional_pct(r.get("pct_change")),
            }
            if r.get("accum_degraded"):
                # 批级降级标记原样透传给写入侧（#704）：取数层「整段没取到累计净值」与
                # 「该产品本来没有累计净值序列」在 `accumulated_nav` 上同形，只有这个标记
                # 能把两者分开。仅 akshare 场外会产生它。
                row["accum_degraded"] = True
            seen[td] = row
    return list(seen.values())


def _optional_pct(value: Any) -> Optional[Decimal]:
    """日涨跌原值 → 4 位百分数 Decimal；缺值/坏值（None、空串、NaN、Inf、非数字）→ None。

    坏值判据复用 `_usable_price`（#580）：上游缺值形态与单价同族，刻意不另立一套。
    **不含正负判据**（#701）——`_usable_nav` 管的是净值列，日涨跌是比率、平盘 `0.0`
    必须落成 `0.0000`，两类输入不共用一个「必须为正」。
    不经这道过滤直接 `quantize_nav` 的话，pandas 的 NaN 会让 `Decimal.quantize` 抛
    InvalidOperation，把一次同步整体打成 failed。
    """
    if not _usable_price(value):
        return None
    return quantize_nav(value)


def _db_prev_accum_nav(
    db: Session,
    product_code: str,
    market: str,
    before: date,
) -> Optional[Decimal]:
    """该产品同市场 `price_date` 更早的最近一条 accumulated_nav 非空行（#692）。

    刻意不引交易日历倒推「上一交易日」：停牌、缺行、净值尚未发布都会让上一交易日无
    记录，此时基准应是**实际存在的上一条**，而不是一个查不到行的日期。与迁移 0020 的
    `LAG(accumulated_nav) OVER (… ORDER BY price_date)`（跑在非空子集上）同口径。
    由 `uix_price_record (product_code, market, price_date)` 前缀支撑，每产品每次同步
    最多一次查询。
    """
    row = (
        db.query(PriceRecord.accumulated_nav)
        .filter(
            PriceRecord.product_code == product_code,
            PriceRecord.market == market,
            PriceRecord.price_date < before,
            PriceRecord.accumulated_nav.is_not(None),
        )
        .order_by(PriceRecord.price_date.desc())
        .first()
    )
    return row[0] if row else None


def _derive_otc_pct_change(
    db: Session,
    product_code: str,
    market: str,
    rows: List[dict],
) -> None:
    """就地补 tushare 场外行的 `pct_change`（#692）。上游 `fund_nav` 无涨跌幅字段。

    **成文口径**：`日涨跌 = (accum_nav_d / accum_nav_prev − 1) × 100`，百分数，
    `quantize_nav` 收口（4 位 HALF_UP）。基准 prev = 该产品同市场更早的最近一条
    accumulated_nav 非空行；批内首行的基准取自 DB（`_db_prev_accum_nav`），增量窗首行
    因此接得上存量历史，跨批不留缺口。

    用累计净值而非单位净值：现金分红除息日 unit_nav 直接下调 div_cash，按 unit_nav
    相邻比值会把分红算成假暴跌，而修正需要产品级 ex_date 表——本仓没有
    （`task_runner._detect_dividends` 只为有持仓的组合建组合级事件，港互认无任何事件
    源）。accum_nav 在除息日不跳变（现金分红同步计入累计分红），推导值连续，且与同页
    「区间收益率」（`_nav_interval_return`，同为 accum 比值）同源，两列可互相印证
    ——**该互证只在 `nav_basis="accumulated"` 时成立**（#703）：产品整段历史都没有
    累计净值时区间收益率回退到单位净值口径，与这里的 accum 推导值不再同源。

    已知限制（成文，不做自动检测）：
    - **份额折算日**基金公司按比例同时缩放 unit_nav 与 accum_nav，当日推导值失真一天，
      次日以新基准自动恢复（一次性、不累积）。事后修正路径 = 人工把受影响行 pct_change
      置 NULL 后重跑迁移 0020（幂等只补 NULL），或等上游勘误后 sync-history 覆盖。
      注意 #698 起增量窗会重放回看段（约 11 个交易日），窗内的人工置 NULL 会被下一次
      nav_sync 重新推导填回——置 NULL 修正只对滑出回看窗的日期稳定。
    - 港互认与 akshare 场外走上游字段透传，语义是**单位净值**日涨跌（除息日含跳水），
      与此处的总收益口径在除息日存在一次性口径差。

    无基准（产品首条记录）、基准非正、accum 缺失/坏值/非正时保持 None，**不伪造 0.00%**：
    首行无基准与平盘是两件事。
    """
    if not rows:
        return
    # tushare 不保证返回顺序（实测多为降序），链式推导前先按日期升序排。
    ordered = sorted(rows, key=lambda r: r["trade_date"])
    first = ordered[0]["trade_date"]
    prev = _db_prev_accum_nav(
        db, product_code, market,
        date(int(first[:4]), int(first[4:6]), int(first[6:8])),
    )
    for r in ordered:
        accum = r.get("accumulated_nav")
        if not _usable_nav(accum):
            # 缺失/坏值/非正行既算不出涨跌，也不作后续基准（与迁移的 LAG 非空子集同口径）；
            # 判正的理由与 `_bulk_upsert_prices` 同源：0.0 作分子会推出 −100.0000 的假暴跌
            # （#701），而取数层 `get_fund_nav` 现已不再把 0 折成缺值，只能在这一层拦住。
            continue
        if prev is not None and prev > 0:
            r["pct_change"] = quantize_nav(
                (Decimal(str(accum)) / Decimal(str(prev)) - 1) * 100
            )
        prev = Decimal(str(accum))


def _bulk_upsert_prices(
    db: Session,
    product_code: str,
    market: str,
    rows: List[dict],
    source: str,
) -> int:
    """批量 upsert。MySQL 用 ON DUPLICATE KEY UPDATE，SQLite 用 ORM fallback（测试环境）。

    `accum_degraded`（#704）是唯一一处「NULL 不覆盖既有值」：该批累计净值整段没取到时，
    冲突行保留库里已有的 `accumulated_nav`，只更新单价等其余列。范围刻意只到降级批次
    ——全局改成 COALESCE 会让真实逐日缺值（净值发布时差、坏值）永远修不回来，那是
    既有语义（`test_resync_overwrites_pct_unconditionally`）。
    """
    from sqlalchemy import text

    if not rows:
        return 0

    values, skipped, nav_dropped = [], [], []
    accum_degraded = False
    for r in rows:
        td = r["trade_date"]
        # #580 无值 / #701 非正：单价不合格的行不入库。落进去的后果比缺一行严重得多——
        # 读取路径 `float(None)` 炸成 500（market_data.py）；0.0 更隐蔽，快照取价把这一行
        # 当作「有价的一天」、把市值算成 0，nav_coverage 还把它误计为已覆盖，事后再难从
        # 这里定位缺失的那个日期。负数连读取侧的 truthiness 判空都挡不住（算出负份额）。
        if not _usable_nav(r.get("unit_price")):
            skipped.append(td)
            continue
        # 累计净值不合格**只丢这一列**：unit_price 已合格，这一天确实有价，整行丢弃会把
        # 「一个派生口径的缺值」升级成「这一天没有净值」（快照报 MISSING_NAV）。
        accum = r.get("accumulated_nav")
        if accum is not None and not _usable_nav(accum):
            nav_dropped.append(td)
            accum = None
        if r.get("accum_degraded"):
            accum_degraded = True
        d = date(int(td[:4]), int(td[4:6]), int(td[6:8]))
        values.append({
            "product_code": product_code,
            "market": market,
            "price_date": d,
            "unit_price": r.get("unit_price"),
            "accumulated_nav": accum,
            "pre_close": r.get("pre_close"),
            "pct_change": r.get("pct_change"),
            "source": source,
        })

    if skipped or nav_dropped:
        logger.warning(
            "行情行写入侧过滤（#580 无值 / #701 非正）",
            extra={
                "operation": "bulk_upsert_prices",
                "product_code": product_code,
                "market": market,
                "skipped": skipped,
                "accumulated_nav_dropped": nav_dropped,
            },
        )

    if not values:
        # 整批都无可用单价（数据源整段缺净值，正是存量 NULL 行的来源形态）：上面已经
        # WARNING 记过被跳过的交易日，这里必须早退。MySQL 分支把空列表交给
        # `db.execute(sql, [])` 会抛 StatementError（SQLAlchemy 2.0：A value is required
        # for bind parameter …），让「跳过」被上层记成假失败——同步任务里该产品落成
        # failed、错误信息还是 SQLAlchemy 的内部文案；SQLite 分支遍历空列表恰好无感，
        # 所以这条方言分叉只有生产会踩到。
        return 0

    if accum_degraded:
        logger.warning(
            "累计净值整段降级，冲突行保留既有 accumulated_nav（#704）",
            extra={
                "operation": "bulk_upsert_prices",
                "product_code": product_code,
                "market": market,
                "accum_degraded": True,
                "rows": len(values),
            },
        )

    if db.bind.dialect.name == "mysql":
        # 降级批次里 accumulated_nav 恒为 NULL，COALESCE 因此等价于「保留旧值」；
        # 非降级批次仍走无条件覆盖。插值项是模块内固定字面量，不含外部输入。
        accum_term = (
            "COALESCE(VALUES(accumulated_nav), accumulated_nav)"
            if accum_degraded else "VALUES(accumulated_nav)"
        )
        sql = text(f"""
            INSERT INTO price_record (product_code, market, price_date, unit_price, accumulated_nav, pre_close, pct_change, source)
            VALUES (:product_code, :market, :price_date, :unit_price, :accumulated_nav, :pre_close, :pct_change, :source)
            ON DUPLICATE KEY UPDATE
              unit_price=VALUES(unit_price), accumulated_nav={accum_term},
              pre_close=VALUES(pre_close), pct_change=VALUES(pct_change), source=VALUES(source)
        """)
        db.execute(sql, values)
    else:
        conflict_keys = ("unit_price", "pre_close", "pct_change", "source")
        if not accum_degraded:
            conflict_keys += ("accumulated_nav",)
        for v in values:
            existing = db.query(PriceRecord).filter_by(
                product_code=v["product_code"], market=v["market"], price_date=v["price_date"]
            ).first()
            if existing:
                for k in conflict_keys:
                    setattr(existing, k, v[k])
            else:
                db.add(PriceRecord(**v))

    return len(values)


# ==================== 后台任务编排（P3.1） ====================

import threading
from concurrent.futures import ThreadPoolExecutor
from app.config import get_settings

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


class ConflictError(Exception):
    """已有同步任务在运行中"""
    pass


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=get_settings().sync_worker_count,
                    thread_name_prefix="price-sync",
                )
    return _executor


def submit_price_sync_job(params: dict, triggered_by: str = "manual") -> int:
    """提交价格同步后台任务，立即返回 job_id。单 active 锁：已有 pending/running job 则抛 ConflictError。

    自持 SessionLocal（#592 方案 A）：后台线程另开会话按 job_id 查任务，故任务必须先
    commit 才对工作线程可见——只 flush 会丢任务。**不接受注入会话**：注入形态下这里的
    commit 会连带提交调用方未提交的其他修改，且调用方随后的 rollback 撤不回。
    """
    from app.database import SessionLocal
    from app.models.sync_job import SyncJob

    db = SessionLocal()
    try:
        # 仅锁价格同步类型（#89：sync_job 表现已承载 snapshot_recalc，两类互不阻塞）
        active = db.query(SyncJob).filter(
            SyncJob.job_type.in_(["price_history_sync", "price_incremental_sync"]),
            SyncJob.status.in_(["pending", "running"]),
        ).count()
        if active > 0:
            raise ConflictError("已有价格同步任务在运行中")

        job = SyncJob(
            job_type=params.get("job_type", "price_history_sync"),
            status="pending",
            params=params,
            triggered_by=triggered_by,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

        # 投递失败不得留下 pending 孤儿：单 active 锁计 pending+running，一行永不执行的
        # pending 会此后 409 掉所有同类提交，且启动期孤儿恢复之外无人清理（#592 实证）。
        # submit 抛出即任务从未入队，不会有 worker 抢先改状态，此处改写终态无竞态。
        try:
            _get_executor().submit(_run_price_sync_job_impl, job_id)
        except Exception as e:
            job.status = "failed"
            job.error_message = f"任务投递失败: {e}"[:1000]
            job.finished_at = datetime.utcnow()
            db.commit()
            raise
        return job_id
    finally:
        db.close()


def _run_price_sync_job_impl(job_id: int):
    """后台线程执行体：每个线程独立 Session。"""
    from app.database import SessionLocal
    from app.models.sync_job import SyncJob
    from app.models.nav_sync_detail import NavSyncDetail

    db = SessionLocal()
    try:
        job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
        if not job:
            return
        params = job.params or {}
        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()

        scope = params.get("scope", "all")
        if scope == "by_product":
            target = params.get("products", [])
            from sqlalchemy import or_, and_
            conditions = [and_(Product.code == c, Product.market == m) for c, m in target]
            if conditions:
                products = db.query(Product).filter(or_(*conditions)).all()
            else:
                products = []
        else:
            ds_filter = [Product.data_source.in_(["tushare", "akshare"])]
            if params.get("data_source"):
                ds_filter = [Product.data_source == params["data_source"]]
            products = db.query(Product).filter(
                Product.market.in_(["CN_EXCHANGE", "CN_OTC", "HK_MUTUAL"]),
                *ds_filter,
            ).all()

        job.total = len(products)
        db.commit()

        for product in products:
            # 异常明细的 nav_date 与成功分支同取窗口上界（#746 审计）：end_date 在
            # try 内定妥、except 兜底业务日，不再各取一次进程钟把失败行记到另一天；
            # 上界缺省是业务日概念，UTC 进程钟在 07:00 CST 触发时会算出 T-2
            end_date = None
            try:
                start_date = None
                if params.get("job_type") == "price_incremental_sync":
                    # 与 run_nav_sync 同一实现，「两处窗口口径分叉」（#698 根因第 2 条）
                    # 自此由结构排除
                    start_date = resolve_incremental_start_date(
                        db, product.code, product.market
                    )
                elif params.get("start_date"):
                    start_date = datetime.strptime(params["start_date"], "%Y-%m-%d").date()

                if params.get("end_date"):
                    end_date = datetime.strptime(params["end_date"], "%Y-%m-%d").date()
                else:
                    end_date = business_today() - timedelta(days=1)

                result = sync_product_prices(
                    db=db,
                    product_code=product.code,
                    market=product.market,
                    start_date=start_date,
                    end_date=end_date,
                )

                status = "success" if result["success"] else "failed"
                db.add(NavSyncDetail(
                    job_id=job_id,
                    product_code=product.code,
                    market=product.market,
                    nav_date=end_date.strftime("%Y-%m-%d"),
                    status=status,
                    synced_count=result.get("synced_count", 0),
                    source=result.get("source"),
                    # 成功但降级的批次也要留痕（#704）：否则任务终态与「累计净值全部
                    # 正常取到」的成功批次同形，运维无从得知哪些产品的累计净值待补。
                    error_message=(
                        result.get("message") if status == "failed"
                        else (ACCUM_DEGRADED_NOTE if result.get("accum_degraded") else None)
                    ),
                ))
                if status == "success":
                    job.success_count += 1
                else:
                    job.failed_count += 1
            except Exception as e:
                db.add(NavSyncDetail(
                    job_id=job_id,
                    product_code=product.code,
                    market=product.market,
                    nav_date=(end_date or business_today()).strftime("%Y-%m-%d"),
                    status="failed",
                    error_message=str(e)[:500],
                ))
                job.failed_count += 1
            job.done += 1
            db.commit()

        if job.failed_count == 0:
            job.status = "success"
        elif job.success_count > 0:
            job.status = "partial"
        else:
            job.status = "failed"
        job.finished_at = datetime.utcnow()
        db.commit()

    except Exception as e:
        try:
            job.status = "failed"
            job.error_message = str(e)[:1000]
            job.finished_at = datetime.utcnow()
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


def recover_orphan_jobs():
    """启动时扫描遗留的 sync_job（running 与 pending），标记为 interrupted。

    pending 同样纳入（#592）：本函数只在启动期调用（main.py lifespan、init_scheduler），
    此时线程池是全新的进程内单例，**任何 pending 行都不可能被执行**——提交落库后进程崩溃
    留下的 pending 孤儿会永久占住单 active 锁。故无需时间阈值判断。
    """
    from app.database import SessionLocal
    from app.models.sync_job import SyncJob

    db = SessionLocal()
    try:
        orphans = db.query(SyncJob).filter(SyncJob.status.in_(["running", "pending"])).all()
        for job in orphans:
            reason = (
                "提交后未被执行体接管" if job.status == "pending" else "可能上次崩溃遗留"
            )
            job.status = "interrupted"
            job.error_message = (job.error_message or "") + f" [启动时标记为 interrupted：{reason}]"
            job.finished_at = datetime.utcnow()
        db.commit()
        return len(orphans)
    finally:
        db.close()