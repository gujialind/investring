from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date
from decimal import Decimal
from app.database import get_db
from app.models.asset_classification import AssetClassification
from app.models.platform import Platform
from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.product import Product
from app.schemas.position import PositionCreate, PositionUpdate, PositionResponse, CashPositionUpdate
from app.services.position_service import (
    calculate_available_cash,
    calculate_available_shares,
    calculate_investor_available_shares,
    compute_derived_fields,
)
from app.dependencies import get_current_user, get_current_admin

router = APIRouter()


@router.get("")
def get_positions(
    portfolio_code: Optional[str] = None,
    snapshot_date: Optional[date] = None,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(PortfolioPosition)
    if portfolio_code:
        query = query.filter(PortfolioPosition.portfolio_code == portfolio_code)
    if snapshot_date:
        query = query.filter(PortfolioPosition.snapshot_date == snapshot_date)
    else:
        # 默认查询最新快照：每个组合的最新快照日（组合级，非产品级）
        # 按组合取 max(snapshot_date)，避免清仓/在途消除后回退到陈旧行（#105）
        subq = (
            db.query(
                PortfolioPosition.portfolio_code,
                func.max(PortfolioPosition.snapshot_date).label("max_date"),
            )
            .group_by(PortfolioPosition.portfolio_code)
            .subquery()
        )
        query = query.join(
            subq,
            (PortfolioPosition.portfolio_code == subq.c.portfolio_code)
            & (PortfolioPosition.snapshot_date == subq.c.max_date),
        )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()

    # 读侧派生字段（全部批量查询，防 N+1）：
    # 产品名称、五维度标签（code+name 成对）、盈亏/收益率、daily_profit（issue #99/#128）
    codes = {p.product_code for p in items}
    name_map = {}
    dims_by_product = {}
    qdii_map = {}
    nav_lag_map = {}
    dim_codes = set()
    if codes:
        for prod in db.query(
            Product.code, Product.market, Product.name, Product.is_qdii,
            Product.nav_lag_days,
            Product.asset_class_code, Product.region_code, Product.style_code,
            Product.size_code, Product.segment_code,
        ).filter(Product.code.in_(codes)).all():
            name_map[(prod.code, prod.market)] = prod.name
            qdii_map[(prod.code, prod.market)] = prod.is_qdii
            nav_lag_map[(prod.code, prod.market)] = prod.nav_lag_days
            dims = (prod.asset_class_code, prod.region_code, prod.style_code,
                    prod.size_code, prod.segment_code)
            dims_by_product[(prod.code, prod.market)] = dims
            dim_codes.update(c for c in dims if c)

    # 维度值 code → 展示名（维度字典批量查询，issue #128）
    dim_name_by_code = {}
    if dim_codes:
        for ac_code, ac_name in db.query(
            AssetClassification.code, AssetClassification.name
        ).filter(AssetClassification.code.in_(dim_codes)).all():
            dim_name_by_code[ac_code] = ac_name

    # platform_name：平台编码 → 名称（批量 enrich，防 N+1，issue #106）
    platform_codes = {p.platform_code for p in items if p.platform_code}
    platform_name_map = {}
    if platform_codes:
        for plat in db.query(Platform.code, Platform.name).filter(
            Platform.code.in_(platform_codes)
        ).all():
            platform_name_map[plat.code] = plat.name

    # daily_profit / 现金累计收益 / 分红加回：共享一次流水聚合（issue #103，防三函数重复查询）
    derived = compute_derived_fields(db, items)
    daily_profits = derived["daily_profits"]
    cash_profits = derived["cash_profits"]
    event_addbacks = derived["event_addbacks"]

    enriched = []
    for p in items:
        row = PositionResponse.model_validate(p).model_dump()
        key = (p.portfolio_code, p.product_code, p.market, p.platform_code)
        row["product_name"] = name_map.get((p.product_code, p.market))
        row["is_qdii"] = qdii_map.get((p.product_code, p.market))
        row["nav_lag_days"] = nav_lag_map.get((p.product_code, p.market)) or 0
        dims = dims_by_product.get((p.product_code, p.market))
        if dims:
            for field, code in zip(
                ("asset_class", "region", "style", "size", "segment"), dims
            ):
                row[f"{field}_code"] = code
                row[f"{field}_name"] = dim_name_by_code.get(code) if code else None
        row["platform_name"] = platform_name_map.get(p.platform_code) if p.platform_code else None
        row["daily_profit"] = daily_profits.get(key)
        if p.shares is not None and p.cost_price is not None and p.market_value is not None:
            # 非现金行：市值 − 成本 + 累计分红加回（复权口径，路线 C）
            cost = float(p.shares) * float(p.cost_price)
            profit_loss = float(p.market_value) - cost + event_addbacks.get(key, 0.0)
            row["profit_loss"] = round(profit_loss, 4)
            row["profit_loss_percent"] = round(profit_loss / cost * 100, 4) if cost else None
        elif p.product_code == "CASH" and p.shares is None:
            # CASH 行：现金累计收益口径，≈0
            row["profit_loss"] = cash_profits.get(key)
        enriched.append(row)

    return {
        "items": enriched,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=PositionResponse)
def create_position(
    position: PositionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    # (g) 禁止直接操作 portfolio_position 表（快照为系统生成，不可手动创建）
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": "POSITION_TABLE_PROTECTED",
            "message": "持仓快照由系统自动生成，不可手动创建。如需修改现金持仓，请使用 /portfolio/{portfolio_code}/cash-position 端点",
        },
    )


@router.get("/{id}", response_model=PositionResponse)
def get_position(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    position = db.query(PortfolioPosition).filter(PortfolioPosition.id == id).first()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


@router.put("/{id}", response_model=PositionResponse)
def update_position(
    id: int,
    position: PositionUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    # (g) 禁止直接操作 portfolio_position 表
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": "POSITION_TABLE_PROTECTED",
            "message": "持仓快照由系统自动生成，不可手动修改。如需修改现金持仓，请使用 /portfolio/{portfolio_code}/cash-position 端点",
        },
    )


@router.delete("/{id}")
def delete_position(
    id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    # (g) 禁止直接操作 portfolio_position 表
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": "POSITION_TABLE_PROTECTED",
            "message": "持仓快照由系统自动生成，不可手动删除。如需删除快照，请使用 DELETE /snapshots/{portfolio_code}/{snapshot_date}",
        },
    )


@router.get("/portfolio/{portfolio_code}/available-cash")
def get_available_cash(
    portfolio_code: str,
    platform_code: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.models.portfolio import Portfolio

    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    cash = calculate_available_cash(db, portfolio_code, platform_code, as_of_date=date.today())
    result = {"portfolio_code": portfolio_code, "available_cash": float(cash)}
    if platform_code:
        result["platform_code"] = platform_code
    return result


@router.get("/portfolio/{portfolio_code}/product/{product_code}/available-shares")
def get_available_shares(
    portfolio_code: str,
    product_code: str,
    market: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.models.portfolio import Portfolio

    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    shares = calculate_available_shares(db, portfolio_code, product_code, market)
    return {
        "portfolio_code": portfolio_code,
        "product_code": product_code,
        "market": market,
        "available_shares": float(shares),
    }


@router.get("/portfolio/{portfolio_code}/investor/{investor_code}/available-shares")
def get_investor_available_shares(
    portfolio_code: str,
    investor_code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.models.investor import Investor
    from app.models.portfolio import Portfolio

    portfolio = db.query(Portfolio).filter(Portfolio.code == portfolio_code).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    investor = db.query(Investor).filter(Investor.code == investor_code).first()
    if not investor:
        raise HTTPException(status_code=404, detail="Investor not found")

    shares = calculate_investor_available_shares(db, portfolio_code, investor_code)
    return {
        "portfolio_code": portfolio_code,
        "investor_code": investor_code,
        "available_shares": float(shares),
    }


@router.post("/portfolio/{portfolio_code}/cash-position")
def update_cash_position(
    portfolio_code: str,
    request: CashPositionUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """
    更新非净值型资产（现金）金额
    
    权限：仅admin
    规则：
    - 必须在交易日进行
    - 必须指定平台代码
    - 写入 manual_market_value 表（绝对替换），不再直接写 portfolio_position
    - 写入后提示用户重新生成快照（非强制）
    - 同日存在已确认现金交易时附 warnings 提示（issue #88，不阻断）
    """
    from app.services.position_service import update_cash_position as update_cash_service

    result = update_cash_service(
        db,
        portfolio_code=portfolio_code,
        platform_code=request.platform_code,
        amount=request.cash_amount,
        update_date=request.update_date,
        created_by=current_user.code if hasattr(current_user, 'code') else None,
    )
    db.commit()

    return {
        "success": True,
        "message": "现金市值覆盖已写入 manual_market_value，建议重新生成快照以更新持仓",
        "portfolio_code": result["portfolio_code"],
        "platform_code": result["platform_code"],
        "cash_amount": result["cash_amount"],
        "computed_value": result["computed_value"],
        "update_date": result["update_date"].isoformat(),
        "requires_snapshot_regen": True,
        "warnings": result["warnings"],
    }


@router.get("/portfolio/{portfolio_code}/cash-position")
def list_cash_overrides(
    portfolio_code: str,
    platform_code: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """
    查询现金手动覆盖记录（manual_market_value，issue #88）

    权限：仅admin
    """
    from app.services.position_service import list_manual_cash_overrides

    items = list_manual_cash_overrides(
        db,
        portfolio_code,
        platform_code=platform_code,
        start_date=start_date,
        end_date=end_date,
    )
    return {"items": items, "total": len(items)}


@router.delete("/portfolio/{portfolio_code}/cash-position")
def delete_cash_override(
    portfolio_code: str,
    platform_code: str,
    update_date: date,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """
    删除现金手动覆盖记录（issue #88）

    删除后该日该平台回退到自然计算值；若覆盖已 baked in 快照，
    需重算快照才生效（requires_snapshot_regen）。

    权限：仅admin
    """
    from app.services.position_service import delete_manual_cash_override

    result = delete_manual_cash_override(
        db,
        portfolio_code=portfolio_code,
        platform_code=platform_code,
        value_date=update_date,
    )
    db.commit()

    return {
        "success": True,
        "message": f"已删除 {portfolio_code}/{platform_code} 在 {update_date} 的现金覆盖记录",
        "portfolio_code": result["portfolio_code"],
        "platform_code": result["platform_code"],
        "update_date": result["value_date"].isoformat(),
        "deleted_value": result["deleted_value"],
        "requires_snapshot_regen": result["requires_snapshot_regen"],
    }
