"""
申购赎回确认服务

将申购赎回确认/取消确认的核心业务逻辑从路由层提取，
供 HTTP API、CLI、快照重算自动确认等多处复用。
"""
import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.subscription import Subscription
from app.models.portfolio import Portfolio
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.trade import Trade
from app.models.investor import Investor
from app.models.platform import Platform
from app.services.trading_utils import (
    get_next_trading_day,
    is_trading_day,
    get_latest_snapshot_date,
)
from app.services.position_service import (
    calculate_available_cash,
    calculate_investor_available_shares,
)
from app.services.exceptions import BusinessError, NotFoundError
from app.services.audit_service import record_audit, _diff_fields
from app.constants.audit_actions import (
    ACTION_CREATE, ACTION_UPDATE, ACTION_CONFIRM, ACTION_UNCONFIRM,
    ACTION_CANCEL, ACTION_DELETE,
    RESOURCE_SUBSCRIPTION,
)
from app.utils.quantize import quantize_amount, quantize_shares

logger = logging.getLogger(__name__)


def _as_date(value):
    """归一为 date：portfolio.started_at 是 DateTime 列但承载日期语义，
    驱动读回类型不稳定（date/datetime），与 confirm_date 比较前先归一。"""
    return value.date() if hasattr(value, "date") and callable(value.date) else value


class NavNotAvailableError(BusinessError):
    """申请日组合快照不存在时抛出（映射 NAV_NOT_AVAILABLE）"""

    def __init__(self, portfolio_code: str, apply_date: date):
        self.portfolio_code = portfolio_code
        self.apply_date = apply_date
        super().__init__(
            "NAV_NOT_AVAILABLE",
            f"申请日 {apply_date} 的组合 {portfolio_code} 净值快照不存在，请先生成快照",
        )


class InvalidStatusError(BusinessError):
    """状态不符合要求时抛出（映射 INVALID_STATUS）"""

    def __init__(self, message: str):
        super().__init__("INVALID_STATUS", message)


def calculate_subscription_confirm_preview(
    db: Session,
    subscription: Subscription,
) -> dict:
    """
    计算申赎确认结果（纯计算，不落库），供确认前预览与真实确认共用。

    只做查询与计算：**不修改 subscription 对象、不创建配对 CASH trade、
    不 flush/commit**。confirm_single_subscription 调用本函数取结果后回写，
    保证「预览 == 真实确认」（与 trade_service.calculate_confirm_preview 同模式）。

    计算规则（与确认语义完全一致）：
    - 确认日 = 申请日的下一个交易日（T+1）
    - 净值三级决策：申请日组合快照净值 / 初始窗口 1.0000 / 抛 NAV_NOT_AVAILABLE
    - 申购 shares = quantize(amount / nav)；赎回 amount = quantize(shares × nav)

    Args:
        db: 数据库会话
        subscription: 待确认的申购/赎回记录（必须为 pending 状态）

    Returns:
        dict，keys：
        - nav: Decimal，确认将写入的净值
        - shares: Decimal，确认后的份额（申购为计算值，赎回为原值）
        - amount: Decimal，确认后的金额（申购为原值，赎回为计算值）
        - confirm_date: date，T+1 交易日
        - is_first: bool，是否组合首笔确认申购
        - portfolio: Portfolio，组合 ORM 对象（仅供 confirm_single_subscription
          复用、避免重复查询；router 层剔除，不进 API 响应）

    Raises:
        InvalidStatusError: 状态不是 pending
        NavNotAvailableError: 申请日快照不存在
        BusinessError: CONFIRM_BEFORE_STARTED——申购确认日早于组合首笔到账日（issue #179 硬闸门）
    """
    if subscription.status != "pending":
        raise InvalidStatusError("仅 pending 状态可确认")

    # 1. 确认日期：T+1（申请日的下一个交易日）
    confirm_date = get_next_trading_day(db, subscription.apply_date, days=1)

    # 2. 判断是否为首次申购
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.code == subscription.portfolio_code)
        .first()
    )

    # 2.5 乱序补录硬闸门（issue #179）：申购确认日不得早于组合首笔到账日。
    # #180 不变量保证 started_at = 现存最小 confirm_date，闸门封死「后来者把
    # 到账日插到已按 1.0 定价申购的申请日之前」的污染空间。
    # confirm_date == started_at 放行（同日多平台申购生命线）；started_at 为空豁免。
    if subscription.sub_type == "subscribe" and portfolio and portfolio.started_at:
        if confirm_date < _as_date(portfolio.started_at):
            raise BusinessError(
                "CONFIRM_BEFORE_STARTED",
                f"申购确认日 {confirm_date} 早于组合首笔到账日 {portfolio.started_at}，"
                f"如需补录更早的申购，请先取消确认该首笔申购后依序重录",
            )

    is_first = (
        db.query(Subscription)
        .filter(
            Subscription.portfolio_code == subscription.portfolio_code,
            Subscription.sub_type == "subscribe",
            Subscription.status == "confirmed",
        )
        .count()
        == 0
    )

    # 3. 净值三级决策（issue #179）
    snapshot = (
        db.query(PortfolioValueSnapshot)
        .filter(
            PortfolioValueSnapshot.portfolio_code == subscription.portfolio_code,
            PortfolioValueSnapshot.snapshot_date == subscription.apply_date,
        )
        .first()
    )
    if snapshot:
        nav = Decimal(str(snapshot.unit_price))
    else:
        # 初始窗口例外：申购是组合现金的唯一来源（CASH trade 受 CASH_TRADE_FORBIDDEN
        # 限制、现金转移只搬运存量、事件需先有持仓），故「不存在 confirm_date <=
        # apply_date 的 confirmed 申购」⟺ 申请日零持仓 ⟺ 净值结构性恒为 1.0000。
        # 动态计算，天然覆盖任意 confirm/unconfirm 顺序；赎回分支自然免疫
        # （有可用份额必存在更早到账的申购，此查询必然命中）。
        cash_arrived = (
            db.query(Subscription.id)
            .filter(
                Subscription.portfolio_code == subscription.portfolio_code,
                Subscription.sub_type == "subscribe",
                Subscription.status == "confirmed",
                Subscription.confirm_date <= subscription.apply_date,
            )
            .first()
        )
        if cash_arrived:
            raise NavNotAvailableError(
                subscription.portfolio_code, subscription.apply_date
            )
        nav = Decimal("1.0000")

    # 4. 计算份额/金额
    if subscription.sub_type == "subscribe":
        # 确认份额量化到 2 位（四舍五入）；金额为创建时已量化的用户输入（issue #94）
        shares = quantize_shares(Decimal(str(subscription.amount)) / nav)
        amount = Decimal(str(subscription.amount))
    else:
        # 赎回金额量化到 2 位（issue #94）：shares(2位) × nav(4位) 四舍五入，
        # 误差计入基金财产，现金划出与平台 2 位口径一致
        shares = Decimal(str(subscription.shares))
        amount = quantize_amount(Decimal(str(subscription.shares)) * nav)

    return {
        "nav": nav,
        "shares": shares,
        "amount": amount,
        "confirm_date": confirm_date,
        "is_first": is_first,
        "portfolio": portfolio,
    }


def confirm_single_subscription(
    db: Session,
    subscription: Subscription,
    *,
    auto_flush: bool = False,
    skip_cash_check: bool = False,
) -> Subscription:
    """
    确认单笔申购/赎回的核心逻辑。

    - 确认日期由后端自动计算（T+1）
    - 净值自动确定（首次申购固定1.0000，否则取申请日组合快照净值）
    - 首次申购确认后自动激活组合状态

    计算全部经 calculate_subscription_confirm_preview（与预览端点共用实现）。

    Args:
        db: 数据库会话
        subscription: 待确认的申购/赎回记录（必须为 pending 状态）
        auto_flush: 是否在结束时自动 flush（默认为 False，由调用者控制事务）
        skip_cash_check: 跳过赎回现金充足性校验（auto_confirm 重算历史场景专用，
            对齐调仓确认 skip_available_check 口径；重算按日序重放，当日现金
            流入交易可能尚未重确认，实时校验会误杀）

    Returns:
        确认后的 subscription 对象

    Raises:
        InvalidStatusError: 状态不是 pending
        NavNotAvailableError: 申请日快照不存在
        BusinessError: CONFIRM_BEFORE_STARTED——申购确认日早于组合首笔到账日（issue #179 硬闸门）；
            INSUFFICIENT_CASH——赎回确认时平台可用现金不足以支付赎回金额（issue #203 消费点校验）
    """
    preview = calculate_subscription_confirm_preview(db, subscription)
    nav = preview["nav"]
    confirm_date = preview["confirm_date"]
    is_first = preview["is_first"]
    # 组合对象复用预览内的同条件查询结果，不再重复 SELECT（#257 评审）
    portfolio = preview["portfolio"]

    # 赎回现金充足性校验（issue #203 消费点）：确认生成的配对 CASH sell 腿扣减
    # 平台现金，确认日时点可用现金不足即拒绝，从源头阻断负现金快照；
    # 金额与可用现金均 2 位口径精确比较（无容差，同调仓买入口径）
    if subscription.sub_type == "redeem" and not skip_cash_check:
        amount_d = quantize_amount(Decimal(str(preview["amount"])))
        available = calculate_available_cash(
            db, subscription.portfolio_code, subscription.platform_code,
            as_of_date=confirm_date,
        )
        if amount_d > available:
            raise BusinessError(
                "INSUFFICIENT_CASH",
                f"平台 {subscription.platform_code} 可用现金不足"
                f"（需 {amount_d}，可用 {available}），无法确认赎回",
                details={
                    "deficit": str(amount_d - available),
                    "required": str(amount_d),
                    "available": str(available),
                },
            )

    # 4. 回写份额/金额
    subscription.unit_price = nav
    if subscription.sub_type == "subscribe":
        subscription.shares = preview["shares"]
    else:
        subscription.amount = preview["amount"]

    # 5. 设置确认状态
    subscription.status = "confirmed"
    subscription.confirm_date = confirm_date

    # 5.5 生成配对 CASH trade（显式记录现金变动）
    cash_trade = Trade(
        portfolio_code=subscription.portfolio_code,
        platform_code=subscription.platform_code,
        product_code="CASH",
        market="",
        trade_type="buy" if subscription.sub_type == "subscribe" else "sell",
        amount=Decimal(str(subscription.amount)),
        price=Decimal("1"),
        fee=Decimal("0"),
        actual_amount=Decimal(str(subscription.amount)),
        trade_date=subscription.apply_date,
        confirm_date=confirm_date,
        status="confirmed",
        transfer_group=f"sub_{subscription.id}",
    )
    db.add(cash_trade)

    # 6. 首次申购激活组合（激活轮次与 started_at 是两个正交概念，issue #180）
    if is_first and subscription.sub_type == "subscribe" and portfolio and portfolio.status == "draft":
        portfolio.status = "active"
    # started_at = 现存最小 confirm_date（到账事实）：写入条件放宽为 started_at is
    # None（reactivate 空组合后新首购不漏设）；闸门保证后续申购 confirm_date >=
    # started_at，故首次写入的必然是最小值
    if subscription.sub_type == "subscribe" and portfolio and portfolio.started_at is None:
        portfolio.started_at = confirm_date

    if auto_flush:
        db.flush()

    logger.info(
        f"申购确认: id={subscription.id}, type={subscription.sub_type}, "
        f"nav={nav}, confirm_date={confirm_date}"
    )

    record_audit(
        db,
        action=ACTION_CONFIRM,
        resource_type=RESOURCE_SUBSCRIPTION,
        resource_id=str(subscription.id),
        resource_name=f"{subscription.portfolio_code}/{subscription.investor_code}/{subscription.sub_type}",
        old_value={"status": "pending"},
        new_value={
            "status": "confirmed",
            "unit_price": nav,
            "shares": subscription.shares,
            "amount": subscription.amount,
            "confirm_date": confirm_date,
            "cash_transfer_group": f"sub_{subscription.id}",
        },
    )

    return subscription


def unconfirm_single_subscription(
    db: Session,
    subscription: Subscription,
    *,
    check_snapshot: bool = True,
    auto_flush: bool = False,
) -> Subscription:
    """
    取消确认单笔申购/赎回的核心逻辑。

    将状态从 confirmed 回退至 pending，清空确认相关字段。

    Args:
        db: 数据库会话
        subscription: 待取消确认的记录（必须为 confirmed 状态）
        check_snapshot: 是否执行快照保护（默认 True）。用户触发的 unconfirm
            应保持 True；快照删除级联回退（_cascade_unconfirm_subscriptions）
            须显式传 False——此时快照正在被删除，不应被本检查阻断。
        auto_flush: 是否在结束时自动 flush

    Returns:
        回退后的 subscription 对象

    Raises:
        InvalidStatusError: 状态不是 confirmed
        BusinessError: 确认日及之后已有快照（SNAPSHOT_DEPENDENCY）
    """
    if subscription.status != "confirmed":
        raise InvalidStatusError("仅 confirmed 状态可取消确认")

    # 快照保护：确认日及之后已有快照则拒绝（级联删除快照时 check_snapshot=False 跳过）
    if check_snapshot and subscription.confirm_date:
        snapshots_after = (
            db.query(PortfolioValueSnapshot)
            .filter(
                PortfolioValueSnapshot.portfolio_code == subscription.portfolio_code,
                PortfolioValueSnapshot.snapshot_date >= subscription.confirm_date,
            )
            .count()
        )
        if snapshots_after > 0:
            raise BusinessError(
                "SNAPSHOT_DEPENDENCY",
                f"该申赎已被快照纳入（{subscription.confirm_date} 及之后有 {snapshots_after} 张快照），"
                f"请先删除 {subscription.confirm_date} 及之后的快照",
            )

    # 负现金防护（issue #203 重构）：原 #180 在此拦截申购 unconfirm 的守卫已移除——
    # 该守卫会阻断快照删除级联回退、且无法覆盖其他现金消耗路径。现金充足性改由
    # 两处保证：①赎回确认消费点校验（INSUFFICIENT_CASH）②快照生成阻断（NEGATIVE_CASH）。

    old_unit_price = subscription.unit_price
    old_confirm_date = subscription.confirm_date

    subscription.status = "pending"
    # 重算期望确认日（申赎恒 T+1）而非置 None，保持 pending 记录 confirm_date 非空，
    # 避免快照校验 confirm_date <= target 因 SQL NULL 比较漏检（与 unconfirm_trade 对齐）
    subscription.confirm_date = get_next_trading_day(db, subscription.apply_date, days=1)
    subscription.unit_price = None
    # 申购时 shares 由确认计算得出，回退后应清空让重新确认时再算
    if subscription.sub_type == "subscribe":
        subscription.shares = None
    else:
        # 赎回时 amount 由确认计算得出，回退后应清空
        subscription.amount = None

    # 物理删除配对 CASH trade（派生记录，生命周期跟随 subscription）
    db.query(Trade).filter(
        Trade.transfer_group == f"sub_{subscription.id}"
    ).delete(synchronize_session=False)

    # 生产 session 为 autoflush=False（app/database.py）：上面的 status/confirm_date
    # 回退仍在 ORM 内存态，下面的 min 聚合直接走 SQL 会把本条仍算作 confirmed
    # （级联循环中前序兄弟记录同理），导致 started_at 重算脏读——查询前显式 flush。
    db.flush()

    # started_at 重算（issue #180）：started_at = 现存 confirmed 申购的最小
    # confirm_date（到账事实），无则 NULL。只查申购即可：「无 confirmed 申购 ⟹
    # 无 confirmed 赎回」（赎回需快照→持仓→申购，快照保护阻断反向删除）。
    # 状态回退仅 active→draft（closed 是用户意图态，级联删快照是数据修复副作用）。
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.code == subscription.portfolio_code)
        .first()
    )
    if portfolio:
        min_confirm = (
            db.query(func.min(Subscription.confirm_date))
            .filter(
                Subscription.portfolio_code == subscription.portfolio_code,
                Subscription.sub_type == "subscribe",
                Subscription.status == "confirmed",
            )
            .scalar()
        )
        portfolio.started_at = min_confirm
        if min_confirm is None and portfolio.status == "active":
            portfolio.status = "draft"

    if auto_flush:
        db.flush()

    logger.info(f"取消确认: id={subscription.id}")

    record_audit(
        db,
        action=ACTION_UNCONFIRM,
        resource_type=RESOURCE_SUBSCRIPTION,
        resource_id=str(subscription.id),
        resource_name=f"{subscription.portfolio_code}/{subscription.investor_code}/{subscription.sub_type}",
        old_value={"status": "confirmed", "unit_price": old_unit_price, "confirm_date": old_confirm_date},
        new_value={"status": "pending", "confirm_date": subscription.confirm_date},
    )

    return subscription


def create_subscription(
    db: Session,
    *,
    portfolio_code: str,
    investor_code: str,
    platform_code: str,
    sub_type: str,
    apply_date: date,
    amount: Optional[Decimal] = None,
    shares: Optional[Decimal] = None,
    notes: Optional[str] = None,
) -> Subscription:
    """创建申购/赎回（含全部校验），供 REST 与 CLI 共用。

    创建时即设定 confirm_date=T+1（与 REST 口径一致）。不 commit。
    """
    if not is_trading_day(db, apply_date):
        raise BusinessError("NON_TRADING_DAY", "非交易日，请等待交易日再提交")

    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", "组合不存在")
    # 首次申购时组合状态为 draft，确认后变为 active
    if portfolio.status not in ("active", "draft"):
        raise BusinessError("PORTFOLIO_NOT_ACTIVE", "组合未激活")

    # 申请日必须晚于最新快照日
    latest_snapshot_date = get_latest_snapshot_date(db, portfolio_code)
    if latest_snapshot_date and apply_date <= latest_snapshot_date:
        raise BusinessError(
            "DATE_BEFORE_SNAPSHOT",
            f"申请日必须晚于最新快照日（{latest_snapshot_date}）",
        )

    investor = db.query(Investor).filter(Investor.code == investor_code).first()
    if not investor:
        raise NotFoundError("NOT_FOUND", "投资人不存在")

    platform = db.query(Platform).filter(Platform.code == platform_code).first()
    if not platform:
        raise NotFoundError("PLATFORM_NOT_FOUND", f"平台 {platform_code} 不存在")

    confirm_date = get_next_trading_day(db, apply_date, days=1)

    if sub_type == "subscribe":
        if amount is None or Decimal(str(amount)) <= 0:
            raise BusinessError("INVALID_AMOUNT", "申购金额必须大于0")
        # 用户输入金额先量化到 2 位（四舍五入）（issue #94）
        amount_d = quantize_amount(Decimal(str(amount)))
        if amount_d <= 0:
            raise BusinessError("INVALID_AMOUNT", "申购金额必须大于0")
        new_sub = Subscription(
            portfolio_code=portfolio_code, investor_code=investor_code,
            platform_code=platform_code, sub_type="subscribe",
            amount=amount_d, apply_date=apply_date,
            confirm_date=confirm_date, status="pending", notes=notes,
        )
    elif sub_type == "redeem":
        if shares is None or Decimal(str(shares)) <= 0:
            raise BusinessError("INVALID_SHARES", "赎回份额必须大于0")
        # 用户输入份额先量化到 2 位（四舍五入），再做精确比较
        shares_d = quantize_shares(Decimal(str(shares)))
        if shares_d <= 0:
            raise BusinessError("INVALID_SHARES", "赎回份额必须大于0")
        available = calculate_investor_available_shares(
            db, portfolio_code, investor_code, as_of_date=apply_date
        )
        if shares_d > available:
            raise BusinessError("INSUFFICIENT_SHARES", "赎回份额超过可用份额")
        new_sub = Subscription(
            portfolio_code=portfolio_code, investor_code=investor_code,
            platform_code=platform_code, sub_type="redeem",
            shares=shares_d, apply_date=apply_date,
            confirm_date=confirm_date, status="pending", notes=notes,
        )
    else:
        raise BusinessError("INVALID_TYPE", "类型必须为 subscribe 或 redeem")

    db.add(new_sub)
    db.flush()

    record_audit(
        db,
        action=ACTION_CREATE,
        resource_type=RESOURCE_SUBSCRIPTION,
        resource_id=str(new_sub.id),
        resource_name=f"{portfolio_code}/{investor_code}/{sub_type}",
        new_value={
            "portfolio_code": portfolio_code,
            "investor_code": investor_code,
            "platform_code": platform_code,
            "sub_type": sub_type,
            "apply_date": apply_date,
            "amount": new_sub.amount,
            "shares": new_sub.shares,
            "confirm_date": confirm_date,
            "status": "pending",
        },
    )

    return new_sub


def update_subscription(
    db: Session,
    subscription: Subscription,
    updates: dict,
) -> Subscription:
    """更新申赎（仅 pending 可改，confirmed/cancelled 均拒绝），供 REST 与 CLI 共用。不 commit。

    updates 为 SubscriptionUpdate.dict(exclude_unset=True)。校验与创建同口径：
    - 状态收口：confirmed 拒绝 CANNOT_MODIFY_CONFIRMED（状态流转走
      confirm/cancel/unconfirm）；cancelled 拒绝 INVALID_STATUS（终态不可复活改值，
      与 update_trade 同口径）
    - 显式 null 收口（PR #204 评审）：除 notes（null=清除备注）外，字段显式
      传 null 拒绝 INVALID_PARAM，防止绕过量化/可用份额闸门并落库脏数据
    - 类型拆分（与创建同口径）：subscribe 仅接受 amount、redeem 仅接受 shares，
      错位字段拒绝 INVALID_PARAM
    - apply_date：交易日 + 晚于最新快照日，并重算预计确认日（T+1）
    - amount（申购）/shares（赎回）：先量化再校验大于 0
    - 赎回份额闸门：新份额（或仅改日期时的原份额）不得超过可用份额，
      可用份额已扣本条自身 pending 旧份额，先加回再比较（与 trade PUT 同口径）
    - platform_code：平台必须存在
    """
    if subscription.status == "confirmed":
        raise BusinessError(
            "CANNOT_MODIFY_CONFIRMED",
            "已确认的申购赎回事件不可直接修改，请先取消确认后再修改",
        )
    if subscription.status == "cancelled":
        raise InvalidStatusError("已取消的申赎不可修改")

    # 显式 null 收口：exclude_unset 不含 exclude_none，null 会穿透量化/闸门
    # 校验经 setattr 落库脏数据；notes 例外（null 用于清除备注）
    null_fields = [f for f, v in updates.items() if f != "notes" and v is None]
    if null_fields:
        raise BusinessError(
            "INVALID_PARAM",
            f"字段不可为空: {', '.join(sorted(null_fields))}",
        )

    # 与创建同口径：字段按申赎类型收口，防止语义不一致记录
    if subscription.sub_type == "subscribe" and "shares" in updates:
        raise BusinessError("INVALID_PARAM", "申购仅可修改金额，不可修改份额")
    if subscription.sub_type == "redeem" and "amount" in updates:
        raise BusinessError("INVALID_PARAM", "赎回仅可修改份额，不可修改金额")

    apply_date = subscription.apply_date

    if updates.get("platform_code") is not None:
        platform = db.query(Platform).filter(Platform.code == updates["platform_code"]).first()
        if not platform:
            raise NotFoundError("PLATFORM_NOT_FOUND", f"平台 {updates['platform_code']} 不存在")

    if updates.get("apply_date") is not None:
        apply_date = updates["apply_date"]
        if not is_trading_day(db, apply_date):
            raise BusinessError("NON_TRADING_DAY", "非交易日，请等待交易日再提交")
        latest_snapshot_date = get_latest_snapshot_date(db, subscription.portfolio_code)
        if latest_snapshot_date and apply_date <= latest_snapshot_date:
            raise BusinessError(
                "DATE_BEFORE_SNAPSHOT",
                f"申请日必须晚于最新快照日（{latest_snapshot_date}）",
            )
        updates["confirm_date"] = get_next_trading_day(db, apply_date, days=1)

    if updates.get("amount") is not None:
        amount_d = quantize_amount(Decimal(str(updates["amount"])))
        if amount_d <= 0:
            raise BusinessError("INVALID_AMOUNT", "申购金额必须大于0")
        updates["amount"] = amount_d

    if updates.get("shares") is not None:
        shares_d = quantize_shares(Decimal(str(updates["shares"])))
        if shares_d <= 0:
            raise BusinessError("INVALID_SHARES", "赎回份额必须大于0")
        updates["shares"] = shares_d

    if subscription.sub_type == "redeem" and ("shares" in updates or "apply_date" in updates):
        new_shares = updates.get("shares", subscription.shares)
        if new_shares is not None:
            available = calculate_investor_available_shares(
                db, subscription.portfolio_code, subscription.investor_code,
                as_of_date=apply_date,
            )
            # 加回本条自身 pending 旧份额（可用份额计算已将其扣除）
            if subscription.status == "pending" and subscription.shares:
                available += Decimal(str(subscription.shares))
            if Decimal(str(new_shares)) > available:
                raise BusinessError("INSUFFICIENT_SHARES", "赎回份额超过可用份额")

    old_values, new_values = _diff_fields(subscription, updates)

    for field, value in updates.items():
        setattr(subscription, field, value)

    # 无实际变更不留痕（同 share_change_event_service 与「空删除不留痕」口径）
    if old_values or new_values:
        record_audit(
            db,
            action=ACTION_UPDATE,
            resource_type=RESOURCE_SUBSCRIPTION,
            resource_id=str(subscription.id),
            resource_name=f"{subscription.portfolio_code}/{subscription.investor_code}/{subscription.sub_type}",
            old_value=old_values or None,
            new_value=new_values or None,
        )

    return subscription


def cancel_subscription(db: Session, subscription: Subscription) -> Subscription:
    """取消申赎（仅 pending 可取消）。不 commit。"""
    if subscription.status != "pending":
        raise BusinessError("INVALID_STATUS", "仅 pending 状态可取消")

    subscription.status = "cancelled"

    record_audit(
        db,
        action=ACTION_CANCEL,
        resource_type=RESOURCE_SUBSCRIPTION,
        resource_id=str(subscription.id),
        resource_name=f"{subscription.portfolio_code}/{subscription.investor_code}/{subscription.sub_type}",
        old_value={"status": "pending"},
        new_value={"status": "cancelled"},
    )

    return subscription


def delete_subscription(db: Session, subscription: Subscription) -> None:
    """删除申赎（confirmed 不可直接删除）。不 commit。"""
    if subscription.status == "confirmed":
        raise BusinessError(
            "CANNOT_DELETE_CONFIRMED",
            "已确认的申购赎回事件不可直接删除，请先取消确认后再删除",
        )

    record_audit(
        db,
        action=ACTION_DELETE,
        resource_type=RESOURCE_SUBSCRIPTION,
        resource_id=str(subscription.id),
        resource_name=f"{subscription.portfolio_code}/{subscription.investor_code}/{subscription.sub_type}",
        old_value={
            "status": subscription.status,
            "sub_type": subscription.sub_type,
            "apply_date": subscription.apply_date,
            "amount": subscription.amount,
            "shares": subscription.shares,
        },
    )

    db.delete(subscription)


def list_subscriptions(
    db: Session,
    *,
    portfolio_code: Optional[str] = None,
    investor_code: Optional[str] = None,
    status: Optional[str] = None,
    sub_type: Optional[str] = None,
    platform_code: Optional[str] = None,
    apply_date_start: Optional[date] = None,
    apply_date_end: Optional[date] = None,
    confirm_date_start: Optional[date] = None,
    confirm_date_end: Optional[date] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Subscription], int]:
    """申购赎回列表查询（服务端筛选 + 分页，issue #125）。

    - 日期区间均为闭区间；同组 start > end 抛 INVALID_DATE_RANGE（422）。
    - pending 记录的 confirm_date 为**预计确认日**（创建时按 T+1 设定、unconfirm
      时重算保持非空），确认日期区间筛选对 pending 命中预计值。
    - 排序：apply_date DESC, id DESC（同日期内新记录在前）。
    - viewer 限制由 router 叠加（非 admin 强制 investor_code=current_user.code），
      本函数不感知权限。

    Returns:
        (items, total)：当前页记录与过滤后总数（分页前）。
    """
    if apply_date_start and apply_date_end and apply_date_start > apply_date_end:
        raise BusinessError(
            "INVALID_DATE_RANGE",
            f"start_date ({apply_date_start}) 不能晚于 end_date ({apply_date_end})",
            http_status=422,
        )
    if confirm_date_start and confirm_date_end and confirm_date_start > confirm_date_end:
        raise BusinessError(
            "INVALID_DATE_RANGE",
            f"start_date ({confirm_date_start}) 不能晚于 end_date ({confirm_date_end})",
            http_status=422,
        )

    query = db.query(Subscription)
    if portfolio_code:
        query = query.filter(Subscription.portfolio_code == portfolio_code)
    if investor_code:
        query = query.filter(Subscription.investor_code == investor_code)
    if status:
        query = query.filter(Subscription.status == status)
    if sub_type:
        query = query.filter(Subscription.sub_type == sub_type)
    if platform_code:
        query = query.filter(Subscription.platform_code == platform_code)
    if apply_date_start:
        query = query.filter(Subscription.apply_date >= apply_date_start)
    if apply_date_end:
        query = query.filter(Subscription.apply_date <= apply_date_end)
    if confirm_date_start:
        query = query.filter(Subscription.confirm_date >= confirm_date_start)
    if confirm_date_end:
        query = query.filter(Subscription.confirm_date <= confirm_date_end)

    total = query.count()
    items = (
        query.order_by(Subscription.apply_date.desc(), Subscription.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total
