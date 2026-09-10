"""
定时任务执行体

从 routers/tasks.py 提取的任务执行逻辑，供 CLI 和 router 共用。

事务边界说明（backend/AGENTS.md「分层目录与职责」节的编排层例外）：
本模块是长批处理任务的编排层，多日快照回补/逐产品远程同步需保留部分成功，
故保留有意的 checkpoint 提交（逐日/逐产品 commit）；单次性原子操作
（如 cleanup_old_logs）则不 commit，交调用方。
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog
from app.models.nav_sync_detail import NavSyncDetail
from app.models.product import Product
from app.models.portfolio import Portfolio

logger = logging.getLogger(__name__)


def cleanup_old_logs(db: Session) -> dict:
    """
    清理过期日志

    清理策略：
    - 登录日志：保留 30 天
    - 审计日志：保留 90 天
    - 净值同步明细：保留 90 天
    - 任务执行日志：保留 90 天
    - 系统错误日志：保留 30 天

    nav_sync_detail 必须先于 task_execution_log 删除（#426）：其 task_log_id
    外键无 ondelete，先删父行会撞约束抛 IntegrityError、中断整个清理
    （system_error_log 排在其后即永远执行不到）。

    不 commit（backend/AGENTS.md「分层目录与职责」节），事务边界交调用方（router tasks）。
    """
    from app.models.login_log import LoginLog
    from app.models.audit_log import AuditLog
    from app.models.system_error_log import SystemErrorLog

    cutoff_login = datetime.now() - timedelta(days=30)
    cutoff_audit = datetime.now() - timedelta(days=90)
    cutoff_task = datetime.now() - timedelta(days=90)
    cutoff_error = datetime.now() - timedelta(days=30)

    deleted = {
        "login_logs": 0,
        "audit_logs": 0,
        "nav_sync_details": 0,
        "task_logs": 0,
        "error_logs": 0,
    }

    deleted["login_logs"] = db.query(LoginLog).filter(
        LoginLog.created_at < cutoff_login
    ).delete()

    deleted["audit_logs"] = db.query(AuditLog).filter(
        AuditLog.created_at < cutoff_audit
    ).delete()

    # #426：子行除按自身 created_at 老化外，还要兜住「子行比父行新几秒、
    # 自身未老化但仍引用老化父行」的边界行（两者在同一次任务运行中先后创建）
    aged_task_log_ids = select(TaskExecutionLog.id).where(
        TaskExecutionLog.created_at < cutoff_task
    )
    deleted["nav_sync_details"] = db.query(NavSyncDetail).filter(
        or_(
            NavSyncDetail.created_at < cutoff_task,
            NavSyncDetail.task_log_id.in_(aged_task_log_ids),
        )
    ).delete(synchronize_session=False)

    deleted["task_logs"] = db.query(TaskExecutionLog).filter(
        TaskExecutionLog.created_at < cutoff_task
    ).delete()

    deleted["error_logs"] = db.query(SystemErrorLog).filter(
        SystemErrorLog.created_at < cutoff_error
    ).delete()

    return deleted


def run_nav_sync(db: Session, log_id: Optional[int] = None) -> dict:
    """执行净值同步任务：基金列表→逐只净值→分红检测（#156 起不再串联快照生成，
    快照由独立任务 run_snapshot_generate 负责）。"""
    from app.services.market_data_service import sync_product_prices
    from app.models.trading_calendar import TradingCalendar
    from app.models.price_record import PriceRecord
    from sqlalchemy import func

    target_date = (datetime.now().date() - timedelta(days=1))

    products = db.query(Product).filter(
        Product.market.in_(["CN_EXCHANGE", "CN_OTC", "HK_MUTUAL"]),
        Product.data_source.in_(["tushare", "akshare"]),
    ).all()

    if not products:
        return {
            "synced_count": 0,
            "products_count": 0,
            "failed_products": [],
            "target_date": target_date.isoformat(),
        }

    total_synced = 0
    failed_products = []

    for product in products:
        try:
            latest = db.query(func.max(PriceRecord.price_date)).filter(
                PriceRecord.product_code == product.code,
                PriceRecord.market == product.market,
            ).scalar()
            start_date = (latest + timedelta(days=1)) if latest else None

            result = sync_product_prices(
                db=db,
                product_code=product.code,
                market=product.market,
                start_date=start_date,
                end_date=target_date,
            )

            if result["success"]:
                total_synced += result.get("synced_count", 0)
                db.add(NavSyncDetail(
                    task_log_id=log_id,
                    product_code=product.code,
                    market=product.market,
                    nav_date=target_date.strftime("%Y-%m-%d"),
                    status="success",
                    synced_count=result.get("synced_count", 0),
                    source=result.get("source"),
                ))
            else:
                failed_products.append(product.code)
                db.add(NavSyncDetail(
                    task_log_id=log_id,
                    product_code=product.code,
                    market=product.market,
                    nav_date=target_date.strftime("%Y-%m-%d"),
                    status="failed",
                    error_message=result.get("message", "未知错误"),
                ))
        except Exception as e:
            failed_products.append(product.code)
            db.add(NavSyncDetail(
                task_log_id=log_id,
                product_code=product.code,
                market=product.market,
                nav_date=target_date.strftime("%Y-%m-%d"),
                status="failed",
                error_message=str(e)[:500],
            ))
        # 逐产品 checkpoint commit（编排层语义）：sync_product_prices 已不自行 commit，
        # 此处保留增量持久化：中途崩溃不丢已同步产品的价格与同步明细
        db.commit()

    dividends_detected = _detect_dividends(db, products)

    return {
        "synced_count": total_synced,
        "products_count": len(products),
        "failed_products": failed_products,
        "dividends_detected": dividends_detected,
        "target_date": target_date.isoformat(),
    }


def run_snapshot_generate(db: Session, log_id: Optional[int] = None) -> dict:
    """执行组合快照生成任务（issue #156，自 run_nav_sync 剥离）。

    仅处理 active 且开启自动快照（auto_snapshot_enabled）的组合；依赖当日净值
    同步先成功，缺净值将由快照校验 fail-fast 并记入日志（次日回补自愈）。

    #305：返回值含逐日告警与自动确认失败条目，供手动触发响应与任务日志可见。
    """
    target_date = datetime.now().date() - timedelta(days=1)
    gen = _generate_snapshots_for_date(db, target_date)
    return {
        "snapshots_generated": gen["generated"],
        "warnings": gen["warnings"],
        "auto_confirm_failed": gen["auto_confirm_failed"],
        "target_date": target_date.isoformat(),
    }


def _generate_snapshots_for_date(db: Session, target_date) -> dict:
    """为开启自动快照的活跃组合逐日补齐快照：从每组合最新快照日之后首个交易日起，
    逐交易日 generate + auto_confirm，直到 target_date（含）。
    单组合单日失败即停止该组合回补（#35 fail-fast）。

    仅自动任务走本函数并受 portfolio.auto_snapshot_enabled 开关过滤（#156）；
    手动生成/重算端点不经此过滤。

    逐日 commit/rollback 是编排层有意的 checkpoint 语义（与 recalculate 的
    整体原子语义相反）：多日回补中已完成的日子须保留，失败日仅回滚当日。

    #305 返回结构：{"generated": int, "warnings": [...], "auto_confirm_failed": [...]}
    ——告警逐条补 date 键；自动确认失败条目透传（含 code）。
    """
    from app.services.snapshot_service import generate_daily_snapshots, auto_confirm_after_snapshot
    from app.services.exceptions import BusinessError
    from app.services.trading_utils import is_trading_day, get_next_trading_day, get_prev_trading_day
    from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
    from sqlalchemy import func

    outcome = {"generated": 0, "warnings": [], "auto_confirm_failed": []}

    end_date = target_date if is_trading_day(db, target_date) else get_prev_trading_day(db, target_date, days=1)
    if not end_date:
        return outcome

    active_portfolios = db.query(Portfolio).filter(
        Portfolio.status == "active",
        Portfolio.auto_snapshot_enabled.is_(True),
    ).all()
    for portfolio in active_portfolios:
        latest_snapshot = db.query(func.max(PortfolioValueSnapshot.snapshot_date)).filter(
            PortfolioValueSnapshot.portfolio_code == portfolio.code
        ).scalar()
        current = get_next_trading_day(db, latest_snapshot, days=1) if latest_snapshot else end_date
        while current and current <= end_date:
            try:
                gen_result = generate_daily_snapshots(db=db, portfolio_code=portfolio.code, target_date=current)
                for w in gen_result.get("warnings") or []:
                    outcome["warnings"].append({**w, "date": current.isoformat()})
                auto_results = auto_confirm_after_snapshot(db=db, portfolio_code=portfolio.code, snapshot_date=current)
                outcome["auto_confirm_failed"].extend(
                    r for r in auto_results if r.get("action") == "auto_confirm_failed"
                )
                db.commit()
                outcome["generated"] += 1
            except Exception as e:
                db.rollback()
                code = e.code if isinstance(e, BusinessError) else type(e).__name__
                logger.error(
                    f"组合 {portfolio.code} 于 {current} 快照生成失败: code={code}, error={str(e)}"
                )
                break
            nxt = get_next_trading_day(db, current, days=1)
            if not nxt or nxt == current:
                break
            current = nxt
    return outcome


def _detect_dividends(db: Session, products: list) -> int:
    """分红检测：从 tushare fund_div 获取分红数据，自动创建 pending reinvest_dividend 事件。

    逐产品 commit/rollback 是编排层有意的 checkpoint 语义：逐只远程 API 调用，
    部分成功须保留，单产品失败仅回滚该产品。"""
    from app.services.tushare_client import get_fund_div, TushareNotConfiguredError, TushareAPIError
    from app.models.share_change_event import ShareChangeEvent
    from app.models.portfolio_position import PortfolioPosition
    from sqlalchemy import func as sa_func, and_

    detected = 0
    otc_products = [p for p in products if p.market == "CN_OTC" and (p.data_source or "tushare") == "tushare"]

    for product in otc_products:
        try:
            ts_code = f"{product.code}.OF"
            dividends = get_fund_div(ts_code)

            for div in dividends:
                if div.get("div_proc") and div["div_proc"] != "实施":
                    continue

                ex_date_str = div.get("ex_date", "")
                record_date_str = div.get("record_date", "")
                if not ex_date_str or not record_date_str:
                    continue

                try:
                    ex_date = datetime.strptime(ex_date_str, "%Y%m%d").date()
                    entitlement_date = datetime.strptime(record_date_str, "%Y%m%d").date()
                except ValueError:
                    continue

                if ex_date <= entitlement_date:
                    continue

                latest_dates = db.query(
                    PortfolioPosition.portfolio_code,
                    sa_func.max(PortfolioPosition.snapshot_date).label("max_date"),
                ).group_by(PortfolioPosition.portfolio_code).subquery()

                positions = db.query(PortfolioPosition).join(
                    latest_dates,
                    and_(
                        PortfolioPosition.portfolio_code == latest_dates.c.portfolio_code,
                        PortfolioPosition.snapshot_date == latest_dates.c.max_date,
                    ),
                ).filter(
                    PortfolioPosition.product_code == product.code,
                    PortfolioPosition.market == product.market,
                    PortfolioPosition.shares > 0,
                ).all()

                for pos in positions:
                    if not pos.platform_code:
                        continue
                    if ex_date <= pos.snapshot_date:
                        continue

                    existing = db.query(ShareChangeEvent).filter(
                        ShareChangeEvent.portfolio_code == pos.portfolio_code,
                        ShareChangeEvent.product_code == product.code,
                        ShareChangeEvent.ex_date == ex_date,
                        ShareChangeEvent.platform_code == pos.platform_code,
                    ).first()

                    if existing:
                        continue

                    event = ShareChangeEvent(
                        portfolio_code=pos.portfolio_code,
                        product_code=product.code,
                        market=product.market,
                        event_type="reinvest_dividend",
                        ex_date=ex_date,
                        entitlement_date=entitlement_date,
                        platform_code=pos.platform_code,
                        event_source="tushare",
                        div_cash=div.get("div_cash"),
                        status="pending",
                        notes=f"自动检测：tushare fund_div {ts_code}",
                    )
                    db.add(event)
                    detected += 1

            db.commit()

        except (TushareNotConfiguredError, TushareAPIError) as e:
            logger.warning(f"分红检测跳过 {product.code}: {e}")
            db.rollback()
        except Exception as e:
            logger.error(f"分红检测失败 {product.code}: {e}")
            db.rollback()

    return detected


def run_calendar_sync(db: Session, year: Optional[int] = None) -> dict:
    """执行交易日历同步任务"""
    from app.services.trading_calendar_service import sync_trading_calendar

    if year is None:
        year = datetime.now().year

    result = sync_trading_calendar(db=db, year=year)
    return {
        "synced_count": result["synced_count"],
        "year": result["year"],
    }


def run_log_cleanup(db: Session) -> dict:
    """执行日志清理任务"""
    return cleanup_old_logs(db)
