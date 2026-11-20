"""快照管理相关的Schema定义"""
from datetime import date
from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class SnapshotGenerateRequest(BaseModel):
    """单日快照生成请求"""
    portfolio_code: str
    target_date: date


class SnapshotRecalculateRequest(BaseModel):
    """区间快照重算请求"""
    portfolio_code: Optional[str] = None  # None表示所有活跃组合
    start_date: date
    end_date: date


class SnapshotCatchUpRequest(BaseModel):
    """快照追平请求（issue #84）"""
    portfolio_code: str
    to_date: date


class SnapshotGenerateNextRequest(BaseModel):
    """生成下一交易日快照请求（issue #84）"""
    portfolio_code: str


class SnapshotCatchUpResult(BaseModel):
    """快照追平结果（issue #84）

    逐日 checkpoint 语义：失败时 generated_dates 中已成功的日子已落库，
    failed_date/error 标记中断点。
    """
    portfolio_code: str
    to_date: str  # ISO格式
    generated_count: int
    generated_dates: List[str]  # ISO格式日期列表（升序）
    latest_snapshot_date: Optional[str] = None
    message: Optional[str] = None
    failed_date: Optional[str] = None
    error: Optional[str] = None
    # #305：逐日告警（带 date 键）与自动确认结果透传，无则 None
    warnings: Optional[List[Dict[str, Any]]] = None
    auto_confirmed: Optional[List[Dict[str, Any]]] = None
    # #305：中断点的结构化错误（error 仍为消息文本，向后兼容）
    error_code: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None


class SnapshotGenerateNextResult(BaseModel):
    """生成下一交易日快照结果（issue #84）"""
    success: bool
    message: str
    portfolio_code: str
    generated_date: str  # ISO格式
    total_value: Optional[float] = None
    total_shares: Optional[float] = None
    unit_price: Optional[float] = None
    warnings: Optional[List[Dict[str, Any]]] = None
    # #305：自动确认结果透传（含失败条目），无则 None
    auto_confirmed: Optional[List[Dict[str, Any]]] = None


class ValidationCheckResult(BaseModel):
    """数据校验结果"""
    check_type: str  # "trading_day", "pending_transactions", "price_data", "share_change_events"
    status: str  # "passed", "failed", "warning"
    message: str


class SnapshotValidationResult(BaseModel):
    """快照依赖数据校验结果"""
    portfolio_code: str
    target_date: date
    is_valid: bool
    checks: List[ValidationCheckResult]


class SnapshotGenerationResult(BaseModel):
    """快照生成结果"""
    success: bool
    message: str
    portfolio_code: str
    snapshot_date: date
    total_value: Optional[float] = None
    total_shares: Optional[float] = None
    unit_price: Optional[float] = None
    # 非阻断性告警（如 event_zeroed_position；无告警时为 None，旧客户端可忽略）
    warnings: Optional[List[Dict[str, Any]]] = None


class RecalculationPortfolioResult(BaseModel):
    """单个组合的重算结果"""
    portfolio_code: str
    processed_dates: List[str]  # ISO格式日期列表
    total_processed: int
    errors: List[Dict[str, Any]]
    # 逐日重建累积的告警（如 event_zeroed_position；负现金已走 NEGATIVE_CASH 进 errors）
    warnings: Optional[List[Dict[str, Any]]] = None
    # #305：逐日自动确认结果透传（含 auto_confirm_failed 条目），空列表为 []
    auto_confirmed: Optional[List[Dict[str, Any]]] = None
    # #305：删旧快照级联回退的申购记录，无则 []
    cascaded_unconfirmed: Optional[List[Dict[str, Any]]] = None
    # #305：end_date 因后续快照自动扩展到的实际重算终点（未扩展则无此字段）
    end_date_extended_to: Optional[str] = None


class RecalculationResult(BaseModel):
    """区间重算结果"""
    success: bool
    message: str
    results: List[RecalculationPortfolioResult]


class SnapshotListItem(BaseModel):
    """快照历史列表项（#146）。涨跌不由后端给——前端按相邻行 unit_price 推导。"""
    snapshot_date: date
    unit_price: float
    total_shares: float
    total_value: float
    in_transit_total: float

    class Config:
        from_attributes = True


class SnapshotListResponse(BaseModel):
    """快照历史列表响应。total = 过滤后全量计数（limit 截断前），防无声截断。"""
    portfolio_code: str
    items: List[SnapshotListItem]
    total: int
    limit: int


class SnapshotStatusResponse(BaseModel):
    """组合快照状态响应"""
    portfolio_code: str
    latest_snapshot_date: Optional[date] = None
    total_snapshots: int
    first_snapshot_date: Optional[date] = None
    missing_dates: List[str]  # 缺失的交易日（ISO格式）
    # issue #906：最新快照日之后至业务昨日应生成而未生成的 SSE 交易日（trailing
    # 缺口，与 missing_dates 内部空洞互补）；台账型恒空、无快照基线恒空
    catchup_pending_dates: List[str] = []
    # issue #71：最新快照日 CASH 持仓 cash_amount < 0 的平台清单（正常为空）
    negative_cash_platforms: List[str] = []
    # issue #156：组合自动快照开关（仅约束自动任务，手动生成/重算不受影响）
    auto_snapshot_enabled: bool = False
