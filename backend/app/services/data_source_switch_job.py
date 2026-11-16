"""产品数据源批量切换后台任务编排（issue #882，SPEC v1.1 Block 3 / V4）。

复用 sync_job 表与 market_data_service 的线程池，照 snapshot_recalc_job 先例：
- submit_data_source_switch_job：单 active 锁（仅同类型，与价格同步/快照重算互不阻塞）
  → 整批只读预校验（枚举/上限/重复/存在性/市场兼容/虚拟产品，任一非法整批拒绝零写入）
  → 源级快检（一次轻量探测确认目标源可达，源整体故障整批早拒）→ 落 job 记录
  （job_type=data_source_switch）并提交后台线程，立即返回 job_id，客户端经
  GET /api/sync-jobs/{id} 轮询终态（N 只串行探测约 50–150s 超 nginx 60s，必须异步）。
- 投递入口与执行体**均自持 SessionLocal**（#592 方案 A，不接受注入会话）。
- 执行体逐产品 checkpoint（否决跨远程调用单事务）：产品级探测 → 通过则写 data_source
  + 状态重置（V5：status=pending、清 sync_error、last_sync_at 保留）+ diff-only 审计
  （与单只切换同一埋点 ACTION_UPDATE/RESOURCE_PRODUCT）→ commit → 台账；探测失败该只
  skipped 不写、其余继续。同值条目照单只 F1 语义 no-op（不探/不写/不审计/不重置），
  计 success——反向批量切回等幂等恢复路径会携带已是目标源的条目。
- 终态 success/partial/failed 照 sync-all（#821/#831）语义：全部成功（含 no-op）success；
  有成功也有 skipped/failed 为 partial；零成功响亮 failed。
- 台账：job 计数 + params["results"] 逐产品 JSON（code/market/old_source/new_source/
  status/reason）+ error_message 摘要（截断 1000）。GET /api/sync-jobs/{id}/details
  对本类型返回 params["results"]（本类型不写 NavSyncDetail 行）。
- 审计 actor：跨线程只传 request_id、不传 actor（共用线程池不复制 contextvars），
  后台审计恒 SYSTEM 哨兵（logging.md#logging-audit）。批量关联经 job 台账（old_source
  清单供反向切回）；audit_log 无 details 列，审计载荷保持与单只同形 diff-only，
  不加 batch_job_id（SPEC V4「可选」项，实施期按载荷口径单一形状定）。
- 不支持运行中取消（既有 sync_job 无 cancel 机制）；误操作恢复 = 反向批量切回
  （台账 old_source）+ 逐产品审计追溯 + 必要时 sync-history（SPEC §6）。
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.context import RequestContext, get_request_id, request_context_var
from app.services.market_data_service import ConflictError, _get_executor

logger = logging.getLogger(__name__)

JOB_TYPE = "data_source_switch"
# 显式清单上限（SPEC v1.1 V4，常量可调）；不做「按筛选全选」的无界集合
BATCH_MAX_PRODUCTS = 50


def submit_data_source_switch_job(
    products: list,
    data_source: Optional[str],
    triggered_by: str = "manual",
) -> int:
    """提交批量切换后台任务，立即返回 job_id。预校验/快检失败抛 BusinessError（零写入）。

    products: [[code, market], ...] 显式清单；data_source: 目标源（tushare/akshare）。
    单 active 锁（仅同类型）：已有 pending/running 的 data_source_switch job 抛 ConflictError。
    自持 SessionLocal、不接受注入会话，理由同 submit_price_sync_job（#592 方案 A）。
    """
    from sqlalchemy import and_, or_

    from app.database import SessionLocal
    from app.models.product import Product
    from app.models.sync_job import SyncJob
    from app.services.data_source_probe import quick_check_data_source
    from app.services.exceptions import BusinessError
    from app.services.product_service import VALID_DATA_SOURCES, validate_data_source

    # submit 不复制 contextvars，且线程池与价格同步共用（线程复用），故显式传参；
    # 只传 request_id——后台执行体无请求主体，审计侧按约定落 SYSTEM 哨兵。
    # 须在请求线程内读取，且早于投递。
    request_id = get_request_id()

    # 清单形状预校验（纯参数问题先于开库会话拒绝）
    if data_source is None:
        raise BusinessError("INVALID_PARAM", "data_source 必填（tushare/akshare）")
    if data_source not in VALID_DATA_SOURCES:
        raise BusinessError(
            "INVALID_DATA_SOURCE",
            f"非法数据源：{data_source}",
            details={"valid": list(VALID_DATA_SOURCES), "data_source": data_source},
        )
    if not products:
        raise BusinessError(
            "INVALID_PARAM",
            "products 清单不能为空（显式清单，不支持按筛选全选）",
        )
    if len(products) > BATCH_MAX_PRODUCTS:
        raise BusinessError(
            "INVALID_PARAM",
            f"批量切换上限 {BATCH_MAX_PRODUCTS} 只，收到 {len(products)} 只",
            details={"limit": BATCH_MAX_PRODUCTS, "received": len(products)},
        )
    pairs = []
    seen = set()
    for entry in products:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise BusinessError(
                "INVALID_PARAM",
                f"products 每个条目须为 [code, market] 两段，收到: {entry!r}",
            )
        code_raw, market_raw = entry
        if (
            code_raw is None
            or market_raw is None
            or not str(code_raw).strip()
            or not str(market_raw).strip()
        ):
            raise BusinessError(
                "INVALID_PARAM",
                f"products 条目的 code/market 不能为空，收到: {entry!r}",
            )
        key = (str(code_raw).strip(), str(market_raw).strip())
        if key in seen:
            raise BusinessError(
                "INVALID_PARAM",
                f"清单含重复条目: {key[0]}({key[1]})",
                details={"duplicate": list(key)},
            )
        seen.add(key)
        pairs.append(key)

    db = SessionLocal()
    try:
        # 仅锁批量切换类型（#89 先例：sync_job 表承载多类型，各自单 active 锁互不阻塞）
        active = db.query(SyncJob).filter(
            SyncJob.job_type == JOB_TYPE,
            SyncJob.status.in_(["pending", "running"]),
        ).count()
        if active > 0:
            raise ConflictError("已有批量切换任务在运行中")

        # 存在性整批核对（fail-fast：任一不存在即整批拒绝，防手滑清单造成部分写入）
        found = db.query(Product).filter(
            or_(*[and_(Product.code == c, Product.market == m) for c, m in pairs])
        ).all()
        found_keys = {(p.code, p.market) for p in found}
        missing = [f"{c}({m})" for c, m in pairs if (c, m) not in found_keys]
        if missing:
            raise BusinessError(
                "INVALID_PARAM",
                "清单含不存在的产品: " + ", ".join(missing),
                details={"reason": "product_not_found", "missing": missing},
            )
        # 市场兼容/虚拟产品复用单只校验器（单一实现，INVALID_DATA_SOURCE 同形）
        for p in found:
            validate_data_source(p.market, p.product_type, data_source)

        # 源级快检（token 预检在内）：源整体故障整批早拒；产品级 no_data 不早拒，
        # 交逐产品循环把该只 skipped。用清单首只做代表，不另起一套探测实现。
        first_code, first_market = pairs[0]
        quick_check_data_source(db, first_code, first_market, data_source)

        job = SyncJob(
            job_type=JOB_TYPE,
            status="pending",
            params={
                "products": [list(p) for p in pairs],
                "data_source": data_source,
                "results": [],
            },
            total=len(pairs),
            triggered_by=triggered_by,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

        # 投递失败不得留下 pending 孤儿（理由同 submit_price_sync_job，#592）：
        # 单 active 锁计 pending+running，一行永不执行的 pending 会此后 409 掉所有同类提交。
        try:
            _get_executor().submit(_run_data_source_switch_job_impl, job_id, request_id)
        except Exception as e:
            job.status = "failed"
            job.error_message = f"任务投递失败: {e}"[:1000]
            job.finished_at = datetime.utcnow()
            db.commit()
            raise
        return job_id
    finally:
        db.close()


def _run_data_source_switch_job_impl(
    job_id: int,
    request_id: Optional[str] = None,
    db: Optional[Session] = None,
):
    """后台线程执行体：自持 Session（db 参数仅供测试注入）。

    逐产品 checkpoint 提交（产品行 + 审计行 + job 计数/台账同一事务），保留部分成功；
    单只探测失败仅该只 skipped，不拖累其余。job 终态在全部产品处理后落库。
    """
    from app.constants.audit_actions import ACTION_UPDATE, RESOURCE_PRODUCT
    from app.database import SessionLocal
    from app.models.product import Product
    from app.models.sync_job import SyncJob
    from app.services.audit_service import record_audit
    from app.services.data_source_probe import probe_data_source
    from app.services.exceptions import BusinessError

    own_db = db is None
    if own_db:
        db = SessionLocal()

    # 逐任务绑定/重置：stdout 日志带上触发请求的 request_id，且不泄漏给复用线程的下一个任务
    token = request_context_var.set(RequestContext(request_id=request_id))
    try:
        job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
        if not job:
            return
        params = job.params or {}
        target = params.get("data_source")
        pairs = [tuple(p) for p in params.get("products", [])]
        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()

        results = []
        for idx, (code, market) in enumerate(pairs):
            entry = {
                "code": code,
                "market": market,
                "old_source": None,
                "new_source": target,
                "status": "failed",
                "reason": None,
            }
            try:
                product = db.query(Product).filter(
                    Product.code == code, Product.market == market
                ).first()
                if product is None:
                    # 预校验后到执行前被删除（竞态窗口）：该只 failed，其余继续
                    entry["reason"] = "产品不存在（提交后可能被删除）"
                elif product.data_source == target:
                    # 同值 no-op（照单只 F1：不探/不写/不审计/不重置），幂等恢复路径
                    entry.update(status="success", old_source=target, reason="同值无变更")
                else:
                    probe_data_source(db, code, market, target)
                    old = product.data_source
                    product.data_source = target
                    # V5 状态重置：pending（前端「待验证」）、清 sync_error；
                    # last_sync_at 保留（旧源上一次成功同步的历史事实）；探测通过 ≠ 同步成功
                    product.data_source_status = "pending"
                    product.sync_error = None
                    record_audit(
                        db,
                        action=ACTION_UPDATE,
                        resource_type=RESOURCE_PRODUCT,
                        resource_id=f"{code}({market})",
                        resource_name=product.name,
                        old_value={"data_source": old},
                        new_value={"data_source": target},
                    )
                    entry.update(status="success", old_source=old)
            except BusinessError as e:
                # 探测失败（no_data/unreachable/auth_error/token_missing）→ 该只 skipped 不写
                db.rollback()
                reason = (e.details or {}).get("reason")
                entry.update(
                    status="skipped",
                    reason=e.message + (f"（{reason}）" if reason else ""),
                )
            except Exception as e:
                db.rollback()
                entry["reason"] = str(e)[:200]

            results.append(entry)
            # rollback 后重新取 job 行，避免过期状态；计数按台账重算（n≤50，无漂移）
            job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
            job.done = idx + 1
            job.success_count = sum(1 for r in results if r["status"] == "success")
            job.skipped_count = sum(1 for r in results if r["status"] == "skipped")
            job.failed_count = sum(1 for r in results if r["status"] == "failed")
            # 台账必须写独立副本：JSON 列无 MutableDict 跟踪，若与活动 results 共享
            # 引用，提交态会被原地 append 同步改写， SQLAlchemy 判旧值==新值而跳过
            # UPDATE（计数列是标量照常更新，台账却停在上一轮——实测陷阱）。
            job.params = {**params, "results": [dict(r) for r in results]}
            db.commit()

        job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
        if job.failed_count == 0 and job.skipped_count == 0:
            job.status = "success"
        elif job.success_count > 0:
            job.status = "partial"
        else:
            job.status = "failed"
        bad = [r for r in results if r["status"] != "success"]
        job.error_message = (
            "; ".join(
                f"{r['code']}({r['market']}): {r['reason'] or r['status']}" for r in bad
            )[:1000]
            if bad
            else None
        )
        job.finished_at = datetime.utcnow()
        db.commit()

    except Exception as e:
        logger.error(f"批量切换任务 {job_id} 执行异常: {e}")
        try:
            db.rollback()
            job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)[:1000]
                job.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        if own_db:
            db.close()
        request_context_var.reset(token)