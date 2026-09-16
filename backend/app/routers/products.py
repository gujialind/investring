from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.models.product import Product
from app.schemas.product import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    PaginatedProductResponse,
)
from app.dependencies import get_current_user, get_current_admin
from app.services import product_service

router = APIRouter()


@router.get("", response_model=PaginatedProductResponse)
def get_products(
    product_type: Optional[str] = None,
    market: Optional[str] = None,
    keyword: Optional[str] = None,
    data_source: Optional[str] = None,
    data_source_status: Optional[str] = None,
    # 属性等值筛选（issue #238）：0/False 是合法值，必须判 is not None
    confirm_days: Optional[int] = None,
    nav_lag_days: Optional[int] = None,
    is_qdii: Optional[bool] = None,
    # 维度筛选（issue #128）
    asset_class_code: Optional[str] = None,
    region_code: Optional[str] = None,
    style_code: Optional[str] = None,
    size_code: Optional[str] = None,
    segment_code: Optional[str] = None,
    # issue #327：默认排除虚拟产品（product_type ∈ VIRTUAL_PRODUCT_TYPES，不可交易），
    # 产品管理页等需查看全部的场景显式传 include_virtual=true；
    # 与其余过滤为 AND 关系（如 product_type=CASH 且默认排除时结果为空）
    include_virtual: bool = False,
    page: Optional[int] = 1,
    page_size: Optional[int] = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(Product)
    if not include_virtual:
        query = query.filter(Product.product_type.notin_(product_service.VIRTUAL_PRODUCT_TYPES))
    if product_type:
        query = query.filter(Product.product_type == product_type)
    if market:
        query = query.filter(Product.market == market)
    if keyword:
        # code/name 模糊 OR 匹配（issue #155）：用户输入的 %/_/ 须转义字面化
        kw = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(or_(
            Product.code.ilike(f"%{kw}%", escape="\\"),
            Product.name.ilike(f"%{kw}%", escape="\\"),
        ))
    if data_source:
        query = query.filter(Product.data_source == data_source)
    if data_source_status:
        query = query.filter(Product.data_source_status == data_source_status)
    if confirm_days is not None:
        query = query.filter(Product.confirm_days == confirm_days)
    if nav_lag_days is not None:
        query = query.filter(Product.nav_lag_days == nav_lag_days)
    if is_qdii is not None:
        query = query.filter(Product.is_qdii == is_qdii)
    if asset_class_code:
        query = query.filter(Product.asset_class_code == asset_class_code)
    if region_code:
        query = query.filter(Product.region_code == region_code)
    if style_code:
        query = query.filter(Product.style_code == style_code)
    if size_code:
        query = query.filter(Product.size_code == size_code)
    if segment_code:
        query = query.filter(Product.segment_code == segment_code)
    total = query.count()
    # 确定性排序（#165）：新建优先保证下拉首页（page_size=50）可见新产品；
    # code 次级键保证同秒批量创建时顺序确定（Product 无自增 id）
    items = (
        query.order_by(Product.created_at.desc(), Product.code.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedProductResponse(
        items=[ProductResponse.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ProductResponse)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    new_product = product_service.create_product(
        db,
        code=product.code,
        market=product.market,
        name=product.name,
        product_type=product.product_type,
        asset_class_code=product.asset_class_code,
        region_code=product.region_code,
        style_code=product.style_code,
        size_code=product.size_code,
        segment_code=product.segment_code,
        is_qdii=product.is_qdii,
        nav_lag_days=product.nav_lag_days,
        # #231/#236/#241：仅显式传入才下发（pydantic 默认值不可分辨「未传/显式 null」；
        # 显式 null 穿透到 service 由 validate_confirm_days 拒绝，统一 422 形状）
        **({"confirm_days": product.confirm_days} if "confirm_days" in product.model_fields_set else {}),
        data_source=product.data_source,
        sync_history=bool(product.sync_history),
    )
    db.commit()
    db.refresh(new_product)
    return new_product


@router.get("/{code}/{market}", response_model=ProductResponse)
def get_product(
    code: str,
    market: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    product = db.query(Product).filter(
        Product.code == code,
        Product.market == market
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/{code}", response_model=ProductResponse)
def get_product_auto_market(
    code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """不带 market 的产品详情（#83）：唯一市场自动补全；
    LOF 一码多市场抛 MARKET_AMBIGUOUS，交全局 BusinessError handler 返回 422。"""
    _, market = product_service.resolve_product_market(db, code)
    product = db.query(Product).filter(
        Product.code == code,
        Product.market == market
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.put("/{code}/{market}", response_model=ProductResponse)
def update_product(
    code: str,
    market: str,
    product: ProductUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    updates = product.dict(exclude_unset=True)
    db_product = product_service.update_product(db, code=code, market=market, updates=updates)
    db.commit()
    db.refresh(db_product)
    return db_product


@router.delete("/{code}/{market}")
def delete_product(
    code: str,
    market: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    product = db.query(Product).filter(
        Product.code == code,
        Product.market == market
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    db.delete(product)
    db.commit()
    return {"message": "Product deleted successfully"}
