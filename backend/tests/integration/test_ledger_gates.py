# ============================================================================
# 集成测试：台账型逐入口类型闸门（#905 PR1，验收 4）
# 服务层直调各写入口，断言 PORTFOLIO_KIND_NOT_ALLOWED + 该入口对应表零写入。
# 按入口断言各自那张表（可独立变红，不用恒真兜底）；含 share_change_event /
# position_service（update + delete）/ dividend_config 现无 status 前置的高危入口。
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from tests.factories import create_portfolio, create_investor
from app.services.exceptions import BusinessError
from app.services import (
    subscription_service,
    trade_service,
    cash_transfer_service,
    share_change_event_service,
    position_service,
    snapshot_service,
    dividend_config_service,
)
from app.models.subscription import Subscription
from app.models.trade import Trade
from app.models.share_change_event import ShareChangeEvent
from app.models.manual_market_value import ManualMarketValue
from app.models.dividend_method_config import DividendMethodConfig

D = date(2026, 10, 19)
CODE = "LED_G"


@pytest.fixture
def ledger_portfolio(test_db):
    create_investor(test_db, code="INV_G")
    return create_portfolio(
        test_db, code=CODE, kind="ledger", owner_investor_code="INV_G", status="active"
    )


def _assert_rejected(db, fn, model):
    """断言闸门拒绝 + 该入口对应表零写入（按入口断言各自表，可独立变红）。"""
    with pytest.raises(BusinessError) as ei:
        fn()
    assert ei.value.code == "PORTFOLIO_KIND_NOT_ALLOWED"
    assert db.query(model).filter_by(portfolio_code=CODE).count() == 0


def test_subscription_gate(test_db, ledger_portfolio):
    _assert_rejected(test_db, lambda: subscription_service.create_subscription(
        test_db, portfolio_code=CODE, investor_code="INV_G", platform_code="MYCF",
        sub_type="subscribe", apply_date=D, amount=Decimal("100")), Subscription)


def test_trade_gate(test_db, ledger_portfolio):
    _assert_rejected(test_db, lambda: trade_service.create_trade(
        test_db, portfolio_code=CODE, product_code="000300.OF", market="CN_OTC",
        trade_type="buy", trade_date=D, amount=Decimal("100")), Trade)


def test_cash_transfer_gate(test_db, ledger_portfolio):
    # 现金转移两腿落 Trade 表
    _assert_rejected(test_db, lambda: cash_transfer_service.create_cash_transfer(
        test_db, portfolio_code=CODE, from_platform="MYCF", to_platform="OTHER",
        amount=Decimal("100"), transfer_date=D), Trade)


def test_share_event_gate(test_db, ledger_portfolio):
    _assert_rejected(test_db, lambda: share_change_event_service.create_share_change_event(
        test_db, portfolio_code=CODE, event_type="cash_dividend",
        ex_date=D, entitlement_date=D, product_code="000300.OF"), ShareChangeEvent)


def test_cash_position_update_gate(test_db, ledger_portfolio):
    _assert_rejected(test_db, lambda: position_service.update_cash_position(
        test_db, portfolio_code=CODE, platform_code="MYCF", amount=Decimal("100")),
        ManualMarketValue)


def test_cash_position_delete_gate(test_db, ledger_portfolio):
    _assert_rejected(test_db, lambda: position_service.delete_manual_cash_override(
        test_db, portfolio_code=CODE, platform_code="MYCF", value_date=D),
        ManualMarketValue)


def test_dividend_config_create_gate(test_db, ledger_portfolio):
    _assert_rejected(test_db, lambda: dividend_config_service.create_config(
        test_db, portfolio_code=CODE, product_code="000300.OF", market="CN_OTC",
        platform_code="MYCF", dividend_method="cash_dividend", effective_from=D),
        DividendMethodConfig)


def test_snapshot_validate_gate(test_db, ledger_portfolio):
    from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
    with pytest.raises(BusinessError) as ei:
        snapshot_service._validate_portfolio(test_db, CODE)
    assert ei.value.code == "PORTFOLIO_KIND_NOT_ALLOWED"
    assert test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code=CODE).count() == 0


def test_nav_portfolio_passes_gate(test_db):
    """净值型零行为变更：nav 组合通过类型闸门（不抛 PORTFOLIO_KIND_NOT_ALLOWED）。"""
    from app.services.portfolio_service import assert_nav_portfolio
    p = create_portfolio(test_db, code="NAV_G", status="active")
    assert_nav_portfolio(p)  # 不抛异常即通过