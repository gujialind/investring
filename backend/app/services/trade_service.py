"""
调仓交易确认服务

将 Trade 确认的核心业务逻辑（净值获取、份额/金额重算、配对 CASH 腿同步）
从路由层提取，供 HTTP API 手动确认与快照重算 auto_confirm 多处复用。
"""
import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.trade import Trade
from app.models.product import Product
from app.models.price_record import PriceRecord
from app.models.portfolio import Portfolio
from app.models.platform import Platform
from app.services.trading_utils import get_next_trading_day, is_trading_day, get_latest_snapshot_date
from app.services.position_service import calculate_available_cash, calculate_available_shares
from app.services.product_service import resolve_product_market
from app.services.exceptions import BusinessError, NotFoundError
from app.services.audit_service import record_audit
from app.constants.audit_actions import (
    ACTION_CREATE, ACTION_UPDATE, ACTION_CONFIRM, ACTION_UNCONFIRM,
    ACTION_CANCEL, ACTION_DELETE,
    RESOURCE_TRADE,
)
from app.utils.quantize import quantize_amount, quantize_nav, quantize_shares

logger = logging.getLogger(__name__)


def validate_trade_date(db: Session, portfolio_code: str, trade_date: date) -> None:
    """交易日与快照日校验（#182 从 create_trade 抽取，创建/编辑共用）。

    纯查询、不触碰 ORM，供「先校验后写入」路径复用：
    - 非交易日 -> NON_TRADING_DAY（编辑路径废除静默滚交易日，D4）
    - trade_date <= 最新快照日 -> DATE_BEFORE_SNAPSHOT
    """
    if not is_trading_day(db, trade_date):
        raise BusinessError("NON_TRADING_DAY", "非交易日，请等待交易日再提交")
    latest_snapshot_date = get_latest_snapshot_date(db, portfolio_code)
    if latest_snapshot_date and trade_date <= latest_snapshot_date:
        raise BusinessError(
            "DATE_BEFORE_SNAPSHOT",
            f"交易日必须晚于最新快照日（{latest_snapshot_date}）",
        )


def _is_rebal_group_cash_leg(trade: Trade) -> bool:
    """是否为调仓组（`rebal_`）的配对 CASH 腿（#493）。

    现金腿只能由基金腿驱动；该判据以**调仓组为边界**，不动申赎（`sub_`）与
    独立跨平台现金转移（裸 uuid）的生命周期。
    """
    return (
        trade is not None
        and trade.product_code == "CASH"
        and bool(trade.transfer_group)
        and trade.transfer_group.startswith("rebal_")
    )


def _group_legs(db: Session, trade: Trade) -> list:
    """组内全部腿（含自身）；无组号时退化为 [trade]（#493 组级口径的基础读取）。"""
    if trade is None:
        return []
    if not trade.transfer_group:
        return [trade]
    return db.query(Trade).filter(
        Trade.transfer_group == trade.transfer_group
    ).all()


def _paired_cash_leg(db: Session, trade: Trade) -> Optional[Trade]:
    """基金腿的反向配对 CASH 腿（#493：不要求两腿同状态）。"""
    if not trade or not trade.transfer_group or trade.product_code == "CASH":
        return None
    expected_type = "sell" if trade.trade_type == "buy" else "buy"
    return db.query(Trade).filter(
        Trade.transfer_group == trade.transfer_group,
        Trade.product_code == "CASH",
        Trade.trade_type == expected_type,
    ).first()


def _own_cash_sell_legs(db: Session, trade: Trade) -> list:
    """自身组内 CASH sell 腿（买入扣款腿，#91；#493 起含 confirmed）。

    买入扣款腿创建即 confirmed，故加回口径必须覆盖 pending/confirmed 两态；
    cancelled 腿不参与（金额已回退，不再占用可用现金）。
    """
    if not trade or not trade.transfer_group:
        return []
    return db.query(Trade).filter(
        Trade.transfer_group == trade.transfer_group,
        Trade.product_code == "CASH",
        Trade.trade_type == "sell",
        Trade.status.in_(["pending", "confirmed"]),
    ).all()


def _leg_accounting_date(leg: Trade) -> Optional[date]:
    """腿的会计生效日（#493）：确认日优先，缺省退下单日。"""
    return leg.confirm_date or leg.trade_date


def _snapshot_count_from(db: Session, portfolio_code: str, from_date: date) -> int:
    """该组合 `from_date` 及之后的快照张数（计数查询，只读）。"""
    from app.models.portfolio_value_snapshot import PortfolioValueSnapshot

    return (
        db.query(func.count(PortfolioValueSnapshot.id))
        .filter(
            PortfolioValueSnapshot.portfolio_code == portfolio_code,
            PortfolioValueSnapshot.snapshot_date >= from_date,
        )
        .scalar()
        or 0
    )


def validate_group_snapshot_free(
    db: Session, trade: Trade, *, extra_dates: Optional[list] = None
) -> None:
    """组级快照保护（#493 决策 4）：组内任一腿确认日及之后已有快照 → 拒绝改动。

    - **纯只读**：调用方在全部校验通过前不得 setattr（校验失败必须零写入）。
    - 判据取组内最早会计生效日（`confirm_date`，缺省 `trade_date`）——半确认组
      里买入扣款腿的 T 日、卖出到账腿的 A 日都在保护范围内。
    - **cancelled 腿不参与保护**（#518 评审）：快照只累计 confirmed 交易，
      cancelled 腿不进入任何聚合、对历史零影响。若把它算作「组内任一腿」，
      「T 日创建买入 → 当天取消（此时无快照、放行）→ T 日快照生成（cancelled
      不阻断 pending 校验）」这条常规路径上的整组就永久删不掉，而拒绝文案却
      要求用户为清理一个无会计影响的对象销毁快照链。过滤后若无参与腿且
      `extra_dates` 为空即直接放行——保护的是**会计事实**，不是组号出现过。
    - `extra_dates`：改日期场景把**新值**一并纳入保护，否则把日期后移会让
      已入快照的旧腿脱离保护。
    """
    legs = [leg for leg in _group_legs(db, trade) if leg.status != "cancelled"]
    candidates = [_leg_accounting_date(leg) for leg in legs] + list(extra_dates or [])
    candidates = [d for d in candidates if d is not None]
    if not candidates:
        return
    from_date = min(candidates)
    count = _snapshot_count_from(db, trade.portfolio_code, from_date)
    if count:
        raise BusinessError(
            "SNAPSHOT_DEPENDENCY",
            f"该交易的配对组已被快照纳入（{from_date} 及之后有 {count} 张快照），"
            f"请先删除 {from_date} 及之后的快照",
            details={"from_date": from_date.isoformat()},
        )


def _require_date_not_snapshot_consumed(
    db: Session, portfolio_code: str, consumed_date: date, what: str
) -> None:
    """确认期校正保护（#493 决策 5）：日期已被快照消费时不得静默改写历史。"""
    count = _snapshot_count_from(db, portfolio_code, consumed_date)
    if count:
        raise BusinessError(
            "SNAPSHOT_DEPENDENCY",
            f"{what}（{consumed_date}）已被 {count} 张快照消费，"
            f"请先删除 {consumed_date} 及之后的快照再校正",
            details={"from_date": consumed_date.isoformat()},
        )


def validate_buy_cash_with_addback(
    db: Session,
    portfolio_code: str,
    new_cash_out,
    *,
    as_of: Optional[date] = None,
    cash_platform: Optional[str] = None,
    self_trade: Optional[Trade] = None,
) -> Decimal:
    """买入含费现金支出校验（#182 从 create_trade/confirm 抽取，创建/编辑/确认共用）。

    - new_cash_out 量化 2 位后须 > 0（INVALID_AMOUNT）
    - 扣款平台：self_trade 的配对 CASH sell 腿平台（#91 跨平台扣款，
      同 confirm_single_trade 模式），无配对腿时回退 cash_platform
      （创建场景 = cash_platform_code or 基金腿平台），再回退 self_trade 平台
    - 可用现金 = calculate_available_cash(as_of) + **已在本次余额口径中扣掉的**
      自身 CASH sell 腿金额（#493：口径从「pending」扩展到 pending/confirmed，
      因买入扣款腿创建即 confirmed；已进入快照基线的扣款同样加回——
      `calculate_available_cash` 的基线已扣过它，不加回会把合法确认误拒）。
      `as_of` 之前的扣款（`trade_date > as_of`）尚未被计提，**不加回**；
      cancelled 腿不计入。每条腿只加回一次。
    - 超限抛 INSUFFICIENT_CASH（details 含 required/available/deficit）

    Returns:
        量化后的含费现金支出（2 位）
    """
    if new_cash_out is None:
        raise BusinessError("INVALID_AMOUNT", "买入金额必须大于0")
    # 用户输入金额先量化到 2 位（四舍五入），再做精确比较（issue #94）
    cash_out_d = quantize_amount(Decimal(str(new_cash_out)))
    if cash_out_d <= 0:
        raise BusinessError("INVALID_AMOUNT", "买入金额必须大于0")

    # 平台判据取**全部**自身扣款腿（不受查询时点裁剪）：跨平台买入的实际扣款
    # 平台不能因「日期前移」而丢失
    own_legs = _own_cash_sell_legs(db, self_trade) if self_trade else []
    check_platform = cash_platform
    if own_legs:
        check_platform = own_legs[0].platform_code
    if check_platform is None and self_trade is not None:
        check_platform = self_trade.platform_code

    available = calculate_available_cash(
        db, portfolio_code, check_platform, as_of_date=as_of
    )
    # 加回自身 CASH sell 腿：该腿金额已在本时点的余额口径中被扣掉（快照基线或
    # 快照后增量），不加回会与新支出双重计数导致误拒。查询日之后的扣款尚未
    # 被计提（时点口径锚定 trade_date），加回会高估可用现金，故排除。
    own_addback = sum(
        (
            Decimal(str(leg.amount or 0))
            for leg in own_legs
            if as_of is None or (leg.trade_date is not None and leg.trade_date <= as_of)
        ),
        Decimal("0"),
    )
    available_excl_own = available + own_addback
    if cash_out_d > available_excl_own:
        platform_label = check_platform if check_platform else "组合"
        raise BusinessError(
            "INSUFFICIENT_CASH",
            f"平台 {platform_label} 可用现金不足"
            f"（需 {cash_out_d}，可用 {available_excl_own}）",
            details={
                "deficit": str(cash_out_d - available_excl_own),
                "required": str(cash_out_d),
                "available": str(available_excl_own),
            },
        )
    return cash_out_d


def validate_sell_shares_with_addback(
    db: Session,
    portfolio_code: str,
    product_code: str,
    market: Optional[str],
    new_shares,
    *,
    as_of: Optional[date] = None,
    self_trade: Optional[Trade] = None,
) -> Decimal:
    """卖出份额校验（#182 从 create_trade 抽取，创建/编辑/确认共用）。

    - new_shares 量化 2 位后须 > 0（INVALID_SHARES）
    - 可用份额 = calculate_available_shares(as_of) + 自身 pending 卖出旧份额
      （该函数扣减全部 pending 卖出含自身，加回防编辑/确认场景误拒；创建加回 0）
    - 超限抛 INSUFFICIENT_SHARES

    Returns:
        量化后的卖出份额（2 位）
    """
    if new_shares is None:
        raise BusinessError("INVALID_SHARES", "卖出份额必须大于0")
    # 用户输入份额先量化到 2 位（四舍五入），再做精确比较（issue #94）
    shares_d = quantize_shares(Decimal(str(new_shares)))
    if shares_d <= 0:
        raise BusinessError("INVALID_SHARES", "卖出份额必须大于0")
    available_shares = calculate_available_shares(
        db, portfolio_code, product_code, market, as_of_date=as_of
    )
    if (
        self_trade is not None
        and self_trade.status == "pending"
        and self_trade.trade_type == "sell"
    ):
        available_shares += Decimal(str(self_trade.shares or 0))
    if shares_d > available_shares:
        raise BusinessError("INSUFFICIENT_SHARES", "卖出份额超过可用份额")
    return shares_d


def get_nav_for_trade_confirmation(
    db: Session, product_code: str, market: str, trade_date: date
) -> Optional[Decimal]:
    """
    获取调仓交易确认时的净值

    规则：
    - 场外基金统一取 T 日（成交当日）净值，禁止向前查找，不区分 QDII/非 QDII。
      QDII 与互认基金的差异仅在确认间隔，已通过创建时按 confirm_days（落库字段）
      设定的 confirm_date 体现；到确认日净值理应可取，缺失则由调用方拒绝确认。
    - issue #228：快照估值侧的滞后取价由 product.nav_lag_days 驱动，与本函数正交
      （确认侧恒取 T 日净值，不受 nav_lag_days 影响）。

    Args:
        db: 数据库会话
        product_code: 产品代码
        market: 市场类型
        trade_date: 交易日期（T日）

    Returns:
        净值（Decimal），如果不存在则返回 None（由调用方决定是否拒绝）
    """
    price_record = db.query(PriceRecord).filter(
        PriceRecord.product_code == product_code,
        PriceRecord.market == market,
        PriceRecord.price_date == trade_date
    ).first()

    if price_record and price_record.unit_price:
        return Decimal(str(price_record.unit_price))

    return None


def new_transfer_group() -> str:
    """调仓组号（`rebal_` 前缀是「调仓组」的业务标识；申赎 `sub_`、现金转移为裸 uuid）。

    #493：组号在 create_trade 的**任何 flush 之前**分配，使「卖出创建时已有组号、
    但没有 CASH 腿」这一半成品组在整个 pending 期间稳定可寻；确认时补齐 CASH 腿，
    unconfirm 后再确认也不换组。
    """
    return f"rebal_{uuid.uuid4().hex[:12]}"


def attach_paired_cash_leg(
    db: Session,
    fund_trade: Trade,
    cash_amount: Decimal,
    *,
    status: str = "confirmed",
    cash_platform_code: Optional[str] = None,
    cash_confirm_date: Optional[date] = None,
) -> Trade:
    """为基金腿构造配对 CASH 腿（#493：买入在创建期调用，卖出在确认期调用）。

    组号：复用 `fund_trade.transfer_group`（create_trade 在任何 flush 前已分配），
    仅在缺失时新生成——保证卖出组在确认补齐 CASH 腿时沿用创建期的稳定组号。

    日期口径（#493，按基金腿方向分叉，不再是「组内 trade_date 恒等」）：
    - **买入**（fund=buy）：CASH 腿为 sell（扣款）。`trade_date` = 下单日 T，
      `confirm_date` = T——创建即 confirmed，现金当日到账/扣减。
    - **卖出**（fund=sell）：CASH 腿为 buy（到账）。`trade_date` = 基金腿**确认日 C**
      （不从下单日传播），`confirm_date` = 到账日 A，缺省 A=C；A>C 即「份额已扣、
      资金在途」窗口（在途金额由 `_compute_in_transit_amounts` 承担）。

    `cash_amount` 采用 router 语义 = 基金腿 actual_amount（买入=支出含费、
    卖出=确认后到手净额）。卖出**不得**使用创建期占位金额（#493 §3.1.3）。
    `cash_platform_code`（#91）：跨平台扣款/到账，缺省同基金腿平台。

    Returns:
        新建的 CASH 腿（已 db.add，未 commit）
    """
    if not fund_trade.transfer_group:
        fund_trade.transfer_group = new_transfer_group()
    is_buy = fund_trade.trade_type == "buy"
    if is_buy:
        cash_trade_date = fund_trade.trade_date
        effective_cash_confirm = cash_confirm_date or fund_trade.trade_date
    else:
        # 卖出到账腿：生效锚点是基金腿确认日，不是下单日
        cash_trade_date = fund_trade.confirm_date
        effective_cash_confirm = cash_confirm_date or fund_trade.confirm_date
    cash_trade = Trade(
        portfolio_code=fund_trade.portfolio_code,
        platform_code=cash_platform_code or fund_trade.platform_code,
        product_code="CASH",
        market="",
        trade_type="sell" if is_buy else "buy",
        shares=None,
        amount=cash_amount,
        price=Decimal("1"),
        fee=Decimal("0"),
        actual_amount=cash_amount,
        trade_date=cash_trade_date,
        confirm_date=effective_cash_confirm,
        status=status,
        transfer_group=fund_trade.transfer_group,
    )
    db.add(cash_trade)
    return cash_trade


def cash_leg_audit_payload(
    leg: Optional[Trade], action: str
) -> Optional[dict]:
    """配对 CASH 腿的审计载荷片段（#493 §3.1.8）。

    配对腿的创建/删除/状态与到账日期、平台变化折入**同一业务审计载荷**的
    `cash_leg` 键，不另开第二条现金腿审计记录。
    """
    if leg is None:
        return {"action": action} if action else None
    return {
        "action": action,
        "trade_id": leg.id,
        "platform_code": leg.platform_code,
        "trade_date": leg.trade_date,
        "confirm_date": leg.confirm_date,
        "amount": leg.amount,
        "status": leg.status,
    }


def sync_transfer_group(
    db: Session,
    trade: Trade,
    target_status: str,
    confirm_date: Optional[date] = None,
    *,
    propagate_dates: bool = False,
) -> Optional[dict]:
    """按 #493 操作矩阵同步配对 CASH 腿（组内状态不再无条件同状态）。

    「两腿同状态、同 `trade_date`」是旧模型的不变量，#493 起按方向与目标状态分叉：

    - `cancelled`（整组取消/回退）：组内其余腿一并置 cancelled；买入组的扣款腿
      随之失效（现金回退），卖出组本就没有 CASH 腿。
    - `pending`（基金腿 unconfirm）：**买入组保留 CASH confirmed**——扣款是既成
      事实，日期与金额也不动；**卖出组删除 CASH 腿**，回到「未创建」态，再次确认
      时按新的到账信息重建。
    - 其它（编辑基金腿日期/金额）：只同步**买入**方向 CASH 腿——`trade_date` 与
      `confirm_date` 恒为下单日 T、金额镜像基金腿 `actual_amount`；卖出方向不制造
      pending 调仓 CASH 腿（到账腿只由确认路径创建）。

    不传播卖出腿的 `confirm_date`（到账日 A 是独立录入量），也不制造新的
    pending 调仓 CASH 腿。

    Returns:
        审计载荷片段（交由调用方折进同一业务审计），无变化时为 None
    """
    if not trade.transfer_group:
        return None
    is_fund_leg = trade.product_code != "CASH"
    paired = db.query(Trade).filter(
        Trade.transfer_group == trade.transfer_group,
        Trade.id != trade.id,
    ).all()
    # #37 savepoint：配对腿更新失败回滚 savepoint，不影响外层事务
    # 保持连接级：本函数失败即 rollback + raise，从不承诺 session 继续可用，用不上
    # session 级 db.begin_nested() 的 ORM 事务状态复位——那是 #419 给 auto_confirm
    # 「单条失败仍要继续循环」那条路径修的。旧注释担心的监听器冲突并非不存在，而是
    # 只发生在 `with db.begin_nested():` 形式（closed 事务仍是 _trans_context_manager，
    # 夹具的 after_transaction_end 补 savepoint 时撞 InvalidRequestError 掩盖根因）；
    # 显式 begin/commit/rollback 形式不复现，详见 backend/AGENTS.md §1.3「可观测性」。
    is_rebal_group = bool(trade.transfer_group) and trade.transfer_group.startswith("rebal_")
    mirror_amount = None
    if is_fund_leg:
        mirror_amount = (
            trade.actual_amount if trade.actual_amount is not None else trade.amount
        )
    audit: Optional[dict] = None
    sp = db.connection().begin_nested()
    try:
        if is_rebal_group:
            cash_legs = [p for p in paired if p.product_code == "CASH"]
            if is_fund_leg and trade.trade_type == "sell" and target_status in (
                "pending", "cancelled",
            ):
                # 卖出组回退：pending = 回到「未创建」；cancelled 时若已重建过到账腿，
                # 同样整组取消（该腿由确认路径创建，不能再留 confirmed）
                for cash_leg in cash_legs:
                    audit = cash_leg_audit_payload(cash_leg, "deleted")
                    db.delete(cash_leg)
            elif (
                is_fund_leg and trade.trade_type == "buy"
                and target_status == "pending" and not propagate_dates
            ):
                # 买入组 unconfirm：扣款既成事实——confirmed、日期、金额均保持
                for cash_leg in cash_legs:
                    audit = cash_leg_audit_payload(cash_leg, "kept_confirmed")
            else:
                for cash_leg in cash_legs:
                    # #493：买入扣款腿创建即 confirmed、**永不回退为 pending**。
                    # 编辑（propagate_dates=True）只同步日期与金额；若把它打回
                    # pending，该腿会被 `calculate_available_cash` 按 trade_date
                    # 照常扣减，却不满足 `_compute_in_transit_amounts` 的
                    # 「CASH sell confirmed」前提，于是钱从可用现金和市值里同时
                    # 消失（凭空少一笔）。整组取消（cancelled）仍须跟随失效。
                    if not (
                        is_fund_leg
                        and trade.trade_type == "buy"
                        and target_status == "pending"
                    ):
                        cash_leg.status = target_status
                    if target_status != "cancelled" and is_fund_leg and trade.trade_type == "buy":
                        # 买入扣款腿的生效锚点恒为下单日 T（不从 confirm_date 推导）
                        cash_leg.trade_date = trade.trade_date
                        cash_leg.confirm_date = trade.trade_date
                    if target_status != "cancelled" and mirror_amount is not None:
                        cash_leg.amount = mirror_amount
                        cash_leg.actual_amount = mirror_amount
                    audit = cash_leg_audit_payload(cash_leg, "status_synced")
        else:
            # 非调仓组（跨平台现金转移等）：保持 #493 之前的状态/日期同步行为
            for paired_trade in paired:
                paired_trade.trade_date = trade.trade_date
                paired_trade.status = target_status
                # #93: 各腿保持创建时设定的独立确认日，不再同步 confirm_date
                if target_status == "pending":
                    if paired_trade.product_code == "CASH":
                        if paired_trade.trade_type == "sell":
                            paired_trade.confirm_date = paired_trade.trade_date
                        else:
                            fund_leg = next(
                                (p for p in paired if p.product_code != "CASH"), None
                            )
                            if fund_leg is None and is_fund_leg:
                                fund_leg = trade
                            if fund_leg and fund_leg.confirm_date:
                                paired_trade.confirm_date = fund_leg.confirm_date
                    else:
                        paired_product = db.query(Product).filter(
                            Product.code == paired_trade.product_code,
                            Product.market == paired_trade.market,
                        ).first()
                        if paired_product:
                            paired_trade.confirm_date = get_next_trading_day(
                                db, paired_trade.trade_date,
                                days=paired_product.confirm_days or 0,
                            )
                if mirror_amount is not None and paired_trade.product_code == "CASH":
                    paired_trade.amount = mirror_amount
                    paired_trade.actual_amount = mirror_amount
                if paired_trade.product_code == "CASH":
                    audit = cash_leg_audit_payload(paired_trade, "status_synced")
        db.flush()  # 确保 ORM 变更在 savepoint 内写入 DB
        sp.commit()
    except Exception:
        sp.rollback()
        raise
    return audit


def calculate_confirm_preview(
    db: Session,
    trade: Trade,
    product: Optional[Product],
    *,
    confirm_date: Optional[date] = None,
    price: Optional[Decimal] = None,
) -> dict:
    """
    计算交易确认结果（纯计算，不落库），供确认前预览与真实确认共用。

    只做查询与计算：**不修改 trade 对象、不同步配对腿、不 flush/commit**。
    confirm_single_trade 调用本函数取结果后回写，保证「预览 == 真实确认」。

    计算规则（与确认语义完全一致）：
    - 场外净值型基金（OEF/LOF 且 CN_OTC/HK_MUTUAL）：取 T 日净值重算 shares/amount，
      缺失抛 MISSING_NAV；传入 price 仅作一致性校验（不一致抛 PRICE_NAV_MISMATCH）
    - 非净值型且传入 price：按传入成交价重算（补录/手动覆盖场景）
    - 否则（场内不传价）：不重算，返回 trade 现有字段原样

    Args:
        db: 数据库会话
        trade: 待确认交易（须为 pending）
        product: 交易对应产品（CASH/未知产品可为 None，跳过净值逻辑）
        confirm_date: 覆盖确认日（补录场景）
        price: 手动价格；场外基金仅用于与 T 日净值一致性校验（不覆盖净值），
            场内基金作为覆盖成交价

    Returns:
        {
            "price"/"shares"/"amount"/"actual_amount": 确认后将写入的数值,
            "fee": trade.fee,
            "confirm_date": 生效确认日（传参覆盖或 trade.confirm_date）,
            "nav_date": OTC 净值型时取净值的 T 日（trade.trade_date），否则 None,
            "is_otc_nav_fund": 是否场外净值型基金,
            "paired_cash_amount": 配对 CASH 腿将同步的金额
                （actual_amount 优先，否则 amount，与 sync_transfer_group 镜像规则一致；
                CASH 腿自身为 None）,
        }
    """
    effective_confirm_date = confirm_date if confirm_date is not None else trade.confirm_date

    # 场外净值型基金（CN_OTC/HK_MUTUAL 的 OEF/LOF）确认时必须获取 T 日净值进行计算
    is_otc_nav_fund = (
        bool(product)
        and product.product_type in ["OEF", "LOF"]
        and trade.market in ("CN_OTC", "HK_MUTUAL")
    )

    result_price = trade.price
    result_shares = trade.shares
    result_amount = trade.amount
    result_actual_amount = trade.actual_amount

    if is_otc_nav_fund:
        # 净值型产品：获取T日净值
        nav_price = get_nav_for_trade_confirmation(
            db, trade.product_code, trade.market, trade.trade_date
        )

        # 净值不存在，无法确认交易
        if nav_price is None:
            raise BusinessError(
                "MISSING_NAV",
                f"产品{trade.product_code}在T={trade.trade_date}的净值尚未同步，无法确认交易",
            )

        # 手动价格为可选校验项：传入时必须与 T 日净值一致，否则拒绝确认由用户修正；
        # 场外基金一律以 T 日净值计算，手动价不参与计算、也不覆盖净值
        # #428：两侧都经 quantize_nav 归一到 4 位 HALF_UP 再精确比较（无容差）——
        # nav_price 来自 Numeric(10,4) 列、本来恰是 4 位，一侧归一即保证标度无关
        if price is not None:
            input_price = quantize_nav(price)
            if input_price != quantize_nav(nav_price):
                raise BusinessError(
                    "PRICE_NAV_MISMATCH",
                    f"传入价格({input_price})与T={trade.trade_date}净值({nav_price})不一致，请核对后修改，或不传价格直接取净值",
                )

        final_price = nav_price

        result_price = final_price
        if trade.trade_type == "buy":
            # actual_amount / fee 创建时已量化到 2 位（issue #94），差值仍精确为 2 位
            amount = Decimal(str(trade.actual_amount)) - Decimal(str(trade.fee))
            result_shares = quantize_shares(amount / final_price)
            result_amount = amount
        else:
            # 金额统一量化到 2 位（issue #94）：shares(2位) × nav(4位) 的四舍五入
            # 误差计入基金财产，现金回笼与平台 2 位口径一致
            amount = quantize_amount(Decimal(str(trade.shares)) * final_price)
            result_actual_amount = amount - Decimal(str(trade.fee))
            result_amount = amount
    elif price is not None:
        # 场内基金/其他非净值型：仅在传入价格时按传入成交价重算（补录/手动覆盖场景）
        result_price = Decimal(str(price))
        if trade.trade_type == "buy":
            # actual_amount / fee 创建时已量化到 2 位（issue #94），差值仍精确为 2 位
            amount = Decimal(str(trade.actual_amount)) - Decimal(str(trade.fee))
            result_shares = quantize_shares(amount / Decimal(str(price)))
            result_amount = amount
        else:
            # 金额统一量化到 2 位（issue #94），同场外卖出分支
            amount = quantize_amount(Decimal(str(trade.shares)) * Decimal(str(price)))
            result_actual_amount = amount - Decimal(str(trade.fee))
            result_amount = amount
    # else：场内不传价 → 不重算，返回 trade 现有字段原样

    # 配对 CASH 腿镜像金额（与 sync_transfer_group 规则一致，仅基金腿有意义）
    paired_cash_amount = None
    if trade.product_code != "CASH":
        paired_cash_amount = (
            result_actual_amount if result_actual_amount is not None else result_amount
        )

    return {
        "price": result_price,
        "shares": result_shares,
        "amount": result_amount,
        "actual_amount": result_actual_amount,
        "fee": trade.fee,
        "confirm_date": effective_confirm_date,
        "nav_date": trade.trade_date if is_otc_nav_fund else None,
        "is_otc_nav_fund": is_otc_nav_fund,
        "paired_cash_amount": paired_cash_amount,
    }


def resolve_cash_leg_plan(
    db: Session,
    trade: Trade,
    *,
    effective_confirm_date: date,
    cash_platform_code: Optional[str] = None,
    cash_confirm_date: Optional[date] = None,
) -> dict:
    """确认/预览共用的现金腿校验与计划（**纯只读**：不修改任何 ORM 对象）。

    #493 §3.3：日期、平台、快照保护与配对现金腿计划由 preview 与 confirm 共用
    同一实现，「预览 == 确认」由此保证；缺省值与显式覆盖走同一条校验路径。

    校验：
    - 有效基金确认日 C 须为交易日、不早于下单日 T、**严格晚于最新快照日**
      （快照已含该基金腿则须先删快照）；
    - 卖出到账日 A 缺省 = C，须为交易日且不早于 C；
    - 买入扣款日固定 T：`cash_confirm_date` 只接受等于 T；买入也不允许借确认
      改扣款平台（`cash_platform_code` 只接受等于既有扣款腿平台）；
    - CASH 腿自身（申赎/现金转移）不参与本计划——调仓 CASH 腿在确认入口已被
      `CASH_TRADE_FORBIDDEN` 拦截，其余现金腿保持既有生命周期。

    Returns:
        {
          "cash_leg_action": "create"|"verify"|"none",
          "cash_platform_code": 有效扣款/到账平台,
          "cash_confirm_date": 有效现金日（买=T、卖=A）,
          "fund_confirm_date": 有效基金确认日 C,
        }
    """
    if trade.product_code == "CASH":
        return {
            "cash_leg_action": "none",
            "cash_platform_code": None,
            "cash_confirm_date": None,
            "fund_confirm_date": effective_confirm_date,
        }

    if not is_trading_day(db, effective_confirm_date):
        raise BusinessError(
            "NON_TRADING_DAY",
            f"确认日 {effective_confirm_date} 非交易日，请改为交易日",
        )
    if trade.trade_date and effective_confirm_date < trade.trade_date:
        raise BusinessError(
            "INVALID_DATE_ORDER",
            f"确认日 {effective_confirm_date} 不能早于下单日 {trade.trade_date}",
        )
    latest_snapshot_date = get_latest_snapshot_date(db, trade.portfolio_code)
    if latest_snapshot_date and effective_confirm_date <= latest_snapshot_date:
        raise BusinessError(
            "SNAPSHOT_DEPENDENCY",
            f"基金确认日必须晚于最新快照日（{latest_snapshot_date}），"
            f"请先删除 {latest_snapshot_date} 及之后的快照",
            details={"from_date": latest_snapshot_date.isoformat()},
        )

    if cash_platform_code and not db.query(Platform).filter(
        Platform.code == cash_platform_code
    ).first():
        raise NotFoundError(
            "PLATFORM_NOT_FOUND", f"现金平台 {cash_platform_code} 不存在"
        )

    existing_leg = _paired_cash_leg(db, trade)
    if trade.trade_type == "buy":
        # 买入：扣款日与扣款平台在创建期即固定（创建即扣款），确认期不得借机改写
        if cash_confirm_date is not None and cash_confirm_date != trade.trade_date:
            raise BusinessError(
                "CASH_CONFIRM_DATE_NOT_ALLOWED",
                f"买入扣款日固定为下单日 {trade.trade_date}，"
                f"cash_confirm_date 只接受等于该日",
            )
        plan_platform = (
            existing_leg.platform_code if existing_leg
            else (cash_platform_code or trade.platform_code)
        )
        if cash_platform_code and cash_platform_code != plan_platform:
            raise BusinessError(
                "CASH_PLATFORM_NOT_ALLOWED",
                "买入扣款平台在创建时确定，确认时不可修改",
            )
        return {
            "cash_leg_action": "verify",
            "cash_platform_code": plan_platform,
            "cash_confirm_date": trade.trade_date,
            "fund_confirm_date": effective_confirm_date,
        }

    if trade.trade_type != "sell":
        raise BusinessError("INVALID_TYPE", "类型必须为 buy 或 sell")

    # 卖出：确认时才录入到账信息；A 缺省 = C，平台缺省 = 基金腿平台
    arrival_date = cash_confirm_date if cash_confirm_date is not None else effective_confirm_date
    if not is_trading_day(db, arrival_date):
        raise BusinessError(
            "NON_TRADING_DAY", f"到账日 {arrival_date} 非交易日，请改为交易日"
        )
    if arrival_date < effective_confirm_date:
        raise BusinessError(
            "INVALID_DATE_ORDER",
            f"到账日 {arrival_date} 不能早于基金确认日 {effective_confirm_date}",
        )
    return {
        "cash_leg_action": "create",
        "cash_platform_code": cash_platform_code or trade.platform_code,
        "cash_confirm_date": arrival_date,
        "fund_confirm_date": effective_confirm_date,
    }


def compute_confirm_plan(
    db: Session,
    trade: Trade,
    product: Optional[Product],
    *,
    confirm_date: Optional[date] = None,
    price: Optional[Decimal] = None,
    cash_platform_code: Optional[str] = None,
    cash_confirm_date: Optional[date] = None,
) -> dict:
    """确认计划（预览与真实确认共用，**纯只读**）。

    在 `calculate_confirm_preview`（NAV/份额/金额）之上叠加现金腿计划：两者共同
    构成「确认/preview 会写入或校验什么」的单一事实来源。不修改 trade、不构腿、
    不写审计、不 flush——`sync_nav` 只是确认端点的显式动作，预览不触发。
    """
    preview = calculate_confirm_preview(
        db, trade, product, confirm_date=confirm_date, price=price
    )
    plan = resolve_cash_leg_plan(
        db,
        trade,
        effective_confirm_date=preview["confirm_date"],
        cash_platform_code=cash_platform_code,
        cash_confirm_date=cash_confirm_date,
    )
    preview["cash_leg_action"] = plan["cash_leg_action"]
    preview["cash_platform_code"] = plan["cash_platform_code"]
    preview["cash_confirm_date"] = plan["cash_confirm_date"]
    return preview


def _cash_leg_needs_fix(existing_leg: Optional[Trade], amount: Decimal) -> bool:
    """买入扣款腿是否与本次确认的金额/状态不一致（#493 决策 5）。"""
    return (
        existing_leg is None
        or existing_leg.status != "confirmed"
        or existing_leg.actual_amount is None
        or quantize_amount(Decimal(str(existing_leg.actual_amount))) != amount
    )


def validate_confirm_cash_leg(db: Session, fund_trade: Trade, plan: dict) -> None:
    """确认前对配对现金腿的**只读**校验（#493：校验通过前不得改变 ORM 对象）。

    买入方向需要校正扣款腿时，扣款日已被快照消费即拒绝；其余情况无需前置校验
    （卖出的到账腿由确认路径新建，日期合法性已在 `resolve_cash_leg_plan` 把住）。
    """
    if plan.get("cash_leg_action") != "verify":
        return
    amount = plan["paired_cash_amount"]
    if amount is None:
        return
    expected = quantize_amount(Decimal(str(amount)))
    if _cash_leg_needs_fix(_paired_cash_leg(db, fund_trade), expected):
        _require_date_not_snapshot_consumed(
            db, fund_trade.portfolio_code, fund_trade.trade_date, "买入扣款日"
        )


def _apply_confirm_cash_leg(db: Session, fund_trade: Trade, plan: dict) -> Optional[dict]:
    """确认时落定配对 CASH 腿（#493 §3.1.3 / 决策 5），返回审计载荷片段。

    - **卖出**：用本次确认后的净额（`paired_cash_amount`）新建/就地对齐到账腿，
      `status=confirmed`、`trade_date=C`、`confirm_date=A`；不追加第二条现金腿。
    - **买入**：核验既有扣款腿的状态与金额；不一致时校正（调用方已通过
      `validate_confirm_cash_leg` 拦住「已被快照消费」的情形）。

    **新建腿后必须 `flush()` 再取审计载荷**（#518 评审）：`Trade.id` 是自增整型，
    flush 前恒为 `None`，而 `cash_leg_audit_payload` 直接读 `leg.id`——不 flush 会
    让 confirm 审计的 `cash_leg.trade_id` 永远是 null（create_trade 早已先 flush
    再取载荷，本路径漏了）。flush 留在同一事务内，不 commit。
    """
    if plan["cash_leg_action"] == "none":
        return None
    amount = plan["paired_cash_amount"]
    if amount is None:
        return None
    amount = quantize_amount(Decimal(str(amount)))
    existing_leg = _paired_cash_leg(db, fund_trade)

    if plan["cash_leg_action"] == "create":
        if existing_leg is not None:
            # 不追加第二条现金腿：就地按本次确认结果对齐（组号不变）
            existing_leg.platform_code = plan["cash_platform_code"]
            existing_leg.trade_date = fund_trade.confirm_date
            existing_leg.confirm_date = plan["cash_confirm_date"]
            existing_leg.amount = amount
            existing_leg.actual_amount = amount
            existing_leg.status = "confirmed"
            return cash_leg_audit_payload(existing_leg, "synced")
        leg = attach_paired_cash_leg(
            db, fund_trade, amount,
            status="confirmed",
            cash_platform_code=plan["cash_platform_code"],
            cash_confirm_date=plan["cash_confirm_date"],
        )
        db.flush()  # 新腿自增 id 落定，审计载荷才能带上真实 trade_id
        return cash_leg_audit_payload(leg, "created")

    # 买入：核验既有扣款腿（已被快照消费的不一致情形已在前置校验拒绝）
    if not _cash_leg_needs_fix(existing_leg, amount):
        return cash_leg_audit_payload(existing_leg, "unchanged")
    if existing_leg is None:
        leg = attach_paired_cash_leg(
            db, fund_trade, amount,
            status="confirmed",
            cash_platform_code=plan["cash_platform_code"],
        )
        db.flush()  # 同上：兜底新建的扣款腿也要先拿到自增 id
        return cash_leg_audit_payload(leg, "created")
    existing_leg.status = "confirmed"
    existing_leg.amount = amount
    existing_leg.actual_amount = amount
    return cash_leg_audit_payload(existing_leg, "corrected")


def confirm_single_trade(
    db: Session,
    trade: Trade,
    product: Optional[Product],
    *,
    confirm_date: Optional[date] = None,
    price: Optional[Decimal] = None,
    skip_available_check: bool = False,
    sync_nav: bool = False,
    cash_confirm_date: Optional[date] = None,
    cash_platform_code: Optional[str] = None,
) -> Trade:
    """
    确认单笔调仓交易的核心逻辑（REST 手动确认与补录共用）。

    - 计算统一委托 `compute_confirm_plan`（确认与预览共用同一实现），本函数负责
      将结果回写 trade 并置 confirmed
    - confirm_date 已在创建时设定；若传入参数则覆盖（补录场景）
    - 场外基金（OEF/LOF 且 CN_OTC/HK_MUTUAL）确认时统一获取 T 日（成交当日）净值并重算 shares/amount，
      不区分 QDII/非 QDII，一律以净值计算；缺失 T 日净值时抛 MISSING_NAV 拒绝确认
    - sync_nav=True（issue #90，显式选择）：命中 MISSING_NAV 时自动回填该标的历史净值
      后重试一次；同步后仍缺失则照常抛 MISSING_NAV
    - 场外基金若传入 price 仅作一致性校验：须与 T 日净值相等，否则抛 PRICE_NAV_MISMATCH，
      手动价不覆盖净值（不传则直接取净值）
    - 场内基金不取净值，使用创建时录入的成交价（成交价录入时必填，见 trades.py 创建校验）
    - 可用量校验（#70/#78 按生效确认日时点口径，#182 卖出份额对称补齐）：
      买入按扣款平台校验可用现金、卖出校验可用份额（均加回自身 pending/confirmed
      旧值防双重计数），不足抛 INSUFFICIENT_CASH / INSUFFICIENT_SHARES；
      skip_available_check=True 跳过（历史重放场景）
    - **配对 CASH 腿（#493）**：卖出在确认时按本次净额新建到账腿（A 缺省 = C）；
      买入核验既有扣款腿的状态/金额，不一致且未被快照消费时校正、已消费则拒绝。
      确认、建腿与审计在**同一事务**内完成（本函数不 commit）。
    - 调仓组的 CASH 腿不可直接确认（只能由基金腿驱动）→ CASH_TRADE_FORBIDDEN

    Args:
        db: 数据库会话
        trade: 待确认交易（须为 pending）
        product: 交易对应产品（CASH/未知产品可为 None，跳过净值逻辑）
        confirm_date: 覆盖确认日（补录场景）
        price: 手动价格；场外基金仅用于与 T 日净值一致性校验（不覆盖净值），
            场内基金作为覆盖成交价
        skip_available_check: 跳过买入现金/卖出份额可用量校验（历史重放专用）
        sync_nav: MISSING_NAV 时自动回填净值并重试一次（显式选择，会访问外部数据源）
        cash_confirm_date: 卖出到账日 A（缺省 = 本次有效确认日 C）
        cash_platform_code: 卖出到账平台（缺省 = 基金腿平台）

    Returns:
        确认后的 trade 对象（未 commit，事务由调用方控制）
    """
    if _is_rebal_group_cash_leg(trade):
        raise BusinessError(
            "CASH_TRADE_FORBIDDEN",
            "调仓配对 CASH 腿不可直接确认，请确认对应基金腿",
        )

    def _plan() -> dict:
        return compute_confirm_plan(
            db, trade, product, confirm_date=confirm_date, price=price,
            cash_platform_code=cash_platform_code,
            cash_confirm_date=cash_confirm_date,
        )

    try:
        preview = _plan()
    except BusinessError as e:
        if not (sync_nav and e.code == "MISSING_NAV"):
            raise
        # issue #90：显式请求时自动回填该标的历史净值后重试一次
        from app.services.market_data_service import sync_price_data

        try:
            sync_price_data(db, trade.product_code, trade.market, None, date.today())
        except Exception as sync_err:
            raise BusinessError(
                "MISSING_NAV",
                f"{e.message}；自动同步净值失败: {sync_err}",
            )
        preview = _plan()

    # 可用量校验（#70/#78：按生效确认日时点口径；#182 卖出份额对称补齐）
    if not skip_available_check and trade.product_code != "CASH":
        effective_confirm_date = preview["confirm_date"]
        if trade.trade_type == "buy":
            # 买入：按扣款平台校验可用现金（配对 CASH sell 腿平台 + 自身腿加回，
            # 与创建/编辑共用同一实现；金额缺失的异常数据维持旧口径跳过）
            if preview["paired_cash_amount"] is not None:
                validate_buy_cash_with_addback(
                    db, trade.portfolio_code,
                    preview["paired_cash_amount"],
                    as_of=effective_confirm_date,
                    self_trade=trade,
                )
        elif trade.trade_type == "sell":
            # 卖出：校验可用份额（自身 pending 卖出加回；#182 封超卖确认漏洞）
            validate_sell_shares_with_addback(
                db, trade.portfolio_code, trade.product_code, trade.market,
                trade.shares,
                as_of=effective_confirm_date,
                self_trade=trade,
            )

    # ---- 校验全部通过，开始写入（#493：确认、建腿、审计同一事务）----
    # 现金腿校正的前置校验必须在任何 setattr 之前（拒绝即零写入）
    validate_confirm_cash_leg(db, trade, preview)

    # confirm_date 已在创建时设定；若传入参数则覆盖（补录场景）
    if confirm_date is not None:
        trade.confirm_date = confirm_date

    # 仅在重算分支回写数值字段（场内不传价时保持 trade 现有字段不动）
    if preview["is_otc_nav_fund"] or price is not None:
        trade.price = preview["price"]
        trade.shares = preview["shares"]
        trade.amount = preview["amount"]
        trade.actual_amount = preview["actual_amount"]

    trade.status = "confirmed"
    cash_leg_audit = _apply_confirm_cash_leg(db, trade, preview)

    logger.info(
        f"交易确认: trade_id={trade.id}, type={trade.trade_type}, "
        f"product={trade.product_code}, confirm_date={trade.confirm_date}"
    )

    record_audit(
        db,
        action=ACTION_CONFIRM,
        resource_type=RESOURCE_TRADE,
        resource_id=str(trade.id),
        resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
        old_value={"status": "pending"},
        new_value={
            "status": "confirmed",
            "price": trade.price,
            "shares": trade.shares,
            "amount": trade.amount,
            "actual_amount": trade.actual_amount,
            "confirm_date": trade.confirm_date,
            "transfer_group": trade.transfer_group,
            "cash_leg": cash_leg_audit,
        },
    )

    return trade


def _derive_sell_amounts(
    shares_d: Decimal,
    price_d: Optional[Decimal],
    fee_d: Decimal,
    input_actual: Optional[float],
    market: str,
) -> tuple[Decimal, Decimal]:
    """卖出金额纯派生量（#190）：返回 (毛额 amount, 到手净额 actual_amount)。

    创建（create_trade）与编辑（update_trade）共用，保证两路径口径一致：
    - 有价格：毛额 = quantize(shares × price)、净额 = 毛额 − fee；
      净额非正抛 INVALID_AMOUNT（fee 不小于毛额属录入错误）。
      input_actual（amount/actual_amount 两参同义、调用方已择一传入）仅作对账：
      场内（CN_EXCHANGE）差超 0.01 抛 AMOUNT_MISMATCH；场外传价只推导展示、
      不强对账（参考价与预估净值天然有偏差，确认时 T 日净值重算覆盖兜底）。
    - 无价格（场外未传价）：创建期占位（显式输入暂存否则 0），
      毛额 = 净额 + fee，确认时按净值重算自愈。
    """
    if price_d is not None:
        gross_amount = quantize_amount(shares_d * price_d)      # 毛额
        actual_amount_final = gross_amount - fee_d              # 到手净额
        if actual_amount_final <= 0:
            raise BusinessError(
                "INVALID_AMOUNT",
                f"手续费 {fee_d} 不小于卖出毛额 {gross_amount}，到手净额非正，"
                f"请核对 fee/price",
            )
        if input_actual is not None and market == "CN_EXCHANGE":
            input_actual_d = quantize_amount(input_actual)
            if abs(input_actual_d - actual_amount_final) > Decimal("0.01"):
                raise BusinessError(
                    "AMOUNT_MISMATCH",
                    f"到账金额 {input_actual_d} 与 shares×price−fee="
                    f"{actual_amount_final} 不一致，请核对份额/价格/手续费",
                )
        return gross_amount, actual_amount_final
    actual_amount_final = (
        quantize_amount(input_actual) if input_actual is not None else Decimal("0")
    )
    return actual_amount_final + fee_d, actual_amount_final


def create_trade(
    db: Session,
    *,
    portfolio_code: str,
    product_code: str,
    market: Optional[str],
    trade_type: str,
    trade_date: date,
    amount: Optional[Decimal] = None,
    actual_amount: Optional[Decimal] = None,
    fee: Optional[Decimal] = None,
    price: Optional[Decimal] = None,
    shares: Optional[Decimal] = None,
    platform_code: Optional[str] = None,
    notes: Optional[str] = None,
    allow_duplicate: bool = False,
    cash_platform_code: Optional[str] = None,
    cash_confirm_date: Optional[date] = None,
) -> Trade:
    """创建买入/卖出交易（含全部校验与配对 CASH 腿），供 REST 与 CLI 共用。

    买入现金口径统一：cash_out = actual_amount 优先，否则 amount
    （前端传 amount、CLI 传 actual_amount，两者行为一致）。
    卖出金额为纯派生量（#190）：有价格时 amount = quantize(shares × price)、
    actual_amount = amount − fee；显式传入的 amount/actual_amount（两参同义、
    actual_amount 优先，与 buy 分支、PUT 侧对齐）仅作一致性校验
    （差值超 0.01 报 AMOUNT_MISMATCH），落库恒用推导值；
    无价格（场外未传价）时创建期占位，确认按净值重算。
    自然键防重（#82）：同组合/产品/市场/平台/方向/交易日且金额（买）或份额（卖）
    相同的 pending/confirmed 交易视为重复，抛 DUPLICATE_TRADE；
    allow_duplicate=True 强制放行，cancelled 记录不算重复。
    cash_platform_code（issue #91）：现金腿平台，买=扣款平台、卖=到账平台，
    缺省同基金腿；买入可用现金按扣款平台校验。「同平台等价于不传」的归一化只在
    买入侧生效；**卖出侧**创建期传任何非空 `cash_platform_code`（含等于基金腿
    平台的值）都按原始入参拒绝（#518 评审），与 CLI 前置拒绝同码同语义。
    #493 买入/卖出的创建形态分叉：
    - 买入：创建即扣款——配对 CASH sell 腿直接 **confirmed**、现金日 = 下单日 T；
      基金腿 pending，等 T+1/T+2 确认。故 D 日快照不被 pending 交易阻断，现金已
      实扣、等额记在途。`cash_platform_code` 可传（跨平台扣款）；
      `cash_confirm_date` 只接受等于 trade_date（扣款日固定 T）。
    - 卖出：**只建基金腿**，组号照常分配；到账日 `cash_confirm_date` 与到账平台
      `cash_platform_code` 一律在 **confirm** 时录入，创建期传入报
      CASH_CONFIRM_DATE_NOT_ALLOWED / CASH_PLATFORM_NOT_ALLOWED。
    不 commit，事务由调用方控制。返回基金腿 Trade。
    """
    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", "组合不存在")
    if portfolio.status != "active":
        raise BusinessError("PORTFOLIO_NOT_ACTIVE", "组合未激活")

    # 交易日 + 快照日校验（#182 起与 PUT 编辑路径共用同一实现）
    validate_trade_date(db, portfolio_code, trade_date)

    # #83：market 省略时按产品唯一市场自动补全；LOF 一码多市场抛 MARKET_AMBIGUOUS
    product_code, market = resolve_product_market(db, product_code, market)

    product = db.query(Product).filter(
        Product.code == product_code, Product.market == market
    ).first()
    if not product:
        # details 携带 product_code 与同 code 其他市场，供 CLI hints 消费
        details = {"product_code": product_code, "market": market}
        other_markets = sorted(
            row[0] or ""
            for row in db.query(Product.market).filter(Product.code == product_code).all()
        )
        if other_markets:
            details["available_markets"] = other_markets
        raise NotFoundError(
            "NOT_FOUND", f"产品 {product_code}({market}) 不存在", details=details
        )

    # 禁止直接创建裸 CASH 交易：现金变动只能来自申赎/调仓配对/现金转移
    if product_code == "CASH":
        raise BusinessError(
            "CASH_TRADE_FORBIDDEN",
            "不支持直接创建 CASH 交易，请使用现金转移或申购赎回入口",
        )

    # 平台必填 + 存在性（与 share_change_event_service PLATFORM_REQUIRED 先例同口径）：
    # 基金腿平台决定持仓平台归属，缺省时现金闸门退化为全组合聚合（根 AGENTS.md「平台与现金账本」节旁路），必须拦截
    if not platform_code:
        raise BusinessError("PLATFORM_REQUIRED", "调仓交易必须指定交易平台 platform_code")
    if not db.query(Platform).filter(Platform.code == platform_code).first():
        raise NotFoundError("PLATFORM_NOT_FOUND", f"平台 {platform_code} 不存在")

    # #493 现金腿输入的方向闸门：创建期只有买入有现金腿，卖出腿在确认时才建。
    # **必须在下面的「同平台等价于不传」归一化之前判定原始入参**（#518 评审）：
    # 归一化会把「卖出 + 与基金腿同平台」静默折成 None，于是 CLI 前置拒绝的同一个
    # 输入在 REST 上被放行（两端行为相反），「卖出创建期不接受 cash_platform_code」
    # 这条承诺在 REST 上不成立。买入侧语义不变（归一化与存在性校验照旧执行；
    # 唯一差别是「买入同时传非法平台与非法现金日」时错误码优先级倒转，两者皆 422）。
    if trade_type == "sell":
        if cash_platform_code:
            raise BusinessError(
                "CASH_PLATFORM_NOT_ALLOWED",
                "卖出交易在创建时不能指定到账平台，请在确认时传入 cash_platform_code",
            )
        if cash_confirm_date is not None:
            raise BusinessError(
                "CASH_CONFIRM_DATE_NOT_ALLOWED",
                "卖出交易在创建时不能指定到账日期，请在确认时传入 cash_confirm_date",
            )
    elif trade_type == "buy" and cash_confirm_date is not None:
        # 买入扣款日固定为下单日 T（创建即扣款），显式传入只接受等于 T
        if cash_confirm_date != trade_date:
            raise BusinessError(
                "CASH_CONFIRM_DATE_NOT_ALLOWED",
                f"买入交易的扣款日固定为下单日 {trade_date}，"
                f"cash_confirm_date 只接受等于该日",
            )

    # #91：现金腿平台规范化——与基金腿同平台时等价于不传；传入时校验存在
    if cash_platform_code == platform_code:
        cash_platform_code = None
    if cash_platform_code and not db.query(Platform).filter(
        Platform.code == cash_platform_code
    ).first():
        raise NotFoundError(
            "PLATFORM_NOT_FOUND", f"现金平台 {cash_platform_code} 不存在"
        )

    # 场内交易必须提供有效价格（实时撮合价，不能用收盘价替代）；
    # 任意市场显式传价均须为正数（卖出传价参与金额推导，负价会污染推导结果）
    if product.market == "CN_EXCHANGE" and price is None:
        raise BusinessError(
            "MISSING_OR_INVALID_PRICE",
            "场内交易必须提供有效的正数交易价格（--price）",
        )
    if price is not None and Decimal(str(price)) <= 0:
        raise BusinessError(
            "MISSING_OR_INVALID_PRICE",
            "交易价格必须为正数（--price）",
        )

    confirm_days = product.confirm_days or 0
    expected_confirm_date = get_next_trading_day(db, trade_date, days=confirm_days)
    # 手续费为金额字段，统一量化到 2 位（issue #94）
    fee_d = quantize_amount(fee) if fee else Decimal("0")
    price_d = Decimal(str(price)) if price else None

    if trade_type == "buy":
        # 买入现金口径：actual_amount 优先，否则 amount（含费现金支出）
        cash_out = actual_amount if actual_amount is not None else amount
        # 正值 + 量化 + 按扣款平台校验可用现金（#182 起与编辑/确认共用同一实现）
        cash_out_d = validate_buy_cash_with_addback(
            db, portfolio_code, cash_out,
            as_of=trade_date, cash_platform=cash_platform_code or platform_code,
        )
        net_amount = cash_out_d - fee_d
        shares_d = quantize_shares(net_amount / price_d) if price_d else Decimal("0")
        actual_amount_final = cash_out_d
        new_trade = Trade(
            portfolio_code=portfolio_code, product_code=product_code, market=market,
            platform_code=platform_code, trade_type="buy",
            shares=shares_d, amount=net_amount, price=price_d, fee=fee_d,
            actual_amount=actual_amount_final, trade_date=trade_date,
            confirm_date=expected_confirm_date, status="pending", notes=notes,
        )
    elif trade_type == "sell":
        # 份额校验（锚）：正值 + 量化 + 可用份额校验（#182 起与编辑/确认共用同一实现）
        shares_d = validate_sell_shares_with_addback(
            db, portfolio_code, product_code, market, shares, as_of=trade_date,
        )
        # 卖出金额为纯派生量（#190）：推导 + 对账口径单一实现于 _derive_sell_amounts
        # （与 PUT 侧共用）；输入层 amount / actual_amount 两参同义、actual_amount 优先
        input_actual = actual_amount if actual_amount is not None else amount
        gross_amount, actual_amount_final = _derive_sell_amounts(
            shares_d, price_d, fee_d, input_actual, market,
        )
        new_trade = Trade(
            portfolio_code=portfolio_code, product_code=product_code, market=market,
            platform_code=platform_code, trade_type="sell",
            shares=shares_d, amount=gross_amount, price=price_d, fee=fee_d,
            actual_amount=actual_amount_final, trade_date=trade_date,
            confirm_date=expected_confirm_date, status="pending", notes=notes,
        )
    else:
        raise BusinessError("INVALID_TYPE", "类型必须为 buy 或 sell")

    # 自然键防重（#82）：命中 pending/confirmed 同参数交易且未显式放行时拒绝
    if not allow_duplicate:
        candidates = db.query(Trade).filter(
            Trade.portfolio_code == portfolio_code,
            Trade.product_code == product_code,
            Trade.market == market,
            Trade.platform_code == platform_code,
            Trade.trade_type == trade_type,
            Trade.trade_date == trade_date,
            Trade.status.in_(["pending", "confirmed"]),
        ).all()
        existing = None
        for c in candidates:
            if trade_type == "buy":
                # 买入比对现金支出（actual_amount = cash_out，与落库值同口径）
                if c.actual_amount is not None and Decimal(str(c.actual_amount)) == cash_out_d:
                    existing = c
                    break
            else:
                # 卖出比对量化后份额（与落库值同口径）
                if c.shares is not None and Decimal(str(c.shares)) == shares_d:
                    existing = c
                    break
        if existing:
            raise BusinessError(
                "DUPLICATE_TRADE",
                f"存在相同参数的交易（id={existing.id}），如确为重复操作请检查，"
                f"如需强制创建请传 allow_duplicate",
                details={"existing_trade_id": existing.id},
            )

    # #493：组号在任何 flush 之前分配——买入据此建 CASH 扣款腿，卖出留作
    # 「有组号、无 CASH 腿」的半成品，待确认时补齐（unconfirm→再确认不换组）。
    new_trade.transfer_group = new_transfer_group()
    db.add(new_trade)
    cash_leg_audit = None
    if trade_type == "buy":
        # #493：买入创建即扣款——CASH sell 腿直接 confirmed，现金日 = 下单日 T。
        # 基金腿保持 pending（等待 T+1/T+2 净值确认），故 D 日快照不再被
        # pending 交易阻断，D 日现金已实扣、等额记 IN_TRANSIT_BUY。
        cash_leg = attach_paired_cash_leg(
            db, new_trade, actual_amount_final,
            status="confirmed",
            cash_platform_code=cash_platform_code,
        )
        db.flush()
        cash_leg_audit = cash_leg_audit_payload(cash_leg, "created")
    # 卖出不建 CASH 腿：到账日与到账平台在确认时录入（#493 决策 3）
    db.flush()

    record_audit(
        db,
        action=ACTION_CREATE,
        resource_type=RESOURCE_TRADE,
        resource_id=str(new_trade.id),
        resource_name=f"{portfolio_code}/{product_code}/{trade_type}",
        new_value={
            "portfolio_code": portfolio_code,
            "product_code": product_code,
            "market": market,
            "trade_type": trade_type,
            "trade_date": trade_date,
            "amount": new_trade.amount,
            "actual_amount": new_trade.actual_amount,
            "shares": new_trade.shares,
            "price": new_trade.price,
            "fee": new_trade.fee,
            "platform_code": new_trade.platform_code,
            "status": "pending",
            "transfer_group": new_trade.transfer_group,
            "cash_leg": cash_leg_audit,
        },
    )

    return new_trade


def _update_notes_only(db: Session, trade: Trade, update_data: dict) -> Trade:
    """notes-only 的非会计更新分支（#493 §3.1.7）。

    不重算金额、不镜像配对腿、不触发会计快照保护——备注对账本零影响；
    pending/confirmed 均可改（状态门与 CASH 腿旁路门已在 update_trade 入口放行）。
    """
    old_notes = trade.notes
    trade.notes = update_data["notes"]
    if old_notes != trade.notes:
        record_audit(
            db,
            action=ACTION_UPDATE,
            resource_type=RESOURCE_TRADE,
            resource_id=str(trade.id),
            resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
            old_value={"notes": old_notes},
            new_value={"notes": trade.notes},
        )
    return trade


def _update_confirmed_sell_arrival_date(
    db: Session, trade: Trade, update_data: dict
) -> Trade:
    """已确认卖出的到账日修正（#493 决策 3 / §3.1.7）。

    只动配对 CASH buy 腿的 `confirm_date`，**不调用普通基金金额重算**。
    入口已保证：不传 cash_confirm_date 不进本分支（保持原值）、显式 null 与被
    拒绝的混入字段在此前整体拒绝，故本分支命中即代表「合法子集」。
    """
    new_arrival = update_data["cash_confirm_date"]
    if new_arrival is None:
        raise BusinessError(
            "INVALID_PARAM",
            "cash_confirm_date 不能显式传 null；不需要修改时不传该字段",
        )
    cash_leg = _paired_cash_leg(db, trade)
    if cash_leg is None:
        raise BusinessError(
            "CASH_LEG_MISSING",
            "该卖出交易没有配对 CASH 腿，无法修改到账日；"
            "请先取消确认后重新确认并录入到账信息",
        )
    if not is_trading_day(db, new_arrival):
        raise BusinessError(
            "NON_TRADING_DAY", f"到账日 {new_arrival} 非交易日，请改为交易日"
        )
    if trade.confirm_date and new_arrival < trade.confirm_date:
        raise BusinessError(
            "INVALID_DATE_ORDER",
            f"到账日 {new_arrival} 不能早于基金确认日 {trade.confirm_date}",
        )
    # 组级保护 + 新到账日：旧值（组内各腿）与新值都不得落在已生成的快照区间
    validate_group_snapshot_free(db, trade, extra_dates=[new_arrival])

    old_value: dict = {}
    new_value: dict = {}
    if cash_leg.confirm_date != new_arrival:
        old_value["cash_leg"] = cash_leg_audit_payload(cash_leg, "arrival_date")
        cash_leg.confirm_date = new_arrival
        new_value["cash_leg"] = cash_leg_audit_payload(cash_leg, "arrival_date")
    if "notes" in update_data and update_data["notes"] != trade.notes:
        old_value["notes"] = trade.notes
        trade.notes = update_data["notes"]
        new_value["notes"] = trade.notes
    if old_value:
        record_audit(
            db,
            action=ACTION_UPDATE,
            resource_type=RESOURCE_TRADE,
            resource_id=str(trade.id),
            resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
            old_value=old_value,
            new_value=new_value,
        )
    return trade


def update_trade(db: Session, trade: Trade, update_data: dict) -> Trade:
    """PUT 直改 pending 交易（#182：校验全部通过前零 setattr，不 commit）。

    校验与联动与 create_trade 同口径（消除旁路实现漂移）：
    - 状态拦截：confirmed -> CANNOT_MODIFY_CONFIRMED；cancelled -> INVALID_STATUS；
      CASH 腿仅 notes 放行（CASH_TRADE_FORBIDDEN，防止配对腿金额被旁路改写）；
      组合须 active（PORTFOLIO_NOT_ACTIVE）
    - 金额语义（D1，与创建对齐）：buy 的 amount/actual_amount 均视为含费现金
      支出 X（actual_amount 优先），联动 actual_amount=X、amount=X−fee、配对
      CASH 腿=X；sell 有价格时与创建同口径（#190）：按新 shares/price/fee 重推导
      amount=quantize(shares×price)、actual_amount=amount−fee，显式金额仅作对账
      （场内超差拒绝、场外静默）；sell 无价格占位单保持输入为准
      （actual=X、amount=X+fee）
    - 可用量校验（amount/actual_amount/shares/trade_date 实际变动时触发，
      price/fee/notes-only 改动跳过）：trade_date 变动须为交易日且晚于最新
      快照日（D4，废除静默滚交易日）；buy 按 as_of=新 trade_date 校验扣款
      平台可用现金、sell 校验可用份额，均加回自身 pending 旧值
    - 自然键防重（D5）：trade_date/金额（买）/份额（卖）变动时按创建同口径
      比对，排除自身 id，无 allow_duplicate 逃生口（编辑撞车属误操作）
    - 组级快照保护（#493 决策 4）：组内任一非 cancelled 腿会计生效日及之后已有
      快照则拒绝；改日期时**新值**（新 trade_date 与其联动 confirm_date）一并纳入
    - trade_date 变动联动重算 confirm_date，并经 sync_transfer_group 同步
      配对 CASH 腿（日期/状态/金额镜像）

    Args:
        db: 数据库会话
        trade: 待修改交易（须为 pending）
        update_data: PUT 请求体（exclude_unset 后的字典）

    Returns:
        修改后的 trade 对象（未 commit，事务由调用方控制）
    """
    # ---- 1. 状态拦截（纯校验，尚未写入任何字段）----
    # ---- 1. 守卫（顺序即语义：CASH 腿旁路 → cancelled → confirmed → 组合状态）----
    # notes-only 是非会计更新：不改账、不镜像配对腿、不触发快照保护（#493 §3.1.7），
    # 故对 pending/confirmed 一律放行（cancelled 仍拒，组合状态仍须 active）。
    is_notes_only = set(update_data) <= {"notes"}
    if trade.product_code == "CASH" and not is_notes_only:
        raise BusinessError(
            "CASH_TRADE_FORBIDDEN",
            "CASH 腿不可直接修改，请编辑对应基金腿（仅 notes 放行）",
        )
    if trade.status == "cancelled":
        raise BusinessError("INVALID_STATUS", "已取消的交易不可修改")
    # confirmed 卖出的窄例外：只额外开放到账日修改（#493 决策 3/§3.1.7），
    # 且必须真的传了 cash_confirm_date；混入任何其他字段整体拒绝、不部分应用。
    confirmed_arrival_only = (
        trade.status == "confirmed"
        and trade.product_code != "CASH"
        and trade.trade_type == "sell"
        and "cash_confirm_date" in update_data
        and set(update_data) <= {"notes", "cash_confirm_date"}
    )
    if trade.status == "confirmed" and not is_notes_only and not confirmed_arrival_only:
        raise BusinessError(
            "CANNOT_MODIFY_CONFIRMED", "已确认的交易不可直接修改，请先取消确认后再修改"
        )
    portfolio = db.query(Portfolio).filter(
        Portfolio.code == trade.portfolio_code
    ).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", "组合不存在")
    if portfolio.status != "active":
        raise BusinessError("PORTFOLIO_NOT_ACTIVE", "组合未激活")

    if not update_data:
        return trade

    if is_notes_only:
        return _update_notes_only(db, trade, update_data)

    if confirmed_arrival_only:
        return _update_confirmed_sell_arrival_date(db, trade, update_data)

    # pending 交易不接受 cash_confirm_date：买入扣款日固定 T、卖出到账日在确认时录入
    if "cash_confirm_date" in update_data:
        raise BusinessError(
            "INVALID_PARAM",
            "cash_confirm_date 只在已确认卖出上可改；"
            "买入扣款日固定为下单日，卖出到账日在确认时录入",
        )

    # ---- 2. 语义归一（D1）与量化：数值字段 None 视为未提供 ----
    def _dec(key: str) -> Optional[Decimal]:
        value = update_data.get(key)
        return Decimal(str(value)) if value is not None else None

    amount_input = _dec("actual_amount")  # 同创建口径：actual_amount 优先
    if amount_input is None:
        amount_input = _dec("amount")
    shares_input = _dec("shares")
    fee_input = _dec("fee")
    price_input = _dec("price")
    trade_date_input = update_data.get("trade_date")

    old_fee = Decimal(str(trade.fee)) if trade.fee is not None else Decimal("0")
    new_fee = quantize_amount(fee_input) if fee_input is not None else quantize_amount(old_fee)
    new_trade_date = trade_date_input if trade_date_input is not None else trade.trade_date

    date_changed = trade_date_input is not None and trade_date_input != trade.trade_date
    amount_changed = amount_input is not None and (
        trade.actual_amount is None
        or quantize_amount(amount_input) != Decimal(str(trade.actual_amount))
    )
    shares_changed = shares_input is not None and (
        trade.shares is None
        or quantize_shares(shares_input) != Decimal(str(trade.shares))
    )

    # ---- 3. 校验（全部通过前零 setattr）----
    # 改日期先走既有交易日/快照日闸门（更具体的 NON_TRADING_DAY /
    # DATE_BEFORE_SNAPSHOT 优先），再做组级保护。
    # 新 confirm_date 在此算出并留给下方写入段复用（同一次计算，避免两处推导漂移）。
    new_confirm_date: Optional[date] = None
    if date_changed:
        validate_trade_date(db, trade.portfolio_code, trade_date_input)
        product = db.query(Product).filter(
            Product.code == trade.product_code, Product.market == trade.market
        ).first()
        new_confirm_date = get_next_trading_day(
            db, trade_date_input,
            days=(product.confirm_days or 0) if product else 0,
        )
    # 组级快照保护（#493 决策 4）：组内任一非 cancelled 腿确认日及之后已有快照则
    # 拒绝——半确认组里买入扣款腿的 T、卖出到账腿的 A 都在组内，故「日期后移」
    # 逃不过旧快照（旧值即组内各腿当前日期）。**改日期时新值一并纳入保护**
    # （`trade_date_input` 与其联动的 `confirm_date`）：当前 `validate_trade_date`
    # 强制新日期晚于最新快照日、该分支实际打不到，但保护不得依赖「恰有另一道闸门
    # 先拒绝」——放宽 `validate_trade_date` 时它会静默失效（#518 评审：承诺 > 实现）。
    validate_group_snapshot_free(
        db, trade,
        extra_dates=[trade_date_input, new_confirm_date] if date_changed else None,
    )

    if trade.trade_type == "buy" and (amount_changed or shares_changed or date_changed):
        # 待校验的含费现金支出：有输入用输入，否则沿用现有 actual_amount
        # （amount 列为净额，仅在 actual_amount 缺失时加 fee 反推）
        if amount_input is not None:
            base_cash_out = quantize_amount(amount_input)
        elif trade.actual_amount is not None:
            base_cash_out = Decimal(str(trade.actual_amount))
        elif trade.amount is not None:
            base_cash_out = Decimal(str(trade.amount)) + old_fee
        else:
            base_cash_out = None
        validate_buy_cash_with_addback(
            db, trade.portfolio_code, base_cash_out,
            as_of=new_trade_date, self_trade=trade,
        )
    elif trade.trade_type == "sell" and (shares_changed or date_changed):
        # sell 金额输入不再改落库值（有价格时仅对账、无价格时占位本就待覆盖），
        # 无需因 amount_changed 触发份额校验
        base_shares = shares_input if shares_input is not None else trade.shares
        validate_sell_shares_with_addback(
            db, trade.portfolio_code, trade.product_code, trade.market,
            base_shares, as_of=new_trade_date, self_trade=trade,
        )

    # 自然键防重（D5）：买比对含费支出（actual_amount）、卖比份额，排除自身
    dup_relevant = date_changed or (
        trade.trade_type == "buy" and amount_changed
    ) or (
        trade.trade_type == "sell" and shares_changed
    )
    if dup_relevant:
        if trade.trade_type == "buy":
            compare_value = (
                quantize_amount(amount_input) if amount_input is not None
                else Decimal(str(trade.actual_amount or 0))
            )
        else:
            compare_value = (
                quantize_shares(shares_input) if shares_input is not None
                else Decimal(str(trade.shares or 0))
            )
        candidates = db.query(Trade).filter(
            Trade.portfolio_code == trade.portfolio_code,
            Trade.product_code == trade.product_code,
            Trade.market == trade.market,
            Trade.platform_code == trade.platform_code,
            Trade.trade_type == trade.trade_type,
            Trade.trade_date == new_trade_date,
            Trade.status.in_(["pending", "confirmed"]),
            Trade.id != trade.id,
        ).all()
        for c in candidates:
            hit = False
            if trade.trade_type == "buy":
                hit = (
                    c.actual_amount is not None
                    and Decimal(str(c.actual_amount)) == compare_value
                )
            else:
                hit = (
                    c.shares is not None
                    and Decimal(str(c.shares)) == compare_value
                )
            if hit:
                raise BusinessError(
                    "DUPLICATE_TRADE",
                    f"存在相同参数的交易（id={c.id}），编辑后将与其他交易重复，请核对",
                    details={"existing_trade_id": c.id},
                )

    # ---- 4. 写入与联动（校验全部通过，此时才 setattr）----
    _audit_old = {
        "notes": trade.notes, "price": trade.price, "fee": trade.fee,
        "shares": trade.shares, "amount": trade.amount,
        "actual_amount": trade.actual_amount, "trade_date": trade.trade_date,
        "confirm_date": trade.confirm_date,
    }
    if "notes" in update_data:
        trade.notes = update_data["notes"]
    if price_input is not None:
        trade.price = price_input
    if fee_input is not None:
        trade.fee = new_fee
    if shares_input is not None:
        trade.shares = quantize_shares(shares_input)

    # D1 金额联动：buy actual_amount=X（含费支出）、amount=X−fee；
    # sell 有价格：与创建同口径按新 shares/price/fee 重推导（#190），
    # 显式金额仅作对账；sell 无价格占位单：actual_amount=X、amount=X+fee
    if trade.trade_type == "sell" and trade.product_code != "CASH":
        # CASH 腿（基金买的配对现金腿 trade_type 恰为 sell）金额由镜像维护，
        # 且仅 notes 放行，不参与派生重算
        final_shares = (
            quantize_shares(shares_input) if shares_input is not None
            else Decimal(str(trade.shares or 0))
        )
        final_price = price_input if price_input is not None else trade.price
        final_price = (
            Decimal(str(final_price)) if final_price is not None else None
        )
        if final_price is not None:
            # shares/price/fee 任一变动均自动随动；amount_input 仅对账
            trade.amount, trade.actual_amount = _derive_sell_amounts(
                final_shares, final_price, new_fee, amount_input, trade.market,
            )
        elif amount_input is not None or fee_input is not None:
            if amount_input is not None:
                x = quantize_amount(amount_input)
            elif trade.actual_amount is not None:
                x = Decimal(str(trade.actual_amount))
            elif trade.amount is not None:
                # actual_amount 缺失时按净额+fee 反推
                x = Decimal(str(trade.amount)) - old_fee
            else:
                x = None
            if x is not None:
                trade.actual_amount = x
                trade.amount = x + new_fee
    elif amount_input is not None or fee_input is not None:
        if amount_input is not None:
            x = quantize_amount(amount_input)
        elif trade.actual_amount is not None:
            x = Decimal(str(trade.actual_amount))
        elif trade.amount is not None:
            # actual_amount 缺失时按净额+fee 反推
            x = Decimal(str(trade.amount)) + old_fee
        else:
            x = None
        if x is not None:
            trade.actual_amount = x
            trade.amount = x - new_fee

    # trade_date 变动：联动重算 confirm_date（输入必为交易日，不再吞非交易日）。
    # 新值在步骤 3 已算好（同一份结果也进了组级保护的新值集合），此处只回写。
    if date_changed:
        trade.trade_date = trade_date_input
        trade.confirm_date = new_confirm_date

    # trade_date 变动 -> 同步配对 CASH 腿（#493 矩阵：买入扣款腿跟随 T；
    # 卖出 pending 组没有 CASH 腿，不制造 pending 调仓现金腿）
    cash_leg_audit = None
    if date_changed and trade.transfer_group:
        cash_leg_audit = sync_transfer_group(
            db, trade, trade.status, trade.confirm_date, propagate_dates=True
        )

    # 金额相关字段变动 -> 镜像配对 CASH 腿金额
    # （CASH 腿金额恒等于基金腿 actual_amount，与 sync_transfer_group 镜像规则一致）
    mirror_fields = {"amount", "actual_amount", "fee", "shares", "price"}
    if (
        trade.transfer_group
        and trade.product_code != "CASH"
        and set(update_data) & mirror_fields
    ):
        paired = db.query(Trade).filter(
            Trade.transfer_group == trade.transfer_group,
            Trade.id != trade.id,
            Trade.product_code == "CASH",
        ).first()
        if paired:
            mirror_amount = (
                trade.actual_amount if trade.actual_amount is not None else trade.amount
            )
            if mirror_amount is not None:
                paired.amount = mirror_amount
                paired.actual_amount = mirror_amount
                cash_leg_audit = cash_leg_audit_payload(paired, "amount_mirrored")

    _audit_new = {
        "notes": trade.notes, "price": trade.price, "fee": trade.fee,
        "shares": trade.shares, "amount": trade.amount,
        "actual_amount": trade.actual_amount, "trade_date": trade.trade_date,
        "confirm_date": trade.confirm_date,
    }
    _old_diff = {k: v for k, v in _audit_old.items() if v != _audit_new[k]}
    _new_diff = {k: _audit_new[k] for k in _old_diff}
    if cash_leg_audit is not None:
        _old_diff["cash_leg"] = None
        _new_diff["cash_leg"] = cash_leg_audit
    # 无实际变更不留痕（同 share_change_event_service 与「空删除不留痕」口径）。
    # 此处两侧恒为 Decimal——数值入参在函数开头已经 _dec() 归一，故不需要
    # audit_service._is_same 的跨类型比较
    if _old_diff:
        record_audit(
            db,
            action=ACTION_UPDATE,
            resource_type=RESOURCE_TRADE,
            resource_id=str(trade.id),
            resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
            old_value=_old_diff or None,
            new_value=_new_diff or None,
        )

    return trade


def cancel_trade(db: Session, trade: Trade) -> Trade:
    """取消交易（仅 pending + 非场内），整组回退（含配对 CASH 腿）。不 commit。

    #493：组级快照保护——组内任一腿确认日及之后已有快照即拒绝（已进快照的
    现金/在途不得被事后改写，须先删快照）。调仓 CASH 腿不可直接取消。
    """
    if _is_rebal_group_cash_leg(trade):
        raise BusinessError(
            "CASH_TRADE_FORBIDDEN",
            "调仓配对 CASH 腿不可直接取消，请取消对应基金腿",
        )
    if trade.status != "pending":
        raise BusinessError("INVALID_STATUS", "仅 pending 状态可取消")
    if trade.market == "CN_EXCHANGE":
        raise BusinessError(
            "CANNOT_CANCEL_EXCHANGE",
            "场内交易不可取消，请使用 PUT 修改字段或 DELETE 删除后重新创建",
        )
    validate_group_snapshot_free(db, trade)
    trade.status = "cancelled"
    cash_leg_audit = sync_transfer_group(db, trade, "cancelled")

    record_audit(
        db,
        action=ACTION_CANCEL,
        resource_type=RESOURCE_TRADE,
        resource_id=str(trade.id),
        resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
        old_value={"status": "pending"},
        new_value={
            "status": "cancelled",
            "transfer_group": trade.transfer_group,
            "cash_leg": cash_leg_audit,
        },
    )

    return trade


def unconfirm_trade(db: Session, trade: Trade) -> Trade:
    """取消确认（confirmed -> pending）：组级快照保护 + 重算 confirm_date + 整组回退。

    #493 操作矩阵：**买入组保留 CASH 扣款腿 confirmed**（扣款是既成事实，日期与
    金额都不动，等待基金腿重新确认）；**卖出组删除配对 CASH 腿**（回到「未创建」
    态，再次确认时重建并重新录入到账日）。调仓 CASH 腿不可直接取消确认。
    """
    if _is_rebal_group_cash_leg(trade):
        raise BusinessError(
            "CASH_TRADE_FORBIDDEN",
            "调仓配对 CASH 腿不可直接取消确认，请取消确认对应基金腿",
        )
    if trade.status != "confirmed":
        raise BusinessError("INVALID_STATUS", "仅 confirmed 状态可取消确认")

    # 组级快照保护（#493 决策 4）：组内任一腿确认日及之后已有快照则拒绝
    validate_group_snapshot_free(db, trade)

    trade.status = "pending"
    # 重新计算期望确认日（创建时设定 confirm_date，unconfirm 后需恢复）
    if trade.product_code and trade.market:
        product = db.query(Product).filter(
            Product.code == trade.product_code, Product.market == trade.market
        ).first()
        if product:
            trade.confirm_date = get_next_trading_day(
                db, trade.trade_date, days=product.confirm_days or 0
            )
    cash_leg_audit = sync_transfer_group(db, trade, "pending")

    record_audit(
        db,
        action=ACTION_UNCONFIRM,
        resource_type=RESOURCE_TRADE,
        resource_id=str(trade.id),
        resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
        old_value={"status": "confirmed"},
        new_value={
            "status": "pending",
            "confirm_date": trade.confirm_date,
            "transfer_group": trade.transfer_group,
            "cash_leg": cash_leg_audit,
        },
    )

    return trade


def delete_trade(db: Session, trade: Trade) -> None:
    """删除交易（confirmed 不可直接删除），整组级联删除配对 CASH 腿。不 commit。

    #493：组级快照保护——半确认组的买入扣款腿可能已进快照，删除会让快照失去
    对应事实，故任一**非 cancelled** 腿确认日及之后有快照即拒绝（须先删快照）；
    cancelled 腿（整组取消后）无会计效力，删除它们对历史零影响，不纳入保护
    （#518 评审：否则「当天取消 → 当天快照」后整组永久删不掉）。调仓 CASH 腿
    不可直接删除（只能由基金腿驱动）。
    """
    if _is_rebal_group_cash_leg(trade):
        raise BusinessError(
            "CASH_TRADE_FORBIDDEN",
            "调仓配对 CASH 腿不可直接删除，请删除对应基金腿",
        )
    if trade.status == "confirmed":
        raise BusinessError(
            "CANNOT_DELETE_CONFIRMED",
            "已确认的交易不可直接删除，请先取消确认后再删除",
        )
    validate_group_snapshot_free(db, trade)

    record_audit(
        db,
        action=ACTION_DELETE,
        resource_type=RESOURCE_TRADE,
        resource_id=str(trade.id),
        resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
        old_value={
            "status": trade.status,
            "trade_type": trade.trade_type,
            "trade_date": trade.trade_date,
            "amount": trade.amount,
            "shares": trade.shares,
            "transfer_group": trade.transfer_group,
        },
    )

    if trade.transfer_group:
        db.query(Trade).filter(
            Trade.transfer_group == trade.transfer_group,
            Trade.id != trade.id,
        ).delete(synchronize_session=False)

    db.delete(trade)


def list_trades(
    db: Session,
    *,
    portfolio_code: Optional[str] = None,
    status: Optional[str] = None,
    trade_type: Optional[str] = None,
    product_code: Optional[str] = None,
    market: Optional[str] = None,
    products: Optional[str] = None,
    platform_code: Optional[str] = None,
    trade_date_start: Optional[date] = None,
    trade_date_end: Optional[date] = None,
    confirm_date_start: Optional[date] = None,
    confirm_date_end: Optional[date] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Trade], int]:
    """调仓交易列表查询（服务端筛选 + 分页，issue #126）。

    - 日期区间均为闭区间；同组 start > end 抛 INVALID_DATE_RANGE（422）。
    - product_code 与 market 独立可选：都给则精确过滤（LOF 一码多市场场景）；
      只给 product_code 则跨市场全匹配。
    - products（issue #155）：逗号分隔的 `code|market` 复合值多选过滤，
      market 段可为空字符串（如 `CASH|` 匹配 market="" 的现金腿；缺省 `|`
      时按空 market 处理）。解析为 (code, market) 对后逐对精确匹配、对间 OR。
      空段/空串忽略，解析后全空视为未传。
      与单值参数互斥：products 与 product_code 或 market 同传抛
      PRODUCTS_PARAM_CONFLICT（422），避免两套过滤语义叠加产生歧义。
    - 排序：trade_date DESC, transfer_group, id DESC——transfer_group 入排序键
      使同组两腿大概率同页相邻（同组两腿同事务插入 id 连续，此为双保险）。
    - trades 无 viewer 过滤（组合级操作，保持现状语义，不在本 PR 引入权限变化）。

    Returns:
        (items, total)：当前页记录与过滤后总数（分页前）。
    """
    if trade_date_start and trade_date_end and trade_date_start > trade_date_end:
        raise BusinessError(
            "INVALID_DATE_RANGE",
            f"start_date ({trade_date_start}) 不能晚于 end_date ({trade_date_end})",
            http_status=422,
        )
    if confirm_date_start and confirm_date_end and confirm_date_start > confirm_date_end:
        raise BusinessError(
            "INVALID_DATE_RANGE",
            f"start_date ({confirm_date_start}) 不能晚于 end_date ({confirm_date_end})",
            http_status=422,
        )

    # products 多选解析（issue #155）："A|CN_OTC,B|CN_EXCHANGE" → [(A, CN_OTC), (B, CN_EXCHANGE)]
    product_pairs: list[tuple[str, str]] = []
    if products:
        for part in products.split(","):
            part = part.strip()
            if not part:
                continue
            code, _, mkt = part.partition("|")
            if code:
                product_pairs.append((code, mkt))
    if product_pairs and (product_code or market is not None):
        raise BusinessError(
            "PRODUCTS_PARAM_CONFLICT",
            "products 与 product_code/market 互斥，不能同时传参",
            http_status=422,
        )

    query = db.query(Trade)
    if portfolio_code:
        query = query.filter(Trade.portfolio_code == portfolio_code)
    if status:
        query = query.filter(Trade.status == status)
    if trade_type:
        query = query.filter(Trade.trade_type == trade_type)
    if product_pairs:
        query = query.filter(or_(*(
            and_(Trade.product_code == code, Trade.market == mkt)
            for code, mkt in product_pairs
        )))
    if product_code:
        query = query.filter(Trade.product_code == product_code)
    if market is not None:
        query = query.filter(Trade.market == market)
    if platform_code:
        query = query.filter(Trade.platform_code == platform_code)
    if trade_date_start:
        query = query.filter(Trade.trade_date >= trade_date_start)
    if trade_date_end:
        query = query.filter(Trade.trade_date <= trade_date_end)
    if confirm_date_start:
        query = query.filter(Trade.confirm_date >= confirm_date_start)
    if confirm_date_end:
        query = query.filter(Trade.confirm_date <= confirm_date_end)

    total = query.count()
    items = (
        query.order_by(Trade.trade_date.desc(), Trade.transfer_group, Trade.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def build_paired_cash_leg_map(db: Session, trades: list) -> dict:
    """批量读取基金腿的反向配对 CASH 腿（#493 §3.3 读侧单一实现）。

    Returns:
        {(portfolio_code, transfer_group): Trade}——**只**收录基金腿所在组，
        方向取与基金腿相反的那条（买→CASH sell、卖→CASH buy）。

    设计要点：
    - 一次查询覆盖整页，**不受**列表的 status/platform/分页筛选影响（被筛掉的
      现金腿仍能回填到账信息），也不随行数产生 N+1 查询；
    - 不要求两腿同状态（半确认组：基金 pending / CASH confirmed 同样可见）；
    - CASH 腿自身不收录（派生字段只服务基金腿）；
    - 组号为空或非基金腿的行直接跳过。
    """
    expected_type: dict = {}
    groups = set()
    portfolios = set()
    for trade in trades:
        if trade is None or trade.product_code == "CASH" or not trade.transfer_group:
            continue
        key = (trade.portfolio_code, trade.transfer_group)
        expected_type[key] = "sell" if trade.trade_type == "buy" else "buy"
        groups.add(trade.transfer_group)
        portfolios.add(trade.portfolio_code)
    if not groups:
        return {}

    legs = db.query(Trade).filter(
        Trade.product_code == "CASH",
        Trade.transfer_group.in_(groups),
        Trade.portfolio_code.in_(portfolios),
    ).all()

    mapping: dict = {}
    for leg in legs:
        key = (leg.portfolio_code, leg.transfer_group)
        if expected_type.get(key) == leg.trade_type:
            mapping[key] = leg
    return mapping
