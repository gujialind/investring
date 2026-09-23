"""
sync_product_prices 改造测试（P5.2）

验证：
- 批量 upsert 幂等性（同区间跑两次不翻倍）
- 数据源路由（tushare CN_EXCHANGE / akshare HK_MUTUAL）
- 禁止硬编码 data_source 覆盖
- sync_error 失败落库
- 不支持的数据源 → skipped
- 无单价行的写入侧过滤（#580）：跳过 + WARNING，整批无价不是失败
"""
import logging

import pytest
from datetime import date, datetime
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

from app.models.price_record import PriceRecord
from app.models.product import Product
from app.services.market_data_service import (
    sync_product_prices, _normalize_raw, _bulk_upsert_prices,
    _mark_failed, _mark_skipped, _usable_price,
)

MARKET_DATA_LOGGER = "app.services.market_data_service"


def _skip_warnings(caplog) -> list:
    """「跳过无单价的行情行」的 WARNING 记录（静默丢弃正是 #580 要消除的形态）。"""
    return [
        r for r in caplog.records
        if r.name == MARKET_DATA_LOGGER and "跳过无单价" in r.getMessage()
    ]


def _session_on_mysql_dialect() -> MagicMock:
    """顶一个「自报 MySQL 方言」的 session，供下面两条空批次用例使用。

    `_bulk_upsert_prices` 按 `db.bind.dialect.name` 分叉，而「整批被滤空」这条只在
    MySQL 分支会炸（SQLite 分支遍历空列表恰好无感）。做成 helper 而不是在用例里写字面量，
    是为了不打 `@pytest.mark.dialect`：这两条用例**不依赖 MySQL 方言行为**（session 是假的），
    任何方言下都该跑；打了标记，将来按 `-m` 收窄就会把它们从 SQLite job 里筛掉，
    正好丢掉这条回归（`tests/unit/test_dialect_marker_guard.py` 头部登记的 helper 盲区，
    此处是刻意使用，理由即上述）。
    """
    db = MagicMock()
    db.bind.dialect.name = "mysql"
    return db


class TestBulkUpsertIdempotent:
    """批量 upsert 幂等性"""

    def test_upsert_idempotent(self, test_db: Session, sample_etf_product: Product):
        """同产品同区间跑两次，price_record 记录数不翻倍"""
        rows = [
            {"trade_date": "20250106", "close": 3.5, "pre_close": 3.4, "pct_chg": 0.029},
            {"trade_date": "20250107", "close": 3.6, "pre_close": 3.5, "pct_chg": 0.028},
        ]
        normalized = _normalize_raw(rows, "CN_EXCHANGE")

        _bulk_upsert_prices(test_db, "510300.SH", "CN_EXCHANGE", normalized, "tushare")
        test_db.commit()
        count1 = test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "510300.SH",
            PriceRecord.market == "CN_EXCHANGE",
        ).count()

        _bulk_upsert_prices(test_db, "510300.SH", "CN_EXCHANGE", normalized, "tushare")
        test_db.commit()
        count2 = test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "510300.SH",
            PriceRecord.market == "CN_EXCHANGE",
        ).count()

        assert count1 == count2 == 2, f"幂等失败: 第一次={count1}, 第二次={count2}"

    def test_upsert_updates_existing_values(self, test_db: Session, sample_etf_product: Product):
        """重跑时更新已有记录的值"""
        rows = [{"trade_date": "20250106", "close": 3.5}]
        normalized = _normalize_raw(rows, "CN_EXCHANGE")
        _bulk_upsert_prices(test_db, "510300.SH", "CN_EXCHANGE", normalized, "tushare")
        test_db.commit()

        updated = [{"trade_date": "20250106", "close": 4.0}]
        normalized2 = _normalize_raw(updated, "CN_EXCHANGE")
        _bulk_upsert_prices(test_db, "510300.SH", "CN_EXCHANGE", normalized2, "tushare")
        test_db.commit()

        record = test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "510300.SH",
            PriceRecord.price_date == date(2025, 1, 6),
        ).first()
        assert float(record.unit_price) == 4.0, "值应被更新为 4.0"


class TestDataSourceRouting:
    """数据源路由"""

    @patch("app.services.market_data_service.get_fund_daily")
    def test_tushare_cn_exchange(self, mock_get_fund_daily, test_db: Session, sample_etf_product: Product):
        """tushare + CN_EXCHANGE → 走 get_fund_daily"""
        mock_get_fund_daily.return_value = []
        result = sync_product_prices(test_db, "510300.SH", "CN_EXCHANGE",
                                     start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))
        mock_get_fund_daily.assert_called_once()
        assert result["source"] == "tushare"

    @patch("app.services.market_data_service.get_fund_nav")
    def test_tushare_cn_otc(self, mock_get_fund_nav, test_db: Session, sample_otc_product: Product):
        """tushare + CN_OTC → 走 get_fund_nav"""
        mock_get_fund_nav.return_value = []
        result = sync_product_prices(test_db, "000300.OF", "CN_OTC",
                                     start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))
        mock_get_fund_nav.assert_called_once()
        assert result["source"] == "tushare"

    def test_tushare_hk_mutual_skipped(self, test_db: Session):
        """tushare + HK_MUTUAL → skipped"""
        from sqlalchemy import and_
        product = test_db.query(Product).filter(
            Product.code == "1001767344", Product.market == "HK_MUTUAL"
        ).first()
        # 临时改 data_source 为 tushare 测试跳过
        product.data_source = "tushare"
        test_db.commit()

        result = sync_product_prices(test_db, "1001767344", "HK_MUTUAL",
                                     start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))
        assert result["success"] is True
        assert result["synced_count"] == 0
        assert "跳过" in result["message"]

        test_db.refresh(product)
        assert product.data_source_status == "skipped"


class TestNoHardcodeDataSourceOverride:
    """禁止硬编码 data_source"""

    @patch("app.services.market_data_service.get_fund_daily")
    def test_data_source_not_overwritten(self, mock_get_fund_daily, test_db: Session, sample_etf_product: Product):
        """成功路径不覆盖 product.data_source"""
        mock_get_fund_daily.return_value = [
            {"trade_date": "20250106", "close": 3.5, "pre_close": 3.4, "pct_chg": 2.9},
        ]
        original_ds = sample_etf_product.data_source

        sync_product_prices(test_db, "510300.SH", "CN_EXCHANGE",
                            start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))

        test_db.refresh(sample_etf_product)
        assert sample_etf_product.data_source == original_ds, "data_source 不应被覆盖"

    @patch("app.services.market_data_service.get_fund_daily")
    def test_source_field_uses_actual_data_source(self, mock_get_fund_daily, test_db: Session, sample_etf_product: Product):
        """PriceRecord.source 取 product.data_source，不恒写 tushare"""
        mock_get_fund_daily.return_value = [
            {"trade_date": "20250106", "close": 3.5, "pre_close": 3.4, "pct_chg": 2.9},
        ]
        # data_source 保持默认 "tushare"，验证 source 来自 product.data_source 而非硬编码
        sync_product_prices(test_db, "510300.SH", "CN_EXCHANGE",
                            start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))

        record = test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "510300.SH",
            PriceRecord.market == "CN_EXCHANGE",
        ).first()
        assert record is not None, "应有记录写入"
        assert record.source == sample_etf_product.data_source, \
            f"source 应取 product.data_source='{sample_etf_product.data_source}'"


class TestSyncErrorPersisted:
    """sync_error 失败落库"""

    @patch("app.services.market_data_service.get_fund_daily")
    def test_sync_error_written_on_failure(self, mock_get_fund_daily, test_db: Session, sample_etf_product: Product):
        """失败时写 product.sync_error"""
        from app.services.tushare_client import TushareAPIError
        mock_get_fund_daily.side_effect = TushareAPIError("获取基金日线行情失败: API 错误")

        result = sync_product_prices(test_db, "510300.SH", "CN_EXCHANGE",
                                     start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))

        assert result["success"] is False
        test_db.refresh(sample_etf_product)
        assert sample_etf_product.data_source_status == "failed"
        assert sample_etf_product.sync_error is not None
        assert "API 错误" in sample_etf_product.sync_error


class TestSkippedUnsupported:
    """不支持的数据源 → skipped"""

    def test_unknown_data_source_skipped(self, test_db: Session, sample_etf_product: Product):
        """未知 data_source → data_source_status='skipped'"""
        sample_etf_product.data_source = "unknown_source"
        test_db.commit()

        result = sync_product_prices(test_db, "510300.SH", "CN_EXCHANGE",
                                     start_date=date(2025, 1, 6), end_date=date(2025, 1, 10))

        assert result["success"] is True
        assert result["synced_count"] == 0
        test_db.refresh(sample_etf_product)
        assert sample_etf_product.data_source_status == "skipped"


class TestUnusablePriceFiltered:
    """#580 写入侧过滤：无单价的行不入库，「整批无价」是跳过而不是失败。

    为什么单独成组：这道过滤是 `unit_price` NOT NULL 的第一道防线，此前全仓没有任何
    用例直接钉它——摘掉过滤器套件照绿，回归只能等生产以 IntegrityError 暴露（#613 审查
    Suggestion 1）。而「整批被滤空」还有一条只在 MySQL 上炸的方言分叉（#613 审查
    Blocker）：空列表交给 `db.execute(sql, [])`，SQLAlchemy 2.0 抛
    `A value is required for bind parameter …`，于是设计意图里的「跳过 + WARNING + 计 0 条」
    在同步任务里被记成 failed、错误信息还是 SQLAlchemy 的内部文案；SQLite 分支遍历空列表
    恰好无感，所以本地全绿也抓不到。下面用 `_session_on_mysql_dialect()` 顶一个自报
    MySQL 方言的假 session 把这条分叉钉死（`test_all_rows_unusable_returns_zero` 在 CI 的
    MySQL job 上则是真身回归）。
    """

    @pytest.mark.parametrize("value", [None, "", float("nan"), float("inf"), "abc", "N/A"])
    def test_unusable_values_are_rejected(self, value):
        assert _usable_price(value) is False, f"{value!r} 不该被当作可入库单价"

    @pytest.mark.parametrize("value", [3.5, "3.5", 1])
    def test_usable_values_are_accepted(self, value):
        assert _usable_price(value) is True, f"{value!r} 是可入库单价"

    def test_mixed_rows_write_only_usable_ones(self, test_db: Session, sample_etf_product: Product, caplog):
        """混合批次：只有可价行入库、计数只算它们，被跳过的交易日进 WARNING"""
        rows = [
            {"trade_date": "20250106", "unit_price": 3.5, "pre_close": 3.4},
            {"trade_date": "20250107", "unit_price": None, "pre_close": 3.5},
            {"trade_date": "20250108", "unit_price": "", "pre_close": 3.5},
            {"trade_date": "20250109", "unit_price": float("nan"), "pre_close": 3.5},
        ]
        with caplog.at_level(logging.WARNING, logger=MARKET_DATA_LOGGER):
            count = _bulk_upsert_prices(test_db, "510300.SH", "CN_EXCHANGE", rows, "tushare")
        test_db.commit()

        assert count == 1, f"只应计入 1 条可价行，实际 {count}"
        written = test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "510300.SH",
            PriceRecord.market == "CN_EXCHANGE",
        ).all()
        assert [r.price_date for r in written] == [date(2025, 1, 6)], "无价行不该落库"
        assert all(r.unit_price is not None for r in written)

        warnings = _skip_warnings(caplog)
        assert warnings, "跳过无价行却没打 WARNING——又变回静默丢弃"
        assert warnings[-1].skipped == ["20250107", "20250108", "20250109"]
        assert warnings[-1].product_code == "510300.SH"

    def test_all_rows_unusable_returns_zero(self, test_db: Session, sample_etf_product: Product, caplog):
        """整批无价 → 计 0 条、不写库、不抛异常（MySQL job 上即 Blocker 的真身回归）"""
        rows = [
            {"trade_date": "20250106", "unit_price": None},
            {"trade_date": "20250107", "unit_price": ""},
        ]
        with caplog.at_level(logging.WARNING, logger=MARKET_DATA_LOGGER):
            count = _bulk_upsert_prices(test_db, "510300.SH", "CN_EXCHANGE", rows, "tushare")
        test_db.commit()

        assert count == 0
        assert test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "510300.SH",
            PriceRecord.market == "CN_EXCHANGE",
        ).count() == 0
        warnings = _skip_warnings(caplog)
        assert warnings and warnings[-1].skipped == ["20250106", "20250107"], \
            "整批被跳过时仍要留下全部交易日，否则事后无从定位缺哪几天"

    def test_mysql_branch_never_executes_an_empty_batch(self, caplog):
        """MySQL 分支的空批次早退：`db.execute(sql, [])` 会抛 StatementError（Blocker 本体）"""
        db = _session_on_mysql_dialect()
        rows = [{"trade_date": "20250106", "unit_price": None}]

        with caplog.at_level(logging.WARNING, logger=MARKET_DATA_LOGGER):
            count = _bulk_upsert_prices(db, "510300.SH", "CN_EXCHANGE", rows, "tushare")

        assert count == 0
        db.execute.assert_not_called()
        assert _skip_warnings(caplog), "早退不该把 WARNING 一起省掉"

    def test_mysql_branch_receives_only_usable_values(self):
        """非空批次仍走一次 executemany，且参数里只剩可价行"""
        db = _session_on_mysql_dialect()
        rows = [
            {"trade_date": "20250106", "unit_price": 3.5},
            {"trade_date": "20250107", "unit_price": None},
        ]

        count = _bulk_upsert_prices(db, "510300.SH", "CN_EXCHANGE", rows, "tushare")

        assert count == 1
        assert db.execute.call_count == 1
        values = db.execute.call_args.args[1]
        assert [v["price_date"] for v in values] == [date(2025, 1, 6)]
