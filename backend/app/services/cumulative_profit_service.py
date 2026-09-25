"""全历史累计收益（#598），独立于持仓列表的旧 profit_loss 口径。"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.share_change_event import ShareChangeEvent
from app.models.trade import Trade
from app.services.exceptions import BusinessError, NotFoundError
from app.services.position_service import IN_TRANSIT_PRODUCT_CODES
from app.utils.quantize import quantize_nav


_ZERO = Decimal("0")


def _sort_key(key: tuple) -> tuple:
    """保留历史可空维度，NULL 排在非空值之前。"""
    return tuple((part is not None, part or "") for part in key)


def compute_cumulative_profits(
    db: Session,
    portfolio_code: str,
    snapshot_date: date,
) -> dict:
    """按显式快照日返回平台产品、产品市场、平台三粒度累计收益。

    基金 = D 日市值 + confirmed 卖出实际净额 - confirmed 买入含费支出
           + confirmed 平台/子事件现金变动（ex_date <= D）。
    CASH = D 日现金 - confirmed CASH 腿净流入 - 基金事件已到账现金。
    CASH 自身调整、手动重估留在现金损益；在途不产生独立收益。

    固定四次批量 SELECT，不依赖列表分页，不写 ORM 或提交事务。历史流水键
    与当日持仓键取并集，清仓缺行市值为零，绝不回查旧快照复活持仓。
    逐行取 Decimal 后在内存累加（避免 SQLite SUM 的浮点累计误差），仅在
    基础键返回时用 quantize_nav 量化，另两粒度直接加总这些四位数值。
    """
    with db.no_autoflush:
        snapshot = db.query(PortfolioValueSnapshot.id).filter(
            PortfolioValueSnapshot.portfolio_code == portfolio_code,
            PortfolioValueSnapshot.snapshot_date == snapshot_date,
        ).first()
        if snapshot is None:
            raise NotFoundError(
                "NOT_FOUND", f"组合 {portfolio_code} 在 {snapshot_date} 无市值快照"
            )

        positions = db.query(
            PortfolioPosition.product_code,
            PortfolioPosition.market,
            PortfolioPosition.platform_code,
            PortfolioPosition.market_value,
            PortfolioPosition.cash_amount,
        ).filter(
            PortfolioPosition.portfolio_code == portfolio_code,
            PortfolioPosition.snapshot_date == snapshot_date,
            PortfolioPosition.product_code.notin_(IN_TRANSIT_PRODUCT_CODES),
        ).all()
        trades = db.query(
            Trade.id,
            Trade.product_code,
            Trade.market,
            Trade.platform_code,
            Trade.trade_type,
            Trade.actual_amount,
        ).filter(
            Trade.portfolio_code == portfolio_code,
            Trade.status == "confirmed",
            Trade.confirm_date <= snapshot_date,
            Trade.product_code.notin_(IN_TRANSIT_PRODUCT_CODES),
        ).all()
        events = db.query(
            ShareChangeEvent.product_code,
            ShareChangeEvent.market,
            ShareChangeEvent.platform_code,
            ShareChangeEvent.cash_effective_date,
            ShareChangeEvent.cash_change,
        ).filter(
            ShareChangeEvent.portfolio_code == portfolio_code,
            ShareChangeEvent.status == "confirmed",
            # 基金级父记录已持汇总值，只取平台/子记录，防父子双计。
            ShareChangeEvent.platform_code.isnot(None),
            ShareChangeEvent.product_code != "CASH",
            ShareChangeEvent.product_code.notin_(IN_TRANSIT_PRODUCT_CODES),
            ShareChangeEvent.ex_date <= snapshot_date,
            ShareChangeEvent.cash_change.isnot(None),
            ShareChangeEvent.cash_change != 0,
        ).all()

    market_values = defaultdict(lambda: _ZERO)
    profit_flows = defaultdict(lambda: _ZERO)
    for product, market, platform, market_value, cash_amount in positions:
        value = cash_amount if product == "CASH" else market_value
        market_values[(product, market, platform)] += value or _ZERO

    for trade_id, product, market, platform, trade_type, actual_amount in trades:
        # 确认写入方保证实际金额存在；异常历史数据不能退回 amount 或伪装为零。
        if actual_amount is None:
            raise BusinessError("INVALID_AMOUNT", f"已确认交易 {trade_id} 缺少实际金额")
        # CASH 净流入取负、基金净投入取负，两者都是 buy 减、sell 加。
        profit_flows[(product, market, platform)] += (
            -actual_amount if trade_type == "buy" else actual_amount
        )

    for product, market, platform, cash_date, cash_change in events:
        profit_flows[(product, market, platform)] += cash_change
        if cash_date <= snapshot_date:
            profit_flows[("CASH", "", platform)] -= cash_change

    platform_products = []
    product_totals = defaultdict(lambda: [_ZERO, _ZERO])
    platform_totals = defaultdict(lambda: [_ZERO, _ZERO])
    for key in sorted(market_values.keys() | profit_flows.keys(), key=_sort_key):
        product, market, platform = key
        market_value = quantize_nav(market_values[key])
        cumulative_profit = quantize_nav(market_values[key] + profit_flows[key])
        platform_products.append({
            "product_code": product,
            "market": market,
            "platform_code": platform,
            "market_value": market_value,
            "cumulative_profit": cumulative_profit,
        })
        for totals in (product_totals[(product, market)], platform_totals[(platform,)]):
            totals[0] += market_value
            totals[1] += cumulative_profit

    return {
        "snapshot_date": snapshot_date,
        "platform_products": platform_products,
        "products": [
            {
                "product_code": product,
                "market": market,
                "market_value": totals[0],
                "cumulative_profit": totals[1],
            }
            for (product, market), totals in sorted(
                product_totals.items(), key=lambda item: _sort_key(item[0])
            )
        ],
        "platforms": [
            {
                "platform_code": platform,
                "market_value": totals[0],
                "cumulative_profit": totals[1],
            }
            for (platform,), totals in sorted(
                platform_totals.items(), key=lambda item: _sort_key(item[0])
            )
        ],
    }
