"""红利再投资应收（#743）。

场外分红普遍「权益登记日 = 除息日」：红利按除息日净值折算份额，到红利发放日
（`share_confirm_date`）才到账。除息日到到账日之间这笔红利既不是现金也不是份额，
按 AMAC 口径记**应收股利**——计入组合市值与在途合计，不增加份额、不进 CASH、
不计可用现金。

**派生而非落库**：应收在需要时由「基数份额 × div_cash」现算，pending 与 confirmed
都计入，故除息日快照在事件仍 pending 时即可生成且已含应收；确认后份额在
`share_confirm_date` 落地、应收窗口自然关闭，重建/级联天然幂等（无需重算或
`requires_snapshot_regen`）。事后改 `div_cash`/日期会让派生值与已生成快照漂移，
由 `share_change_event_service._require_receivable_not_snapshotted` 收口。

独立模块的理由：快照生成与读侧收益连续要同一口径，而 `snapshot_service` 与
`position_service` 互不依赖——放任一侧都会造成反向 import。本模块只依赖 models
与 quantize，且**恒为一次批量 SELECT**（事件左连权益登记日持仓，
`uix_position_snapshot` 保证不放大行数）：读侧禁止 N+1，且
`test_cumulative_profits.py` 以固定条数钉住累计收益的查询数。
"""

from datetime import date
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.portfolio_position import PortfolioPosition
from app.models.share_change_event import ShareChangeEvent
from app.utils.quantize import quantize_amount

# 应收窗口的判据与基数取法都要求事件「还活着」：cancelled 不计（从未生效），
# confirmed 与 pending 都计（派生口径不依赖确认状态）。
RECEIVABLE_STATUSES = ("pending", "confirmed")


def reinvest_dividend_amount(
    entitlement_shares: Optional[Decimal], div_cash: Optional[Decimal]
) -> Decimal:
    """红利金额 = quantize_amount(基数份额 × 每份分红)。

    `compute_event_fields` 的 reinvest 分支同样调本函数——应收金额与确认时折算
    份额所用的金额必须同一口径（#425 的两步量化：先到分，再按净值折份额），
    否则除息日的应收与到账日的份额对不上，总市值出现分红缺口。
    """
    es = Decimal(str(entitlement_shares or 0))
    return quantize_amount(es * Decimal(str(div_cash or 0)))


class ReinvestReceivable(NamedTuple):
    """一条再投资应收（除息日 <= D < 份额确认日）。"""

    event_id: int
    portfolio_code: str
    product_code: str
    market: str
    platform_code: str
    ex_date: date
    share_confirm_date: date
    amount: Decimal


class _Row(NamedTuple):
    """事件 + 权益登记日持仓的左连结果（基数解析的原始输入）。"""

    event_id: int
    portfolio_code: str
    product_code: str
    market: str
    platform_code: str
    ex_date: date
    share_confirm_date: date
    entitlement_date: date
    status: str
    entitlement_shares: Optional[Decimal]
    div_cash: Optional[Decimal]
    position_shares: Optional[Decimal]


def _query_rows(
    db: Session, portfolio_codes: Sequence[str], upper_date: date
) -> List[_Row]:
    """取可能产生应收的事件行及其权益登记日持仓份额（一次查询，未过滤窗口）。"""
    codes = tuple(portfolio_codes)
    rows = db.query(
        ShareChangeEvent.id,
        ShareChangeEvent.portfolio_code,
        ShareChangeEvent.product_code,
        ShareChangeEvent.market,
        ShareChangeEvent.platform_code,
        ShareChangeEvent.ex_date,
        ShareChangeEvent.share_confirm_date,
        ShareChangeEvent.entitlement_date,
        ShareChangeEvent.status,
        ShareChangeEvent.entitlement_shares,
        ShareChangeEvent.div_cash,
        PortfolioPosition.shares.label("position_shares"),
    ).outerjoin(
        PortfolioPosition,
        and_(
            PortfolioPosition.portfolio_code == ShareChangeEvent.portfolio_code,
            PortfolioPosition.product_code == ShareChangeEvent.product_code,
            PortfolioPosition.market == ShareChangeEvent.market,
            PortfolioPosition.platform_code == ShareChangeEvent.platform_code,
            PortfolioPosition.snapshot_date == ShareChangeEvent.entitlement_date,
        ),
    ).filter(
        ShareChangeEvent.portfolio_code.in_(codes),
        ShareChangeEvent.event_type == "reinvest_dividend",
        ShareChangeEvent.status.in_(RECEIVABLE_STATUSES),
        ShareChangeEvent.share_confirm_date.isnot(None),
        ShareChangeEvent.platform_code.isnot(None),
        ShareChangeEvent.ex_date <= upper_date,
    ).all()
    return [_Row(*row) for row in rows]


def _resolve_base(
    row: _Row,
    target_date: date,
    live_positions: Optional[Dict[tuple, dict]],
) -> Decimal:
    """基数份额：confirmed 用落库的 entitlement_shares，pending 用权益登记日持仓。

    pending 且 `entitlement_date == target_date` 时优先取内存 `live_positions`——
    这正是同日登记/除息的形态：当日快照尚未落库，左连必落空，基数只能来自
    生成中的持仓字典。读侧不传 live_positions，一律用已落库快照（同日场景下
    `entitlement_date == ex_date` 的持仓行正是生成该快照时写入的份额，同值）。
    """
    if row.status == "confirmed" and row.entitlement_shares is not None:
        base = Decimal(str(row.entitlement_shares))
    elif live_positions is not None and row.entitlement_date == target_date:
        live = live_positions.get((row.product_code, row.market, row.platform_code))
        base = Decimal(str((live or {}).get("shares") or 0)) if live else Decimal("0")
    else:
        base = Decimal(str(row.position_shares or 0))
    return base if base > 0 else Decimal("0")


def _amount(row: _Row, target_date: date, live_positions: Optional[Dict[tuple, dict]]) -> Decimal:
    return reinvest_dividend_amount(
        _resolve_base(row, target_date, live_positions), row.div_cash
    )


def list_reinvest_receivables(
    db: Session,
    portfolio_code: str,
    target_date: date,
    *,
    live_positions: Optional[Dict[tuple, dict]] = None,
) -> List[ReinvestReceivable]:
    """列出 `target_date` 当日的再投资应收（`ex_date <= D < share_confirm_date`）。

    Args:
        live_positions: 快照生成中的内存持仓字典（键 `(product_code, market,
            platform_code)`，值含 `shares`）。只在生成 `D == entitlement_date`
            的快照时提供——此时当日持仓尚未落库。

    金额为 0（基数为 0、`div_cash` 为空或折算后不足一分）的应收跳过：不落
    0 值在途行，与既有「0 在途不建行」口径一致。
    """
    result = []
    for row in _query_rows(db, [portfolio_code], target_date):
        if not (row.ex_date <= target_date < row.share_confirm_date):
            continue
        amount = _amount(row, target_date, live_positions)
        if amount == 0:
            continue
        result.append(
            ReinvestReceivable(
                event_id=row.event_id,
                portfolio_code=row.portfolio_code,
                product_code=row.product_code,
                market=row.market,
                platform_code=row.platform_code,
                ex_date=row.ex_date,
                share_confirm_date=row.share_confirm_date,
                amount=amount,
            )
        )
    return result


def aggregate_receivable_flows(
    db: Session, portfolio_codes: Sequence[str], max_date: date
) -> Dict[Tuple[str, str, str, str], List[Tuple[date, float]]]:
    """读侧收益连续用的应收流水（批量，恒一次查询）。

    每条应收产出一对 `(ex_date, +R)` 与 `(share_confirm_date, −R)`：基金市值在
    除息日因净值除权下跌 R、在确认日因份额到账回升 R，两端各加回一次即让
    单产品的日收益、持有收益与累计收益在 `[ex_date, share_confirm_date)` 区间
    连续（总市值只随净值波动）。

    刻意**只进 `event_rows`（产品维度），不进 `event_cash_flows`（现金维度）**：
    再投资不产生现金，`cash_change` 恒为 0，计入现金腿会凭空抬高现金累计收益。

    两端都无条件产出、由消费方按行快照日过滤（`compute_event_cash_addbacks` 取
    `<= 行快照日`、`compute_daily_profits` 取 `== 行快照日`），与既有事件现金腿
    的聚合形态一致。
    """
    flows: Dict[Tuple[str, str, str, str], List[Tuple[date, float]]] = {}
    if not portfolio_codes:
        return flows
    for row in _query_rows(db, portfolio_codes, max_date):
        amount = _amount(row, row.ex_date, None)
        if amount == 0:
            continue
        key = (row.portfolio_code, row.product_code, row.market, row.platform_code)
        value = float(amount)
        flows.setdefault(key, []).append((row.ex_date, value))
        flows.setdefault(key, []).append((row.share_confirm_date, -value))
    return flows
