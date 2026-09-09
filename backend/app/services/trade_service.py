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

from sqlalchemy import and_, or_
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
from app.utils.quantize import quantize_amount, quantize_shares

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


def _pending_cash_sell_legs(db: Session, trade: Trade) -> list:
    """查 trade 所在 transfer_group 的 pending CASH sell 腿（买入扣款腿，#91）"""
    if not trade or not trade.transfer_group:
        return []
    return db.query(Trade).filter(
        Trade.transfer_group == trade.transfer_group,
        Trade.product_code == "CASH",
        Trade.trade_type == "sell",
        Trade.status == "pending",
    ).all()


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
    - 扣款平台：self_trade 的配对 pending CASH sell 腿平台（#91 跨平台扣款，
      同 confirm_single_trade 模式），无配对腿时回退 cash_platform
      （创建场景 = cash_platform_code or 基金腿平台），再回退 self_trade 平台
    - 可用现金 = calculate_available_cash(as_of) + 自身 pending CASH sell 腿
      当前 DB 金额（编辑/确认场景该腿已被计提预留，加回防双重计数；创建加回 0）
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

    own_legs = _pending_cash_sell_legs(db, self_trade) if self_trade else []
    check_platform = cash_platform
    if own_legs:
        check_platform = own_legs[0].platform_code
    if check_platform is None and self_trade is not None:
        check_platform = self_trade.platform_code

    available = calculate_available_cash(
        db, portfolio_code, check_platform, as_of_date=as_of
    )
    # 加回自身在途 CASH sell 腿：该腿已作为 pending sell 计提预留，
    # 不加回会与新支出双重计数导致误拒
    own_addback = sum(
        (Decimal(str(leg.amount or 0)) for leg in own_legs), Decimal("0")
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


def attach_paired_cash_leg(
    db: Session,
    fund_trade: Trade,
    cash_amount: Decimal,
    confirm_date: Optional[date],
    status: str = "pending",
    cash_platform_code: Optional[str] = None,
    cash_confirm_date: Optional[date] = None,
) -> Trade:
    """为基金腿生成 transfer_group 并构造/加入配对 CASH 腿。

    cash_amount 采用 router 语义 = 基金腿 actual_amount（买入=支出含费，卖出=收入）。
    cash_platform_code（issue #91）：CASH 腿平台，缺省同基金腿；传入时支持
    跨平台扣款（买）/到账（卖），两腿同 transfer_group 原子翻转。
    cash_confirm_date（#93）：CASH 腿独立确认日，缺省时按基金腿方向推导——
    买入扣款 T 日即扣（=trade_date），卖出到账默认与基金确认日一致（无延迟）。
    供 REST router 与后端 CLI 共用，确保基金买/卖必配 CASH 腿。

    Returns:
        新建的 CASH 腿（已 db.add，未 commit）
    """
    group = f"rebal_{uuid.uuid4().hex[:12]}"
    fund_trade.transfer_group = group
    # #93: CASH 腿确认日独立于基金腿
    if cash_confirm_date is None:
        if fund_trade.trade_type == "buy":
            # 买入扣款：T日即扣
            effective_cash_confirm = fund_trade.trade_date
        else:
            # 卖出到账：默认与基金确认日一致（无延迟）
            effective_cash_confirm = confirm_date
    else:
        effective_cash_confirm = cash_confirm_date
    cash_trade = Trade(
        portfolio_code=fund_trade.portfolio_code,
        platform_code=cash_platform_code or fund_trade.platform_code,
        product_code="CASH",
        market="",
        trade_type="sell" if fund_trade.trade_type == "buy" else "buy",
        shares=None,
        amount=cash_amount,
        price=Decimal("1"),
        fee=Decimal("0"),
        actual_amount=cash_amount,
        trade_date=fund_trade.trade_date,
        confirm_date=effective_cash_confirm,
        status=status,
        transfer_group=group,
    )
    db.add(cash_trade)
    return cash_trade


def sync_transfer_group(
    db: Session, trade: Trade, target_status: str, confirm_date: Optional[date] = None
):
    """同步 transfer_group 关联的另一腿状态、日期与金额

    - 传播 `trade.trade_date`（组内不变量：同 transfer_group 各腿 trade_date 恒等；
      PUT 改基金腿 trade_date 时据此随动 CASH 腿，其余路径为幂等写入）
    - 传播 `target_status`（unconfirm 时各腿独立重算期望确认日，#93）
    - #93: 不再传播 confirm_date，各腿保持创建时设定的独立确认日
    - 若源腿为基金腿（product_code != "CASH"），将其 actual_amount 镜像给配对
      CASH 腿（CASH 腿金额恒等于基金腿 actual_amount）。确认时净值型基金
      actual_amount 可能被重算，需同步；金额未变时为幂等写入
    """
    if not trade.transfer_group:
        return
    paired = db.query(Trade).filter(
        Trade.transfer_group == trade.transfer_group,
        Trade.id != trade.id,
    ).all()
    # CASH 腿金额 = 基金腿 actual_amount（买入=支出、卖出=收入）；
    # 仅当源腿为基金腿时向 CASH 腿镜像，避免 CASH→CASH / CASH→基金误写
    mirror_amount = None
    if trade.product_code != "CASH":
        mirror_amount = trade.actual_amount if trade.actual_amount is not None else trade.amount
    # #37 savepoint：配对腿更新失败回滚 savepoint，不影响外层事务
    # 用连接级 SAVEPOINT（db.connection().begin_nested()）而非 session 级
    # db.begin_nested()，避免触发 after_transaction_end 事件与测试隔离监听器冲突
    sp = db.connection().begin_nested()
    try:
        for paired_trade in paired:
            # 先同步 trade_date，保证下方 unconfirm 分支用新日期重算确认日
            paired_trade.trade_date = trade.trade_date
            paired_trade.status = target_status
            # #93: 各腿保持创建时设定的独立确认日，不再同步 confirm_date
            if target_status == "pending":
                if paired_trade.product_code == "CASH":
                    # #93: unconfirm 时 CASH 腿回退到创建时的默认确认日
                    if paired_trade.trade_type == "sell":
                        # 买入的 CASH sell：回退到 trade_date（T日扣款）
                        paired_trade.confirm_date = paired_trade.trade_date
                    else:
                        # 卖出的 CASH buy：回退到基金确认日（默认一致，无延迟）
                        fund_leg = next(
                            (p for p in paired if p.product_code != "CASH"), None
                        )
                        # 源腿本身可能是基金腿（unconfirm_trade 场景），补充查找
                        if fund_leg is None and trade.product_code != "CASH":
                            fund_leg = trade
                        if fund_leg and fund_leg.confirm_date:
                            paired_trade.confirm_date = fund_leg.confirm_date
                else:
                    # unconfirm 时需要重新计算期望确认日（基金腿按 product.confirm_days）
                    paired_product = db.query(Product).filter(
                        Product.code == paired_trade.product_code,
                        Product.market == paired_trade.market,
                    ).first()
                    if paired_product:
                        paired_confirm_days = paired_product.confirm_days or 0
                        paired_trade.confirm_date = get_next_trading_day(
                            db, paired_trade.trade_date, days=paired_confirm_days
                        )
            # 同步配对 CASH 腿金额（确认时净值重算后需同步）
            if mirror_amount is not None and paired_trade.product_code == "CASH":
                paired_trade.amount = mirror_amount
                paired_trade.actual_amount = mirror_amount
        db.flush()  # 确保 ORM 变更在 savepoint 内写入 DB
        sp.commit()
    except Exception:
        sp.rollback()
        raise


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
        if price is not None:
            input_price = Decimal(str(price)).quantize(Decimal("0.0001"))
            if input_price != nav_price.quantize(Decimal("0.0001")):
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


def confirm_single_trade(
    db: Session,
    trade: Trade,
    product: Optional[Product],
    *,
    confirm_date: Optional[date] = None,
    price: Optional[Decimal] = None,
    skip_available_check: bool = False,
    sync_nav: bool = False,
) -> Trade:
    """
    确认单笔调仓交易的核心逻辑，供手动确认与 auto_confirm 共用。

    - 计算统一委托 calculate_confirm_preview（确认与预览共用同一实现），
      本函数负责将结果回写 trade 并置 confirmed
    - confirm_date 已在创建时设定；若传入参数则覆盖（补录场景）
    - 场外基金（OEF/LOF 且 CN_OTC/HK_MUTUAL）确认时统一获取 T 日（成交当日）净值并重算 shares/amount，
      不区分 QDII/非 QDII，一律以净值计算；缺失 T 日净值时抛 MISSING_NAV 拒绝确认
    - sync_nav=True（issue #90，显式选择）：命中 MISSING_NAV 时自动回填该标的历史净值
      后重试一次；同步后仍缺失则照常抛 MISSING_NAV
    - 场外基金若传入 price 仅作一致性校验：须与 T 日净值相等，否则抛 PRICE_NAV_MISMATCH，
      手动价不覆盖净值（不传则直接取净值）
    - 场内基金不取净值，使用创建时录入的成交价（成交价录入时必填，见 trades.py 创建校验）
    - 可用量校验（#70/#78 按生效确认日时点口径，#182 卖出份额对称补齐）：
      买入按扣款平台校验可用现金、卖出校验可用份额（均加回自身 pending 旧值
      防双重计数），不足抛 INSUFFICIENT_CASH / INSUFFICIENT_SHARES；
      skip_available_check=True 跳过（auto_confirm 重算历史场景）
    - 置 status=confirmed 并原子同步配对 CASH 腿

    Args:
        db: 数据库会话
        trade: 待确认交易（须为 pending）
        product: 交易对应产品（CASH/未知产品可为 None，跳过净值逻辑）
        confirm_date: 覆盖确认日（补录场景）
        price: 手动价格；场外基金仅用于与 T 日净值一致性校验（不覆盖净值），
            场内基金作为覆盖成交价
        skip_available_check: 跳过买入现金/卖出份额可用量校验（auto_confirm 专用）
        sync_nav: MISSING_NAV 时自动回填净值并重试一次（显式选择，会访问外部数据源）

    Returns:
        确认后的 trade 对象（未 commit，事务由调用方控制）
    """
    try:
        preview = calculate_confirm_preview(
            db, trade, product, confirm_date=confirm_date, price=price
        )
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
        preview = calculate_confirm_preview(
            db, trade, product, confirm_date=confirm_date, price=price
        )

    # 可用量校验（#70/#78：按生效确认日时点口径；#182 卖出份额对称补齐）
    if not skip_available_check and trade.product_code != "CASH":
        effective_confirm_date = (
            confirm_date if confirm_date is not None else trade.confirm_date
        )
        if trade.trade_type == "buy":
            # 买入：按扣款平台校验可用现金（配对 pending CASH sell 腿平台 + 自身腿加回，
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
    # 同步 transfer_group 配对腿（如基金调仓的配对 CASH 腿）
    sync_transfer_group(db, trade, "confirmed", trade.confirm_date)

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
    缺省同基金腿；买入可用现金按扣款平台校验。
    cash_confirm_date（#93）：CASH 腿独立确认日（卖出到账日），缺省时由
    attach_paired_cash_leg 按基金腿方向推导（买入=T日扣款，卖出=基金确认日）。
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

    # 基金腿必配 CASH 腿（显式记录现金变动；#91 支持跨平台现金腿）
    db.add(new_trade)
    attach_paired_cash_leg(
        db, new_trade, actual_amount_final, expected_confirm_date,
        cash_platform_code=cash_platform_code,
        cash_confirm_date=cash_confirm_date,
    )
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
        },
    )

    return new_trade


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
    if trade.status == "confirmed":
        raise BusinessError(
            "CANNOT_MODIFY_CONFIRMED", "已确认的交易不可直接修改，请先取消确认后再修改"
        )
    if trade.status == "cancelled":
        raise BusinessError("INVALID_STATUS", "已取消的交易不可修改")
    if trade.product_code == "CASH" and set(update_data) - {"notes"}:
        raise BusinessError(
            "CASH_TRADE_FORBIDDEN",
            "CASH 腿不可直接修改，请编辑对应基金腿（仅 notes 放行）",
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
    if date_changed:
        validate_trade_date(db, trade.portfolio_code, trade_date_input)

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

    # trade_date 变动：联动重算 confirm_date（输入必为交易日，不再吞非交易日）
    if date_changed:
        trade.trade_date = trade_date_input
        product = db.query(Product).filter(
            Product.code == trade.product_code, Product.market == trade.market
        ).first()
        trade.confirm_date = get_next_trading_day(
            db, trade.trade_date,
            days=(product.confirm_days or 0) if product else 0,
        )

    # trade_date 变动 -> 同步配对 CASH 腿（trade_date/状态/确认日回退/金额镜像）
    if date_changed and trade.transfer_group:
        sync_transfer_group(db, trade, trade.status, trade.confirm_date)

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

    _audit_new = {
        "notes": trade.notes, "price": trade.price, "fee": trade.fee,
        "shares": trade.shares, "amount": trade.amount,
        "actual_amount": trade.actual_amount, "trade_date": trade.trade_date,
        "confirm_date": trade.confirm_date,
    }
    _old_diff = {k: v for k, v in _audit_old.items() if v != _audit_new[k]}
    _new_diff = {k: _audit_new[k] for k in _old_diff}
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
    """取消交易（仅 pending + 非场内），并同步配对 CASH 腿。不 commit。"""
    if trade.status != "pending":
        raise BusinessError("INVALID_STATUS", "仅 pending 状态可取消")
    if trade.market == "CN_EXCHANGE":
        raise BusinessError(
            "CANNOT_CANCEL_EXCHANGE",
            "场内交易不可取消，请使用 PUT 修改字段或 DELETE 删除后重新创建",
        )
    trade.status = "cancelled"
    sync_transfer_group(db, trade, "cancelled")

    record_audit(
        db,
        action=ACTION_CANCEL,
        resource_type=RESOURCE_TRADE,
        resource_id=str(trade.id),
        resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
        old_value={"status": "pending"},
        new_value={"status": "cancelled"},
    )

    return trade


def unconfirm_trade(db: Session, trade: Trade) -> Trade:
    """取消确认（confirmed -> pending）：快照保护 + 重算 confirm_date + 同步配对腿。不 commit。"""
    from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
    from sqlalchemy import func as _func

    if trade.status != "confirmed":
        raise BusinessError("INVALID_STATUS", "仅 confirmed 状态可取消确认")

    # 快照保护：确认日及之后已有快照则拒绝
    if trade.confirm_date:
        snapshots_after = (
            db.query(_func.count(PortfolioValueSnapshot.id))
            .filter(
                PortfolioValueSnapshot.portfolio_code == trade.portfolio_code,
                PortfolioValueSnapshot.snapshot_date >= trade.confirm_date,
            )
            .scalar()
        )
        if snapshots_after and snapshots_after > 0:
            raise BusinessError(
                "SNAPSHOT_DEPENDENCY",
                f"该交易已被快照纳入（{trade.confirm_date} 及之后有 {snapshots_after} 张快照），"
                f"请先删除 {trade.confirm_date} 及之后的快照",
            )

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
    sync_transfer_group(db, trade, "pending")

    record_audit(
        db,
        action=ACTION_UNCONFIRM,
        resource_type=RESOURCE_TRADE,
        resource_id=str(trade.id),
        resource_name=f"{trade.portfolio_code}/{trade.product_code}/{trade.trade_type}",
        old_value={"status": "confirmed"},
        new_value={"status": "pending", "confirm_date": trade.confirm_date},
    )

    return trade


def delete_trade(db: Session, trade: Trade) -> None:
    """删除交易（confirmed 不可直接删除），级联删除配对 CASH 腿。不 commit。"""
    if trade.status == "confirmed":
        raise BusinessError(
            "CANNOT_DELETE_CONFIRMED",
            "已确认的交易不可直接删除，请先取消确认后再删除",
        )

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
