# ============================================================================
# 单元测试：service 层不 commit（issue #58，backend/AGENTS.md「分层目录与职责」节）
# ============================================================================
# 断言以下 service 函数全程不调用 db.commit()（事务边界交调用方）：
# - snapshot_service.generate_daily_snapshots / recalculate_snapshots
# - trading_calendar_service.sync_trading_calendar
# - market_data_service.sync_product_prices（含 _mark_failed 失败路径）
# - task_runner.cleanup_old_logs
# - trade_service.calculate_confirm_preview（纯计算，不修改 trade，issue #65）
# 方式：monkeypatch 会话的 commit 为直接抛 AssertionError，
# 函数若能正常完成即证明无 commit 调用（flush 允许）。
# ============================================================================

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.models import PortfolioValueSnapshot, PriceRecord, TradingCalendar
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_product,
    create_trade,
    create_price_record,
)


D0 = date(2025, 6, 6)       # 周五（conftest 日历工作日为交易日）
NEXT_DAY = date(2025, 6, 9)  # 下一交易日（周一）


def _forbid_commit(monkeypatch, db):
    """将会话 commit 替换为断言失败，捕捉 service 层的违规提交"""
    def _fail(*args, **kwargs):
        raise AssertionError("service 层不得调用 db.commit()（backend/AGENTS.md「分层目录与职责」节）")
    monkeypatch.setattr(db, "commit", _fail)


def _setup_cash_snapshot(db, portfolio_code: str, snapshot_date: date, amount: float = 10000.0):
    """制造指定日的持仓+市值快照（仅 CASH，无需行情）"""
    create_position_snapshot(
        db, portfolio_code, "CASH", "",
        snapshot_date=snapshot_date,
        cash_amount=amount, unit_price=None, cost_price=None,
        market_value=amount, platform_code="MYCF",
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=amount, total_shares=amount, unit_price=1.0,
    )


class TestSnapshotServiceNoCommit:
    """快照 service 全程无 commit（issue #58 核心 + 遗漏缺口）"""

    def test_generate_daily_snapshots_no_commit(self, test_db, monkeypatch):
        """generate_daily_snapshots 正常生成路径不 commit"""
        from app.services.snapshot_service import generate_daily_snapshots

        create_portfolio(test_db, code="NC_GEN", status="active")
        _setup_cash_snapshot(test_db, "NC_GEN", D0)

        _forbid_commit(monkeypatch, test_db)
        result = generate_daily_snapshots(test_db, "NC_GEN", NEXT_DAY)

        assert result["success"] is True
        # flush 后同事务内可见
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "NC_GEN",
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first() is not None

    def test_generate_daily_snapshots_skip_path_no_commit(self, test_db, monkeypatch):
        """无持仓跳过路径也不 commit"""
        from app.services.snapshot_service import generate_daily_snapshots

        create_portfolio(test_db, code="NC_SKIP", status="active")

        _forbid_commit(monkeypatch, test_db)
        result = generate_daily_snapshots(test_db, "NC_SKIP", D0)
        assert result["success"] is True
        assert "跳过" in result["message"]

    def test_recalculate_snapshots_no_commit(self, test_db, monkeypatch):
        """recalculate_snapshots 全区间重算不 commit（issue #58 本体）"""
        from app.services.snapshot_service import recalculate_snapshots

        create_portfolio(test_db, code="NC_RECALC", status="active")
        _setup_cash_snapshot(test_db, "NC_RECALC", D0)
        _setup_cash_snapshot(test_db, "NC_RECALC", NEXT_DAY)

        _forbid_commit(monkeypatch, test_db)
        result = recalculate_snapshots(test_db, "NC_RECALC", D0, NEXT_DAY)

        assert result["success"] is True
        assert result["results"][0]["errors"] == []
        assert result["results"][0]["total_processed"] == 2

    def test_recalculate_precheck_failure_no_deletion(self, test_db, monkeypatch):
        """预校验失败（NAV 缺失）→ 抛 ValueError，不删除任何快照"""
        from app.services.snapshot_service import recalculate_snapshots

        create_portfolio(test_db, code="NC_PRE", status="active")
        create_product(test_db, code="NAVX.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        _setup_cash_snapshot(test_db, "NC_PRE", D0)
        # 最新持仓含无任何价格记录的基金 → price_data 预校验必失败
        create_position_snapshot(
            test_db, "NC_PRE", "NAVX.OF", "CN_OTC",
            snapshot_date=D0, shares=100.0, market_value=100.0,
            platform_code="MYCF",
        )

        _forbid_commit(monkeypatch, test_db)
        with pytest.raises(ValueError, match="预校验失败"):
            recalculate_snapshots(test_db, "NC_PRE", D0, D0)

        # 未进入删除/重建流程，原快照仍在
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "NC_PRE",
            PortfolioValueSnapshot.snapshot_date == D0,
        ).first() is not None


class TestTradingCalendarServiceNoCommit:
    """sync_trading_calendar 不 commit"""

    @patch("app.services.trading_calendar_service.get_trade_calendar")
    def test_sync_trading_calendar_no_commit(self, mock_cal, test_db, monkeypatch):
        mock_cal.return_value = [
            {"date": "2030-01-02", "is_open": True},
            {"date": "2030-01-03", "is_open": True},
        ]
        _forbid_commit(monkeypatch, test_db)
        from app.services.trading_calendar_service import sync_trading_calendar
        result = sync_trading_calendar(test_db, 2030)

        assert result["synced_count"] == 2
        # flush 后同事务内可见
        assert test_db.query(TradingCalendar).filter(
            TradingCalendar.calendar_date == date(2030, 1, 2)
        ).first() is not None


class TestMarketDataServiceNoCommit:
    """sync_product_prices 及失败标记路径不 commit"""

    @patch("app.services.market_data_service.get_fund_daily")
    def test_sync_product_prices_no_commit(self, mock_daily, test_db, monkeypatch, sample_etf_product):
        mock_daily.return_value = [
            {"trade_date": "20250606", "close": 4.0, "pre_close": 3.9, "pct_chg": 2.56},
        ]
        _forbid_commit(monkeypatch, test_db)
        from app.services.market_data_service import sync_product_prices
        result = sync_product_prices(
            test_db, sample_etf_product.code, sample_etf_product.market,
            start_date=D0, end_date=D0,
        )
        assert result["success"] is True
        assert test_db.query(PriceRecord).filter(
            PriceRecord.product_code == sample_etf_product.code,
            PriceRecord.price_date == D0,
        ).first() is not None

    @patch("app.services.market_data_service.get_fund_daily")
    def test_mark_failed_path_no_commit(self, mock_daily, test_db, monkeypatch, sample_etf_product):
        """数据源异常 → _mark_failed 不 commit，失败标记停留在事务内待调用方提交"""
        mock_daily.side_effect = Exception("tushare boom")
        _forbid_commit(monkeypatch, test_db)
        from app.services.market_data_service import sync_product_prices
        result = sync_product_prices(
            test_db, sample_etf_product.code, sample_etf_product.market,
            start_date=D0, end_date=D0,
        )
        assert result["success"] is False
        # 同事务内失败状态已写入（flush）
        assert sample_etf_product.data_source_status == "failed"


class TestTaskRunnerCleanupNoCommit:
    """cleanup_old_logs 不 commit（单次性原子操作，事务交调用方）"""

    def test_cleanup_old_logs_no_commit(self, test_db, monkeypatch):
        from app.services.task_runner import cleanup_old_logs

        _forbid_commit(monkeypatch, test_db)
        result = cleanup_old_logs(test_db)
        assert set(result.keys()) == {
            "login_logs", "audit_logs", "nav_sync_details", "task_logs", "error_logs",
        }


class TestTradePreviewNoCommit:
    """calculate_confirm_preview 纯计算：不 commit、不修改 trade（issue #65）"""

    def test_calculate_confirm_preview_no_commit_no_mutation(self, test_db, monkeypatch):
        from app.services.trade_service import calculate_confirm_preview

        create_portfolio(test_db, code="NC_PRV", status="active")
        product = create_product(
            test_db, code="PRVNC.OF", market="CN_OTC",
            product_type="OEF", asset_class_code="ASSET_STOCK", confirm_days=1,
        )
        create_price_record(test_db, "PRVNC.OF", "CN_OTC", D0, unit_price=1.25)
        trade = create_trade(
            test_db, "NC_PRV", "PRVNC.OF", "CN_OTC",
            trade_type="buy", amount=10000.0, actual_amount=10000.0,
            price=None, trade_date=D0, confirm_date=NEXT_DAY, status="pending",
        )

        _forbid_commit(monkeypatch, test_db)
        result = calculate_confirm_preview(test_db, trade, product)

        # 计算结果正确（与 confirm 共用同一实现）
        assert result["price"] == Decimal("1.25")
        assert result["shares"] == Decimal("8000")
        assert result["confirm_date"] == NEXT_DAY
        assert result["is_otc_nav_fund"] is True
        assert result["paired_cash_amount"] == Decimal("10000")
        # 纯计算：trade 对象未被修改
        assert trade.status == "pending"
        assert trade.price is None
        assert trade.shares is None


class TestSubscriptionPreviewNoCommit:
    """calculate_subscription_confirm_preview 纯计算：不 commit、不修改 subscription（#248）"""

    def test_calculate_subscription_confirm_preview_no_commit_no_mutation(
        self, test_db, monkeypatch
    ):
        from app.services.subscription_service import (
            calculate_subscription_confirm_preview,
        )
        from tests.factories import (
            create_investor,
            create_subscription,
            ensure_trading_day,
            create_value_snapshot,
        )

        create_portfolio(test_db, code="NC_SPRV", status="active")
        create_investor(test_db, code="NC_SPINV")
        ensure_trading_day(test_db, D0, is_open=True)
        ensure_trading_day(test_db, NEXT_DAY, is_open=True)
        create_value_snapshot(test_db, "NC_SPRV", D0,
                              total_value=12500, total_shares=10000, unit_price=1.25)
        sub = create_subscription(
            test_db, "NC_SPRV", "NC_SPINV", sub_type="subscribe",
            amount=10000.0, apply_date=D0,
        )

        _forbid_commit(monkeypatch, test_db)
        result = calculate_subscription_confirm_preview(test_db, sub)

        # 计算结果正确（与 confirm 共用同一实现）
        assert result["nav"] == Decimal("1.25")
        assert result["shares"] == Decimal("8000")
        assert result["amount"] == Decimal("10000")
        assert result["confirm_date"] == NEXT_DAY
        assert result["is_first"] is True
        # 纯计算：subscription 对象未被修改
        assert sub.status == "pending"
        assert sub.unit_price is None
        assert sub.shares is None
        assert sub.confirm_date is None
