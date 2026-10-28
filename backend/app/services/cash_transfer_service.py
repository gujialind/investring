"""
平台间现金转移服务

复用 Trade 表实现：一次转移生成两条 CASH 交易（卖出+买入），通过 transfer_group 关联。
非对称状态模型：当天完成两腿立即 confirmed；跨天到账转出方当日 confirmed、转入方 pending
至下一交易日确认，保证 D 日 NAV 不因在途转移虚跌。供 REST 与 CLI 共用。

service 层只抛领域异常，不 import fastapi、不 commit。
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.portfolio import Portfolio
from app.models.platform import Platform
from app.models.trade import Trade
from app.services.trading_utils import (
    is_trading_day,
    get_next_trading_day,
    get_latest_snapshot_date,
)
from app.services.position_service import calculate_available_cash
from app.services.trade_service import validate_group_snapshot_free
from app.services.exceptions import BusinessError, NotFoundError
from app.services.audit_service import record_audit
from app.constants.audit_actions import (
    ACTION_CREATE, ACTION_CONFIRM, ACTION_CANCEL, RESOURCE_CASH_TRANSFER,
)
from app.utils.quantize import quantize_amount


def create_cash_transfer(
    db: Session,
    *,
    portfolio_code: str,
    from_platform: str,
    to_platform: str,
    amount: Decimal,
    transfer_date: date,
    cross_day: bool = False,
    notes: Optional[str] = None,
) -> dict:
    """创建平台间现金转移（非对称状态）。不 commit。

    - cross_day=False：两腿立即 confirmed，confirm_date = transfer_date
    - cross_day=True：转出方当日 confirmed，转入方 pending 至下一交易日（D 日 NAV 不跌）
    """
    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise NotFoundError("PORTFOLIO_NOT_FOUND", f"组合 {portfolio_code} 不存在")
    if portfolio.status != "active":
        raise BusinessError("PORTFOLIO_NOT_ACTIVE", "组合未激活")

    if from_platform == to_platform:
        raise BusinessError("SAME_PLATFORM", "转出平台和转入平台不能相同")
    if not db.query(Platform).filter(Platform.code == from_platform).first():
        raise NotFoundError("PLATFORM_NOT_FOUND", f"转出平台 {from_platform} 不存在")
    if not db.query(Platform).filter(Platform.code == to_platform).first():
        raise NotFoundError("PLATFORM_NOT_FOUND", f"转入平台 {to_platform} 不存在")

    if not is_trading_day(db, transfer_date):
        raise BusinessError("NON_TRADING_DAY", "非交易日，请等待交易日再提交")

    # 转移日必须晚于最新快照日
    latest_snapshot_date = get_latest_snapshot_date(db, portfolio_code)
    if latest_snapshot_date and transfer_date <= latest_snapshot_date:
        raise BusinessError(
            "DATE_BEFORE_SNAPSHOT",
            f"转移日必须晚于最新快照日（{latest_snapshot_date}）",
        )

    if Decimal(str(amount)) <= 0:
        raise BusinessError("INVALID_AMOUNT", "转移金额必须大于0")

    # 用户输入金额先量化到 2 位（四舍五入），再做精确比较（issue #94）
    amt = quantize_amount(amount)
    if amt <= 0:
        raise BusinessError("INVALID_AMOUNT", "转移金额必须大于0")

    available_cash = calculate_available_cash(
        db, portfolio_code, from_platform, as_of_date=transfer_date
    )
    if amt > available_cash:
        raise BusinessError(
            "INSUFFICIENT_CASH",
            f"平台 {from_platform} 的可用现金不足（当前: {float(available_cash)}）",
        )

    transfer_group = uuid.uuid4().hex[:12]

    sell_trade = Trade(
        portfolio_code=portfolio_code, platform_code=from_platform,
        product_code="CASH", market="", trade_type="sell",
        transfer_group=transfer_group, amount=amt, price=Decimal("1"),
        fee=Decimal("0"), actual_amount=amt, trade_date=transfer_date,
        status="pending", notes=notes or f"现金转移至 {to_platform}",
    )
    db.add(sell_trade)
    buy_trade = Trade(
        portfolio_code=portfolio_code, platform_code=to_platform,
        product_code="CASH", market="", trade_type="buy",
        transfer_group=transfer_group, amount=amt, price=Decimal("1"),
        fee=Decimal("0"), actual_amount=amt, trade_date=transfer_date,
        status="pending", notes=notes or f"现金从 {from_platform} 转入",
    )
    db.add(buy_trade)
    db.flush()

    if not cross_day:
        # 当天完成：两腿立即 confirm
        sell_trade.status = "confirmed"
        sell_trade.confirm_date = transfer_date
        buy_trade.status = "confirmed"
        buy_trade.confirm_date = transfer_date
    else:
        # #93: 跨天到账——转出方当日确认，转入方 pending
        next_trading_day = get_next_trading_day(db, transfer_date, days=1)
        sell_trade.status = "confirmed"  # 转出方当日确认（资金已划出）
        sell_trade.confirm_date = transfer_date
        buy_trade.status = "pending"     # 转入方 pending（待到账）
        buy_trade.confirm_date = next_trading_day

    db.flush()

    record_audit(
        db,
        action=ACTION_CREATE,
        resource_type=RESOURCE_CASH_TRANSFER,
        resource_id=transfer_group,
        resource_name=f"{portfolio_code}/{from_platform}→{to_platform}",
        new_value={
            "portfolio_code": portfolio_code,
            "from_platform": from_platform,
            "to_platform": to_platform,
            "amount": amt,
            "transfer_date": transfer_date,
            "cross_day": cross_day,
            "sell_status": sell_trade.status,
            "buy_status": buy_trade.status,
        },
    )

    return {
        "transfer_group": transfer_group,
        "from_platform": from_platform,
        "to_platform": to_platform,
        "amount": float(amt),
        "cross_day": cross_day,
        "sell_trade_id": sell_trade.id,
        "buy_trade_id": buy_trade.id,
        "sell_status": sell_trade.status,
        "buy_status": buy_trade.status,
        "transfer_date": transfer_date,
    }


def confirm_cash_transfer(
    db: Session,
    *,
    portfolio_code: str,
    transfer_group: str,
) -> dict:
    """确认跨天转移中所有仍为 pending 的 CASH legs（新模型下通常仅转入腿）。不 commit。"""
    pending_legs = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.transfer_group == transfer_group,
        Trade.product_code == "CASH",
        Trade.status == "pending",
    ).all()

    if not pending_legs:
        raise NotFoundError(
            "TRANSFER_NOT_FOUND", f"未找到待确认的转移记录 {transfer_group}"
        )

    confirm_date = pending_legs[0].confirm_date or get_next_trading_day(db, pending_legs[0].trade_date, days=1)
    if confirm_date > date.today():
        raise BusinessError(
            "TRANSFER_NOT_READY",
            f"跨天转移尚未到确认日期（预计确认日: {confirm_date}）",
        )

    for leg in pending_legs:
        leg.status = "confirmed"
        # 保留各腿自身的 confirm_date 语义（新模型下：sell=转出日，buy=到账日）

    record_audit(
        db,
        action=ACTION_CONFIRM,
        resource_type=RESOURCE_CASH_TRANSFER,
        resource_id=transfer_group,
        resource_name=f"{portfolio_code}/{transfer_group}",
        old_value={"pending_count": len(pending_legs)},
        new_value={"confirmed_count": len(pending_legs), "confirm_date": confirm_date},
    )

    return {
        "transfer_group": transfer_group,
        "confirmed_count": len(pending_legs),
        "confirm_date": confirm_date,
    }


def list_cash_transfers(db: Session, portfolio_code: str) -> List[dict]:
    """按 transfer_group 分组返回现金转移记录（未分页）。供 REST 与 CLI 共用。"""
    trades = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.transfer_group.isnot(None),
        Trade.product_code == "CASH",
    ).order_by(Trade.created_at.desc()).all()

    groups: dict = {}
    order: List[str] = []
    for t in trades:
        if t.transfer_group not in groups:
            groups[t.transfer_group] = {"sell": None, "buy": None}
            order.append(t.transfer_group)
        if t.trade_type == "sell":
            groups[t.transfer_group]["sell"] = t
        elif t.trade_type == "buy":
            groups[t.transfer_group]["buy"] = t

    items: List[dict] = []
    for tg in order:
        pair = groups[tg]
        sell = pair.get("sell")
        buy = pair.get("buy")
        if not sell or not buy:
            continue
        items.append({
            "transfer_group": tg,
            "from_platform": sell.platform_code or "",
            "to_platform": buy.platform_code or "",
            "amount": float(sell.amount or 0),
            # #93: 跨天判定按「buy 腿确认日 > 转移日」（sell 当日确认，buy 延后）；
            # #787: 与 buy 腿状态解耦——已取消的当天转移两腿 confirm_date=transfer_date，
            # 不再被误标「跨天到账」
            "cross_day": buy.confirm_date is not None and buy.confirm_date > sell.trade_date,
            "sell_status": sell.status,
            "buy_status": buy.status,
            "transfer_date": sell.trade_date,
            "sell_confirm_date": sell.confirm_date,
            "buy_confirm_date": buy.confirm_date,
            "notes": sell.notes,
        })
    return items


def cancel_cash_transfer(
    db: Session,
    *,
    portfolio_code: str,
    transfer_group: str,
) -> dict:
    """撤销平台间现金转移（#788）：两腿逻辑置 cancelled，保留 trade 行 + 审计。不 commit。

    生命周期总纲「confirmed 不可直接 cancel」的转移组例外（business-constraints.md
    生命周期章）：转错平台/金额填错/重复转移时的正式撤销通道，逻辑取消而非物理删除。

    - 已取消组幂等拒绝（TRANSFER_ALREADY_CANCELLED，零写入）；
    - 两腿状态混合（一条已 cancelled）保守拒绝（INVALID_STATUS），不猜语义；
    - 组级快照保护（与 cancel_trade 同口径）：组内非 cancelled 腿确认日及之后
      已有快照 → SNAPSHOT_DEPENDENCY；
    - 转入平台现金守卫（#788 决策：阻断）：买入腿 confirmed（资金已入账）时，
      撤销会把转移金额从转入平台可用现金减回，不足则 INSUFFICIENT_CASH 拒绝，
      避免负余额；买入腿 pending（跨天在途未入账）时无此守卫。
    """
    if transfer_group.startswith(("rebal_", "sub_")):
        raise NotFoundError(
            "TRANSFER_NOT_FOUND",
            f"{transfer_group} 不是平台间现金转移组"
            "（调仓/申赎组请走对应生命周期操作）",
        )

    # 行锁后校验（与 routers/trades.py 生命周期端点同口径，#788 评审）：
    # 并发双撤销时第二笔在锁上读到 cancelled → 走 TRANSFER_ALREADY_CANCELLED 幂等拒绝，
    # 不会重复写状态或落第二条审计。populate_existing 确保拿到锁定后的最新值。
    legs = db.query(Trade).filter(
        Trade.portfolio_code == portfolio_code,
        Trade.transfer_group == transfer_group,
        Trade.product_code == "CASH",
    ).populate_existing().order_by(Trade.id).with_for_update().all()

    if len(legs) != 2:
        raise NotFoundError("TRANSFER_NOT_FOUND", f"未找到转移记录 {transfer_group}")

    sell = next((leg for leg in legs if leg.trade_type == "sell"), None)
    buy = next((leg for leg in legs if leg.trade_type == "buy"), None)
    if sell is None or buy is None:
        raise NotFoundError(
            "TRANSFER_NOT_FOUND", f"转移组 {transfer_group} 两腿结构异常，无法撤销"
        )

    if sell.status == "cancelled" and buy.status == "cancelled":
        raise BusinessError("TRANSFER_ALREADY_CANCELLED", "该转移已撤销，请勿重复操作")
    if sell.status == "cancelled" or buy.status == "cancelled":
        raise BusinessError(
            "INVALID_STATUS",
            f"转移组 {transfer_group} 两腿状态不一致"
            f"（sell={sell.status}, buy={buy.status}），请先处理异常腿后再撤销",
        )

    # 组级快照保护（#493 决策 4 同口径）：组内非 cancelled 腿确认日及之后有快照即拒绝
    validate_group_snapshot_free(db, sell)

    # 转入平台现金守卫：买入腿已入账时，撤销后其金额从转入平台可用现金减回
    amt = quantize_amount(sell.amount or 0)
    if buy.status == "confirmed" and buy.platform_code:
        available = calculate_available_cash(db, portfolio_code, buy.platform_code)
        if amt > available:
            raise BusinessError(
                "INSUFFICIENT_CASH",
                f"转入平台 {buy.platform_code} 的可用现金不足"
                f"（当前 {float(available)}，撤销需减回 {float(amt)}），"
                "请先处理该平台的持仓/现金后再撤销",
                details={
                    "platform_code": buy.platform_code,
                    "required": float(amt),
                    "available": float(available),
                    "deficit": float(amt - available),
                },
            )

    old_status = {"sell_status": sell.status, "buy_status": buy.status}
    sell.status = "cancelled"
    buy.status = "cancelled"

    record_audit(
        db,
        action=ACTION_CANCEL,
        resource_type=RESOURCE_CASH_TRANSFER,
        resource_id=transfer_group,
        resource_name=f"{portfolio_code}/{sell.platform_code}→{buy.platform_code}",
        old_value=old_status,
        new_value={
            "sell_status": "cancelled",
            "buy_status": "cancelled",
            "amount": amt,  # 审计载荷不经 float()（code-review §Q1 / #428 #421）
        },
    )

    return {
        "transfer_group": transfer_group,
        "from_platform": sell.platform_code,
        "to_platform": buy.platform_code,
        "amount": float(amt),
        "sell_status": sell.status,
        "buy_status": buy.status,
        "transfer_date": sell.trade_date,
    }
