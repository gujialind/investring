"""现金流水 API（issue #725）

薄适配器：鉴权 + 参数透传 + 序列化，业务口径全在 `cash_flow_service`。
纯读端点，无 commit 分支。

命名区分：本端点 `/{portfolio_code}/cash-flows`（复数）是**全来源现金流水**
（调仓/申赎 CASH 腿、平台间转移、平台级现金事件、手动重估）；既有
`/{portfolio_code}/cash-flow`（单数，routers/portfolios.py）是申赎汇总，两者并存、
口径不同，见 docs/reference/business-constraints.md#rule-cash。
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.schemas.cash_flow import PaginatedCashFlowResponse
from app.services.cash_flow_service import (
    list_cash_flows as list_cash_flows_service,
)

router = APIRouter()


@router.get("/{portfolio_code}/cash-flows", response_model=PaginatedCashFlowResponse)
def get_cash_flows(
    portfolio_code: str,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    status: Optional[str] = None,
    platform_code: Optional[str] = None,
    flow_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """现金流水列表（含页头概览四卡与按平台构成）。

    - `from_date` / `to_date`：现金日期闭区间，均可空（上界缺省开放，否则预计到账日
      在未来的在途行会被默认视图藏掉）。
    - `status`：`settled`（已生效）/ `in_transit`（在途）——映射派生的 settled 两值，
      不透出 raw `trade.status`；cancelled 腿不出行。
    - `flow_type`：`rebal_buy` / `rebal_sell` / `sub_buy` / `redeem` / `dividend` /
      `transfer` / `revalue`。
    - 金额一律 4 位原值直出（Decimal → JSON 字符串），展示量化由前端承担。
    """
    result = list_cash_flows_service(
        db,
        portfolio_code,
        from_date=from_date,
        to_date=to_date,
        status=status,
        platform_code=platform_code,
        flow_type=flow_type,
        page=page,
        page_size=page_size,
    )
    return PaginatedCashFlowResponse(**result)
