from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class TaskResponse(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    cron_expr: Optional[str] = None
    is_enabled: bool = True
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    next_run_at: Optional[datetime] = None
    timeout_seconds: int = 300
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedTaskResponse(BaseModel):
    """定时任务列表分页响应（issue #512）：此前未声明响应模型，ORM 整行直吐；
    items 元素复用 TaskResponse 收窄口径。"""

    items: List[TaskResponse]
    total: int
    page: int
    page_size: int


class TaskExecutionLogResponse(BaseModel):
    id: int
    task_code: str
    trigger_type: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    records_total: Optional[int] = None
    records_success: Optional[int] = None
    records_failed: Optional[int] = None
    error_message: Optional[str] = None
    error_stack: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaginatedTaskLogResponse(BaseModel):
    items: List[TaskExecutionLogResponse]
    total: int
    page: int
    page_size: int

    class Config:
        from_attributes = True


class TaskDetailResponse(TaskResponse):
    last_execution: Optional[TaskExecutionLogResponse] = None

    class Config:
        from_attributes = True
