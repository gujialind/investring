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
    PaginatedTaskLogResponse,
)
from app.dependencies import get_current_admin
from app.services.exceptions import BusinessError

router = APIRouter()


@router.get("")
def get_tasks(
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    query = db.query(ScheduledTask)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


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


@router.post("/{code}/run")
def run_task(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    # #406：按 code 分支的 status / records / error 推导整体下沉到
    # `task_runner.run_task`（手动与调度共用同一实现），本层不再持有任务业务逻辑
    # （backend/AGENTS.md「分层目录与职责」节：router 是 service 薄适配层）。
    # 响应体与文案逐字保持不变。
    #
    # 刻意不写函数 docstring——FastAPI 会把 docstring 收进 OpenAPI `description`，
    # 中文段落会让 openapi.json 无谓漂移（契约只该因接口语义变化而变）。
    from app.services.task_runner import TRIGGER_MANUAL, run_task as run_task_service

    task = db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not task.is_enabled:
        raise HTTPException(status_code=400, detail="Task is disabled")

    try:
        result = run_task_service(db, code, trigger_type=TRIGGER_MANUAL)
    except HTTPException:
        raise
    except Exception as e:
        # 任务自身抛 BusinessError 时也走这里：经全局 handler 映射为
        # detail={error, message}（与其它端点同口径），而非 500 兜底
        if isinstance(e, BusinessError):
            raise
        raise HTTPException(status_code=500, detail=f"任务执行失败: {str(e)}")

    if code == "log_cleanup":
        return {"message": f"任务 {code} 执行成功", "deleted_logs": result}

    verb = "执行完成" if code in ("nav_sync", "snapshot_generate") else "执行成功"
    return {"message": f"任务 {code} {verb}", **result}


@router.post("/{code}/enable")
def enable_task(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    task = db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.is_enabled = True
    db.commit()
    return {"message": f"Task {code} enabled"}


@router.post("/{code}/disable")
def disable_task(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    task = db.query(ScheduledTask).filter(ScheduledTask.code == code).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.is_enabled = False
    db.commit()
    return {"message": f"Task {code} disabled"}


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
        raise HTTPException(status_code=404, detail="Task not found")

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
