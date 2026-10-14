from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.models.scheduled_task import ScheduledTask
from app.models.task_execution_log import TaskExecutionLog
from app.schemas.task import (
    TaskResponse,
    TaskExecutionLogResponse,
    TaskDetailResponse,
    TaskRunAcceptedResponse,
    PaginatedTaskResponse,
    PaginatedTaskLogResponse,
)
from app.dependencies import get_current_admin
from app.services.exceptions import BusinessError
from app.error_reporting import report_unexpected

router = APIRouter()


@router.get("", response_model=PaginatedTaskResponse)
def get_tasks(
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    query = db.query(ScheduledTask)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedTaskResponse(
        items=[TaskResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/executions", response_model=PaginatedTaskLogResponse)
def get_all_task_executions(
    task_code: Optional[str] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """全局执行历史（跨任务，可选按 task_code 过滤）。

    注意：必须注册在 GET /{code} 之前，否则 "executions" 会被当作任务 code 捕获。
    """
    query = db.query(TaskExecutionLog)
    if task_code:
        query = query.filter(TaskExecutionLog.task_code == task_code)
    query = query.order_by(TaskExecutionLog.created_at.desc(), TaskExecutionLog.id.desc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/executions/{execution_id}", response_model=TaskExecutionLogResponse)
def get_task_execution(
    execution_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """按执行记录 id 查询单条状态（#714：手动触发后的轮询入口）。"""
    log = (
        db.query(TaskExecutionLog)
        .filter(TaskExecutionLog.id == execution_id)
        .first()
    )
    if not log:
        raise HTTPException(
            status_code=404, detail=f"任务执行记录 {execution_id} 不存在"
        )
    return log


@router.post("/{code}/run", response_model=TaskRunAcceptedResponse)
def run_task(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    # #714：改为「提交后台执行 + 轮询终态」。原先本端点在请求内同步跑完任务，
    # 请求寿命 = 任务时长，而 nginx `location /api/` 的 proxy_read_timeout 是 60s，
    # nav_sync 实测 162s 会被网关切断成 504、任务却在服务端照常成功（假失败）。
    # 执行体本身仍是 task_runner.run_task，与调度触发共用同一实现（#406 口径不变）。
    #
    # 刻意不写函数 docstring——FastAPI 会把 docstring 收进 OpenAPI `description`，
    # 中文段落会让 openapi.json 无谓漂移（契约只该因接口语义变化而变）。
    from app.services.market_data_service import ConflictError
    from app.services.task_runner import TRIGGER_MANUAL, submit_task

    task = db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {code} 不存在")

    if not task.is_enabled:
        raise HTTPException(status_code=400, detail="任务未启用，请先启用后再执行")

    try:
        # 投递入口自持会话（#592）；本层的 db 只用于上面的只读前置校验
        execution_id = submit_task(code, triggered_by=TRIGGER_MANUAL)
    except ConflictError as e:
        # 单 active 锁：同 code 已有 pending/running 记录（#714 前无任何并发保护）
        raise HTTPException(
            status_code=409,
            detail={"error": "TASK_ALREADY_RUNNING", "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        # 任务自身抛 BusinessError 时也走这里：经全局 handler 映射为
        # detail={error, message}（与其它端点同口径），而非 500 兜底
        if isinstance(e, BusinessError):
            raise
        report_unexpected(e, operation="run_task", task_code=code)
        raise HTTPException(status_code=500, detail=f"任务执行失败: {str(e)}")

    return TaskRunAcceptedResponse(
        execution_id=execution_id,
        task_code=code,
        status="pending",
        message=(
            f"任务 {code} 已提交，执行记录 #{execution_id}；"
            f"终态请用 GET /api/system/tasks/executions/{execution_id} 查询"
        ),
    )


@router.post("/{code}/enable")
def enable_task(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    task = db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {code} 不存在")

    task.is_enabled = True
    db.commit()
    return {"message": f"任务 {code} 启用成功"}


@router.post("/{code}/disable")
def disable_task(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    task = db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {code} 不存在")

    task.is_enabled = False
    db.commit()
    return {"message": f"任务 {code} 禁用成功"}


@router.get("/{code}/logs", response_model=PaginatedTaskLogResponse)
def get_task_logs(
    code: str,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    query = (
        db.query(TaskExecutionLog)
        .filter(TaskExecutionLog.task_code == code)
        .order_by(TaskExecutionLog.created_at.desc())
    )

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{code}", response_model=TaskDetailResponse)
def get_task(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """查看任务详情：任务全字段 + 最近一次执行记录（last_execution 可为 null）"""
    task = db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {code} 不存在")

    last_execution = (
        db.query(TaskExecutionLog)
        .filter(TaskExecutionLog.task_code == code)
        .order_by(TaskExecutionLog.created_at.desc(), TaskExecutionLog.id.desc())
        .first()
    )

    return TaskDetailResponse(
        **TaskResponse.model_validate(task).model_dump(),
        last_execution=(
            TaskExecutionLogResponse.model_validate(last_execution)
            if last_execution
            else None
        ),
    )
