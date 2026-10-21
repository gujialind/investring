"""确认侧精确日取价（调仓确认与份额变动事件确认共用，不向前回退）。"""
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.price_record import PriceRecord


def get_exact_unit_price(
    db: Session, product_code: str, market: str, nav_date: date
) -> Optional[Decimal]:
    """取 `nav_date` 当日的 `price_record.unit_price`；缺失返回 None，由调用方决定拒绝。"""
    price_record = db.query(PriceRecord).filter(
        PriceRecord.product_code == product_code,
        PriceRecord.market == market,
        PriceRecord.price_date == nav_date,
    ).first()

    if price_record and price_record.unit_price:
        return Decimal(str(price_record.unit_price))

    return None
