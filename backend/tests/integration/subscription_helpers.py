# ============================================================================
# 申赎集成测试共享工厂（自 tests/integration/test_subscriptions.py 拆分，issue #469）
# ============================================================================
# _confirmed_sub_with_cash_leg（原属 #180 章节：started_at 重算不变量 + 回滚链防护）被
# 首窗 / lifecycle / preview 三个测试文件共用，故提取到本模块；无 test_ 前缀，不被 pytest 收集。

from tests.factories import create_subscription, create_trade


def _confirmed_sub_with_cash_leg(
    db, portfolio_code, investor_code, amount, apply_date, confirm_date,
):
    """工厂造 confirmed 申购 + 配对 CASH buy 腿（模拟真实确认产物）"""
    sub = create_subscription(
        db, portfolio_code, investor_code,
        sub_type="subscribe", amount=amount, shares=amount,
        unit_price=1.0, apply_date=apply_date,
        confirm_date=confirm_date, status="confirmed",
    )
    create_trade(
        db, portfolio_code, "CASH", "",
        trade_type="buy", amount=amount, price=1.0,
        trade_date=apply_date, confirm_date=confirm_date,
        actual_amount=amount, status="confirmed",
        transfer_group=f"sub_{sub.id}",
    )
    return sub
