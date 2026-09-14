# ============ 共享测试材料：份额变动事件 #461 LOF 双市场基线（自 test_share_events.py 拆分，issue #469） ============
# 常量与 helper 由下列两文件共用：
#   - test_share_events_create.py：LOF461_CODE / LOF461_ENT / LOF461_EX / _setup_lof_products / _setup_lof_baseline
#   - test_share_events_market_scoping.py：LOF461_CODE / LOF461_ENT / LOF461_EX / _setup_lof_baseline
# 无 test_ 前缀，pytest 不收集；本模块不做用例收集。
# LOF 双市场基线的完整背景见 test_share_events_market_scoping.py 头部（#461）
# ============================================================================

from datetime import date

from tests.factories import (
    create_portfolio, create_product, create_platform,
    create_position_snapshot, create_value_snapshot, ensure_trading_day,
)


LOF461_CODE = "LOF461.SZ"
LOF461_ENT = date(2025, 12, 8)   # 权益登记日（基线快照日）
LOF461_EX = date(2025, 12, 10)   # 除息日


def _setup_lof_products(test_db, portfolio_code):
    create_portfolio(test_db, code=portfolio_code, status="active")
    create_product(test_db, code=LOF461_CODE, market="CN_EXCHANGE",
                   product_type="LOF", asset_class_code="ASSET_STOCK")
    create_product(test_db, code=LOF461_CODE, market="CN_OTC",
                   product_type="LOF", asset_class_code="ASSET_STOCK")
    create_platform(test_db, code="MYCF")
    create_platform(test_db, code="HBZQ")
    ensure_trading_day(test_db, LOF461_ENT, is_open=True)
    ensure_trading_day(test_db, LOF461_EX, is_open=True)


def _setup_lof_baseline(test_db, portfolio_code, *, exch_shares=100.0, otc_shares=50.0,
                        exch_platform="MYCF", otc_platform="MYCF"):
    """双市场权益登记日基线；platform 传 None 表示该市场不建持仓行。
    CN_EXCHANGE 行先建——修复前平台级 .first() 会读到它，让用例在修复前真红。"""
    _setup_lof_products(test_db, portfolio_code)
    total = 0.0
    if exch_platform:
        create_position_snapshot(
            test_db, portfolio_code, LOF461_CODE, "CN_EXCHANGE",
            snapshot_date=LOF461_ENT, shares=exch_shares, unit_price=1.0,
            cost_price=1.0, market_value=exch_shares, platform_code=exch_platform,
        )
        total += exch_shares
    if otc_platform:
        create_position_snapshot(
            test_db, portfolio_code, LOF461_CODE, "CN_OTC",
            snapshot_date=LOF461_ENT, shares=otc_shares, unit_price=1.0,
            cost_price=1.0, market_value=otc_shares, platform_code=otc_platform,
        )
        total += otc_shares
    create_value_snapshot(test_db, portfolio_code, LOF461_ENT,
                          total_value=total, total_shares=total, unit_price=1.0)
