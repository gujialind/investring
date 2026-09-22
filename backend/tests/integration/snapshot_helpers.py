from copy import deepcopy

from sqlalchemy import select

from app.models import (
    InvestorHolding,
    Portfolio,
    PortfolioPosition,
    PortfolioValueSnapshot,
    ShareChangeEvent,
    Subscription,
    Trade,
)


def capture_portfolio_state(db, portfolio_code):
    # 读取纯列并深拷贝，避免 ORM identity map 与可变 JSON 掩盖回滚残留。
    state = {}
    for model in (
        PortfolioPosition, PortfolioValueSnapshot, InvestorHolding,
        Subscription, Trade, ShareChangeEvent, Portfolio,
    ):
        table = model.__table__
        code = table.c.code if model is Portfolio else table.c.portfolio_code
        rows = db.execute(
            select(table).where(code == portfolio_code).order_by(*table.primary_key.columns)
        ).mappings()
        state[table.name] = [dict(row) for row in rows]
    return deepcopy(state)
