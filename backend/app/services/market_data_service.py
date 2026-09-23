from datetime import date, datetime, timedelta
import logging
import math
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.price_record import PriceRecord
from app.models.product import Product
from app.models.trading_calendar import TradingCalendar
from app.services.exceptions import BusinessError, NotFoundError
from app.services.tushare_client import get_fund_daily, get_fund_nav, TushareAPIError

logger = logging.getLogger(__name__)


def _usable_price(value: Any) -> bool:
    """单价是否可入库（#580）。

    数据源缺值时给的是 None/空串，pandas 转换还说不准给 NaN——三者都不能作为
    「这一天的价格」落库。它们一旦进了 price_record，`unit_price` 就从此不再可靠：
    读取侧 `float(None)` 直接 500，快照取价反过来把 NULL 当成说好吧有价。
    """
    if value is None or value == "":
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


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
    返回 {success, synced_count, message, source}
    """
    product = db.query(Product).filter(
        and_(Product.code == product_code, Product.market == market)
    ).first()
    if not product:
        raise ValueError(f"产品 {product_code} ({market}) 不存在")

    if not end_date:
        end_date = date.today()
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
        product.data_source_status = "success"
        product.last_sync_at = datetime.utcnow()
        product.sync_error = None
        db.flush()
        return {"success": True, "message": "无新数据", "synced_count": 0, "source": data_source}

    normalized = _normalize_raw(raw_data, market)
    synced_count = _bulk_upsert_prices(db, product_code, market, normalized, data_source)

    product.data_source_status = "success"
    product.last_sync_at = datetime.utcnow()
    product.sync_error = None
    db.flush()

    return {"success": True, "message": f"同步 {synced_count} 条", "synced_count": synced_count, "source": data_source}


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
    """统一适配：tushare/akshare 返回 -> {trade_date(YYYYMMDD), unit_price, accumulated_nav, pre_close, pct_change}"""
    seen: dict[str, dict] = {}
    for r in raw_data:
        td = r.get("trade_date")
        if not td:
            continue
        if market == "CN_EXCHANGE":
            seen[td] = {
                "trade_date": td,
                "unit_price": r.get("close"),
                "pre_close": r.get("pre_close"),
                "pct_change": r.get("pct_chg") or r.get("pct_change"),
            }
        else:
            seen[td] = {
                "trade_date": td,
                "unit_price": r.get("unit_nav") or r.get("unit_price"),
                "accumulated_nav": r.get("accum_nav") or r.get("accumulated_nav"),
            }
    return list(seen.values())


def _bulk_upsert_prices(
    db: Session,
    product_code: str,
    market: str,
    rows: List[dict],
    source: str,
) -> int:
    """批量 upsert。MySQL 用 ON DUPLICATE KEY UPDATE，SQLite 用 ORM fallback（测试环境）。"""
    from sqlalchemy import text

    if not rows:
        return 0

    values, skipped = [], []
    for r in rows:
        td = r["trade_date"]
        # #580：无单价的行不入库。落进去的后果比缺一行严重得多——读取路径
        # `float(None)` 炸成 500（market_data.py），快照取价把 NULL 当作「有这一天」、
        # 让 nav_coverage 误计为已覆盖，事后再难从这里定位缺失的那个日期。
        if not _usable_price(r.get("unit_price")):
            skipped.append(td)
            continue
        d = date(int(td[:4]), int(td[4:6]), int(td[6:8]))
        values.append({
            "product_code": product_code,
            "market": market,
            "price_date": d,
            "unit_price": r.get("unit_price"),
            "accumulated_nav": r.get("accumulated_nav"),
            "pre_close": r.get("pre_close"),
            "pct_change": r.get("pct_change"),
            "source": source,
        })

    if skipped:
        logger.warning(
            "跳过无单价的行情行（#580）",
            extra={
                "operation": "bulk_upsert_prices",
                "product_code": product_code,
                "market": market,
                "skipped": skipped,
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

    if db.bind.dialect.name == "mysql":
        sql = text("""
            INSERT INTO price_record (product_code, market, price_date, unit_price, accumulated_nav, pre_close, pct_change, source)
            VALUES (:product_code, :market, :price_date, :unit_price, :accumulated_nav, :pre_close, :pct_change, :source)
            ON DUPLICATE KEY UPDATE
              unit_price=VALUES(unit_price), accumulated_nav=VALUES(accumulated_nav),
              pre_close=VALUES(pre_close), pct_change=VALUES(pct_change), source=VALUES(source)
        """)
        db.execute(sql, values)
    else:
        for v in values:
            existing = db.query(PriceRecord).filter_by(
                product_code=v["product_code"], market=v["market"], price_date=v["price_date"]
            ).first()
            if existing:
                for k in ("unit_price", "accumulated_nav", "pre_close", "pct_change", "source"):
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
    from sqlalchemy import func as sa_func

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
            try:
                start_date = None
                if params.get("job_type") == "price_incremental_sync":
                    latest = db.query(sa_func.max(PriceRecord.price_date)).filter(
                        PriceRecord.product_code == product.code,
                        PriceRecord.market == product.market,
                    ).scalar()
                    if latest:
                        start_date = latest + timedelta(days=1)
                elif params.get("start_date"):
                    start_date = datetime.strptime(params["start_date"], "%Y-%m-%d").date()

                end_date = None
                if params.get("end_date"):
                    end_date = datetime.strptime(params["end_date"], "%Y-%m-%d").date()
                else:
                    end_date = date.today() - timedelta(days=1)

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
                    error_message=None if status == "success" else result.get("message"),
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
                    nav_date=date.today().strftime("%Y-%m-%d"),
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