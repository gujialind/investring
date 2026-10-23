# ============================================================================
# InvestRing 测试数据工厂 (factories.py)
# ============================================================================
# 提供快速创建测试数据的辅助函数。
# 所有函数接收 db session 作为第一个参数，并在创建后自动 commit。
# ============================================================================

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
import uuid

from sqlalchemy.orm import Session

from app.models import (
    Investor, Portfolio, Product, Platform,
    AssetClassification, TradingCalendar, PriceRecord,
    PortfolioPosition, PortfolioValueSnapshot, InvestorHolding,
    Subscription, Trade, ShareChangeEvent,
    SyncJob, ManualMarketValue, Notification, IdempotencyCache,
)
from app.utils.security import get_password_hash


# ---------------------------------------------------------------------------
# 投资人
# ---------------------------------------------------------------------------

def create_investor(
    db: Session,
    code: str = "INV001",
    name: str = "测试投资人",
    role: str = "viewer",
    password: str = "test123",
) -> Investor:
    """创建投资人，如已存在则直接返回"""
    existing = db.query(Investor).filter(Investor.code == code).first()
    if existing:
        return existing
    investor = Investor(
        code=code, name=name, role=role,
        password_hash=get_password_hash(password),
    )
    db.add(investor)
    db.commit()
    db.refresh(investor)
    return investor


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------

def create_portfolio(
    db: Session,
    code: str = "PORT001",
    name: str = "测试组合",
    status: str = "draft",
    auto_snapshot_enabled: bool = False,
) -> Portfolio:
    """创建组合，如已存在则直接返回"""
    existing = db.query(Portfolio).filter(Portfolio.code == code).first()
    if existing:
        return existing
    portfolio = Portfolio(
        code=code, name=name, description="测试", status=status,
        auto_snapshot_enabled=auto_snapshot_enabled,
    )
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


# ---------------------------------------------------------------------------
# 平台
# ---------------------------------------------------------------------------

def create_platform(
    db: Session,
    code: str = "PLAT001",
    name: str = "测试平台",
    platform_type: str = "第三方平台",
) -> Platform:
    """创建平台，如已存在则直接返回"""
    existing = db.query(Platform).filter(Platform.code == code).first()
    if existing:
        return existing
    platform = Platform(code=code, name=name, platform_type=platform_type)
    db.add(platform)
    db.commit()
    db.refresh(platform)
    return platform


# ---------------------------------------------------------------------------
# 资产分类
# ---------------------------------------------------------------------------

def create_asset_classification(
    db: Session,
    code: str = "TEST_ASSET",
    dimension: str = "asset_class",
    name: str = "测试分类",
    sort_order: int = 0,
) -> AssetClassification:
    existing = db.query(AssetClassification).filter(AssetClassification.code == code).first()
    if existing:
        return existing
    ac = AssetClassification(
        code=code, dimension=dimension, name=name, sort_order=sort_order,
    )
    db.add(ac)
    db.commit()
    db.refresh(ac)
    return ac


# ---------------------------------------------------------------------------
# 产品
# ---------------------------------------------------------------------------

def create_product(
    db: Session,
    code: str = "000001.OF",
    market: str = "CN_OTC",
    name: str = "测试基金",
    product_type: str = "OEF",
    asset_class_code: str = "ASSET_STOCK",
    region_code: str = "REGION_CN",
    style_code: str = "STYLE_BALANCED",
    size_code: str = "SIZE_LARGE",
    segment_code: str = "SEG_COMPOSITE",
    confirm_days: int = 1,
    is_qdii: bool = False,
    nav_lag_days: int = 0,
) -> Product:
    """创建产品，如已存在则直接返回（维度值默认取 conftest 已种子的字典值）"""
    existing = db.query(Product).filter(
        Product.code == code, Product.market == market
    ).first()
    if existing:
        return existing

    # 确保维度值存在（默认值在 conftest 种子中已存在，自定义 code 时兜底）
    for dim_code in (asset_class_code, region_code, style_code, size_code, segment_code):
        if dim_code and not db.query(AssetClassification).filter(
            AssetClassification.code == dim_code
        ).first():
            create_asset_classification(db, code=dim_code)

    product = Product(
        code=code, market=market, name=name,
        product_type=product_type, asset_class_code=asset_class_code,
        region_code=region_code, style_code=style_code,
        size_code=size_code, segment_code=segment_code,
        confirm_days=confirm_days, is_qdii=is_qdii,
        nav_lag_days=nav_lag_days,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


# ---------------------------------------------------------------------------
# 价格记录
# ---------------------------------------------------------------------------

def create_price_record(
    db: Session,
    product_code: str,
    market: str,
    record_date: date,
    unit_price: float,
) -> PriceRecord:
    """创建价格记录"""
    record = PriceRecord(
        product_code=product_code, market=market,
        price_date=record_date, unit_price=Decimal(str(unit_price)),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


# ---------------------------------------------------------------------------
# 交易日历
# ---------------------------------------------------------------------------

def ensure_trading_day(
    db: Session, d: date, is_open: bool = True, exchange: str = "SSE"
) -> TradingCalendar:
    """确保指定日期在 exchange 日历中存在（#777 双日历后按 (date, exchange) 判定）"""
    existing = db.query(TradingCalendar).filter(
        TradingCalendar.calendar_date == d,
        TradingCalendar.exchange == exchange,
    ).first()
    if existing:
        return existing
    cal = TradingCalendar(calendar_date=d, is_open=is_open, exchange=exchange)
    db.add(cal)
    db.commit()
    db.refresh(cal)
    return cal


# ---------------------------------------------------------------------------
# 申购 / 赎回
# ---------------------------------------------------------------------------

def create_subscription(
    db: Session,
    portfolio_code: str,
    investor_code: str,
    sub_type: str = "subscribe",
    amount: float = 10000.0,
    shares: Optional[float] = None,
    unit_price: Optional[float] = None,
    apply_date: date = date(2025, 1, 6),
    confirm_date: Optional[date] = None,
    status: str = "pending",
    platform_code: str = "MYCF",
) -> Subscription:
    """创建申购/赎回记录"""
    sub = Subscription(
        portfolio_code=portfolio_code,
        investor_code=investor_code,
        platform_code=platform_code,
        sub_type=sub_type,
        amount=Decimal(str(amount)),
        shares=Decimal(str(shares)) if shares else None,
        unit_price=Decimal(str(unit_price)) if unit_price else None,
        apply_date=apply_date,
        confirm_date=confirm_date,
        status=status,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


# ---------------------------------------------------------------------------
# 调仓交易
# ---------------------------------------------------------------------------

def create_trade(
    db: Session,
    portfolio_code: str,
    product_code: str,
    market: str,
    trade_type: str = "buy",
    amount: float = 5000.0,
    shares: Optional[float] = None,
    price: Optional[float] = 1.5,
    fee: float = 0.0,
    platform_code: str = "MYCF",
    trade_date: date = date(2025, 1, 6),
    confirm_date: Optional[date] = None,
    actual_amount: Optional[float] = None,
    transfer_group: Optional[str] = None,
    status: str = "pending",
    notes: Optional[str] = None,
) -> Trade:
    """创建调仓交易记录。

    confirm_date：Trade 创建时按 product.confirm_days 计算，测试中可显式传入。
    transfer_group：配对 CASH trade 的关联标识；未传时自动生成唯一占位值
        （模型层 transfer_group NOT NULL，且满足 uq_trade_transfer_group 唯一约束）。
    """
    trade = Trade(
        portfolio_code=portfolio_code,
        platform_code=platform_code,
        product_code=product_code,
        market=market,
        trade_type=trade_type,
        transfer_group=transfer_group or f"test_{uuid.uuid4().hex[:12]}",
        amount=Decimal(str(amount)),
        shares=Decimal(str(shares)) if shares is not None else None,
        price=Decimal(str(price)) if price is not None else None,
        fee=Decimal(str(fee)),
        actual_amount=Decimal(str(actual_amount)) if actual_amount is not None else None,
        trade_date=trade_date,
        confirm_date=confirm_date,
        status=status,
        notes=notes,
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


# ---------------------------------------------------------------------------
# 持仓快照（PortfolioPosition）
# ---------------------------------------------------------------------------

def create_position_snapshot(
    db: Session,
    portfolio_code: str,
    product_code: str,
    market: str,
    snapshot_date: date,
    shares: Optional[float] = None,
    cash_amount: Optional[float] = None,
    unit_price: Optional[float] = 1.5,
    cost_price: Optional[float] = 1.5,
    market_value: float = 0.0,
    platform_code: Optional[str] = None,
    frozen_shares: Optional[float] = None,
    frozen_amount: Optional[float] = None,
) -> PortfolioPosition:
    """创建持仓快照。

    CHECK 约束：shares 和 cash_amount 二选一，不能同时为 None 或同时非 None。
    - 净值型资产（ETF/OEF/LOF）：传 shares，不传 cash_amount
    - 非净值型资产（CASH）：传 cash_amount，不传 shares
    """
    pos = PortfolioPosition(
        portfolio_code=portfolio_code,
        platform_code=platform_code,
        product_code=product_code,
        market=market,
        shares=Decimal(str(shares)) if shares is not None else None,
        cash_amount=Decimal(str(cash_amount)) if cash_amount is not None else None,
        unit_price=Decimal(str(unit_price)) if unit_price is not None else None,
        cost_price=Decimal(str(cost_price)) if cost_price is not None else None,
        market_value=Decimal(str(market_value)),
        frozen_shares=Decimal(str(frozen_shares)) if frozen_shares is not None else Decimal("0"),
        frozen_amount=Decimal(str(frozen_amount)) if frozen_amount is not None else Decimal("0"),
        snapshot_date=snapshot_date,
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


# ---------------------------------------------------------------------------
# 组合市值快照（PortfolioValueSnapshot）
# ---------------------------------------------------------------------------

def create_value_snapshot(
    db: Session,
    portfolio_code: str,
    snapshot_date: date,
    total_value: float,
    total_shares: float,
    unit_price: float,
) -> PortfolioValueSnapshot:
    """创建组合市值快照"""
    snap = PortfolioValueSnapshot(
        portfolio_code=portfolio_code,
        snapshot_date=snapshot_date,
        total_value=Decimal(str(total_value)),
        total_shares=Decimal(str(total_shares)),
        unit_price=Decimal(str(unit_price)),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


# ---------------------------------------------------------------------------
# 投资人持仓快照（InvestorHolding）
# ---------------------------------------------------------------------------

def create_investor_holding(
    db: Session,
    portfolio_code: str,
    investor_code: str,
    snapshot_date: date,
    shares: float,
    cost_per_share: Optional[float] = 1.0,
    frozen_shares: Optional[float] = None,
    market_value: Optional[float] = None,
    total_cost: Optional[float] = None,
    profit: Optional[float] = None,
) -> InvestorHolding:
    """创建投资人份额快照；派生列缺省不写（保持历史快照 NULL 形态，#693）。

    frozen_shares 未传时**不设属性**——显式赋 None 会压掉列 default=0，
    改变既有调用点的落库形态（SQLAlchemy 只对缺席属性应用 default）。
    """
    holding = InvestorHolding(
        portfolio_code=portfolio_code,
        investor_code=investor_code,
        snapshot_date=snapshot_date,
        shares=Decimal(str(shares)),
        cost_per_share=Decimal(str(cost_per_share)) if cost_per_share else None,
    )
    if frozen_shares is not None:
        holding.frozen_shares = Decimal(str(frozen_shares))
    if market_value is not None:
        holding.market_value = Decimal(str(market_value))
    if total_cost is not None:
        holding.total_cost = Decimal(str(total_cost))
    if profit is not None:
        holding.profit = Decimal(str(profit))
    db.add(holding)
    db.commit()
    db.refresh(holding)
    return holding


# ---------------------------------------------------------------------------
# 同步任务（SyncJob）
# ---------------------------------------------------------------------------

def create_sync_job(
    db: Session,
    job_type: str = "price_history_sync",
    status: str = "pending",
    triggered_by: str = "manual",
    params: Optional[dict] = None,
    total: int = 0,
    done: int = 0,
    success_count: int = 0,
    failed_count: int = 0,
    skipped_count: int = 0,
    error_message: Optional[str] = None,
) -> SyncJob:
    """创建同步任务记录"""
    job = SyncJob(
        job_type=job_type,
        status=status,
        triggered_by=triggered_by,
        params=params,
        total=total,
        done=done,
        success_count=success_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        error_message=error_message,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# 手动市值覆盖（ManualMarketValue）
# ---------------------------------------------------------------------------

def create_manual_market_value(
    db: Session,
    portfolio_code: str,
    platform_code: str,
    product_code: str,
    record_date: date,
    market_value: float,
    computed_value: Optional[float] = None,
    created_by: Optional[str] = None,
) -> ManualMarketValue:
    """创建手动市值覆盖记录（绝对替换，用于非净值型资产重估）"""
    mmv = ManualMarketValue(
        portfolio_code=portfolio_code,
        platform_code=platform_code,
        product_code=product_code,
        value_date=record_date,
        market_value=Decimal(str(market_value)),
        computed_value=Decimal(str(computed_value)) if computed_value is not None else None,
        created_by=created_by,
    )
    db.add(mmv)
    db.commit()
    db.refresh(mmv)
    return mmv


# ---------------------------------------------------------------------------
# 通知（Notification）
# ---------------------------------------------------------------------------

def create_notification(
    db: Session,
    type: str = "info",
    title: str = "测试通知",
    content: Optional[str] = None,
    level: str = "info",
    recipient: Optional[str] = None,
    channel: str = "in_app",
    status: str = "pending",
) -> Notification:
    """创建通知记录"""
    n = Notification(
        type=type,
        title=title,
        content=content,
        level=level,
        recipient=recipient,
        channel=channel,
        status=status,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


# ---------------------------------------------------------------------------
# 幂等性缓存（IdempotencyCache）
# ---------------------------------------------------------------------------

def create_idempotency_cache(
    db: Session,
    key: str,
    portfolio_code: str = "PORT001",
    request_hash: str = "abc123",
    response: str = "{}",
    expires_at: Optional[datetime] = None,
) -> IdempotencyCache:
    """创建幂等性缓存记录（24 小时过期）"""
    if expires_at is None:
        expires_at = datetime.utcnow() + timedelta(hours=24)
    cache = IdempotencyCache(
        key=key,
        portfolio_code=portfolio_code,
        request_hash=request_hash,
        response=response,
        expires_at=expires_at,
    )
    db.add(cache)
    db.commit()
    db.refresh(cache)
    return cache


# ---------------------------------------------------------------------------
# 份额变动事件（ShareChangeEvent）
# ---------------------------------------------------------------------------

def create_share_change_event(
    db: Session,
    portfolio_code: str,
    product_code: str,
    market: str,
    event_type: str = "cash_dividend",
    ex_date: date = date(2025, 1, 10),
    entitlement_date: date = date(2025, 1, 9),
    event_source: str = "manual",
    status: str = "pending",
    platform_code: Optional[str] = None,  # 平台级事件必传，基金级事件不传
    **kwargs,
) -> ShareChangeEvent:
    """创建份额变动事件。
    平台级事件（cash_dividend/reinvest_dividend/forced_adjustment）须传 platform_code。
    基金级事件（share_split/share_merge/bonus_share）不传 platform_code。
    """
    event = ShareChangeEvent(
        portfolio_code=portfolio_code,
        product_code=product_code,
        market=market,
        event_type=event_type,
        ex_date=ex_date,
        entitlement_date=entitlement_date,
        event_source=event_source,
        status=status,
        platform_code=platform_code,
        **kwargs,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
