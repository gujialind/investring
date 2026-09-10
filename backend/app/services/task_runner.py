"""
定时任务执行体

从 routers/tasks.py 提取的任务执行逻辑，供 CLI 和 router 共用。

事务边界说明（backend/AGENTS.md「分层目录与职责」节的编排层例外）：
本模块是长批处理任务的编排层，多日快照回补/逐产品远程同步需保留部分成功，
故保留有意的 checkpoint 提交（逐日/逐产品 commit）；单次性原子操作
（如 cleanup_old_logs）则不 commit，交调用方。

**任务执行记录的唯一编排点**（#406）：`run_task` 把「建 TaskExecutionLog →
跑任务 → 按结果落 status/duration/records/error」收敛为单一实现，手动触发
（routers/tasks.py）与调度触发（services/scheduler_service.py）共用，杜绝两条
路径漂移。它属上述同一豁免（任务日志生命周期本就多段 commit：running → 终态），
不新增分层例外类型。
"""
import logging
import traceback
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog
from app.models.nav_sync_detail import NavSyncDetail
from app.models.product import Product
from app.models.portfolio import Portfolio
from app.services.exceptions import NotFoundError

logger = logging.getLogger(__name__)

# 执行来源（task_execution_log.trigger_type，列宽 String(20)）。
# 埋点处禁止写字面量：漏改一处会让前端「触发方式」列静默显示为空。
TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULED = "scheduled"

# error_message 落库上限（Text 列本身无界，此处按 #305 既有口径截断）
ERROR_MESSAGE_MAX = 1000


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


def run_nav_sync(db: Session, log_id: int) -> dict:
    """执行净值同步任务：基金列表→逐只净值→分红检测（#156 起不再串联快照生成，
    快照由独立任务 run_snapshot_generate 负责）。

    `log_id` 必填（#406 起）：每条 NavSyncDetail 都要挂到当次 TaskExecutionLog 上，
    否则逐产品明细无父记录、答不出「这条明细属于哪次运行」。调用方一律经
    `run_task` 编排（它先建 log 再派发），故不再接受 None。
    """
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


def run_snapshot_generate(db: Session) -> dict:
    """执行组合快照生成任务（issue #156，自 run_nav_sync 剥离）。

    仅处理 active 且开启自动快照（auto_snapshot_enabled）的组合；依赖当日净值
    同步先成功，缺净值将由快照校验 fail-fast 并记入日志（次日回补自愈）。

    #305：返回值含逐日告警与自动确认失败条目，供手动触发响应与任务日志可见。
    #406：`portfolios_processed` 供任务执行记录归一为 records_total——快照链路
    没有 NavSyncDetail 那样的逐日明细表，故此处不带 log_id 入参（曾有过的
    `log_id` 参数自始至终是死参数，已移除）。
    """
    target_date = datetime.now().date() - timedelta(days=1)
    gen = _generate_snapshots_for_date(db, target_date)
    return {
        "snapshots_generated": gen["generated"],
        "portfolios_processed": gen["portfolios_processed"],
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
    #406 增 `portfolios_processed`：进入逐日回补循环的组合数，供 records_total 归一
    （与 `generated` 不同——后者是「组合×日」的成功日数）。
    """
    from app.services.snapshot_service import generate_daily_snapshots, auto_confirm_after_snapshot
    from app.services.exceptions import BusinessError
    from app.services.trading_utils import is_trading_day, get_next_trading_day, get_prev_trading_day
    from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
    from sqlalchemy import func

    outcome = {
        "generated": 0,
        "portfolios_processed": 0,
        "warnings": [],
        "auto_confirm_failed": [],
    }

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
        if current and current <= end_date:
            outcome["portfolios_processed"] += 1
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


# ---------------------------------------------------------------------------
# 任务执行记录编排（#406）
# ---------------------------------------------------------------------------
#
# 派发表：(db, log_id) -> 任务返回 dict。log_id 只被 nav_sync 使用（挂 NavSyncDetail）；
# 其余任务的返回结构不统一，records_* 由 _derive_log_fields 按 task_code 归一。
_TASK_DISPATCH: Dict[str, Callable[[Session, int], dict]] = {
    "nav_sync": run_nav_sync,
    "snapshot_generate": lambda db, log_id: run_snapshot_generate(db),
    "trading_calendar_sync": lambda db, log_id: run_calendar_sync(db),
    "log_cleanup": lambda db, log_id: run_log_cleanup(db),
}


def _derive_log_fields(
    task_code: str, result: dict
) -> Tuple[str, Optional[int], Optional[int], Optional[int], Optional[str]]:
    """把各任务的返回 dict 归一为 (status, records_total, records_success, records_failed,
    error_message)。

    口径（#406）：
    - `nav_sync`：total=products_count、failed=len(failed_products)、success=差额，
      满足 total == success + failed；
    - `snapshot_generate`：total=portfolios_processed、failed=len(auto_confirm_failed)、
      success=差额；#305 的 warnings / auto_confirm_failed 归并语义与 1000 字符截断
      原样保留（原先写在 routers/tasks.py 的按 code 分支里）；
    - `trading_calendar_sync`：total=success=synced_count，failed 为 None
      （成功即写入的交易日数，「失败」无度量口径）；
    - `log_cleanup`：total=success=删除总行数，failed 为 None（同上）。

    **records_failed 为 None 表示「未度量」，不是「零失败」**——语义不明处宁可留空，
    也不写一个会被读成「本次没有失败」的 0（与 #406 消除「恒 null 摆设列」的初衷一致：
    空值要能表达「不知道」，有值必须是真的）。
    """
    if task_code == "nav_sync":
        failed = len(result.get("failed_products") or [])
        total = result.get("products_count") or 0
        return "success" if not failed else "partial_success", total, total - failed, failed, None

    if task_code == "snapshot_generate":
        warnings = result.get("warnings") or []
        failed_items = result.get("auto_confirm_failed") or []
        total = result.get("portfolios_processed") or 0
        error_message = None
        if failed_items:
            status = "partial_success"
            summary = "; ".join(
                f"{r.get('code', 'UNKNOWN')}: {r.get('error', '')}" for r in failed_items
            )
            if warnings:
                summary += " | warnings: " + "; ".join(
                    w.get("type", "unknown") for w in warnings
                )
            error_message = summary[:ERROR_MESSAGE_MAX]
        elif warnings:
            status = "success"
            error_message = (
                "warnings: " + "; ".join(w.get("type", "unknown") for w in warnings)
            )[:ERROR_MESSAGE_MAX]
        else:
            status = "success"
        return status, total, total - len(failed_items), len(failed_items), error_message

    if task_code == "trading_calendar_sync":
        synced = result.get("synced_count") or 0
        return "success", synced, synced, None, None

    if task_code == "log_cleanup":
        deleted = result or {}
        total = sum(v for v in deleted.values() if isinstance(v, int))
        return "success", total, total, None, None

    # 派发表与归一表必须同时覆盖：漏一处说明新增任务只加了 dispatch、没定 records 口径
    raise ValueError(f"未定义任务执行记录口径: {task_code}")


def run_task(
    db: Session,
    task_code: str,
    trigger_type: str = TRIGGER_MANUAL,
) -> dict:
    """任务执行记录的单一编排入口（#406）：建 log → 跑任务 → 落终态。

    手动触发（routers/tasks.py）与调度触发（services/scheduler_service.py）共用本函数，
    两者的 status / records / error 口径由此不会漂移。

    事务与 session（backend/AGENTS.md「分层目录与职责」节）：
    - **复用调用方 session**，不 close、异常路径不 rollback——`running` 行在任务开跑前
      就 commit（独立事务段，长任务期间亦可查），故异常时不能回滚掉它；任务体内未提交的
      业务写入由任务自身的编排语义收口（run_nav_sync 逐产品 checkpoint 保留部分成功、
      _generate_snapshots_for_date 逐日内部 rollback + commit）。
    - 多段 commit（running → 终态）属 §1.1「合理例外」中 task_runner checkpoint 的同族，
      不新增例外类型。

    失败语义：
    - 任务抛异常 → status=failed、error_message=str(e)（保留摘要语义）、
      error_stack=traceback；异常原样上抛，交调用方决定 HTTP 映射（router → 500）。
    - 未知 task_code → 抛 NotFoundError，**不建 log 行**（没执行过的任务不该有执行记录）。
    - `task.last_run_at` 在任务真正执行后写入（成功与失败都写），未知 code 不写。
    """
    if task_code not in _TASK_DISPATCH:
        raise NotFoundError(
            code="TASK_NOT_FOUND",
            message=f"未知任务: {task_code}",
            details={"available_tasks": sorted(_TASK_DISPATCH)},
        )

    started_at = datetime.now()
    log = TaskExecutionLog(
        task_code=task_code,
        trigger_type=trigger_type,
        status="running",
        started_at=started_at,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    result: Optional[dict] = None
    failure: Optional[BaseException] = None
    stack: Optional[str] = None
    try:
        result = _TASK_DISPATCH[task_code](db, log.id)
    except BaseException as exc:  # noqa: BLE001 —— 终态记录后原样上抛，不吞
        failure = exc
        stack = traceback.format_exc()

    finished_at = datetime.now()
    log.finished_at = finished_at
    log.duration_ms = int((finished_at - started_at).total_seconds() * 1000)

    if failure is not None:
        log.status = "failed"
        log.error_message = str(failure)[:ERROR_MESSAGE_MAX] if str(failure) else type(failure).__name__
        log.error_stack = stack
        # exc_info 传异常实例：单行 JSON 形态下堆栈折进 exception 字段，
        # 不会与其他字段拼成多行（#404）。task_code / trigger_type 是自由键，
        # 经 extra 随本条日志落 JSON——注意禁用 "message" 保留字。
        logger.error(
            f"任务 {task_code} 执行失败: {failure}",
            exc_info=failure,
            extra={"task_code": task_code, "trigger_type": trigger_type},
        )
    else:
        status, total, success, failed, error_message = _derive_log_fields(task_code, result)
        log.status = status
        log.records_total = total
        log.records_success = success
        log.records_failed = failed
        log.error_message = error_message

    task = db.query(ScheduledTask).filter(ScheduledTask.code == task_code).first()
    if task:
        task.last_run_at = finished_at

    db.commit()

    if failure is not None:
        raise failure
    return result
