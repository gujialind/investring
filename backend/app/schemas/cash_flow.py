"""现金流水读侧响应契约（issue #725）。

全部字段为**读侧派生**，不落库：行集由 CASH 腿 / 平台级现金事件 / 现金重估三类来源
在 `cash_flow_service` 内合并派生。金额一律 `Decimal` 4 位原值直出（JSON 序列化为
字符串），后端不做展示量化——2 位呈现由前端 `formatCurrency` 承担
（见 docs/reference/business-constraints.md#rule-cash 的读侧口径）。
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


class CashFlowStatus(BaseModel):
    """行状态（由 `settled` 派生，不透出 raw `trade.status`）。

    variant: success=已生效 / warning=在途 / neutral=非流量（重估）。
    """
    variant: str
    label: str
    note: Optional[str] = None


class CashFlowAction(BaseModel):
    """行操作提示（前端据此选弹窗/端点）。kind: detail | confirm | revoke"""
    label: str
    kind: str


class CashFlowOriginRef(BaseModel):
    """行的账本来源标识，供前端详情弹窗回溯原始记录。

    kind: trade | share_change_event | manual_market_value。
    """
    kind: str
    id: int
    transfer_group: Optional[str] = None


class CashFlowPairRef(BaseModel):
    """关联业务（转移对方腿 / 调仓基金腿 / 申赎记录）。

    `amount` 为对方记录自身口径的金额（转移腿带符号、基金腿为正值原额），
    `ref_date` 为该记录的锚点日（转移=对方现金日、基金腿=确认日、申赎=确认日）。
    """
    kind: str
    id: int
    label: str
    platform_code: Optional[str] = None
    platform_name: Optional[str] = None
    ref_date: Optional[date] = None
    amount: Optional[Decimal] = None
    settled: Optional[bool] = None


class CashFlowRow(BaseModel):
    """一笔现金变动 = 某平台现金账本上的一行（平台间转移按腿拆两行）。

    - `cash_date`：该行参与排序/分组/余额查询的唯一权威日期（锚点见 rule-cash）。
    - `amount`：带符号（流入为正、流出为负）；`is_override=True` 的重估行是**绝对值**
      而非增量，不得与增量求和。
    - `settled`：该笔现金效果是否已计入 as_of=today 的可用现金。False 的行不进
      流入/流出、只进 `overview.in_transit`。
    - `platform_end_of_day_balance` / `prev_day_balance`：平台级时点值，同
      (平台, 现金日期) 组内各行相同（前端据此 rowSpan 合并），与筛选无关。
    - `group_total_count`：同 (平台, 现金日期) 在**解除类型筛选**后的总笔数，
      前端「当日另有 N 笔」= 该值 − 在屏同组行数。
    """
    source_kind: str
    flow_type: str
    flow_type_label: str
    cash_date: date
    cash_date_label: str
    cash_date_sub: Optional[str] = None
    platform_code: str
    platform_name: Optional[str] = None
    source_main: str
    source_sub: Optional[str] = None
    amount: Decimal
    is_override: bool = False
    settled: bool
    status: CashFlowStatus
    action: CashFlowAction
    origin_ref: CashFlowOriginRef
    pair_ref: Optional[CashFlowPairRef] = None
    group_total_count: int = 1
    platform_end_of_day_balance: Optional[Decimal] = None
    prev_day_balance: Optional[Decimal] = None


class CashFlowOverview(BaseModel):
    """页头四卡。

    - `available`：`calculate_available_cash(as_of=today)` 单次调用结果（平台筛选
      切换 scope，不随类型/状态/区间筛选变化）。
    - `in_transit`：Σ 未生效行的带符号金额，按到账/扣款平台归属；口径**不同于**
      快照的 `IN_TRANSIT_*` 虚拟产品（后者还含「现金已扣、基金腿未生效」的买入在途，
      其现金腿在本页是已生效行）。
    - `inflow` / `outflow`：对**筛选后全集**（非当页）聚合，仅已生效行；两级均排除
      重估，组合级另排除平台间转移（内部搬运），平台级含转移。outflow 为负值。
    """
    available: Decimal
    in_transit: Decimal
    inflow: Decimal
    outflow: Decimal


class PlatformCashFlowSummary(BaseModel):
    """按平台构成弹窗的一行 = 「筛选该平台时页头四卡会显示的值」。"""
    platform_code: str
    platform_name: Optional[str] = None
    available: Decimal
    in_transit: Decimal
    inflow: Decimal
    outflow: Decimal


class CashFlowRecon(BaseModel):
    """行和 − 合计 的差（= 本期已生效的平台间转移腿净额），供弹窗注记命名差异。"""
    inflow: Decimal
    outflow: Decimal


class PlatformCashFlowBreakdown(BaseModel):
    """构成弹窗数据：rows=各平台口径、total=组合口径、recon=两者之差。

    `total` 不随平台筛选变化（恒为组合口径）；rows 跟随区间/类型/状态筛选，
    与流量卡同源。
    """
    rows: List[PlatformCashFlowSummary]
    total: CashFlowOverview
    recon: CashFlowRecon


class PaginatedCashFlowResponse(BaseModel):
    """现金流水分页响应。total = 筛选后（分页前）的现金变动笔数——
    一笔平台间转移计 2（与各平台账本同构）。"""
    items: List[CashFlowRow]
    total: int
    page: int
    page_size: int
    overview: CashFlowOverview
    platform_breakdown: PlatformCashFlowBreakdown
