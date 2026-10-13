"""
sync_product_prices 改造测试（P5.2）

验证：
- 批量 upsert 幂等性（同区间跑两次不翻倍）
- 数据源路由（tushare CN_EXCHANGE / akshare HK_MUTUAL）
- 禁止硬编码 data_source 覆盖
- sync_error 失败落库
- 不支持的数据源 → skipped
- 无单价行的写入侧过滤（#580）：跳过 + WARNING，整批无价不是失败
- 港互认净值故障的响亮与良性边界（#651）：取数层抛错→failed，合法空窗→仍 success
- 日涨跌的产生点收口（#692）：上游透传、tushare 场外就地推导、平盘 0.0 不折成缺值
- 净值列的非正形态过滤（#701）：0.0/负单价与坏值同族跳过，非正累计净值只丢这一列
"""
import logging

import pytest
from datetime import date, datetime
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

from app.models.price_record import PriceRecord
from app.models.product import Product
from decimal import Decimal

from app.services.market_data_service import (
    sync_product_prices, _normalize_raw, _bulk_upsert_prices,
    _mark_failed, _mark_skipped, _usable_price, _usable_nav,
    _derive_otc_pct_change, _db_prev_accum_nav, _optional_pct, get_nav_coverage,
)

MARKET_DATA_LOGGER = "app.services.market_data_service"


def _skip_warnings(caplog) -> list:
    """写入侧跳过/丢列那道 WARNING 的记录（静默丢弃正是 #580 要消除的形态）。

    按结构化 `operation` 筛而不按 message 子串筛：文案改一个字就让整组用例失去断言对象
    是脆弱的耦合，而 `operation="bulk_upsert_prices"` 是这条日志的身份。
    """
    return [
        r for r in caplog.records
        if r.name == MARKET_DATA_LOGGER and getattr(r, "operation", None) == "bulk_upsert_prices"
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

    #701 把这道防线从「只判可用性」扩成「可用性 + 必须为正」，判据换成 `_usable_nav`：
    `0.0`/负单价落库后是「有价的一天」而不是缺行——快照把市值算成 0、`nav_coverage`
    把这天计为已覆盖，比 NULL 更难从现场定位。参数表两侧都补齐，不再新起一套。
    """

    @pytest.mark.parametrize("value", [
        None, "", float("nan"), float("inf"), "abc", "N/A",
        # #701 补齐的 0/负形态：此前两侧参数表都没有它们，摘掉「必须为正」判据也不会
        # 有任何用例变红——这正是该缺口能潜伏到今天的直接原因。
        0.0, -1.5, 0, "0.0",
    ])
    def test_unusable_values_are_rejected(self, value):
        assert _usable_nav(value) is False, f"{value!r} 不该被当作可入库净值"

    @pytest.mark.parametrize("value", [3.5, "3.5", 1, 0.0001])
    def test_usable_values_are_accepted(self, value):
        assert _usable_nav(value) is True, f"{value!r} 是可入库净值"

    @pytest.mark.parametrize("value", [0.0, -1.5, 0, "0.0"])
    def test_availability_guard_keeps_zero_and_negative(self, value):
        """`_usable_price` 只管「上游给没给值」，正负判据在 `_usable_nav`（#701 分表）。

        这条不是冗余：`pct_change` 经 `_optional_pct` 共用 `_usable_price`，平盘那天的
        `0.0` 是 #692 专门保住的真实值。把判据「一刀切」改成判正会当场弄红平盘套件，
        所以两侧判据的差集必须显式钉住，不能只靠 docstring。
        """
        assert _usable_price(value) is True, f"{value!r} 仍是合格的比率输入"
        assert _optional_pct(value) is not None, "平盘 0.0 不得被折成缺值"

    def test_value_collapsing_to_zero_at_column_scale_is_rejected(self):
        """判正比的是**会落库的那个值**：列标度 4 位，`0.00001` 落库即 `0.0000`。"""
        assert _usable_nav(0.00001) is False
        assert _usable_nav(0.0001) is True

    def test_mixed_rows_write_only_usable_ones(self, test_db: Session, sample_etf_product: Product, caplog):
        """混合批次：只有合格单价的行入库、计数只算它们，被跳过的交易日进 WARNING"""
        rows = [
            {"trade_date": "20250106", "unit_price": 3.5, "pre_close": 3.4},
            {"trade_date": "20250107", "unit_price": None, "pre_close": 3.5},
            {"trade_date": "20250108", "unit_price": "", "pre_close": 3.5},
            {"trade_date": "20250109", "unit_price": float("nan"), "pre_close": 3.5},
            {"trade_date": "20250110", "unit_price": 0.0, "pre_close": 3.5},
            {"trade_date": "20250113", "unit_price": -1.5, "pre_close": 3.5},
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
        assert warnings[-1].skipped == [
            "20250107", "20250108", "20250109", "20250110", "20250113",
        ], "0 价与负价必须和无值同族被点名，否则事后无从分辨缺的是哪几天"
        assert warnings[-1].product_code == "510300.SH"

    def test_all_rows_unusable_returns_zero(self, test_db: Session, sample_etf_product: Product, caplog):
        """整批无价 → 计 0 条、不写库、不抛异常（MySQL job 上即 Blocker 的真身回归）"""
        rows = [
            {"trade_date": "20250106", "unit_price": None},
            {"trade_date": "20250107", "unit_price": ""},
            {"trade_date": "20250108", "unit_price": 0.0},
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
        assert warnings and warnings[-1].skipped == ["20250106", "20250107", "20250108"], \
            "整批被跳过时仍要留下全部交易日，否则事后无从定位缺哪几天"

    def test_mysql_branch_skips_a_whole_batch_of_zero_prices(self, caplog):
        """整批 0 价在 MySQL 分支同样是「跳过 + 计 0 条」，不是 StatementError（#701 × #613）。

        判据一放宽，落到「整批被滤空」这个形态的概率就变了：上游一次故障给出一段
        `close=0.0` 即整批为空，而空列表进 `db.execute(sql, [])` 会抛 SQLAlchemy 内部文案，
        把「跳过」记成 failed——正是 #613 那条只在 MySQL 上显形的分叉。CI 的 MySQL job
        上这条是真身回归。
        """
        db = _session_on_mysql_dialect()
        rows = [
            {"trade_date": "20250106", "unit_price": 0.0},
            {"trade_date": "20250107", "unit_price": -1.5},
        ]

        with caplog.at_level(logging.WARNING, logger=MARKET_DATA_LOGGER):
            count = _bulk_upsert_prices(db, "510300.SH", "CN_EXCHANGE", rows, "tushare")

        assert count == 0
        db.execute.assert_not_called()
        assert _skip_warnings(caplog), "整批 0 价也得留下 WARNING，不能静默计 0 条"

    def test_zero_accumulated_nav_drops_only_that_column(self, test_db: Session,
                                                        sample_otc_product: Product, caplog):
        """累计净值非正**只丢这一列**：单价合格的一天仍然入库，不该降级成「没有净值」。

        与整行跳过的分界：`unit_price` 决定这一天有没有价（快照取价的输入），
        `accumulated_nav` 只是 tushare 场外推导日涨跌的分子/基准与展示列。把后者判坏
        连带丢行，会让快照报 `MISSING_NAV`——那是把一个派生口径的缺值升级成账务缺口。
        """
        rows = [
            {"trade_date": "20260915", "unit_price": 1.2, "accumulated_nav": 0.0},
            {"trade_date": "20260916", "unit_price": 1.3, "accumulated_nav": -1.5},
            {"trade_date": "20260917", "unit_price": 1.4, "accumulated_nav": 1.5},
        ]
        with caplog.at_level(logging.WARNING, logger=MARKET_DATA_LOGGER):
            count = _bulk_upsert_prices(test_db, OTC, "CN_OTC", rows, "tushare")
        test_db.commit()

        assert count == 3, "单价合格的三行都得入库"
        written = test_db.query(PriceRecord).filter(
            PriceRecord.product_code == OTC, PriceRecord.market == "CN_OTC"
        ).order_by(PriceRecord.price_date).all()
        assert [r.accumulated_nav for r in written] == [None, None, Decimal("1.5000")], \
            "非正累计净值必须被丢弃而不是落库成 0/负值"
        assert [r.unit_price for r in written] == [Decimal("1.2000"), Decimal("1.3000"), Decimal("1.4000")]

        warnings = _skip_warnings(caplog)
        assert warnings and warnings[-1].skipped == [], "整行都没被跳过"
        assert warnings[-1].accumulated_nav_dropped == ["20260915", "20260916"], \
            "丢了哪几列也要留痕，否则该日涨跌恒空无从解释"

    def test_none_accumulated_nav_is_not_reported_as_dropped(self, test_db: Session,
                                                             sample_otc_product: Product, caplog):
        """上游本来就没给累计净值（NULL）不算「丢弃」，不进 WARNING。"""
        rows = [{"trade_date": "20260915", "unit_price": 1.2, "accumulated_nav": None}]
        with caplog.at_level(logging.WARNING, logger=MARKET_DATA_LOGGER):
            assert _bulk_upsert_prices(test_db, OTC, "CN_OTC", rows, "tushare") == 1

        assert not _skip_warnings(caplog), "缺值是正常形态，别把每场外一天都记成一条告警"

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


class TestZeroPriceNotCountedAsCovered:
    """#701 端到端：上游给 0 价的那一天，从「看起来很确定的有价日」回到「缺价日」。

    判据收紧的直接后果不是「少写一行」，而是**盘点口径不再假绿**：0 价行不入库 ⇒
    该日落 `missing_dates` ⇒ 快照按既有严格取价报 `MISSING_NAV`（缺行侧的后果由
    `tests/integration/test_snapshot_nav_strict.py` 钉）。这里钉的是中间那段——
    `nav_coverage` 只按 `price_date` 计数，坏值行一旦入库它就永远看不见。
    """

    @patch("app.services.market_data_service.get_fund_daily")
    def test_zero_day_is_missing_not_covered(self, mock_daily, test_db: Session,
                                             sample_etf_product: Product):
        mock_daily.return_value = [
            {"trade_date": "20250106", "close": 4.0, "pre_close": 3.9, "pct_chg": 2.56},
            {"trade_date": "20250107", "close": 0.0, "pre_close": 4.0, "pct_chg": 0.0},
            {"trade_date": "20250108", "close": -1.5, "pre_close": 4.0, "pct_chg": 0.0},
        ]

        result = sync_product_prices(test_db, "510300.SH", "CN_EXCHANGE",
                                     start_date=date(2025, 1, 6), end_date=date(2025, 1, 8))
        test_db.commit()

        assert result["success"] is True, "坏值是一段里被跳过的行，不是整段故障"
        assert result["synced_count"] == 1
        assert test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "510300.SH",
            PriceRecord.price_date.in_([date(2025, 1, 7), date(2025, 1, 8)]),
        ).count() == 0, "0 价与负价都不落库"

        coverage = get_nav_coverage(test_db, "510300.SH", "CN_EXCHANGE",
                                    date(2025, 1, 6), date(2025, 1, 8))
        assert coverage["synced_days"] == 1
        assert coverage["missing_dates"] == ["2025-01-07", "2025-01-08"], \
            "坏值日不得再计为已覆盖，否则快照把市值算成 0 而这里说一切正常"


# 实测的港互认故障窗口（#651，2026-09-14~09-22 共 7 个交易日，已与东财 PDATE/NAV 逐日核对）
_HK_WINDOW_NAV = {
    "20260914": 10.55, "20260915": 10.54, "20260916": 10.56, "20260917": 10.59,
    "20260918": 10.56, "20260921": 10.59, "20260922": 10.60,
}


class TestHkMutualNavFailureIsLoud:
    """#651 的服务层边界：结构变动响亮落 failed，合法空窗仍是有意义的成功。

    打桩打在 `app.services.akshare_client.get_fund_hk_mutual` 而**不是**消费模块：
    `market_data_service` 是在函数体内 `from app.services.akshare_client import …`
    （:348，为的是 akshare 缺失时不影响 tushare 主路径），每次调用都重新从源模块取名，
    所以在 `market_data_service` 上 patch 会 AttributeError。上面那批 tushare 用例
    patch 消费模块是正确的，因为 `get_fund_daily`/`get_fund_nav` 确为顶层导入。
    """

    @patch("app.services.akshare_client.get_fund_hk_mutual")
    def test_parse_error_marks_product_failed(
        self, mock_fetch, test_db: Session
    ):
        """取数层抛 AkshareAPIError → success=False + failed + 可定位的 sync_error。

        复用既有 except 分支（:364-366），服务层不新增任何返回语义——「响亮」全部
        来自取数层那一句判据。
        """
        from app.services.akshare_client import AkshareAPIError

        mock_fetch.side_effect = AkshareAPIError(
            "HK_MUTUAL 1001767344 上游返回 1768 行但无一行可解析，疑似接口结构变动"
        )

        result = sync_product_prices(test_db, "1001767344", "HK_MUTUAL",
                                     start_date=date(2026, 9, 14), end_date=date(2026, 9, 22))

        assert result["success"] is False
        assert result["synced_count"] == 0
        assert "无新数据" not in result["message"], "结构变动被读成「无新数据」即本次故障本体"
        product = test_db.query(Product).filter(Product.code == "1001767344").first()
        test_db.refresh(product)
        assert product.data_source_status == "failed"
        assert "无一行可解析" in product.sync_error

    @patch("app.services.akshare_client.get_fund_hk_mutual")
    def test_benign_empty_window_is_still_success(
        self, mock_fetch, test_db: Session
    ):
        """上游这段确实没数据（周末/停市/尚未发布）→ 仍是 success + 「无新数据」。

        这条是反向保险：`run_nav_sync` 的增量窗是 [本地最大+1, 昨天] 且只在 A 股交易日
        跑，若照 #651 原文把空集一律判 failed，每个周一都会给健康产品记一次失败并写
        system_error_log，噪音反过来淹没真故障。改判此处即测试先红。
        """
        mock_fetch.return_value = []

        result = sync_product_prices(test_db, "1001767344", "HK_MUTUAL",
                                     start_date=date(2026, 9, 26), end_date=date(2026, 9, 27))

        assert result["success"] is True
        assert result["message"] == "无新数据"
        product = test_db.query(Product).filter(Product.code == "1001767344").first()
        test_db.refresh(product)
        assert product.data_source_status == "success"

    @patch("app.services.akshare_client.get_fund_hk_mutual")
    def test_parsed_nav_lands_without_absurd_magnitude(
        self, mock_fetch, test_db: Session
    ):
        """取数契约里的 7 天净值逐日落库，且写入的量级说得通。

        打桩点在 `get_fund_hk_mutual` 之后，所以这条管的是**写入侧**，不是解析侧：
        键序又变了由 `test_reads_by_key_with_real_upstream_shape` 拦。背景仍值得留——
        本次没写脏数据纯属侥幸，旧代码的日期过滤器恰好在 `float(单位净值)` 之前把行
        剔除；两句换个次序，09-12 之后的每个港互认净值都会是 342652919208。
        """
        mock_fetch.return_value = [
            {"trade_date": td, "unit_price": nav, "accumulated_nav": None}
            for td, nav in _HK_WINDOW_NAV.items()
        ]

        result = sync_product_prices(test_db, "1001767344", "HK_MUTUAL",
                                     start_date=date(2026, 9, 14), end_date=date(2026, 9, 22))

        assert result["synced_count"] == len(_HK_WINDOW_NAV)
        written = test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "1001767344",
            PriceRecord.market == "HK_MUTUAL",
        ).all()
        assert {r.price_date.isoformat().replace("-", ""): float(r.unit_price)
                for r in written} == _HK_WINDOW_NAV
        assert all(float(r.unit_price) < 100 for r in written), "写入侧未拦住异常量级"
        assert all(r.unit_price is not None for r in written)

    @patch("app.services.akshare_client.get_fund_hk_mutual")
    def test_hk_mutual_routes_to_akshare_with_compact_bounds(self, mock_fetch, test_db: Session):
        """路由与入参契约：akshare + HK_MUTUAL → 恰好一次取数，日期为 YYYYMMDD 串。"""
        mock_fetch.return_value = []

        sync_product_prices(test_db, "1001767344", "HK_MUTUAL",
                            start_date=date(2026, 9, 14), end_date=date(2026, 9, 22))

        mock_fetch.assert_called_once_with("1001767344", "20260914", "20260922")


# ============================================================================
# #692：日涨跌（pct_change）的产生点收口
# ============================================================================

OTC = "000300.OF"


def _norm_row(td, *, unit=None, accum=None, pct=None):
    """构造一条已过 `_normalize_raw` 形态的场外行（推导就地改这个 dict）。"""
    return {"trade_date": td, "unit_price": unit, "accumulated_nav": accum,
            "pct_change": pct}


def _seed_price(db, code, market, day, *, unit=1.0, accum=None, source="tushare", pct=None):
    db.add(PriceRecord(product_code=code, market=market, price_date=day,
                       unit_price=unit, accumulated_nav=accum, source=source,
                       pct_change=pct))
    db.flush()


def _pct(row):
    """读回值统一成 Decimal 再比：SQLite 的 Numeric 落 float，MySQL 落 Decimal。"""
    return None if row is None else Decimal(str(row))


class TestNormalizeRawPctKeys:
    """`_normalize_raw` 的键集与 pct 归一（透传路径的唯一收口点）。

    两分支键集不同是上游能力差异，不是遗漏；`pct_change` 是百分数，在产生点量化到
    4 位 HALF_UP，读取路径不再量化。
    """

    def test_exchange_branch_key_set(self):
        rows = _normalize_raw(
            [{"trade_date": "20260915", "close": 4.6, "pre_close": 4.5, "pct_chg": 2.22222}],
            "CN_EXCHANGE",
        )
        assert set(rows[0]) == {"trade_date", "unit_price", "pre_close", "pct_change"}
        assert rows[0]["pct_change"] == Decimal("2.2222")

    def test_nav_branch_key_set(self):
        rows = _normalize_raw(
            [{"trade_date": "20260915", "unit_nav": 1.2, "accum_nav": 1.5,
              "pct_change": -0.47619}],
            "CN_OTC",
        )
        assert set(rows[0]) == {"trade_date", "unit_price", "accumulated_nav", "pct_change"}
        assert rows[0]["pct_change"] == Decimal("-0.4762")

    @pytest.mark.parametrize("market", ["CN_EXCHANGE", "CN_OTC", "HK_MUTUAL"])
    def test_flat_day_zero_is_not_folded_into_missing(self, market):
        """平盘日 0.0 折成 None 会让前端读成「无数据」而不是 0%（附带项，#692）。"""
        key = "pct_chg" if market == "CN_EXCHANGE" else "pct_change"
        raw = {"trade_date": "20260915", key: 0.0}
        raw.update({"close": 4.6} if market == "CN_EXCHANGE" else {"unit_nav": 1.2})

        assert _normalize_raw([raw], market)[0]["pct_change"] == Decimal("0.0000")

    def test_zero_accumulated_nav_is_not_folded_into_missing(self):
        """`_normalize_raw` 只做保真映射：`0.0` 与「上游没给」不得混成一件事。

        钉的是**这一层自身**的判空。#701 起取数层 `get_fund_nav` 也不再折 0（改判
        `is not None`），「`0.0` 不是合法净值」统一由写入判据 `_usable_nav` 处置：
        `unit_price=0.0` 整行跳过 + WARNING，`accumulated_nav=0.0` 只丢这一列。
        保真映射因此比从前更有意义——坏值能一路走到唯一那道判据并被点名，而不是
        在取数层就变成「没给值」从而查不到来源。
        """
        rows = _normalize_raw(
            [{"trade_date": "20260915", "unit_nav": 1.2, "accum_nav": 0.0}], "CN_OTC"
        )
        assert rows[0]["accumulated_nav"] == 0.0

    def test_exchange_accepts_pct_change_alias(self):
        """场内上游两种键名都收（tushare 给 pct_chg，akshare 给 pct_change）。"""
        assert _normalize_raw(
            [{"trade_date": "20260915", "close": 4.6, "pct_change": 1.25}], "CN_EXCHANGE"
        )[0]["pct_change"] == Decimal("1.2500")

    @pytest.mark.parametrize("bad", [None, "", float("nan"), float("inf"), "元", object()])
    def test_bad_pct_becomes_none_without_raising(self, bad):
        """NaN 若直穿 `quantize_nav` 会抛 InvalidOperation，把一次同步整体打成 failed。"""
        assert _optional_pct(bad) is None

    def test_missing_pct_stays_missing_not_zero(self):
        assert _normalize_raw([{"trade_date": "20260915", "unit_nav": 1.2}], "CN_OTC")[0][
            "pct_change"
        ] is None


class TestOtcPctChangeDerivation:
    """tushare 场外就地推导：`日涨跌 = (accum_d / accum_prev − 1) × 100`，百分数 4 位。

    为什么盯这么紧：这一列此前恒为 NULL（生产 244373 行），修好后它是详情页唯一
    「本地算出来」的行情字段——口径错了不会有任何异常，只会安静地显示错的百分比。
    """

    def test_consecutive_rows_derive_from_previous_accum(self, test_db: Session,
                                                        sample_otc_product: Product):
        rows = [
            _norm_row("20260914", unit=1.5000, accum=1.5000),
            _norm_row("20260915", unit=1.5300, accum=1.5300),
            _norm_row("20260916", unit=1.5147, accum=1.5147),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert [r["pct_change"] for r in rows] == [None, Decimal("2.0000"), Decimal("-1.0000")]

    def test_ex_dividend_day_is_flat_not_a_fake_crash(self, test_db: Session,
                                                      sample_otc_product: Product):
        """除息日 unit_nav 下调 0.03、accum_nav 连续 ⇒ 0.0000 而不是 −1.9608。

        这正是选 accum 基准的全部理由：按 unit_nav 推导会把现金分红算成假暴跌，而
        修正需要产品级 ex_date 表（本仓没有）。
        """
        rows = [
            _norm_row("20260915", unit=1.5300, accum=1.6000),
            _norm_row("20260916", unit=1.5000, accum=1.6000),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[1]["pct_change"] == Decimal("0.0000")
        # 反面口径的期望值一并写死，防止有人「顺手改成 unit 基准」后本用例仍然通过
        unit_basis = Decimal("1.5000") / Decimal("1.5300") * 100 - 100
        assert round(unit_basis, 4) == Decimal("-1.9608")

    def test_first_row_of_a_batch_uses_the_db_baseline(self, test_db: Session,
                                                       sample_otc_product: Product):
        """增量窗首行接得上存量历史，跨批不留缺口。"""
        _seed_price(test_db, OTC, "CN_OTC", date(2026, 9, 14), unit=1.5, accum=1.5)
        rows = [_norm_row("20260915", unit=1.5300, accum=1.5300)]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[0]["pct_change"] == Decimal("2.0000")

    def test_db_baseline_skips_rows_without_accum(self, test_db: Session,
                                                  sample_otc_product: Product):
        """基准是「最近一条 accum 非空行」，不是「上一交易日那条行」。"""
        _seed_price(test_db, OTC, "CN_OTC", date(2026, 9, 11), unit=1.5, accum=1.5)
        _seed_price(test_db, OTC, "CN_OTC", date(2026, 9, 14), unit=1.51, accum=None)
        rows = [_norm_row("20260915", unit=1.5300, accum=1.5300)]

        assert _db_prev_accum_nav(test_db, OTC, "CN_OTC", date(2026, 9, 15)) is not None
        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[0]["pct_change"] == Decimal("2.0000")

    def test_flat_day_is_zero_not_null(self, test_db: Session, sample_otc_product: Product):
        """平盘是真实的 0%，与「无基准」必须可区分。"""
        rows = [
            _norm_row("20260915", unit=1.5000, accum=1.5000),
            _norm_row("20260916", unit=1.5000, accum=1.5000),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[1]["pct_change"] == Decimal("0.0000")

    def test_non_positive_accum_neither_derives_nor_serves_as_baseline(self, test_db: Session,
                                                                      sample_otc_product: Product):
        """非正 accum 既不算涨跌、也不作后续基准（#701 起与写侧 `_usable_nav` 同判据）。

        此前本用例钉的是相反口径（「守卫只看基准，当日值为负照样出值」）。改判的理由
        不是算术而是自洽：同一行 `accumulated_nav` 在 `_bulk_upsert_prices` 已被判坏、
        不会落库，却用它推导出一个会被落库的百分比，等于让派生列比它的输入更宽松。
        `0.0` 作分子更会直接推出 −100.0000 的假暴跌。
        """
        rows = [
            _norm_row("20260915", unit=1.0, accum=0.0),
            _norm_row("20260916", unit=1.0, accum=1.5),
            _norm_row("20260917", unit=1.0, accum=-1.5),
            _norm_row("20260918", unit=1.0, accum=1.6),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[0]["pct_change"] is None            # 当日值非正：无推导、不作基准
        assert rows[1]["pct_change"] is None            # 无基准（首条合格行）
        assert rows[2]["pct_change"] is None            # 当日值非正：不再出 −200.0000
        assert rows[3]["pct_change"] == Decimal("6.6667"), \
            "坏值行要整条跳过、不能把基准污染成 0/负——否则后一天算不出真实涨跌"

    def test_non_positive_db_baseline_stays_none(self, test_db: Session,
                                                 sample_otc_product: Product):
        """DB 基准非正时无从算比值，保持 None，绝不伪造成 0.00%。

        写侧判正后新库不再有非正基准行，但**存量**行不经写判据（`_seed_price` 直插，
        生产上迁移 0017 之前也留过形态），读侧这条守卫不得因此删掉。
        """
        _seed_price(test_db, OTC, "CN_OTC", date(2026, 9, 14), unit=1.0, accum=0.0)
        rows = [_norm_row("20260915", unit=1.0, accum=1.5)]

        assert _db_prev_accum_nav(test_db, OTC, "CN_OTC", date(2026, 9, 15)) == Decimal("0.0000")
        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[0]["pct_change"] is None

    def test_negative_pct_is_legitimate_when_both_navs_are_positive(self, test_db: Session,
                                                                    sample_otc_product: Product):
        """净值为负是坏值（上一条用例拒收），日涨跌为负是真实下跌——两件事不得混为一谈。

        这正是判据分表在推导侧的镜像：`_usable_nav` 管 accum，`_optional_pct` 走
        `_usable_price` 管涨跌，后者判正会把 −6.6667% 这样的正常跌幅一起判坏。
        """
        rows = [
            _norm_row("20260915", unit=1.0, accum=1.5),
            _norm_row("20260916", unit=1.0, accum=1.4),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[1]["pct_change"] == Decimal("-6.6667")

    def test_rounding_is_half_up_on_the_fifth_decimal(self, test_db: Session,
                                                      sample_otc_product: Product):
        """8.0000 → 8.0001 得 0.00125：HALF_UP → 0.0013（Decimal 缺省的 HALF_EVEN 会给
        0.0012）。这条钉的是舍入模式，不是精度位数。
        """
        rows = [
            _norm_row("20260915", unit=8.0, accum="8.0000"),
            _norm_row("20260916", unit=8.0, accum="8.0001"),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[1]["pct_change"] == Decimal("0.0013")

    def test_descending_upstream_order_is_reordered(self, test_db: Session,
                                                    sample_otc_product: Product):
        """tushare 实测多为降序返回：链式推导前必须排序，否则基准取成后一天。"""
        rows = [
            _norm_row("20260916", unit=1.5147, accum=1.5147),
            _norm_row("20260915", unit=1.5300, accum=1.5300),
            _norm_row("20260914", unit=1.5000, accum=1.5000),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        by_date = {r["trade_date"]: r["pct_change"] for r in rows}
        assert by_date == {"20260914": None, "20260915": Decimal("2.0000"),
                           "20260916": Decimal("-1.0000")}

    @pytest.mark.parametrize("bad", [None, "", float("nan")])
    def test_unusable_accum_is_skipped_and_breaks_nothing(self, test_db: Session,
                                                          sample_otc_product: Product, bad):
        """accum 坏值行自身算不出涨跌，也不作后续基准（与迁移 LAG 非空子集同口径）。"""
        rows = [
            _norm_row("20260914", unit=1.5, accum=1.5),
            _norm_row("20260915", unit=1.51, accum=bad),
            _norm_row("20260916", unit=1.53, accum=1.53),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[1]["pct_change"] is None
        assert rows[2]["pct_change"] == Decimal("2.0000")  # 基准跨过坏值行取 1.5

    def test_upstream_pct_is_overwritten_by_derivation(self, test_db: Session,
                                                       sample_otc_product: Product):
        """tushare `fund_nav` 不给涨跌幅；万一将来给了，也以本地 accum 口径为准。"""
        rows = [
            _norm_row("20260915", unit=1.5, accum=1.5, pct=Decimal("9.9999")),
            _norm_row("20260916", unit=1.53, accum=1.53, pct=Decimal("9.9999")),
        ]

        _derive_otc_pct_change(test_db, OTC, "CN_OTC", rows)

        assert rows[0]["pct_change"] == Decimal("9.9999")   # 无基准：不覆盖成 None
        assert rows[1]["pct_change"] == Decimal("2.0000")

    def test_empty_batch_does_not_hit_the_db(self, test_db: Session):
        _derive_otc_pct_change(test_db, OTC, "CN_OTC", [])


class TestUpsertPctCoverage:
    """端到端：三条源各自的 pct 都真的落库（口径正确但没写进去，症状一模一样）。"""

    def _read(self, db, code, market):
        return {
            r.price_date: r.pct_change
            for r in db.query(PriceRecord).filter(
                PriceRecord.product_code == code, PriceRecord.market == market
            ).all()
        }

    @patch("app.services.market_data_service.get_fund_nav")
    def test_tushare_otc_persists_derived_pct(self, mock_nav, test_db: Session,
                                              sample_otc_product: Product):
        mock_nav.return_value = [
            {"trade_date": "20260915", "unit_nav": 1.5000, "accum_nav": 1.5000},
            {"trade_date": "20260916", "unit_nav": 1.5300, "accum_nav": 1.5300},
        ]

        result = sync_product_prices(test_db, OTC, "CN_OTC",
                                     start_date=date(2026, 9, 15), end_date=date(2026, 9, 16))

        assert result["success"] is True and result["synced_count"] == 2
        got = self._read(test_db, OTC, "CN_OTC")
        assert got[date(2026, 9, 15)] is None
        assert _pct(got[date(2026, 9, 16)]) == Decimal("2.0000")

    @patch("app.services.akshare_client.get_fund_nav_otc")
    def test_akshare_otc_persists_upstream_pct_verbatim(self, mock_nav, test_db: Session,
                                                        sample_otc_product: Product):
        """akshare 场外走上游透传，不得被本地推导覆盖成 accum 口径。"""
        sample_otc_product.data_source = "akshare"
        test_db.flush()
        mock_nav.return_value = [
            {"trade_date": "20260915", "unit_nav": 1.5000, "accum_nav": None,
             "pct_change": 0.1215},
            {"trade_date": "20260916", "unit_nav": 1.4700, "accum_nav": None,
             "pct_change": -2.0},
        ]

        sync_product_prices(test_db, OTC, "CN_OTC",
                            start_date=date(2026, 9, 15), end_date=date(2026, 9, 16))

        got = self._read(test_db, OTC, "CN_OTC")
        assert _pct(got[date(2026, 9, 15)]) == Decimal("0.1215")
        assert _pct(got[date(2026, 9, 16)]) == Decimal("-2.0000")

    @patch("app.services.akshare_client.get_fund_hk_mutual")
    def test_hk_mutual_persists_upstream_pct(self, mock_fetch, test_db: Session):
        mock_fetch.return_value = [
            {"trade_date": "20260915", "unit_price": 10.5700, "accumulated_nav": None,
             "pct_change": -0.4762},
        ]

        sync_product_prices(test_db, "1001767344", "HK_MUTUAL",
                            start_date=date(2026, 9, 15), end_date=date(2026, 9, 15))

        got = self._read(test_db, "1001767344", "HK_MUTUAL")
        assert _pct(got[date(2026, 9, 15)]) == Decimal("-0.4762")

    @patch("app.services.market_data_service.get_fund_daily")
    def test_exchange_flat_day_persists_zero(self, mock_daily, test_db: Session,
                                             sample_etf_product: Product):
        """附带项端到端：场内平盘日落 0.0000，不是 NULL。"""
        mock_daily.return_value = [
            {"trade_date": "20260915", "close": 4.6, "pre_close": 4.6, "pct_chg": 0.0},
        ]

        sync_product_prices(test_db, "510300.SH", "CN_EXCHANGE",
                            start_date=date(2026, 9, 15), end_date=date(2026, 9, 15))

        got = self._read(test_db, "510300.SH", "CN_EXCHANGE")
        assert _pct(got[date(2026, 9, 15)]) == Decimal("0.0000")

    @patch("app.services.market_data_service.get_fund_nav")
    def test_resync_overwrites_pct_unconditionally(self, mock_nav, test_db: Session,
                                                   sample_otc_product: Product):
        """`ON DUPLICATE KEY UPDATE` 是无条件覆盖（`VALUES()` 而非 COALESCE）。

        这条把「写侧修复与回填迁移必须同批上线」的约束钉成可执行事实：只上迁移不修
        写侧，下一次同步就把回填值抹回 NULL。上游列漂移导致 pct 变 None 时同样会被
        抹掉——漂移有 WARNING 留痕（akshare_client），修正路径见迁移 0020 头注释。
        """
        mock_nav.return_value = [
            {"trade_date": "20260915", "unit_nav": 1.5, "accum_nav": 1.5},
            {"trade_date": "20260916", "unit_nav": 1.53, "accum_nav": 1.53},
        ]
        sync_product_prices(test_db, OTC, "CN_OTC",
                            start_date=date(2026, 9, 15), end_date=date(2026, 9, 16))
        assert _pct(self._read(test_db, OTC, "CN_OTC")[date(2026, 9, 16)]) == Decimal("2.0000")

        # 上游这批不再给 accum（列漂移形态）：已有值被覆盖成 NULL，而不是被保留
        mock_nav.return_value = [
            {"trade_date": "20260916", "unit_nav": 1.53, "accum_nav": None},
        ]
        sync_product_prices(test_db, OTC, "CN_OTC",
                            start_date=date(2026, 9, 16), end_date=date(2026, 9, 16))

        assert self._read(test_db, OTC, "CN_OTC")[date(2026, 9, 16)] is None
