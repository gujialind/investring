"""#777：双日历同步单测——HKEX 分支、按 exchange 的增量判重与任务聚合。

tushare client 全 mock（不出网）；探针年份取 2031/2032（远离种子窗口），
autouse fixture 清理，避免污染共享测试库。
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models.trading_calendar import TradingCalendar
from app.services.trading_calendar_service import sync_trading_calendar
from app.services.task_runner import run_calendar_sync

HK_ROWS = [
    {"date": "2031-01-02", "is_open": True},
    {"date": "2031-01-03", "is_open": False},   # 港股独有休市日形态
]
SSE_ROWS = [
    {"date": "2031-01-02", "is_open": True},
    {"date": "2031-01-03", "is_open": True},
]


@pytest.fixture(autouse=True)
def _clean_probe_years(test_db):
    yield
    test_db.query(TradingCalendar).filter(
        TradingCalendar.calendar_date >= date(2031, 1, 1),
        TradingCalendar.calendar_date <= date(2032, 12, 31),
    ).delete(synchronize_session=False)
    test_db.commit()


def _rows(test_db, exchange, year):
    return sorted(
        (r.calendar_date, r.is_open)
        for r in test_db.query(TradingCalendar).filter(
            TradingCalendar.exchange == exchange,
            TradingCalendar.calendar_date >= date(year, 1, 1),
            TradingCalendar.calendar_date <= date(year, 12, 31),
        ).all()
    )


@patch("app.services.trading_calendar_service.get_hk_trade_calendar")
@patch("app.services.trading_calendar_service.get_trade_calendar")
def test_hkex_sync_writes_exchange_rows(mock_sse, mock_hk, test_db):
    mock_hk.return_value = HK_ROWS
    result = sync_trading_calendar(test_db, 2031, exchange="HKEX")
    test_db.commit()

    assert result == {"synced_count": 2, "year": 2031, "exchange": "HKEX"}
    mock_hk.assert_called_once_with(2031)
    mock_sse.assert_not_called()
    assert _rows(test_db, "HKEX", 2031) == [
        (date(2031, 1, 2), True), (date(2031, 1, 3), False),
    ]
    assert _rows(test_db, "SSE", 2031) == []


@patch("app.services.trading_calendar_service.get_hk_trade_calendar")
@patch("app.services.trading_calendar_service.get_trade_calendar")
def test_sse_default_unchanged(mock_sse, mock_hk, test_db):
    """缺省 exchange=SSE：走通用 trade_cal，既有行为零变化。"""
    mock_sse.return_value = SSE_ROWS
    result = sync_trading_calendar(test_db, 2031)
    test_db.commit()

    assert result["synced_count"] == 2
    assert result["exchange"] == "SSE"
    mock_sse.assert_called_once_with(2031, "SSE")
    mock_hk.assert_not_called()
    assert _rows(test_db, "SSE", 2031) == [
        (date(2031, 1, 2), True), (date(2031, 1, 3), True),
    ]


@patch("app.services.trading_calendar_service.get_hk_trade_calendar")
def test_other_exchange_rows_do_not_block_hkex_insert(mock_hk, test_db):
    """同日期已有 SSE 行不挡 HKEX 插入——增量判重按 (date, exchange)（#777 核心）。"""
    test_db.bulk_insert_mappings(TradingCalendar, [
        {"calendar_date": date(2031, 1, 2), "is_open": True, "exchange": "SSE"},
        {"calendar_date": date(2031, 1, 3), "is_open": True, "exchange": "SSE"},
    ])
    test_db.commit()
    mock_hk.return_value = HK_ROWS

    result = sync_trading_calendar(test_db, 2031, exchange="HKEX")
    test_db.commit()

    assert result["synced_count"] == 2
    assert _rows(test_db, "HKEX", 2031) == [
        (date(2031, 1, 2), True), (date(2031, 1, 3), False),
    ]


@patch("app.services.trading_calendar_service.get_hk_trade_calendar")
def test_hkex_resync_idempotent(mock_hk, test_db):
    mock_hk.return_value = HK_ROWS
    sync_trading_calendar(test_db, 2031, exchange="HKEX")
    test_db.commit()
    second = sync_trading_calendar(test_db, 2031, exchange="HKEX")
    test_db.commit()

    assert second["synced_count"] == 0
    assert len(_rows(test_db, "HKEX", 2031)) == 2


def test_unsupported_exchange_rejected(test_db):
    with pytest.raises(ValueError, match="不支持的日历市场"):
        sync_trading_calendar(test_db, 2031, exchange="SZSE")


@patch("app.services.trading_calendar_service.get_hk_trade_calendar")
@patch("app.services.trading_calendar_service.get_trade_calendar")
def test_run_calendar_sync_aggregates_both(mock_sse, mock_hk, test_db):
    """任务入口一次同步两套日历，synced_count 汇总、per_exchange 分列。"""
    mock_sse.return_value = [
        {"date": "2032-01-05", "is_open": True},
        {"date": "2032-01-06", "is_open": True},
    ]
    mock_hk.return_value = [
        {"date": "2032-01-05", "is_open": True},
        {"date": "2032-01-06", "is_open": False},
    ]

    result = run_calendar_sync(test_db, year=2032)
    test_db.commit()

    assert result == {
        "synced_count": 4,
        "year": 2032,
        "per_exchange": {"SSE": 2, "HKEX": 2},
    }
    assert _rows(test_db, "SSE", 2032) == [
        (date(2032, 1, 5), True), (date(2032, 1, 6), True),
    ]
    assert _rows(test_db, "HKEX", 2032) == [
        (date(2032, 1, 5), True), (date(2032, 1, 6), False),
    ]


class TestGetHkTradeCalendarClient:
    """tushare_client.get_hk_trade_calendar 行映射（#777）：hk_tradecal → 与
    get_trade_calendar 对齐的统一结构（date=YYYY-MM-DD、is_open=bool）。"""

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_rows_mapped_and_dates_formatted(self, mock_get_pro, _sleep):
        import pandas as pd
        from app.services import tushare_client

        mock_pro = MagicMock()
        mock_pro.hk_tradecal.return_value = pd.DataFrame({
            "cal_date": ["20261016", "20261019", "20261020"],
            "is_open": [1, 0, 1],
        })
        mock_get_pro.return_value = mock_pro

        rows = tushare_client.get_hk_trade_calendar(2026)

        assert rows == [
            {"date": "2026-10-16", "is_open": True},
            {"date": "2026-10-19", "is_open": False},
            {"date": "2026-10-20", "is_open": True},
        ]
        kwargs = mock_pro.hk_tradecal.call_args.kwargs
        assert kwargs["start_date"] == "20260101"
        assert kwargs["end_date"] == "20261231"
        assert kwargs["fields"] == "cal_date,is_open"

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_empty_frame_returns_empty_list(self, mock_get_pro, _sleep):
        import pandas as pd
        from app.services import tushare_client

        mock_pro = MagicMock()
        mock_pro.hk_tradecal.return_value = pd.DataFrame()
        mock_get_pro.return_value = mock_pro

        assert tushare_client.get_hk_trade_calendar(2026) == []
