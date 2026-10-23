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
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog
from app.models.nav_sync_detail import NavSyncDetail
from app.models.product import Product
from app.models.portfolio import Portfolio
from app.services.exceptions import NotFoundError
from app.utils.business_time import business_today

logger = logging.getLogger(__name__)

# 执行来源（task_execution_log.trigger_type，列宽 String(20)）。
# 埋点处禁止写字面量：漏改一处会让前端「触发方式」列静默显示为空。
# retry / cascade（#715）：定时 nav_sync 非全量成功后的自动重试、重试全量成功后
# 对当日快照的联动补跑，与首跑 scheduled 在执行历史中可区分。
TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_RETRY = "retry"
TRIGGER_CASCADE = "cascade"

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

    返回的 `degraded_products`（#704）是「同步成功但累计净值整段降级」的产品：单位净值
    已落库、既有 accumulated_nav 已保留，不计入 `failed_products`，但经
    `_derive_log_fields` 落到任务终态的 error_message 上，避免与完全正常的成功同形。

    返回的 `dividends_skipped`（#771）同手法：分红公告有效、但 `div_cash` 缺失或非正，
    检测器**刻意不建事件**（建了只会永久 pending），只在终态 error_message 留痕，
    不计入 `failed_products`、不改 status。口径取舍见 `_detect_dividends`。
    """
    from app.services.market_data_service import (
        sync_product_prices, resolve_incremental_start_date, ACCUM_DEGRADED_NOTE,
    )

    target_date = business_today() - timedelta(days=1)

    products = db.query(Product).filter(
        Product.market.in_(["CN_EXCHANGE", "CN_OTC", "HK_MUTUAL"]),
        Product.data_source.in_(["tushare", "akshare"]),
    ).all()

    if not products:
        return {
            "synced_count": 0,
            "products_count": 0,
            "failed_products": [],
            "degraded_products": [],
            "target_date": target_date.isoformat(),
        }

    total_synced = 0
    failed_products = []
    degraded_products = []

    for product in products:
        try:
            # 下界含回看段，使上游迟披露的中间空洞在下一次运行自动回补（#698）；
            # 单点实现见 resolve_incremental_start_date，与 _run_price_sync_job_impl 共用
            start_date = resolve_incremental_start_date(db, product.code, product.market)

            result = sync_product_prices(
                db=db,
                product_code=product.code,
                market=product.market,
                start_date=start_date,
                end_date=target_date,
            )

            if result["success"]:
                total_synced += result.get("synced_count", 0)
                # 累计净值整段降级（#704）：单位净值已落库、冲突行的既有累计净值已保留，
                # 故仍算 success，但要在任务终态里显式可见——否则与「全部取到」的成功批次
                # 同形，运维无从得知哪些产品待补。
                degraded = bool(result.get("accum_degraded"))
                if degraded:
                    degraded_products.append(product.code)
                db.add(NavSyncDetail(
                    task_log_id=log_id,
                    product_code=product.code,
                    market=product.market,
                    nav_date=target_date.strftime("%Y-%m-%d"),
                    status="success",
                    synced_count=result.get("synced_count", 0),
                    source=result.get("source"),
                    error_message=ACCUM_DEGRADED_NOTE if degraded else None,
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

    dividends_detected, dividends_skipped = _detect_dividends(db, products)

    return {
        "synced_count": total_synced,
        "products_count": len(products),
        "failed_products": failed_products,
        "degraded_products": degraded_products,
        "dividends_detected": dividends_detected,
        # #771：因 div_cash 缺失/非正而未建事件的公告，供 _derive_log_fields 落终态留痕
        "dividends_skipped": dividends_skipped,
        "target_date": target_date.isoformat(),
    }


def run_snapshot_generate(db: Session) -> dict:
    """执行组合快照生成任务（issue #156，自 run_nav_sync 剥离）。

    仅处理 active 且开启自动快照（auto_snapshot_enabled）的组合；依赖当日净值
    同步先成功，缺净值将由快照校验 fail-fast 并记入日志，回补后次日重试自然带出
    （迟披露缺口的回补主体是 nav_sync 的回看窗，#698，不是本任务）。

    #305：返回值含逐日告警与自动确认失败条目，供手动触发响应与任务日志可见。
    #406：`portfolios_processed` 供任务执行记录归一为 records_total——快照链路
    没有 NavSyncDetail 那样的逐日明细表，故此处不带 log_id 入参（曾有过的
    `log_id` 参数自始至终是死参数，已移除）。
    #715：`failed_portfolios` 让 fail-fast（如缺净值 MISSING_NAV）在任务终态可见
    （此前只落运行日志、终态仍 success），调度侧的快照联动补跑据此判断
    「当日快照未成功」。
    """
    target_date = business_today() - timedelta(days=1)
    gen = _generate_snapshots_for_date(db, target_date)
    return {
        "snapshots_generated": gen["generated"],
        "portfolios_processed": gen["portfolios_processed"],
        "warnings": gen["warnings"],
        "auto_confirm_failed": gen["auto_confirm_failed"],
        "failed_portfolios": gen.get("failed_portfolios") or [],
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
    #715 增 `failed_portfolios`：fail-fast 中断回补的组合条目（portfolio_code/date/
    code/error），经 _derive_log_fields 落终态 partial_success——此前只 logger.error，
    终态仍 success，调度侧无从判断「当日快照未成功」。
    """
    from app.services.snapshot_service import generate_daily_snapshots, auto_confirm_after_snapshot
    from app.services.exceptions import BusinessError
    from app.services.trading_utils import (
        is_trading_day, try_get_next_trading_day, try_get_prev_trading_day,
    )
    from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
    from sqlalchemy import func

    outcome = {
        "generated": 0,
        "portfolios_processed": 0,
        "warnings": [],
        "auto_confirm_failed": [],
        "failed_portfolios": [],
    }

    # 调度回补把「日历到头」当合法控制流（#591）：用 try_* 显式探测，None 即无日可补。
    # 抛错版会经 run_task 外抛把整个任务判 failed、跳过其余组合——逐日 checkpoint
    # 退化成全有全无，正是本处要保住的东西。
    end_date = target_date if is_trading_day(db, target_date) else try_get_prev_trading_day(db, target_date, days=1)
    if end_date is None:
        outcome["warnings"].append({
            "type": "calendar_exhausted",
            "date": target_date.isoformat(),
            "message": f"交易日历缺少 {target_date} 之前的交易日，本轮快照回补跳过",
        })
        return outcome

    active_portfolios = db.query(Portfolio).filter(
        Portfolio.status == "active",
        Portfolio.auto_snapshot_enabled.is_(True),
    ).all()
    for portfolio in active_portfolios:
        latest_snapshot = db.query(func.max(PortfolioValueSnapshot.snapshot_date)).filter(
            PortfolioValueSnapshot.portfolio_code == portfolio.code
        ).scalar()
        current = (
            try_get_next_trading_day(db, latest_snapshot, days=1)
            if latest_snapshot else end_date
        )
        if current is None:
            # 该组合的最新快照日已贴着日历末尾：跳过本组合（保住其余组合），记告警。
            outcome["warnings"].append({
                "type": "calendar_exhausted",
                "date": target_date.isoformat(),
                "portfolio_code": portfolio.code,
                "message": (
                    f"组合 {portfolio.code} 最新快照日 {latest_snapshot} 之后无交易日，已跳过"
                ),
            })
            continue
        if current <= end_date:
            outcome["portfolios_processed"] += 1
        while current and current <= end_date:
            try:
                gen_result = generate_daily_snapshots(db=db, portfolio_code=portfolio.code, target_date=current)
                for w in gen_result.get("warnings") or []:
                    outcome["warnings"].append({**w, "date": current.isoformat()})
                auto_results = auto_confirm_after_snapshot(db=db, portfolio_code=portfolio.code, snapshot_date=current)
                # 附 portfolio_code：records_failed 按唯一组合去重计数需要组合身份
                # （auto_confirm_failed 条目本身只带 subscription/event 粒度字段，#715 复核 Blocker）
                outcome["auto_confirm_failed"].extend(
                    {**r, "portfolio_code": portfolio.code}
                    for r in auto_results if r.get("action") == "auto_confirm_failed"
                )
                db.commit()
                outcome["generated"] += 1
            except Exception as e:
                db.rollback()
                code = e.code if isinstance(e, BusinessError) else type(e).__name__
                logger.error(
                    f"组合 {portfolio.code} 于 {current} 快照生成失败: code={code}, error={str(e)}"
                )
                # #715：fail-fast 中断的组合落终态可见条目（此前只有上面这行运行日志，
                # 任务终态仍 success，快照联动补跑无从判断当日快照是否真的成功）
                outcome["failed_portfolios"].append({
                    "portfolio_code": portfolio.code,
                    "date": current.isoformat(),
                    "code": code,
                    "error": str(e),
                })
                break
            nxt = try_get_next_trading_day(db, current, days=1)
            if nxt is None:
                break
            current = nxt
    return outcome


def _detect_dividends(db: Session, products: list) -> Tuple[int, List[dict]]:
    """分红检测：从 tushare fund_div 获取分红数据，自动创建 pending reinvest_dividend 事件。

    逐产品 commit/rollback 是编排层有意的 checkpoint 语义：逐只远程 API 调用，
    部分成功须保留，单产品失败仅回滚该产品。

    返回 `(detected, skipped)`：`skipped` 是「公告有效但因 `div_cash` 缺失/非正而
    **刻意不建事件**」的留痕（按 `(产品, 除息日)` 去重），供任务日志可见。

    **为什么是「不建事件」而不是「建一条待补录的事件」**（#771）：
    - 建事件需要新状态（或复用 pending + 一个标记列），而 pending 正是病灶本身——
      快照闸门放行 pending、auto_confirm 逐日重试，`div_cash <= 0` 的行每次撞
      `_validate_dividend_div_cash` 失败，永久停在 pending 并反复刷告警；
    - `div_cash` 缺失/为 0 时确认会折出「0 份额 / 0 现金」的空事件（列表、统计与
      摊薄成本都被污染），补录金额又受 `SNAPSHOT_DEPENDENCY` 冻结（应收一旦进快照
      即不可改），用户能做的只有取消，等于把垃圾行推给用户清理；
    - 新状态/标记列会波及快照闸门、事件列表、响应契约与 CLI，超出本缺陷的范围
      （AGENTS.md 铁律 3：最小必要变更）。
    代价是这类公告不再出现在事件列表里，故必须留痕：跳过项写进 nav_sync 结果的
    `dividends_skipped`，由 `_derive_log_fields` 落任务日志的 error_message（与 #704
    累计净值降级同一手法——不改 status、不改 records，只让终态可见）。红利本身仍
    可由用户按公告手工录入（走服务层校验，正数即通过）。

    幂等性不变：`div_cash` 修复后重跑检测，同 `(组合, 产品, 除息日, 平台)` 仍由
    `existing` 判据去重，不重复建；跳过留痕同样按 `(产品, 除息日)` 去重。
    """
    from app.services.tushare_client import get_fund_div, TushareNotConfiguredError, TushareAPIError
    from app.models.share_change_event import ShareChangeEvent
    from app.models.portfolio_position import PortfolioPosition
    from sqlalchemy import func as sa_func, and_

    detected = 0
    skipped: List[dict] = []
    skipped_keys = set()
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

                # #771：直造 ORM 绕过服务层校验，是「闸门装在正门、检测走后门」的根因。
                # 金额缺失（tushare 该列可为 NULL）或非正（现场实测 -0.0039）都不建事件，
                # 判据与人工录入侧的 `_validate_dividend_div_cash` 一致，另把 NULL 一并挡在
                # 检测口——自动来源没有「先建后补」的语义，宁缺不造空事件。
                div_cash = div.get("div_cash")
                unusable_div_cash = div_cash is None or Decimal(str(div_cash)) <= 0

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

                    if unusable_div_cash:
                        # 留痕只在「本来会建事件」时产生：无持仓/已过除息日/已有事件的
                        # 组合本来就不该有这笔分红，报出来是噪音
                        key = (product.code, ex_date)
                        if key not in skipped_keys:
                            skipped_keys.add(key)
                            skipped.append({
                                "product_code": product.code,
                                "ex_date": ex_date.isoformat(),
                                "div_cash": div_cash,
                                "reason": "每份分红金额缺失或非正，未建事件；请核对公告后手工录入",
                            })
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
                        div_cash=div_cash,
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

    return detected, skipped


def run_calendar_sync(db: Session, year: Optional[int] = None) -> dict:
    """执行交易日历同步任务"""
    from app.services.trading_calendar_service import sync_trading_calendar

    if year is None:
        # 元旦 00:00–07:59 CST 在 UTC 进程钟上仍属旧年（#746）：取年份必须用业务日，
        # 否则 1 月 1 日 02:00 的年度日历同步会同步到旧年度
        year = business_today().year

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
      满足 total == success + failed；`degraded_products`（#704）不改 status/records
      （降级批次单位净值已同步），只落到 error_message；
    - `snapshot_generate`：total=portfolios_processed（每组合 +1）、failed=出现失败的
      **唯一组合数**（auto_confirm_failed 与 failed_portfolios 按 portfolio_code 去重，
      #715 复核：三计数同单位，杜绝多日回补 × 每日多条自动确认失败、或同一组合既
      auto_confirm_failed 又 failed_portfolios 时把 success 算成负数；fail-fast 中断
      回补的组合计入失败 → 终态 partial_success）、success=差额；逐条失败明细仍留在
      error_message；#305 的 warnings / auto_confirm_failed 归并语义与 1000 字符截断
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
        status = "success" if not failed else "partial_success"
        # 降级不是失败（单位净值已同步、既有累计净值已保留），但必须在终态可见（#704），
        # 与 snapshot_generate 用 error_message 承载 warnings 同一手法。
        # 分红跳过（#771）同理：公告有效但金额不可用，未建事件不是失败，不记不静默。
        degraded = result.get("degraded_products") or []
        skipped_dividends = result.get("dividends_skipped") or []
        segments = []
        if degraded:
            segments.append(
                f"累计净值降级: {', '.join(degraded)}（本批未取到累计净值，已有值保留）"
            )
        if skipped_dividends:
            segments.append(
                "分红公告跳过: " + "; ".join(
                    f"{item.get('product_code', '?')}@{item.get('ex_date', '?')} "
                    f"div_cash={item.get('div_cash')}（{item.get('reason', '金额不可用')}）"
                    for item in skipped_dividends
                )
            )
        error_message = (" | ".join(segments)[:ERROR_MESSAGE_MAX] if segments else None)
        return status, total, total - failed, failed, error_message

    if task_code == "snapshot_generate":
        warnings = result.get("warnings") or []
        failed_items = result.get("auto_confirm_failed") or []
        failed_portfolios = result.get("failed_portfolios") or []
        total = result.get("portfolios_processed") or 0
        # records_* 三计数同单位（唯一组合，#715 复核 Blocker）：failed 按出现失败的组合
        # 去重计数——按事件求和会在多日回补/同组合多条失败时把 success 算成负数。逐条明细
        # 仍留在 error_message。min(total) 兜住畸形输入（组合身份缺失或越界）下 failed 不
        # 超过 total，保住 success 非负与 total == success + failed 守恒。
        failed_codes = {
            code
            for code in (
                r.get("portfolio_code") for r in (*failed_items, *failed_portfolios)
            )
            if code is not None
        }
        failed_count = min(len(failed_codes), total)
        error_message = None
        if failed_items or failed_portfolios:
            status = "partial_success"
            segments = []
            if failed_portfolios:
                segments.append("快照生成失败: " + "; ".join(
                    f"{r.get('portfolio_code', 'UNKNOWN')}@{r.get('date', '?')} "
                    f"{r.get('code', 'UNKNOWN')}: {r.get('error', '')}"
                    for r in failed_portfolios
                ))
            if failed_items:
                segments.append("; ".join(
                    f"{r.get('code', 'UNKNOWN')}: {r.get('error', '')}" for r in failed_items
                ))
            if warnings:
                segments.append("warnings: " + "; ".join(
                    w.get("type", "unknown") for w in warnings
                ))
            error_message = " | ".join(segments)[:ERROR_MESSAGE_MAX]
        elif warnings:
            status = "success"
            error_message = (
                "warnings: " + "; ".join(w.get("type", "unknown") for w in warnings)
            )[:ERROR_MESSAGE_MAX]
        else:
            status = "success"
        return status, total, total - failed_count, failed_count, error_message

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
    execution_id: Optional[int] = None,
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

    `execution_id`（#714）：手动异步路径接管 `submit_task` 预建的 pending 记录行，
    不新建行——提交（pending）与真正开始执行（running）分两段，单 active 锁才有
    可计数的对象。调度触发与既有同步调用都不传，行为逐字不变。
    """
    if task_code not in _TASK_DISPATCH:
        raise NotFoundError(
            code="TASK_NOT_FOUND",
            message=f"未知任务: {task_code}",
            details={"available_tasks": sorted(_TASK_DISPATCH)},
        )

    started_at = datetime.now()
    if execution_id is None:
        log = TaskExecutionLog(
            task_code=task_code,
            trigger_type=trigger_type,
            status="running",
            started_at=started_at,
        )
        db.add(log)
    else:
        log = (
            db.query(TaskExecutionLog)
            .filter(TaskExecutionLog.id == execution_id)
            .first()
        )
        if log is None:
            raise NotFoundError(
                code="TASK_NOT_FOUND",
                message=f"任务执行记录 {execution_id} 不存在",
                details={"task_code": task_code},
            )
        log.status = "running"
        log.started_at = started_at
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


# ---------------------------------------------------------------------------
# 手动触发投递入口（#714）
# ---------------------------------------------------------------------------


def submit_task(task_code: str, triggered_by: str = TRIGGER_MANUAL) -> int:
    """提交后台任务执行，立即返回 TaskExecutionLog.id。

    为什么需要它（#714）：`POST /api/system/tasks/{code}/run` 原先在请求内同步跑完任务，
    请求寿命 = 任务时长，而 nginx `location /api/` 的 `proxy_read_timeout` 是 60s——
    nav_sync 实测 162s 会被网关切断成 504，任务却在服务端照常成功，调用方只看到
    「假失败」。改为提交 + 轮询后 HTTP 请求与任务时长解耦。

    单 active 锁：同一 task_code 已有 pending/running 记录时抛 ConflictError。
    此前手动路径**完全没有**并发保护（连击会并发跑两份 nav_sync，各自建一条记录），
    调度侧的 MySQL `GET_LOCK`（scheduler_service）只挡调度、不挡 HTTP，故在此补上。

    事务与 session（backend/AGENTS.md「分层目录与职责」节的任务投递入口口径，#592 方案 A）：
    **自持 SessionLocal、不接受注入会话**——后台线程另开会话按 execution_id 取任务，
    只 flush 会丢任务；正因这里必须 commit，才不得借用调用方的会话。
    """
    from app.context import get_request_id
    from app.database import SessionLocal
    from app.services.market_data_service import ConflictError, _get_executor

    if task_code not in _TASK_DISPATCH:
        raise NotFoundError(
            code="TASK_NOT_FOUND",
            message=f"未知任务: {task_code}",
            details={"available_tasks": sorted(_TASK_DISPATCH)},
        )

    # 须在请求线程内读取，且早于投递（同 snapshot_recalc_job 的理由）
    request_id = get_request_id()

    db = SessionLocal()
    try:
        active = (
            db.query(TaskExecutionLog)
            .filter(
                TaskExecutionLog.task_code == task_code,
                TaskExecutionLog.status.in_(["pending", "running"]),
            )
            .count()
        )
        if active > 0:
            raise ConflictError(
                f"任务 {task_code} 仍在执行中，请等待上一次完成后再触发"
            )

        log = TaskExecutionLog(
            task_code=task_code,
            trigger_type=triggered_by,
            status="pending",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        execution_id = log.id

        # 投递失败不得留下 pending 孤儿：单 active 锁计 pending+running，一行永不执行的
        # pending 会此后 409 掉该 code 的所有提交，只有重启才清得掉。
        # submit 抛出即任务从未入队，不会有执行体抢先改状态，此处改写终态无竞态。
        try:
            _get_executor().submit(
                _run_task_in_background,
                execution_id,
                task_code,
                triggered_by,
                request_id,
            )
        except Exception as e:
            log.status = "failed"
            log.error_message = f"任务投递失败: {e}"[:ERROR_MESSAGE_MAX]
            log.finished_at = datetime.now()
            db.commit()
            raise
        return execution_id
    finally:
        db.close()


def _run_task_in_background(
    execution_id: int,
    task_code: str,
    trigger_type: str,
    request_id: Optional[str] = None,
) -> None:
    """后台线程执行体：自持 Session，接管提交时预建的 pending 记录行。

    上下文传播同 #405：绑定触发请求的 request_id，使任务日志可与该请求串联；
    actor 刻意不传播——后台执行无请求主体。
    """
    from app.context import RequestContext, request_context_var
    from app.database import SessionLocal

    token = request_context_var.set(RequestContext(request_id=request_id))
    db = SessionLocal()
    try:
        run_task(db, task_code, trigger_type=trigger_type, execution_id=execution_id)
    except BaseException:
        # run_task 已落终态（failed + error_message/error_stack）并 logger.error 带栈，
        # 再原样上抛。这里不重复记录：Future 的异常默认无人 join、静默丢弃，
        # 显式吞掉比留一条「看起来没处理」的歧义好。
        pass
    finally:
        db.close()
        request_context_var.reset(token)


def recover_orphan_task_executions() -> int:
    """启动时把遗留的 pending/running 执行记录标记为 interrupted（#714）。

    与 `submit_task` 的单 active 锁配套：进程在提交后崩溃留下的未终态行不会再被执行体
    接管，不回收就会**永久 409 掉该 code 的后续手动触发**——把「假失败」换成「永久拒绝」
    比原问题更糟。判据与 `recover_orphan_jobs` 一致（#592）：本函数只在启动期调用，此时
    线程池是全新的进程内单例，任何未终态行都不可能被执行，故无需时间阈值。

    多 worker 取舍（与 `recover_orphan_jobs` 同族，成文接受、不视为缺陷）：生产为
    `--workers 2`（backend/Dockerfile），任一 worker 重启都会跑本函数，它按 status 全表
    扫描、不区分进程归属，故会把**另一个仍在运行的 worker** 正跑着的 running 行标成
    `interrupted`，提前松开单 active 锁（此后同 code 可再投一份）。「单 worker 内任何未
    终态行都不可能被执行」这句前提只在单进程成立；跨进程收紧需要 DB 级归属标记或时间
    阈值，收益不抵复杂度，暂沿用 #592 的既有取舍。
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        orphans = (
            db.query(TaskExecutionLog)
            .filter(TaskExecutionLog.status.in_(["pending", "running"]))
            .all()
        )
        for log in orphans:
            reason = (
                "提交后未被执行体接管" if log.status == "pending" else "可能上次崩溃遗留"
            )
            log.status = "interrupted"
            log.error_message = (
                (log.error_message or "")
                + f" [启动时标记为 interrupted：{reason}]"
            )[:ERROR_MESSAGE_MAX]
            log.finished_at = datetime.now()
        db.commit()
        return len(orphans)
    finally:
        db.close()
