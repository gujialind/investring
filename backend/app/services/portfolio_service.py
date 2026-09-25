"""
组合管理服务

组合的创建、更新、关闭/重新激活状态机，以及净值历史/收益率/资金流查询，
从路由层提取供 REST 与 CLI 共用。service 层只抛领域异常，不 commit。
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.asset_classification import AssetClassDimensionRule, AssetClassification
from app.models.investor import Investor
from app.models.investor_holding import InvestorHolding
from app.models.portfolio import Portfolio
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.subscription import Subscription
from app.models.trade import Trade
from app.services.exceptions import BusinessError, NotFoundError

# display_config 哨兵：区分 PUT「不传 = 不修改」与「显式 null = 清空」（issue #144）
UNSET = object()


def _get_portfolio_or_404(db: Session, code: str) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.code == code).first()
    if not portfolio:
        raise NotFoundError("NOT_FOUND", f"组合 {code} 不存在")
    return portfolio


def validate_display_config(db: Session, config: Optional[dict]) -> Optional[dict]:
    """校验并归一持仓明细二级分组维度配置（issue #144）。

    返回归一后的配置：空 dict 归一为 None（语义等同默认，保持「仅存显式
    覆盖项」单一表示，DB 不出现 {} 与 NULL 双表示）。

    校验规则（失败抛 INVALID_DISPLAY_CONFIG 422）：
    - key 必须是字典中 dimension==asset_class 的维度值（不校验 is_active，
      停用大类无持仓时配置无副作用）；
    - value 必须在该大类的 asset_class_dimension_rule 登记（required/optional
      均可；无行 = forbidden，如 ASSET_CASH 任何配置均拒绝）。值域不硬编码，
      规则矩阵新登记维度运行期即可配（#135 理念）。
    """
    if config is None:
        return None
    if not isinstance(config, dict):
        raise BusinessError("INVALID_DISPLAY_CONFIG", "display_config 必须是对象")
    if not config:
        return None
    class_codes = {
        row.code
        for row in db.query(AssetClassification.code).filter(
            AssetClassification.dimension == "asset_class"
        )
    }
    rules: dict = {}
    for row in db.query(AssetClassDimensionRule).all():
        rules.setdefault(row.asset_class_code, set()).add(row.dimension)
    for class_code, dimension in config.items():
        if class_code not in class_codes:
            raise BusinessError(
                "INVALID_DISPLAY_CONFIG",
                f"display_config key {class_code} 不是 asset_class 维度值",
            )
        allowed = rules.get(class_code, set())
        if dimension not in allowed:
            raise BusinessError(
                "INVALID_DISPLAY_CONFIG",
                f"大类 {class_code} 不可按维度 {dimension} 二级分组"
                + ("（该大类无维度规则行）" if not allowed else "（未在规则矩阵登记）"),
            )
    return config


def list_portfolios(
    db: Session,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询组合列表，附聚合字段 total_value / cumulative_return / investor_count。

    聚合口径（issue #69）：
    - total_value：最新 portfolio_value_snapshot 的 total_value
    - cumulative_return：首末 unit_price 百分数，与 get_returns 一致
    - investor_count：最新快照日 investor_holding 中 shares > 0 的投资人数
    无快照（draft 等）时 total_value / cumulative_return 为 None，investor_count 为 0。
    """
    query = db.query(Portfolio)
    if status:
        query = query.filter(Portfolio.status == status)
    total = query.count()
    portfolios = query.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for p in portfolios:
        total_value = None
        cumulative_return = None
        investor_count = 0
        latest = (
            db.query(PortfolioValueSnapshot)
            .filter(PortfolioValueSnapshot.portfolio_code == p.code)
            .order_by(PortfolioValueSnapshot.snapshot_date.desc())
            .first()
        )
        if latest:
            total_value = float(latest.total_value) if latest.total_value is not None else None
            first = (
                db.query(PortfolioValueSnapshot)
                .filter(PortfolioValueSnapshot.portfolio_code == p.code)
                .order_by(PortfolioValueSnapshot.snapshot_date.asc())
                .first()
            )
            if first and first.unit_price:
                initial_nav = float(first.unit_price)
                current_nav = float(latest.unit_price)
                cumulative_return = round((current_nav - initial_nav) / initial_nav * 100, 4)
            investor_count = (
                db.query(InvestorHolding)
                .filter(
                    InvestorHolding.portfolio_code == p.code,
                    InvestorHolding.snapshot_date == latest.snapshot_date,
                    InvestorHolding.shares > 0,
                )
                .count()
            )
        items.append({
            "code": p.code,
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "started_at": p.started_at,
            "closed_at": p.closed_at,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "display_config": p.display_config,
            "auto_snapshot_enabled": p.auto_snapshot_enabled,
            "total_value": total_value,
            "cumulative_return": cumulative_return,
            "investor_count": investor_count,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _derive_portfolio_totals(db: Session, code: str) -> dict:
    """组合概要派生字段（issue #99）：total_value / total_profit。

    total_profit = 最新快照 total_value − 净投入；净投入 = Σ confirmed 申购金额
    − Σ confirmed 赎回金额，且仅计 confirm_date <= 最新快照日的记录（#250，
    与 total_value 的快照时点口径对齐）。无快照时两者均为 None。
    """
    latest = (
        db.query(PortfolioValueSnapshot)
        .filter(PortfolioValueSnapshot.portfolio_code == code)
        .order_by(PortfolioValueSnapshot.snapshot_date.desc())
        .first()
    )
    if not latest or latest.total_value is None:
        return {"total_value": None, "total_profit": None}

    subs = (
        db.query(Subscription.sub_type, Subscription.amount)
        .filter(
            Subscription.portfolio_code == code,
            Subscription.status == "confirmed",
            Subscription.confirm_date <= latest.snapshot_date,
        )
        .all()
    )
    net_invested = 0.0
    for sub_type, amount in subs:
        if amount is None:
            continue
        if sub_type == "subscribe":
            net_invested += float(amount)
        elif sub_type == "redeem":
            net_invested -= float(amount)

    total_value = float(latest.total_value)
    return {
        "total_value": total_value,
        "total_profit": round(total_value - net_invested, 2),
    }


def get_latest_value_snapshot(db: Session, code: str) -> PortfolioValueSnapshot:
    """获取组合最新一条市值快照，无快照抛 NOT_FOUND。"""
    _get_portfolio_or_404(db, code)
    latest = (
        db.query(PortfolioValueSnapshot)
        .filter(PortfolioValueSnapshot.portfolio_code == code)
        .order_by(PortfolioValueSnapshot.snapshot_date.desc())
        .first()
    )
    if not latest:
        raise NotFoundError("NOT_FOUND", f"组合 {code} 暂无快照")
    return latest


def get_portfolio_investors(db: Session, code: str) -> List[dict]:
    """获取组合最新快照日的投资人份额列表（shares > 0），无快照返回空列表。"""
    _get_portfolio_or_404(db, code)
    latest_date = (
        db.query(InvestorHolding.snapshot_date)
        .filter(InvestorHolding.portfolio_code == code)
        .order_by(InvestorHolding.snapshot_date.desc())
        .limit(1)
        .scalar()
    )
    if latest_date is None:
        return []
    rows = (
        db.query(InvestorHolding, Investor.name)
        .join(Investor, Investor.code == InvestorHolding.investor_code)
        .filter(
            InvestorHolding.portfolio_code == code,
            InvestorHolding.snapshot_date == latest_date,
            InvestorHolding.shares > 0,
        )
        .order_by(InvestorHolding.shares.desc())
        .all()
    )
    return [
        {
            "investor_code": h.investor_code,
            "name": name,
            "shares": float(h.shares),
        }
        for h, name in rows
    ]


def create_portfolio(
    db: Session,
    *,
    code: str,
    name: str,
    description: Optional[str] = None,
    display_config: Optional[dict] = None,
) -> Portfolio:
    """创建组合（初始状态 draft）。不 commit。"""
    if db.query(Portfolio).filter(Portfolio.code == code).first():
        raise BusinessError("ALREADY_EXISTS", f"组合 {code} 已存在", http_status=400)
    portfolio = Portfolio(
        code=code,
        name=name,
        description=description,
        status="draft",
        display_config=validate_display_config(db, display_config),
    )
    db.add(portfolio)
    return portfolio


def update_portfolio(
    db: Session,
    *,
    code: str,
    name: Optional[str] = None,
    description=UNSET,
    display_config=UNSET,
    auto_snapshot_enabled: Optional[bool] = None,
) -> Portfolio:
    """更新组合信息。不 commit。

    description 哨兵语义（#579 口径 A）：UNSET = 不修改；None = 清空；str = 覆盖。
    显式 null 由 router 层 null_guard 收口后进 allow（列可空且响应 Optional，
    null = 清空，asset_classification.description 先例）——此前 `is not None`
    跳过使 description 恒无法清空。
    display_config 哨兵语义（issue #144）：UNSET = 不修改；None = 清空恢复
    默认；dict = 校验后覆盖（router 以 "display_config" in updates 区分）。
    """
    portfolio = _get_portfolio_or_404(db, code)
    if name is not None:
        portfolio.name = name
    if description is not UNSET:
        portfolio.description = description
    if display_config is not UNSET:
        # 校验并归一（空 dict → None）；JSON 列赋新对象（避免 in-place 突变不触发脏检测）
        portfolio.display_config = validate_display_config(db, display_config)
    if auto_snapshot_enabled is not None:
        portfolio.auto_snapshot_enabled = auto_snapshot_enabled
    return portfolio


def close_portfolio(db: Session, code: str) -> Portfolio:
    """关闭组合（存在 pending 交易或已关闭则拒绝）。不 commit。"""
    portfolio = _get_portfolio_or_404(db, code)
    if portfolio.status == "closed":
        raise BusinessError("PORTFOLIO_ALREADY_CLOSED", "组合已关闭")

    pending_subs = db.query(Subscription).filter(
        Subscription.portfolio_code == code, Subscription.status == "pending"
    ).count()
    pending_trades = db.query(Trade).filter(
        Trade.portfolio_code == code, Trade.status == "pending"
    ).count()
    if pending_subs > 0 or pending_trades > 0:
        raise BusinessError(
            "PENDING_TRANSACTIONS_EXIST",
            f"存在待处理交易: {pending_subs} 笔申赎, {pending_trades} 笔调仓",
        )

    portfolio.status = "closed"
    portfolio.closed_at = datetime.utcnow()
    return portfolio


def reactivate_portfolio(db: Session, code: str) -> Portfolio:
    """重新激活已关闭组合（仅 closed 可激活）。不 commit。"""
    portfolio = _get_portfolio_or_404(db, code)
    if portfolio.status != "closed":
        raise BusinessError(
            "PORTFOLIO_NOT_CLOSED",
            f"仅已关闭组合可激活，当前状态: {portfolio.status}",
        )
    portfolio.status = "active"
    portfolio.closed_at = None
    return portfolio


def get_nav_history(
    db: Session,
    code: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list:
    """查看组合净值历史（单层列表，按日期升序）。"""
    _get_portfolio_or_404(db, code)
    query = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == code
    )
    if start_date:
        query = query.filter(PortfolioValueSnapshot.snapshot_date >= start_date)
    if end_date:
        query = query.filter(PortfolioValueSnapshot.snapshot_date <= end_date)
    snapshots = query.order_by(PortfolioValueSnapshot.snapshot_date.asc()).all()
    return [
        {
            "snapshot_date": s.snapshot_date.isoformat(),
            "unit_price": float(s.unit_price) if s.unit_price is not None else None,
            "total_value": float(s.total_value) if s.total_value is not None else None,
            "total_shares": float(s.total_shares) if s.total_shares is not None else None,
        }
        for s in snapshots
    ]


def get_returns(db: Session, code: str) -> dict:
    """查看组合收益率（快照不足返回 None 字段）。"""
    _get_portfolio_or_404(db, code)
    snapshots = db.query(PortfolioValueSnapshot).filter(
        PortfolioValueSnapshot.portfolio_code == code
    ).order_by(PortfolioValueSnapshot.snapshot_date.asc()).all()

    if not snapshots:
        return {
            "portfolio_code": code,
            "cumulative_return": None,
            "annualized_return": None,
            "initial_nav": None,
            "current_nav": None,
            "holding_days": None,
        }

    initial = snapshots[0]
    current = snapshots[-1]
    initial_nav = float(initial.unit_price)
    current_nav = float(current.unit_price)
    cumulative_return = (current_nav - initial_nav) / initial_nav * 100
    holding_days = (current.snapshot_date - initial.snapshot_date).days
    annualized_return = None
    if holding_days > 0:
        annualized_return = ((current_nav / initial_nav) ** (365 / holding_days) - 1) * 100

    return {
        "portfolio_code": code,
        "cumulative_return": round(cumulative_return, 4),
        "annualized_return": round(annualized_return, 4) if annualized_return is not None else None,
        "initial_nav": initial_nav,
        "current_nav": current_nav,
        "holding_days": holding_days,
    }


def get_cash_flow(db: Session, code: str) -> dict:
    """查看组合资金流（confirmed 申赎汇总）。"""
    _get_portfolio_or_404(db, code)
    subs = db.query(Subscription).filter(
        Subscription.portfolio_code == code,
        Subscription.status == "confirmed",
    ).all()
    inflow = sum(float(s.amount) for s in subs if s.sub_type == "subscribe" and s.amount)
    outflow = sum(float(s.amount) for s in subs if s.sub_type == "redeem" and s.amount)
    return {
        "portfolio_code": code,
        "total_inflow": round(inflow, 4),
        "total_outflow": round(outflow, 4),
        "net_inflow": round(inflow - outflow, 4),
    }
