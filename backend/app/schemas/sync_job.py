from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, List
from datetime import datetime

VALID_SCOPES = ("all", "by_product")


class PriceSyncRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    scope: str = "all"
    products: Optional[List[List[str]]] = None
    data_source: Optional[str] = None

    @field_validator("products")
    @classmethod
    def _validate_products(cls, v):
        # #808：条目须恰好两段非空 code/market；去空格避免精确匹配静默落空（0 产品成功）
        if v is None:
            return v
        cleaned: List[List[str]] = []
        for entry in v:
            if len(entry) != 2:
                raise ValueError(
                    f"products 每个条目须为 [code, market] 两段，收到: {entry!r}"
                )
            code, market = (part.strip() for part in entry)
            if not code or not market:
                raise ValueError(f"products 条目的 code/market 不能为空，收到: {entry!r}")
            cleaned.append([code, market])
        return cleaned

    @field_validator("scope")
    @classmethod
    def _normalize_scope(cls, v: str) -> str:
        # #808：CLI 帮助历史写法 by-product 与后端匹配值 by_product 归一；
        # 未知值此前静默落入全量分支，改为 422 拒绝并给出可用值
        normalized = (v or "all").replace("-", "_")
        if normalized not in VALID_SCOPES:
            raise ValueError(
                f"scope 可用值: {list(VALID_SCOPES)}（by-product 自动归一），收到: {v!r}"
            )
        return normalized

    @model_validator(mode="after")
    def _by_product_requires_products(self):
        # #808 Q3：scope=by_product 空清单会在 worker 静默跑成 0 产品任务，投递前拒绝
        if self.scope == "by_product" and not self.products:
            raise ValueError("scope=by_product 时 products 不能为空")
        return self


class SyncJobResponse(BaseModel):
    id: int
    job_type: str
    status: str
    params: Optional[dict] = None
    total: int = 0
    done: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    error_message: Optional[str] = None
    triggered_by: str = "manual"
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class NavSyncDetailResponse(BaseModel):
    id: int
    job_id: Optional[int] = None
    task_log_id: Optional[int] = None
    product_code: str
    market: str
    nav_date: str
    status: str
    synced_count: int = 0
    source: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
