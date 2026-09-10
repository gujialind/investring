"""
run_nav_sync / run_snapshot_generate 测试（P5.4，#156 任务剥离）

验证：
- target_date = yesterday(T)（D3）
- 增量起点 = MAX(date)+1
- run_nav_sync 不再串联快照生成（无 snapshots_generated 键、不调 _generate_snapshots_for_date）
- 无 ShareChangeEvent 写入（D1 跳过分红检测）
- run_snapshot_generate 仅处理 active 且开启 auto_snapshot_enabled 的组合
- auto_confirm_after_snapshot 被调用
"""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

from app.models.sync_job import SyncJob
from app.models.nav_sync_detail import NavSyncDetail
from app.models.price_record import PriceRecord
from app.models.product import Product
from app.models.share_change_event import ShareChangeEvent
from app.models.task_execution_log import TaskExecutionLog
from app.services.task_runner import (
    run_nav_sync, run_snapshot_generate, _generate_snapshots_for_date,
)


def _task_log_id(db) -> int:
    """建一条真实 TaskExecutionLog 并返回 id。

    run_nav_sync 的 log_id 必填（#406 起），且 NavSyncDetail.task_log_id 是指向
    task_execution_log 的外键（conftest 在 SQLite 上开了 PRAGMA foreign_keys=ON），
    故这里必须造真实父行，不能传一个悬空整数。
    """
    log = TaskExecutionLog(
        task_code="nav_sync", trigger_type="manual", status="running",
        started_at=datetime.now(),
    )
    db.add(log)
    db.flush()
    return log.id


class TestTargetDateYesterday:
    """target_date = yesterday(T)（D3）"""

    @patch("app.services.market_data_service.sync_product_prices")
    def test_end_date_is_yesterday(self, mock_sync, test_db):
        """sync_product_prices 传入的 end_date 应为昨天"""
        mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}

        result = run_nav_sync(test_db, _task_log_id(test_db))

        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        assert result["target_date"] == yesterday

        call_args = mock_sync.call_args_list[0]
        passed_end_date = call_args.kwargs.get("end_date")
        assert passed_end_date == datetime.now().date() - timedelta(days=1)


class TestIncrementalStart:
    """增量起点 = MAX(date)+1"""

    @patch("app.services.market_data_service.sync_product_prices")
    @patch("app.services.task_runner._generate_snapshots_for_date")
    def test_start_date_from_max_date_plus_one(self, mock_snap, mock_sync, test_db):
        """有历史数据时，start_date = MAX(date)+1"""
        yesterday = datetime.now().date() - timedelta(days=1)
        day_before = yesterday - timedelta(days=1)

        record = PriceRecord(
            product_code="510300.SH", market="CN_EXCHANGE",
            price_date=day_before, unit_price=3.5, source="tushare",
        )
        test_db.add(record)
        test_db.commit()

        mock_sync.return_value = {"success": True, "synced_count": 1, "source": "tushare"}

        run_nav_sync(test_db, _task_log_id(test_db))

        # 不依赖产品遍历顺序（SQLite 按插入序、MySQL 按主键序），按 product_code 定位调用
        target_call = next(
            c for c in mock_sync.call_args_list
            if c.kwargs.get("product_code") == "510300.SH"
        )
        passed_start_date = target_call.kwargs.get("start_date")
        assert passed_start_date == day_before + timedelta(days=1)


class TestNoShareChangeEvent:
    """无 ShareChangeEvent 写入（D1 跳过分红检测）"""

    @patch("app.services.market_data_service.sync_product_prices")
    @patch("app.services.task_runner._generate_snapshots_for_date")
    def test_no_share_change_event_written(self, mock_snap, mock_sync, test_db):
        """run_nav_sync 不写 ShareChangeEvent"""
        count_before = test_db.query(ShareChangeEvent).count()

        mock_sync.return_value = {"success": True, "synced_count": 0, "source": "tushare"}
        run_nav_sync(test_db, _task_log_id(test_db))

        count_after = test_db.query(ShareChangeEvent).count()
        assert count_after == count_before


class TestSnapshotNotGatedByFailures:
    """#156：快照生成自 run_nav_sync 剥离，不再串联调用"""

    @patch("app.services.market_data_service.sync_product_prices")
    @patch("app.services.task_runner._generate_snapshots_for_date")
    def test_snapshot_not_attempted_even_with_failures(self, mock_snap, mock_sync, test_db):
        """run_nav_sync 不再调用 _generate_snapshots_for_date，返回 dict 无 snapshots_generated 键"""
        mock_sync.return_value = {"success": False, "synced_count": 0, "message": "API 错误", "source": "tushare"}

        result = run_nav_sync(test_db, _task_log_id(test_db))

        mock_snap.assert_not_called()
        assert "snapshots_generated" not in result


class TestAutoConfirmCalled:
    """auto_confirm_after_snapshot 被调用"""

    @patch("app.services.snapshot_service.auto_confirm_after_snapshot")
    @patch("app.services.snapshot_service.generate_daily_snapshots")
    def test_auto_confirm_called_on_trading_day(self, mock_gen, mock_auto, test_db):
        """快照生成后调 auto_confirm_after_snapshot"""
        from app.models.trading_calendar import TradingCalendar
        from app.models.portfolio import Portfolio

        yesterday = datetime.now().date() - timedelta(days=1)
        cal = test_db.query(TradingCalendar).filter(TradingCalendar.calendar_date == yesterday).first()
        if not cal:
            cal = TradingCalendar(calendar_date=yesterday, is_open=True, exchange="SSE")
            test_db.add(cal)
        else:
            cal.is_open = True
        test_db.commit()

        port = Portfolio(code="AUTO_CONFIRM_PORT", name="测试", status="active",
                         auto_snapshot_enabled=True)
        test_db.add(port)
        test_db.commit()

        _generate_snapshots_for_date(test_db, yesterday)

        mock_gen.assert_called()
        mock_auto.assert_called()


class TestBackfillLoop:
    """#33改动4：逐日循环补齐区间快照"""

    @patch("app.services.snapshot_service.auto_confirm_after_snapshot")
    @patch("app.services.snapshot_service.generate_daily_snapshots")
    def test_backfill_iterates_missing_trading_days(self, mock_gen, mock_auto, test_db):
        """从最新快照日之后首个交易日逐日生成至 target_date"""
        from tests.factories import (
            create_portfolio, create_value_snapshot, ensure_trading_day,
        )

        create_portfolio(test_db, code="BACKFILL_PORT", status="active",
                         auto_snapshot_enabled=True)
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 3), is_open=True)
        create_value_snapshot(
            test_db, portfolio_code="BACKFILL_PORT",
            snapshot_date=date(2025, 9, 1),
            total_value=10000.0, total_shares=10000.0, unit_price=1.0,
        )

        result = _generate_snapshots_for_date(test_db, date(2025, 9, 3))

        # 从 09-01 之后首个交易日 09-02 逐日到 09-03，共 2 天
        assert result["generated"] == 2
        generated_dates = [c.kwargs["target_date"] for c in mock_gen.call_args_list]
        assert generated_dates == [date(2025, 9, 2), date(2025, 9, 3)]

    @patch("app.services.snapshot_service.auto_confirm_after_snapshot")
    @patch("app.services.snapshot_service.generate_daily_snapshots")
    def test_backfill_stops_on_failure(self, mock_gen, mock_auto, test_db):
        """#35：单日失败即停止该组合回补"""
        from tests.factories import (
            create_portfolio, create_value_snapshot, ensure_trading_day,
        )

        create_portfolio(test_db, code="BACKFILL_FAIL", status="active",
                         auto_snapshot_enabled=True)
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 3), is_open=True)
        create_value_snapshot(
            test_db, portfolio_code="BACKFILL_FAIL",
            snapshot_date=date(2025, 9, 1),
            total_value=10000.0, total_shares=10000.0, unit_price=1.0,
        )

        mock_gen.side_effect = ValueError("依赖数据校验失败")

        result = _generate_snapshots_for_date(test_db, date(2025, 9, 3))

        # 首日 09-02 失败即 break，不继续 09-03
        assert result["generated"] == 0
        assert mock_gen.call_count == 1


class TestRunSnapshotGenerate:
    """#156：独立快照生成任务（run_snapshot_generate）与组合开关过滤"""

    @patch("app.services.snapshot_service.auto_confirm_after_snapshot")
    @patch("app.services.snapshot_service.generate_daily_snapshots")
    def test_only_enabled_active_portfolios_processed(self, mock_gen, mock_auto, test_db):
        """仅 active + auto_snapshot_enabled=True 的组合被处理（active+关 / draft+开 均跳过）"""
        from tests.factories import create_portfolio, ensure_trading_day

        create_portfolio(test_db, code="SNAP_ON", status="active",
                         auto_snapshot_enabled=True)
        create_portfolio(test_db, code="SNAP_OFF", status="active",
                         auto_snapshot_enabled=False)
        create_portfolio(test_db, code="SNAP_DRAFT", status="draft",
                         auto_snapshot_enabled=True)
        ensure_trading_day(test_db, date(2025, 9, 3), is_open=True)

        result = _generate_snapshots_for_date(test_db, date(2025, 9, 3))

        assert result["generated"] == 1
        portfolio_codes = [c.kwargs["portfolio_code"] for c in mock_gen.call_args_list]
        assert portfolio_codes == ["SNAP_ON"]

    @patch("app.services.task_runner._generate_snapshots_for_date")
    def test_target_date_is_yesterday(self, mock_snap, test_db):
        """run_snapshot_generate 以昨天为 target_date 调回补，返回结构含两者"""
        mock_snap.return_value = {
            "generated": 0, "portfolios_processed": 0,
            "warnings": [], "auto_confirm_failed": [],
        }

        # #406：log_id 死参数已从签名移除（快照链路无逐日明细表可关联）
        result = run_snapshot_generate(test_db)

        yesterday = datetime.now().date() - timedelta(days=1)
        assert result["target_date"] == yesterday.isoformat()
        assert result["snapshots_generated"] == 0
        assert result["portfolios_processed"] == 0
        mock_snap.assert_called_once()
        assert mock_snap.call_args.args[1] == yesterday

    @patch("app.services.snapshot_service.auto_confirm_after_snapshot")
    @patch("app.services.snapshot_service.generate_daily_snapshots")
    def test_fail_fast_preserved_for_enabled_portfolio(self, mock_gen, mock_auto, test_db):
        """开启开关的组合仍保留 #35 fail-fast：单日失败即停止回补"""
        from tests.factories import (
            create_portfolio, create_value_snapshot, ensure_trading_day,
        )

        create_portfolio(test_db, code="SNAP_FAIL", status="active",
                         auto_snapshot_enabled=True)
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 3), is_open=True)
        create_value_snapshot(
            test_db, portfolio_code="SNAP_FAIL",
            snapshot_date=date(2025, 9, 1),
            total_value=10000.0, total_shares=10000.0, unit_price=1.0,
        )

        mock_gen.side_effect = ValueError("依赖数据校验失败")

        result = _generate_snapshots_for_date(test_db, date(2025, 9, 3))

        assert result["generated"] == 0
        assert mock_gen.call_count == 1
