"""快照管理API路由"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin, get_current_user
from app.models import Investor, Portfolio, PortfolioPosition, PortfolioValueSnapshot
from app.schemas.snapshot import (
    SnapshotGenerateRequest,
    SnapshotRecalculateRequest,
    SnapshotCatchUpRequest,
    SnapshotCatchUpResult,
    SnapshotGenerateNextRequest,
    SnapshotGenerateNextResult,
    SnapshotValidationResult,
    SnapshotGenerationResult,
    RecalculationResult,
    SnapshotListItem,
    SnapshotListResponse,
    SnapshotStatusResponse,
)
from app.services.exceptions import BusinessError
from app.services.snapshot_service import (
    catch_up_snapshots,
    compute_missing_snapshot_dates,
    generate_daily_snapshots,
    generate_next_snapshot,
    list_portfolio_snapshots,
    recalculate_snapshots,
    validate_snapshot_dependencies,
)

router = APIRouter()


@router.post("/generate", response_model=SnapshotGenerationResult)
def generate_snapshot(
    request: SnapshotGenerateRequest,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    手动触发单日快照生成
    
    权限：仅admin
    """
    try:
        result = generate_daily_snapshots(
            db=db,
            portfolio_code=request.portfolio_code,
            target_date=request.target_date,
        )
        # service 不 commit（backend/AGENTS.md「分层目录与职责」节），事务边界在 router
        db.commit()
        return SnapshotGenerationResult(**result)
    except BusinessError:
        # 领域异常（如 SNAPSHOT_NOT_CONTINUOUS）交给全局 handler 映射
        db.rollback()
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "VALIDATION_FAILED", "message": str(e)},
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "SNAPSHOT_GENERATION_FAILED", "message": str(e)},
        )


@router.post("/recalculate", response_model=RecalculationResult)
def recalculate(
    request: SnapshotRecalculateRequest,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    重算指定时间区间的快照（同步模式）
    
    单一事务（issue #58）：无 errors 时统一 commit；任一日失败则整体 rollback，
    被删快照与级联回退状态完整复原，对外「要么完整成功，要么无变化」。
    
    大区间重算易触发客户端 HTTP 超时（issue #89），建议改用
    POST /snapshots/recalculate-async 提交后台任务并轮询终态。
    
    权限：仅admin
    """
    try:
        result = recalculate_snapshots(
            db=db,
            portfolio_code=request.portfolio_code,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        # errors 非空（中途 break）时回滚半截中间态，仍返回 200 + errors 保持响应契约
        if any(r["errors"] for r in result["results"]):
            db.rollback()
        else:
            db.commit()
        return RecalculationResult(**result)
    except BusinessError:
        # 预校验（validate_snapshot_dependencies → _prev_trading_day）在删除任何快照前
        # 即可能抛领域异常（如日历不足 CALENDAR_NOT_SYNCED，#591）。必须在下方 catch-all
        # 之前 rollback 并原样上抛，交全局 handler 映射为 422 + 稳定错误码；否则落到
        # except Exception → 500 RECALCULATION_FAILED，违反「200+errors / 422、绝不 500」。
        # 预校验早于任何删除/写操作，整体回滚后对外仍是「无变化」。
        # （routers/snapshots.py 其余端点的 catch-all→500 通病属 open issue #553，不在本次范围。）
        db.rollback()
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "VALIDATION_FAILED", "message": str(e)},
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "RECALCULATION_FAILED", "message": str(e)},
        )


@router.post("/recalculate-async")
def recalculate_async(
    request: SnapshotRecalculateRequest,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    异步区间重算（issue #89）：提交后台任务立即返回 job_id，
    经 GET /api/sync-jobs/{job_id} 轮询终态（success=已提交 / failed=已整体回滚）。

    事务语义与同步模式一致：后台执行体按 errors 统一 commit/rollback，
    对外仍是「要么完整成功，要么无变化」。

    已有重算任务在运行时返回 409 RECALC_JOB_CONFLICT。

    权限：仅admin
    """
    from app.services.market_data_service import ConflictError
    from app.services.snapshot_recalc_job import submit_snapshot_recalc_job

    # 组合存在性前置校验（后台才报错体验差）
    if request.portfolio_code:
        portfolio = db.query(Portfolio).filter(
            Portfolio.code == request.portfolio_code
        ).first()
        if not portfolio:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "PORTFOLIO_NOT_FOUND",
                    "message": f"组合 {request.portfolio_code} 不存在",
                },
            )

    try:
        # 任务记录的事务归投递入口自持会话（#592）；本端点的 db 只用于上面的只读前置校验
        job_id = submit_snapshot_recalc_job(
            params={
                "portfolio_code": request.portfolio_code,
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
            },
            triggered_by="manual",
        )
    except ConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "RECALC_JOB_CONFLICT", "message": str(e)},
        )

    return {
        "job_id": job_id,
        "status": "pending",
        "message": "重算任务已提交，请用 GET /api/sync-jobs/{job_id} 轮询终态",
    }


@router.post("/catch-up", response_model=SnapshotCatchUpResult)
def catch_up(
    request: SnapshotCatchUpRequest,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    逐交易日追平快照至 to_date（issue #84）

    service 内部逐日 checkpoint commit（编排层例外），router 不再重复 commit；
    单日失败仅回滚当日，已成功日保留，响应附 failed_date/error。

    权限：仅admin
    """
    try:
        result = catch_up_snapshots(
            db=db,
            portfolio_code=request.portfolio_code,
            to_date=request.to_date,
        )
        return SnapshotCatchUpResult(**result)
    except BusinessError:
        # 领域异常（如 NO_SNAPSHOT_BASELINE）交给全局 handler 映射
        db.rollback()
        raise


@router.post("/generate-next", response_model=SnapshotGenerateNextResult)
def generate_next(
    request: SnapshotGenerateNextRequest,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    生成最新快照日的下一个交易日快照（issue #84）

    service 内部 commit（编排层例外），router 不再重复 commit。

    权限：仅admin
    """
    try:
        result = generate_next_snapshot(
            db=db,
            portfolio_code=request.portfolio_code,
        )
        return SnapshotGenerateNextResult(**result)
    except BusinessError:
        db.rollback()
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "VALIDATION_FAILED", "message": str(e)},
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "SNAPSHOT_GENERATION_FAILED", "message": str(e)},
        )


@router.get("/validation", response_model=SnapshotValidationResult)
def validate_dependencies(
    portfolio_code: str = Query(..., description="组合代码"),
    target_date: date = Query(..., description="目标日期"),
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    预检指定日期的依赖数据
    
    权限：仅admin
    """
    checks = validate_snapshot_dependencies(db, portfolio_code, target_date)
    
    is_valid = all(c["status"] != "failed" for c in checks)
    
    return SnapshotValidationResult(
        portfolio_code=portfolio_code,
        target_date=target_date,
        is_valid=is_valid,
        checks=checks,
    )


@router.get("/portfolios/{code}/status", response_model=SnapshotStatusResponse)
def get_snapshot_status(
    code: str,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    """
    查询组合快照状态
    
    权限：所有用户（只能查看自己有权限的组合）
    """
    # 检查组合是否存在
    portfolio = db.query(Portfolio).filter(Portfolio.code == code).first()
    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "PORTFOLIO_NOT_FOUND", "message": f"组合 {code} 不存在"},
        )
    
    # 获取最新快照日期
    latest = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == code
    ).order_by(PortfolioValueSnapshot.snapshot_date.desc()).first()
    
    # 获取最早快照日期
    earliest = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == code
    ).order_by(PortfolioValueSnapshot.snapshot_date.asc()).first()
    
    # 获取快照总数
    total = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == code
    ).count()
    
    # 首末快照日区间内缺失的交易日（#146）：区间无快照（无基线）时为空
    missing_dates: List[str] = []
    if latest and earliest:
        missing_dates = [
            d.isoformat()
            for d in compute_missing_snapshot_dates(
                db, code, earliest.snapshot_date, latest.snapshot_date
            )
        ]
    
    # issue #71：最新快照日 CASH 持仓负现金平台清单（正常为空）
    negative_cash_platforms = []
    if latest:
        negative_cash_platforms = [
            row[0] for row in db.query(PortfolioPosition.platform_code).filter(
                PortfolioPosition.portfolio_code == code,
                PortfolioPosition.snapshot_date == latest.snapshot_date,
                PortfolioPosition.product_code == "CASH",
                PortfolioPosition.cash_amount < 0,
            ).all()
        ]
    
    return SnapshotStatusResponse(
        portfolio_code=code,
        latest_snapshot_date=latest.snapshot_date if latest else None,
        total_snapshots=total,
        first_snapshot_date=earliest.snapshot_date if earliest else None,
        missing_dates=missing_dates,
        negative_cash_platforms=negative_cash_platforms,
        auto_snapshot_enabled=portfolio.auto_snapshot_enabled,
    )


@router.get("/portfolios/{code}/list", response_model=SnapshotListResponse)
def list_snapshots(
    code: str,
    start_date: Optional[date] = Query(None, description="起始日期(含) YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="结束日期(含) YYYY-MM-DD"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_user),
):
    """
    快照历史列表（#146）：snapshot_date 倒序，可选闭区间过滤。

    total 为 limit 截断前的过滤后计数，total > len(items) 即被截断。

    权限：所有用户（与 status 一致）
    """
    portfolio = db.query(Portfolio).filter(Portfolio.code == code).first()
    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "PORTFOLIO_NOT_FOUND", "message": f"组合 {code} 不存在"},
        )

    result = list_portfolio_snapshots(
        db, code, start_date=start_date, end_date=end_date, limit=limit
    )
    return SnapshotListResponse(
        portfolio_code=code,
        items=[SnapshotListItem.model_validate(row) for row in result["items"]],
        total=result["total"],
        limit=limit,
    )


@router.delete("/{portfolio_code}/{snapshot_date}")
def delete_snapshot(
    portfolio_code: str,
    snapshot_date: date,
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    删除指定日期的快照
    
    删除前自动级联回退依赖该快照的已确认申购/赎回和份额变动事件。
    
    权限：仅admin
    """
    from app.services.snapshot_service import _delete_existing_snapshots
    
    try:
        result = _delete_existing_snapshots(db, portfolio_code, snapshot_date)
        db.commit()
        
        response = {
            "success": True,
            "message": f"已删除组合 {portfolio_code} 在 {snapshot_date} 的快照",
            "deleted": result["deleted"],
        }
        
        cascaded = result.get("cascaded_subscriptions", [])
        if cascaded:
            response["cascaded_subscriptions"] = cascaded
            response["message"] += f"（级联回退了 {len(cascaded)} 笔申购/赎回）"
        
        cascaded_events = result.get("cascaded_events", [])
        if cascaded_events:
            response["cascaded_events"] = cascaded_events
            response["message"] += f"（级联回退了 {len(cascaded_events)} 笔份额变动事件）"
        
        return response
    except BusinessError:
        # 业务错误（如级联回退失败整体中止，#203）交全局 handler 返回具体错误码，
        # 不回退为 DELETE_FAILED
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "DELETE_FAILED", "message": str(e)},
        )


@router.delete("/{portfolio_code}/bulk/{from_date}")
def delete_snapshots_bulk(
    portfolio_code: str,
    from_date: date,
    confirm: bool = Query(False, description="必须显式传 confirm=true 才执行删除"),
    dry_run: bool = Query(False, description="仅预览将删除的快照日期，不执行删除"),
    db: Session = Depends(get_db),
    current_user: Investor = Depends(get_current_admin),
):
    """
    批量删除从 from_date 开始的所有快照（含 from_date）
    
    从最新快照日开始倒序删除，确保级联回退的顺序正确。
    每个快照的删除都会触发级联回退（申购/赎回 CASH trade、事件）。
    必须显式传 confirm=true，否则返回 422 CONFIRM_REQUIRED（兼作影响面预览）。
    dry_run=true 时纯预览（issue #75）：返回将删除的日期列表，不校验 confirm、零副作用。
    
    权限：仅admin
    """
    from app.services.snapshot_service import _delete_existing_snapshots
    from sqlalchemy import func
    from app.models import PortfolioPosition
    
    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail={"error": "PORTFOLIO_NOT_FOUND", "message": f"组合 {portfolio_code} 不存在"})
    
    # 获取所有需要删除的快照日期（从 from_date 开始，按日期降序）
    snapshot_dates = [
        row[0] for row in db.query(PortfolioValueSnapshot.snapshot_date)
        .filter(
            PortfolioValueSnapshot.portfolio_code == portfolio_code,
            PortfolioValueSnapshot.snapshot_date >= from_date,
        )
        .order_by(PortfolioValueSnapshot.snapshot_date.desc())
        .all()
    ]
    
    # dry-run 纯预览（issue #75）：不校验 confirm、不删除、零副作用
    if dry_run:
        return {
            "dry_run": True,
            "portfolio_code": portfolio_code,
            "from_date": from_date.isoformat(),
            "count": len(snapshot_dates),
            "snapshot_dates": [d.isoformat() for d in snapshot_dates],
        }
    
    # 破坏性操作守卫：逐日 commit 不可中途回滚，必须显式确认
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "CONFIRM_REQUIRED",
                "message": f"将删除组合 {portfolio_code} 从 {from_date} 起的 {len(snapshot_dates)} 个快照，"
                           f"请携带 confirm=true 确认，可先加 dry_run=true 预览",
            },
        )
    
    if not snapshot_dates:
        return {
            "success": True,
            "message": f"组合 {portfolio_code} 在 {from_date} 之后无快照可删除",
            "deleted_count": 0,
        }
    
    results = []
    total_cascaded_subs = 0
    total_cascaded_events = 0
    
    for snap_date in snapshot_dates:
        try:
            result = _delete_existing_snapshots(db, portfolio_code, snap_date)
            cascaded_subs = result.get("cascaded_subscriptions", [])
            cascaded_events = result.get("cascaded_events", [])
            total_cascaded_subs += len(cascaded_subs)
            total_cascaded_events += len(cascaded_events)
            results.append({
                "snapshot_date": snap_date.isoformat(),
                "deleted": result["deleted"],
                "cascaded_subs": len(cascaded_subs),
                "cascaded_events": len(cascaded_events),
            })
            # 逐日 commit：单日删除立即落库，避免末尾统一提交放大回滚范围
            db.commit()
        except BusinessError:
            # 业务错误（如级联回退失败整体中止当日删除，#203）交全局 handler
            # 返回具体错误码；逐日 commit 语义下已成功的日期保留（端点既有语义）
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "BULK_DELETE_FAILED", "message": f"删除 {snap_date} 快照失败: {str(e)}"},
            )

    return {
        "success": True,
        "message": f"已删除组合 {portfolio_code} 从 {from_date} 起的 {len(snapshot_dates)} 个快照"
                   f"（级联回退 {total_cascaded_subs} 笔申购/赎回，{total_cascaded_events} 笔事件）",
        "deleted_count": len(snapshot_dates),
        "details": results,
    }
