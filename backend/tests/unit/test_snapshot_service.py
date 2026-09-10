# ============================================================================
# 单元测试：快照服务 (test_snapshot_service.py)
# ============================================================================
# 覆盖 app/services/snapshot_service.py 中的核心函数：
# - 快照依赖校验（validate_snapshot_dependencies）
# - 冻结份额计算
# - 交易日判断
# - 快照前置校验（组合状态、交易日）
# ============================================================================

import pytest
from datetime import date
from decimal import Decimal

from app.services.snapshot_service import (
    validate_snapshot_dependencies,
    _validate_portfolio,
    _validate_trading_day,
    _is_trading_day,
    _calculate_frozen_shares,
    _calculate_portfolio_frozen_shares,
    _calculate_investor_frozen_shares,
    _check_trading_day,
    _check_pending_transactions,
    _check_share_change_events,
    _prev_trading_day,
    _generate_portfolio_value_snapshot,
    _generate_portfolio_position,
    _generate_investor_holding,
    _cascade_unconfirm_share_change_events,
)
from app.models import (
    Portfolio, TradingCalendar, Trade, Subscription,
    PortfolioPosition, PortfolioValueSnapshot, Product, Investor,
    ShareChangeEvent,
)
from tests.factories import (
    create_portfolio, create_product, create_trade,
    create_subscription, create_investor, ensure_trading_day,
    create_position_snapshot, create_platform, create_value_snapshot,
    create_investor_holding,
)


class TestValidatePortfolio:
    """组合前置校验测试"""

    def test_nonexistent_portfolio_raises(self, test_db):
        """不存在的组合应抛出 ValueError"""
        with pytest.raises(ValueError, match="不存在"):
            _validate_portfolio(test_db, "NONEXISTENT")

    def test_draft_portfolio_raises(self, test_db):
        """draft 状态组合应抛出 ValueError（未激活）"""
        create_portfolio(test_db, code="DRAFT_P", status="draft")
        with pytest.raises(ValueError, match="未激活"):
            _validate_portfolio(test_db, "DRAFT_P")

    def test_closed_portfolio_raises(self, test_db):
        """closed 状态组合应抛出 ValueError"""
        create_portfolio(test_db, code="CLOSED_P", status="closed")
        with pytest.raises(ValueError, match="未激活"):
            _validate_portfolio(test_db, "CLOSED_P")

    def test_active_portfolio_passes(self, test_db):
        """active 状态组合应通过校验"""
        create_portfolio(test_db, code="ACTIVE_V", status="active")
        _validate_portfolio(test_db, "ACTIVE_V")  # 不抛异常即通过


class TestIsTradingDay:
    """交易日判断测试"""

    def test_trading_day_returns_true(self, test_db):
        """交易日应返回 True"""
        ensure_trading_day(test_db, date(2025, 3, 3), is_open=True)  # 周一
        assert _is_trading_day(test_db, date(2025, 3, 3)) is True

    def test_non_trading_day_returns_false(self, test_db):
        """非交易日应返回 False"""
        ensure_trading_day(test_db, date(2025, 3, 1), is_open=False)  # 周六
        assert _is_trading_day(test_db, date(2025, 3, 1)) is False

    def test_unknown_date_returns_false(self, test_db):
        """交易日历中不存在的日期应返回 False"""
        assert _is_trading_day(test_db, date(2030, 1, 1)) is False


class TestValidateTradingDay:
    """交易日校验测试"""

    def test_non_trading_day_raises(self, test_db):
        """非交易日应抛出 ValueError"""
        ensure_trading_day(test_db, date(2025, 3, 2), is_open=False)
        with pytest.raises(ValueError, match="不是交易日"):
            _validate_trading_day(test_db, date(2025, 3, 2))

    def test_trading_day_passes(self, test_db):
        """交易日应通过校验"""
        ensure_trading_day(test_db, date(2025, 3, 3), is_open=True)
        _validate_trading_day(test_db, date(2025, 3, 3))


class TestPrevTradingDay:
    """前一交易日查找测试"""

    def test_prev_trading_day_basic(self, test_db):
        """应正确找到前一个交易日"""
        ensure_trading_day(test_db, date(2025, 3, 3), is_open=True)  # 周一
        ensure_trading_day(test_db, date(2025, 3, 4), is_open=True)  # 周二
        result = _prev_trading_day(test_db, date(2025, 3, 4), 1)
        assert result == date(2025, 3, 3)

    def test_prev_trading_day_skips_weekend(self, test_db):
        """应跳过周末非交易日"""
        ensure_trading_day(test_db, date(2025, 2, 28), is_open=True)  # 周五
        ensure_trading_day(test_db, date(2025, 3, 1), is_open=False)  # 周六
        ensure_trading_day(test_db, date(2025, 3, 2), is_open=False)  # 周日
        ensure_trading_day(test_db, date(2025, 3, 3), is_open=True)  # 周一
        result = _prev_trading_day(test_db, date(2025, 3, 3), 1)
        assert result == date(2025, 2, 28)


class TestCheckPendingTransactions:
    """Pending 交易检查测试"""

    def test_no_pending_passes(self, test_db):
        """无 pending 交易应通过"""
        create_portfolio(test_db, code="NO_PEND", status="active")
        result = _check_pending_transactions(test_db, "NO_PEND", date(2025, 3, 3))
        assert result["status"] == "passed"

    def test_pending_trade_fails(self, test_db):
        """存在 pending 调仓交易应失败"""
        create_portfolio(test_db, code="PEND_T", status="active")
        create_product(test_db, code="FUND01", market="CN_OTC", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="PLAT_P")
        create_trade(
            test_db, portfolio_code="PEND_T",
            product_code="FUND01", market="CN_OTC",
            status="pending", trade_date=date(2025, 3, 3),
            confirm_date=date(2025, 3, 3),  # confirm_date <= target_date 才会被检查
        )
        result = _check_pending_transactions(test_db, "PEND_T", date(2025, 3, 3))
        assert result["status"] == "failed"
        assert "待确认交易" in result["message"]

    def test_pending_subscription_fails(self, test_db):
        """存在 pending 申购应失败（confirm_date <= target_date 才触发）"""
        create_portfolio(test_db, code="PEND_S", status="active")
        create_investor(test_db, code="INV_PEND")
        create_subscription(
            test_db, portfolio_code="PEND_S", investor_code="INV_PEND",
            status="pending", apply_date=date(2025, 3, 2),
            confirm_date=date(2025, 3, 3),  # confirm_date <= target_date 才触发
        )
        result = _check_pending_transactions(test_db, "PEND_S", date(2025, 3, 3))
        assert result["status"] == "failed"

    def test_pending_trade_null_confirm_date_fails(self, test_db):
        """confirm_date 为 NULL 的 pending 交易应兜底按 trade_date 命中（防 NULL 逃逸）"""
        create_portfolio(test_db, code="PEND_TN", status="active")
        create_product(test_db, code="FUND02", market="CN_OTC", asset_class_code="ASSET_STOCK")
        create_trade(
            test_db, portfolio_code="PEND_TN",
            product_code="FUND02", market="CN_OTC",
            status="pending", trade_date=date(2025, 3, 3),
            confirm_date=None,  # 异常数据：NULL confirm_date
        )
        result = _check_pending_transactions(test_db, "PEND_TN", date(2025, 3, 3))
        assert result["status"] == "failed"

    def test_pending_subscription_null_confirm_date_fails(self, test_db):
        """confirm_date 为 NULL 的 pending 申购应兜底按 apply_date 命中（防 NULL 逃逸）"""
        create_portfolio(test_db, code="PEND_SN", status="active")
        create_investor(test_db, code="INV_PENDN")
        create_subscription(
            test_db, portfolio_code="PEND_SN", investor_code="INV_PENDN",
            status="pending", apply_date=date(2025, 3, 2),
            confirm_date=None,  # 异常数据：NULL confirm_date
        )
        result = _check_pending_transactions(test_db, "PEND_SN", date(2025, 3, 3))
        assert result["status"] == "failed"

    def test_pending_null_confirm_date_after_target_passes(self, test_db):
        """NULL confirm_date 但 trade_date/apply_date 晚于 target 时不应误报"""
        create_portfolio(test_db, code="PEND_NF", status="active")
        create_product(test_db, code="FUND03", market="CN_OTC", asset_class_code="ASSET_STOCK")
        create_trade(
            test_db, portfolio_code="PEND_NF",
            product_code="FUND03", market="CN_OTC",
            status="pending", trade_date=date(2025, 3, 5),
            confirm_date=None,
        )
        result = _check_pending_transactions(test_db, "PEND_NF", date(2025, 3, 3))
        assert result["status"] == "passed"


class TestCheckShareChangeEvents:
    """份额变动事件检查测试"""

    def test_no_pending_events_passes(self, test_db):
        """无 pending 事件应通过"""
        create_portfolio(test_db, code="NO_EVT", status="active")
        result = _check_share_change_events(test_db, "NO_EVT", date(2025, 3, 3))
        assert result["status"] == "passed"


class TestFrozenSharesCalculation:
    """冻结份额计算测试"""

    def test_no_pending_sells_zero_frozen(self, test_db):
        """无 pending 卖出，冻结份额应为 0"""
        create_portfolio(test_db, code="FZ0", status="active")
        result = _calculate_frozen_shares(
            test_db, "FZ0", "510300.SH", "CN_EXCHANGE", date(2025, 3, 3)
        )
        assert result == Decimal("0")

    def test_pending_sell_freezes_shares(self, test_db):
        """pending 卖出应冻结对应份额"""
        create_portfolio(test_db, code="FZ1", status="active")
        create_product(test_db, code="510300.SH", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="PLAT_FZ")
        create_trade(
            test_db, portfolio_code="FZ1",
            product_code="510300.SH", market="CN_EXCHANGE",
            trade_type="sell", shares=500.0, status="pending",
            trade_date=date(2025, 3, 3), platform_code="PLAT_FZ",
        )
        result = _calculate_frozen_shares(
            test_db, "FZ1", "510300.SH", "CN_EXCHANGE", date(2025, 3, 3)
        )
        assert result == Decimal("500.0") or result == Decimal("500")

    def test_portfolio_frozen_shares_from_pending_redeem(self, test_db):
        """pending 赎回应冻结组合份额"""
        create_portfolio(test_db, code="FZP", status="active")
        create_investor(test_db, code="INV_FZP")
        create_subscription(
            test_db, portfolio_code="FZP", investor_code="INV_FZP",
            sub_type="redeem", shares=1000.0, status="pending",
            apply_date=date(2025, 3, 3),
        )
        result = _calculate_portfolio_frozen_shares(test_db, "FZP", date(2025, 3, 3))
        assert result == Decimal("1000.0") or result == Decimal("1000")

    def test_investor_frozen_shares(self, test_db):
        """投资人 pending 赎回冻结份额"""
        create_portfolio(test_db, code="FZI", status="active")
        create_investor(test_db, code="INV_FZI")
        create_subscription(
            test_db, portfolio_code="FZI", investor_code="INV_FZI",
            sub_type="redeem", shares=2000.0, status="pending",
            apply_date=date(2025, 3, 3),
        )
        result = _calculate_investor_frozen_shares(
            test_db, "FZI", "INV_FZI", date(2025, 3, 3)
        )
        assert result == Decimal("2000.0") or result == Decimal("2000")

    def test_confirmed_trade_not_frozen(self, test_db):
        """confirmed 卖出不计入冻结"""
        create_portfolio(test_db, code="FZC", status="active")
        create_product(test_db, code="510300.SH", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="PLAT_FZC")
        create_trade(
            test_db, portfolio_code="FZC",
            product_code="510300.SH", market="CN_EXCHANGE",
            trade_type="sell", shares=300.0, status="confirmed",
            trade_date=date(2025, 3, 3), platform_code="PLAT_FZC",
        )
        result = _calculate_frozen_shares(
            test_db, "FZC", "510300.SH", "CN_EXCHANGE", date(2025, 3, 3)
        )
        assert result == Decimal("0")


class TestValidateSnapshotDependencies:
    """快照依赖校验集成测试"""

    def test_non_trading_day_fails(self, test_db):
        """非交易日校验应失败"""
        create_portfolio(test_db, code="DEP_NTD", status="active")
        ensure_trading_day(test_db, date(2025, 4, 5), is_open=False)
        results = validate_snapshot_dependencies(test_db, "DEP_NTD", date(2025, 4, 5))
        trading_day_check = [r for r in results if r["check_type"] == "trading_day"]
        assert trading_day_check[0]["status"] == "failed"

    def test_all_checks_pass_on_clean_state(self, test_db):
        """干净状态下所有校验应通过"""
        create_portfolio(test_db, code="DEP_OK", status="active")
        ensure_trading_day(test_db, date(2025, 4, 7), is_open=True)
        results = validate_snapshot_dependencies(test_db, "DEP_OK", date(2025, 4, 7))
        # trading_day 和 pending_transactions 应通过
        for r in results:
            if r["check_type"] in ("trading_day", "pending_transactions"):
                assert r["status"] == "passed"

    def test_static_only_is_exact_static_subset(self, test_db):
        """防漂移（issue #58 预校验单一口径）：static_only=True 的检查项
        必须恰为全量检查的静态子集，动态项恰为 auto_confirm 可消化的两项。

        新增检查项时本测试会失败，强制开发者在 validate_snapshot_dependencies
        中显式归类并同步更新此处断言。
        """
        create_portfolio(test_db, code="DEP_ST", status="active")
        ensure_trading_day(test_db, date(2025, 4, 7), is_open=True)

        full_types = {
            r["check_type"]
            for r in validate_snapshot_dependencies(test_db, "DEP_ST", date(2025, 4, 7))
        }
        static_types = {
            r["check_type"]
            for r in validate_snapshot_dependencies(
                test_db, "DEP_ST", date(2025, 4, 7), static_only=True
            )
        }

        assert static_types == {"trading_day", "price_data"}
        assert static_types < full_types
        # 动态项 = 重算循环内 auto_confirm 会逐日消化的 pending 申赎/事件
        assert full_types - static_types == {
            "pending_transactions", "share_change_events"
        }


class TestGeneratePortfolioValueSnapshot:
    """组合市值快照 total_shares 增量计算测试（issue #12 修复）

    验证 total_shares 采用增量法：前序快照 + 窗口内申购 - 窗口内赎回，
    而非读取滞后一日的 investor_holding。
    """

    def _make_cash_position(self, portfolio_code, snapshot_date, market_value):
        """构造一个 CASH 持仓对象供 _generate_portfolio_value_snapshot 使用"""
        return PortfolioPosition(
            portfolio_code=portfolio_code,
            product_code="CASH",
            market="",
            platform_code="MYCF",
            shares=None,
            cash_amount=Decimal(str(market_value)),
            market_value=Decimal(str(market_value)),
            snapshot_date=snapshot_date,
        )

    def test_first_snapshot_fallback(self, test_db):
        """首次快照无前序快照，total_shares = total_value（NAV=1.0）"""
        create_portfolio(test_db, code="PVS1", status="active")
        positions = [self._make_cash_position("PVS1", date(2025, 1, 6), 1000)]
        snap = _generate_portfolio_value_snapshot(
            test_db, "PVS1", date(2025, 1, 6), positions
        )
        assert snap.total_shares == 1000.0
        assert snap.unit_price == 1.0

    def test_subscribe_confirmed_same_day(self, test_db):
        """申购确认日份额应计入 total_shares（核心 bug 修复）"""
        create_portfolio(test_db, code="PVS2", status="active")
        create_investor(test_db, code="INV_PVS2")
        create_value_snapshot(
            test_db, "PVS2", date(2025, 1, 6),
            total_value=100, total_shares=100, unit_price=1.0,
        )
        create_subscription(
            test_db, portfolio_code="PVS2", investor_code="INV_PVS2",
            sub_type="subscribe", shares=100, amount=100,
            unit_price=1.0, apply_date=date(2025, 1, 6),
            confirm_date=date(2025, 1, 7),
            status="confirmed",
        )
        positions = [self._make_cash_position("PVS2", date(2025, 1, 7), 200)]
        snap = _generate_portfolio_value_snapshot(
            test_db, "PVS2", date(2025, 1, 7), positions
        )
        assert snap.total_shares == 200.0  # 100 prev + 100 new
        assert snap.unit_price == 1.0

    def test_redeem_confirmed_same_day(self, test_db):
        """赎回确认日份额应扣减 total_shares"""
        create_portfolio(test_db, code="PVS3", status="active")
        create_investor(test_db, code="INV_PVS3")
        create_value_snapshot(
            test_db, "PVS3", date(2025, 1, 6),
            total_value=200, total_shares=200, unit_price=1.0,
        )
        create_subscription(
            test_db, portfolio_code="PVS3", investor_code="INV_PVS3",
            sub_type="redeem", shares=50, amount=50,
            unit_price=1.0, apply_date=date(2025, 1, 6),
            confirm_date=date(2025, 1, 7),
            status="confirmed",
        )
        positions = [self._make_cash_position("PVS3", date(2025, 1, 7), 150)]
        snap = _generate_portfolio_value_snapshot(
            test_db, "PVS3", date(2025, 1, 7), positions
        )
        assert snap.total_shares == 150.0  # 200 - 50
        assert snap.unit_price == 1.0

    def test_gap_window_covers_multiple_days(self, test_db):
        """间隔窗口：前序快照几天前，窗口内多日申赎都计入"""
        create_portfolio(test_db, code="PVS4", status="active")
        create_investor(test_db, code="INV_PVS4")
        create_value_snapshot(
            test_db, "PVS4", date(2025, 1, 6),
            total_value=100, total_shares=100, unit_price=1.0,
        )
        # 01-07 和 01-08 各确认申购 100 份，但不生成中间快照
        create_subscription(
            test_db, portfolio_code="PVS4", investor_code="INV_PVS4",
            sub_type="subscribe", shares=100, amount=100,
            unit_price=1.0, apply_date=date(2025, 1, 6),
            confirm_date=date(2025, 1, 7),
            status="confirmed",
        )
        create_subscription(
            test_db, portfolio_code="PVS4", investor_code="INV_PVS4",
            sub_type="subscribe", shares=100, amount=100,
            unit_price=1.0, apply_date=date(2025, 1, 7),
            confirm_date=date(2025, 1, 8),
            status="confirmed",
        )
        # 01-09 生成快照（跳过了 01-07 和 01-08）
        positions = [self._make_cash_position("PVS4", date(2025, 1, 9), 300)]
        snap = _generate_portfolio_value_snapshot(
            test_db, "PVS4", date(2025, 1, 9), positions
        )
        assert snap.total_shares == 300.0  # 100 + 100 + 100
        assert snap.unit_price == 1.0

    def test_no_new_subscriptions_keeps_prev(self, test_db):
        """无新申赎时 total_shares = 前序快照"""
        create_portfolio(test_db, code="PVS5", status="active")
        create_value_snapshot(
            test_db, "PVS5", date(2025, 1, 6),
            total_value=500, total_shares=500, unit_price=1.0,
        )
        positions = [self._make_cash_position("PVS5", date(2025, 1, 7), 500)]
        snap = _generate_portfolio_value_snapshot(
            test_db, "PVS5", date(2025, 1, 7), positions
        )
        assert snap.total_shares == 500.0
        assert snap.unit_price == 1.0


class TestGeneratePortfolioPositionCashNone:
    """#20 回归：非现金零持仓且 amount 为 None 时不应抛 TypeError"""

    def test_zero_share_position_with_none_amount_skipped(self, test_db):
        """前一日快照非现金持仓 shares=0, amount=None，生成时应跳过而非崩溃"""
        create_portfolio(test_db, code="ZS20", status="active")
        create_platform(test_db, code="PLAT20")
        create_product(test_db, code="STK20", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_position_snapshot(
            test_db, portfolio_code="ZS20", product_code="STK20",
            market="CN_OTC", snapshot_date=date(2025, 3, 3),
            shares=0.0, cash_amount=None, platform_code="PLAT20",
 market_value=0.0,
        )
        # 修复前：(None <= 0) 抛 TypeError；修复后：零持仓被跳过
        result, _ = _generate_portfolio_position(test_db, "ZS20", date(2025, 3, 4))
        assert all(p.product_code != "STK20" for p in result)


class TestCascadeUnconfirmEvents:
    """#34 回归：级联回退只回退 entitlement_date == snapshot_date 的事件"""

    def _make_event(self, db, portfolio_code, ex_date, entitlement_date):
        evt = ShareChangeEvent(
            portfolio_code=portfolio_code,
            product_code="F34", market="CN_OTC",
            event_type="share_split",
            ex_date=ex_date, entitlement_date=entitlement_date,
            event_source="manual", status="confirmed",
            entitlement_shares=Decimal("1000"),
            shares_before=Decimal("1000"),
            shares_change=Decimal("100"),
            shares_after=Decimal("1100"),
        )
        db.add(evt)
        db.commit()
        db.refresh(evt)
        return evt

    def test_entitlement_date_match_rolled_back(self, test_db):
        """entitlement_date == snapshot_date 的事件应被回退"""
        create_portfolio(test_db, code="CAS34A", status="active")
        create_product(test_db, code="F34", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        evt = self._make_event(
            test_db, "CAS34A",
            ex_date=date(2025, 3, 10), entitlement_date=date(2025, 3, 5),
        )
        result = _cascade_unconfirm_share_change_events(test_db, "CAS34A", date(2025, 3, 5))
        # cascade 未 commit，同会话对象已被置为 pending（不能 refresh）
        assert any(r["id"] == evt.id for r in result)
        assert evt.status == "pending"
        assert evt.entitlement_shares is None

    def test_ex_date_match_not_rolled_back(self, test_db):
        """ex_date == snapshot_date 但 entitlement_date < snapshot_date 不应回退"""
        create_portfolio(test_db, code="CAS34B", status="active")
        create_product(test_db, code="F34", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        evt = self._make_event(
            test_db, "CAS34B",
            ex_date=date(2025, 3, 10), entitlement_date=date(2025, 3, 5),
        )
        result = _cascade_unconfirm_share_change_events(test_db, "CAS34B", date(2025, 3, 10))
        assert all(r["id"] != evt.id for r in result)
        assert evt.status == "confirmed"
        assert evt.entitlement_shares == Decimal("1000")


class TestInvestorHoldingDerivedFields:
    """#40 改进1：investor_holding 派生字段 market_value/total_cost/profit 回填"""

    def test_derived_fields_populated(self, test_db):
        create_portfolio(test_db, code="IHDF1", status="active")
        create_investor(test_db, code="INV_IHDF1")
        # 前序 holding：100 份，成本 1.0
        create_investor_holding(
            test_db, portfolio_code="IHDF1", investor_code="INV_IHDF1",
            snapshot_date=date(2025, 1, 6), shares=100, cost_per_share=1.0,
        )
        # value_snapshot：unit_price=1.2
        snap = PortfolioValueSnapshot(
            portfolio_code="IHDF1", snapshot_date=date(2025, 1, 7),
            total_value=Decimal("120"), total_shares=Decimal("100"),
            unit_price=Decimal("1.2"),
        )
        test_db.add(snap)
        test_db.commit()
        holdings = _generate_investor_holding(test_db, "IHDF1", date(2025, 1, 7), snap)
        target = [h for h in holdings if h.investor_code == "INV_IHDF1"]
        assert target, "应生成 INV_IHDF1 的 holding"
        h = target[0]
        assert h.shares == Decimal("100")
        assert h.market_value == Decimal("120.0")  # 100 * 1.2
        assert h.total_cost == Decimal("100.0")    # 100 * 1.0
        assert h.profit == Decimal("20.0")         # 120 - 100


class TestUnitPriceChangePct:
    """#40 改进1：PortfolioValueSnapshot.unit_price_change_pct 回填"""

    def _make_cash_position(self, portfolio_code, snapshot_date, market_value):
        return PortfolioPosition(
            portfolio_code=portfolio_code, product_code="CASH", market="",
            platform_code="MYCF", shares=None,
            cash_amount=Decimal(str(market_value)), market_value=Decimal(str(market_value)),
            snapshot_date=snapshot_date,
        )

    def test_first_snapshot_pct_zero(self, test_db):
        create_portfolio(test_db, code="PCT1", status="active")
        positions = [self._make_cash_position("PCT1", date(2025, 1, 6), 1000)]
        snap = _generate_portfolio_value_snapshot(test_db, "PCT1", date(2025, 1, 6), positions)
        assert snap.unit_price_change_pct == 0

    def test_second_snapshot_pct_nonzero(self, test_db):
        create_portfolio(test_db, code="PCT2", status="active")
        # 前序快照 unit_price=1.0
        create_value_snapshot(
            test_db, "PCT2", date(2025, 1, 6),
            total_value=100, total_shares=100, unit_price=1.0,
        )
        # 次日：total_value=110, total_shares=100 → unit_price=1.1
        positions = [self._make_cash_position("PCT2", date(2025, 1, 7), 110)]
        snap = _generate_portfolio_value_snapshot(test_db, "PCT2", date(2025, 1, 7), positions)
        # pct = (1.1 - 1.0) / 1.0 = 0.1
        assert abs(float(snap.unit_price_change_pct) - 0.1) < 0.0001


class TestValueSnapshotFourDecimalRounding:
    """#428：快照构造点的 4 位口径必须是 ROUND_HALF_UP（此前走 Decimal 缺省 HALF_EVEN）

    判别式：取 total_value / total_shares 恰好落在「第 5 位为 5 且第 4 位为偶数」的商——
    HALF_UP 进位、HALF_EVEN 舍去，两者分叉。0.45 / 1000 = 0.00045 即精确命中该边界
    （exponent 恰为 -5，不是二进制浮点造出来的近似），期望 0.0005。

    若把构造点改回 `.quantize(Decimal("0.0001"))`（缺省 HALF_EVEN），本用例即红。
    """

    def _make_cash_position(self, portfolio_code, snapshot_date, market_value):
        return PortfolioPosition(
            portfolio_code=portfolio_code, product_code="CASH", market="",
            platform_code="MYCF", shares=None,
            cash_amount=Decimal(str(market_value)), market_value=Decimal(str(market_value)),
            snapshot_date=snapshot_date,
        )

    def test_unit_price_boundary_is_half_up(self, test_db):
        create_portfolio(test_db, code="NAVR1", status="active")
        # 前序快照份额恒为 1000.00，使当日 unit_price 完全由 total_value 决定
        create_value_snapshot(
            test_db, "NAVR1", date(2025, 1, 6),
            total_value=Decimal("1000.0000"), total_shares=Decimal("1000.00"),
            unit_price=Decimal("1.0000"),
        )
        positions = [self._make_cash_position("NAVR1", date(2025, 1, 7), "0.45")]

        snap = _generate_portfolio_value_snapshot(
            test_db, "NAVR1", date(2025, 1, 7), positions
        )

        assert snap.total_shares == Decimal("1000.00")
        assert snap.unit_price == Decimal("0.0005"), (
            f"4 位口径应 HALF_UP 进位得 0.0005，实际 {snap.unit_price}"
            "（0.0004 说明走了 Decimal 缺省 HALF_EVEN）"
        )


class TestFrozenAmount:
    """#40 改进1：CASH 持仓 frozen_amount = pending CASH sells 金额"""

    def test_pending_cash_sell_freezes_amount(self, test_db):
        create_portfolio(test_db, code="FA1", status="active")
        create_platform(test_db, code="PLAT_FA")
        # 前序 CASH 持仓 1000（CASH 产品由 conftest 预置）
        create_position_snapshot(
            test_db, portfolio_code="FA1", product_code="CASH", market="",
            snapshot_date=date(2025, 1, 6), cash_amount=1000.0, market_value=1000.0,
            platform_code="PLAT_FA",
        )
        # pending CASH sell 300
        create_trade(
            test_db, portfolio_code="FA1", product_code="CASH", market="",
            trade_type="sell", amount=300.0, platform_code="PLAT_FA",
            trade_date=date(2025, 1, 6), status="pending",
        )
        result, _ = _generate_portfolio_position(test_db, "FA1", date(2025, 1, 7))
        cash_pos = [p for p in result if p.product_code == "CASH"]
        assert cash_pos, "应生成 CASH 持仓"
        assert cash_pos[0].frozen_amount == Decimal("300.0")


class TestPositionListener:
    """#40 改进2：ORM 层禁止 instance-level update/delete"""

    def test_update_blocked(self, test_db):
        create_portfolio(test_db, code="LST1", status="active")
        pos = create_position_snapshot(
            test_db, portfolio_code="LST1", product_code="CASH", market="",
            snapshot_date=date(2025, 1, 6), cash_amount=100.0, market_value=100.0,
            platform_code="MYCF",
        )
        pos.market_value = Decimal("200")
        with pytest.raises(RuntimeError):
            test_db.commit()

    def test_delete_blocked(self, test_db):
        create_portfolio(test_db, code="LST2", status="active")
        pos = create_position_snapshot(
            test_db, portfolio_code="LST2", product_code="CASH", market="",
            snapshot_date=date(2025, 1, 6), cash_amount=100.0, market_value=100.0,
            platform_code="MYCF",
        )
        test_db.delete(pos)
        with pytest.raises(RuntimeError):
            test_db.commit()


class TestValueSnapshotListener:
    """#59 加固：portfolio_value_snapshot ORM 层禁止 instance-level update/delete"""

    def test_update_blocked(self, test_db):
        create_portfolio(test_db, code="LSTV1", status="active")
        snap = create_value_snapshot(
            test_db, "LSTV1", date(2025, 1, 6),
            total_value=100, total_shares=100, unit_price=1.0,
        )
        snap.total_value = Decimal("200")
        with pytest.raises(RuntimeError):
            test_db.commit()

    def test_delete_blocked(self, test_db):
        create_portfolio(test_db, code="LSTV2", status="active")
        snap = create_value_snapshot(
            test_db, "LSTV2", date(2025, 1, 6),
            total_value=100, total_shares=100, unit_price=1.0,
        )
        test_db.delete(snap)
        with pytest.raises(RuntimeError):
            test_db.commit()


class TestInvestorHoldingListener:
    """#59 加固：investor_holding ORM 层禁止 instance-level update/delete"""

    def test_update_blocked(self, test_db):
        create_portfolio(test_db, code="LSTH1", status="active")
        create_investor(test_db, code="INV_L1")
        holding = create_investor_holding(
            test_db, portfolio_code="LSTH1", investor_code="INV_L1",
            snapshot_date=date(2025, 1, 6), shares=100.0,
        )
        holding.shares = Decimal("200")
        with pytest.raises(RuntimeError):
            test_db.commit()

    def test_delete_blocked(self, test_db):
        create_portfolio(test_db, code="LSTH2", status="active")
        create_investor(test_db, code="INV_L2")
        holding = create_investor_holding(
            test_db, portfolio_code="LSTH2", investor_code="INV_L2",
            snapshot_date=date(2025, 1, 6), shares=100.0,
        )
        test_db.delete(holding)
        with pytest.raises(RuntimeError):
            test_db.commit()


class TestCashIncrementalWithManual:
    """快照 CASH 增量累加 + manual_market_value 绝对覆盖"""

    def test_manual_market_value_override_applied(self, test_db):
        """快照生成时 manual_market_value 绝对替换生效"""
        from tests.factories import create_manual_market_value
        create_portfolio(test_db, code="MMV_P", status="active")
        create_platform(test_db, code="MMV_PLAT")
        ensure_trading_day(test_db, date(2025, 3, 10), is_open=True)
        # 前日快照：CASH = 9900
        create_position_snapshot(
            test_db, portfolio_code="MMV_P", product_code="CASH", market="",
            snapshot_date=date(2025, 3, 7), cash_amount=9900.0, market_value=9900.0,
            platform_code="MMV_PLAT",
        )
        # 设置 manual_market_value 覆盖为 8888
        create_manual_market_value(
            test_db, portfolio_code="MMV_P", platform_code="MMV_PLAT",
            product_code="CASH", record_date=date(2025, 3, 10),
            market_value=8888.0,
        )
        # 生成快照
        positions, _ = _generate_portfolio_position(test_db, "MMV_P", date(2025, 3, 10))
        cash_positions = [p for p in positions if p.product_code == "CASH"]
        assert len(cash_positions) == 1
        # 应被 manual 覆盖为 8888，而非前日 9900
        assert Decimal(str(cash_positions[0].cash_amount)) == Decimal("8888")

    def test_cash_incremental_from_prev_snapshot_plus_window(self, test_db):
        """前日快照 + 窗口内 CASH trades 增量累加"""
        create_portfolio(test_db, code="CV_P", status="active")
        create_platform(test_db, code="CV_PLAT")
        ensure_trading_day(test_db, date(2025, 3, 10), is_open=True)
        # 前日快照：CASH = 5000
        create_position_snapshot(
            test_db, portfolio_code="CV_P", product_code="CASH", market="",
            snapshot_date=date(2025, 3, 7), cash_amount=5000.0, market_value=5000.0,
            platform_code="CV_PLAT",
        )
        # 窗口内 CASH buy +3000, CASH sell -1000
        create_trade(
            test_db, "CV_P", "CASH", "",
            trade_type="buy", amount=3000.0, price=None,
            platform_code="CV_PLAT", trade_date=date(2025, 3, 8),
            confirm_date=date(2025, 3, 8), status="confirmed",
        )
        create_trade(
            test_db, "CV_P", "CASH", "",
            trade_type="sell", amount=1000.0, price=None,
            platform_code="CV_PLAT", trade_date=date(2025, 3, 9),
            confirm_date=date(2025, 3, 9), status="confirmed",
        )
        positions, _ = _generate_portfolio_position(test_db, "CV_P", date(2025, 3, 10))
        cash_positions = [p for p in positions if p.product_code == "CASH"]
        assert len(cash_positions) == 1
        # 5000 + 3000 - 1000 = 7000
        assert Decimal(str(cash_positions[0].cash_amount)) == Decimal("7000")

    def test_cash_manual_inherits_to_next_day(self, test_db):
        """前日 manual 覆盖值作为后续日基线（增量继承）"""
        from tests.factories import create_manual_market_value
        create_portfolio(test_db, code="INH_P", status="active")
        create_platform(test_db, code="INH_PLAT")
        ensure_trading_day(test_db, date(2025, 3, 7), is_open=True)
        ensure_trading_day(test_db, date(2025, 3, 10), is_open=True)
        # D-3 快照：CASH = 9900
        create_position_snapshot(
            test_db, portfolio_code="INH_P", product_code="CASH", market="",
            snapshot_date=date(2025, 3, 7), cash_amount=9900.0, market_value=9900.0,
            platform_code="INH_PLAT",
        )
        # D-2 有 manual 覆盖为 10000，先生成 D-2 快照
        create_manual_market_value(
            test_db, portfolio_code="INH_P", platform_code="INH_PLAT",
            product_code="CASH", record_date=date(2025, 3, 8),
            market_value=10000.0,
        )
        # 模拟 D-2 快照落库（manual 覆盖后的值）
        create_position_snapshot(
            test_db, portfolio_code="INH_P", product_code="CASH", market="",
            snapshot_date=date(2025, 3, 8), cash_amount=10000.0, market_value=10000.0,
            platform_code="INH_PLAT",
        )
        # D-1 窗口内申购 +1000
        create_trade(
            test_db, "INH_P", "CASH", "",
            trade_type="buy", amount=1000.0, price=None,
            platform_code="INH_PLAT", trade_date=date(2025, 3, 9),
            confirm_date=date(2025, 3, 9), status="confirmed",
        )
        # 生成 D-1 快照
        positions, _ = _generate_portfolio_position(test_db, "INH_P", date(2025, 3, 10))
        cash_positions = [p for p in positions if p.product_code == "CASH"]
        assert len(cash_positions) == 1
        # 基线 = D-2 快照 10000（含 manual）+ 窗口 1000 = 11000
        assert Decimal(str(cash_positions[0].cash_amount)) == Decimal("11000")
