"""分红检测的 `div_cash` 口径（issue #771）。

`_detect_dividends` 直造 ORM、不经服务层，是 #764 那道「闸门装在正门」的后门：
`div_cash` 缺失或为 0/负的公告照样落一条 pending `reinvest_dividend`，确认时撞
`_validate_dividend_div_cash` 失败，于是**永久停在 pending**、每批快照刷一条
`auto_confirm_failed`（现场：`div_cash = -0.0039`）。

本文件钉住检测口的处置：三态（正常建；0/缺失/负**不建**）、不建时按
`(产品, 除息日)` 去重留痕、留痕只覆盖「本来会建事件」的有效公告，以及幂等
（重复抓取不重复建、金额修复后重跑能补上）。跳过项经任务终态可见的用例在
`test_task_log_orchestration.py::TestNavSyncDerivation`。

另钉 NaN/Inf（评审补充）：pandas 的缺失值是 NaN 而非 None，`is not None` 拦不住，
而旧判据的有序比较遇 NaN 直接抛异常。取数层 `tushare_client.get_fund_div` 已把它
归一为 None（形态用例在 `test_tushare_rate_limit.py::TestFundDivRowMapping`），检测口
复用服务层判据自带 `is_finite` 防御——本文件的用例**打桩绕过取数层**，钉的是第二道。
"""
from datetime import date
import math
from unittest.mock import patch

import pytest

from app.models.product import Product
from app.models.share_change_event import ShareChangeEvent
from app.services.task_runner import _detect_dividends
from tests.factories import (
    create_platform, create_portfolio, create_position_snapshot, create_product,
)

MARKET = "CN_OTC"
HOLD_DATE = date(2026, 10, 16)      # 持仓所在快照日（该组合的 max snapshot_date）
ENT = date(2026, 10, 19)            # 权益登记日
EX = date(2026, 10, 20)             # 除息日（晚于登记日、晚于快照日 → 有效公告）
REASON = "每份分红金额缺失或非正，未建事件；请核对公告后手工录入"


def _div(**overrides):
    """一条 tushare fund_div 形态的「实施中」公告（键与 `get_fund_div` 返回一致）。"""
    row = {
        "ex_date": EX.strftime("%Y%m%d"),
        "record_date": ENT.strftime("%Y%m%d"),
        "div_cash": 0.0039,
        "div_proc": "实施",
    }
    row.update(overrides)
    return row


def _env(db, suffix, *, positions=(("PF", "PL"),), hold_date=HOLD_DATE, shares=1000.0):
    """建一只 CN_OTC 基金及若干 `(组合, 平台)` 持仓行，返回基金代码。

    持仓必须落在该组合**最新**快照日上：检测器按 `max(snapshot_date)` 选行，并要求
    `ex_date > 该行快照日`，否则本来就不会建事件（也就不该留痕）。
    """
    fund = f"F771_{suffix}"
    create_product(db, code=fund, market=MARKET, product_type="OEF")
    for port, plat in positions:
        create_portfolio(db, code=f"{port}_{suffix}", status="active")
        create_platform(db, code=f"{plat}_{suffix}")
        create_position_snapshot(
            db, f"{port}_{suffix}", fund, MARKET, snapshot_date=hold_date,
            platform_code=f"{plat}_{suffix}", shares=shares, market_value=shares,
        )
    db.commit()
    return fund


def _run(db, fund, dividends):
    """取回产品对象后跑检测（`get_fund_div` 打桩，不触网），返回 `(detected, skipped)`。"""
    product = db.query(Product).filter_by(code=fund, market=MARKET).one()
    with patch("app.services.tushare_client.get_fund_div", return_value=dividends):
        return _detect_dividends(db, [product])


def _rows(db, fund):
    return db.query(ShareChangeEvent).filter_by(product_code=fund).all()


def _skip(fund, div_cash):
    return {
        "product_code": fund, "ex_date": EX.isoformat(),
        "div_cash": div_cash, "reason": REASON,
    }


class TestDetectorDivCashStates:
    def test_positive_div_cash_creates_pending_event(self, test_db):
        fund = _env(test_db, "OK")
        detected, skipped = _run(test_db, fund, [_div()])

        assert (detected, skipped) == (1, [])
        event = _rows(test_db, fund)[0]
        assert (event.status, event.event_type, event.event_source) == (
            "pending", "dividend_pending", "tushare",
        )
        assert event.shares_change is None and event.cash_change is None
        assert event.reinvest_nav is None and event.share_confirm_date is None
        assert float(event.div_cash) == 0.0039
        assert (event.entitlement_date, event.ex_date) == (ENT, EX)

    @pytest.mark.parametrize(
        "suffix,div_cash",
        [("ZERO", 0), ("NULL", None), ("NEG", -0.0039)],
        ids=["zero", "missing", "negative"],
    )
    def test_missing_or_non_positive_creates_no_row(self, test_db, suffix, div_cash):
        """「不可用」的三态：不建事件（建了只会永久 pending），只留一条可操作的痕。"""
        fund = _env(test_db, suffix)
        assert _run(test_db, fund, [_div(div_cash=div_cash)]) == (0, [_skip(fund, div_cash)])
        assert _rows(test_db, fund) == []

    def test_positive_still_creates_when_other_rows_are_bad(self, test_db):
        """逐条判定：同批有一条坏公告，不得放弃好公告。"""
        fund = _env(test_db, "MIX")
        detected, skipped = _run(test_db, fund, [
            _div(div_cash=0),
            _div(ex_date="20261022", record_date="20261021", div_cash=0.0051),
        ])

        assert detected == 1
        assert [item["div_cash"] for item in skipped] == [0]
        assert [float(e.div_cash) for e in _rows(test_db, fund)] == [0.0051]

    @pytest.mark.parametrize(
        "suffix,div_cash",
        [("NAN", float("nan")), ("INF", float("inf")), ("NINF", float("-inf"))],
        ids=["nan", "positive_infinity", "negative_infinity"],
    )
    def test_non_finite_is_skipped_instead_of_raising(self, test_db, suffix, div_cash):
        """非有限值走「跳过留痕」，不得抛异常（#771 评审）。

        `Decimal(str(nan)) <= 0` 抛 `InvalidOperation` 而不是返回 False：旧写法下异常被
        产品级 `except Exception` 兜住并 `db.rollback()`，该产品本轮**已建的有效事件一起
        撤销**、后续公告也不再处理，只要这行公告还在就每轮 nav_sync 重演。
        """
        fund = _env(test_db, suffix)
        detected, skipped = _run(test_db, fund, [_div(div_cash=div_cash)])

        assert detected == 0
        assert len(skipped) == 1
        assert skipped[0]["product_code"] == fund and skipped[0]["ex_date"] == EX.isoformat()
        assert _rows(test_db, fund) == []

    def test_non_finite_does_not_discard_the_rest_of_the_batch(self, test_db):
        """一条非有限公告只作废自己：同产品同批的好公告照常建事件。"""
        fund = _env(test_db, "MIXNAN")
        detected, skipped = _run(test_db, fund, [
            _div(div_cash=float("nan")),
            _div(ex_date="20261022", record_date="20261021", div_cash=0.0051),
        ])

        assert detected == 1
        assert len(skipped) == 1 and math.isnan(skipped[0]["div_cash"])
        assert [float(e.div_cash) for e in _rows(test_db, fund)] == [0.0051]


class TestSkipScope:
    def test_deduped_per_product_and_ex_date(self, test_db):
        """同一公告跨多组合/平台只留一条痕（按 `(产品, 除息日)` 去重）。"""
        fund = _env(test_db, "DUP", positions=(("PF", "PL1"), ("PF", "PL2"), ("PG", "PL1")))
        detected, skipped = _run(test_db, fund, [_div(div_cash=None)])

        assert detected == 0
        assert skipped == [_skip(fund, None)]

    def test_no_skip_entry_when_no_event_would_be_created(self, test_db):
        """留痕只覆盖「本来会建事件」的公告：除息日已过 / 无持仓 → 零噪音。

        持仓日在除息日之后时这笔分红早已生效，检测器本来就不建事件；报「请手工录入」
        是把无关公告写成待办。
        """
        late = _env(test_db, "LATE", hold_date=EX)
        assert _run(test_db, late, [_div(div_cash=0)]) == (0, [])

        empty = _env(test_db, "EMPTY", positions=())
        assert _run(test_db, empty, [_div(div_cash=0)]) == (0, [])

    def test_invalid_announcement_shape_not_reported_as_backfill(self, test_db):
        """公告本身无效（非「实施」/除息日不晚于登记日）时既不建也不留痕（既有过滤不变）。"""
        fund = _env(test_db, "INVALID")
        assert _run(test_db, fund, [_div(div_proc="取消", div_cash=0)]) == (0, [])
        assert _run(
            test_db, fund, [_div(ex_date="20261018", record_date="20261019", div_cash=0)]
        ) == (0, [])


class TestDetectorIdempotency:
    @pytest.mark.parametrize("event_type", ["cash_dividend", "reinvest_dividend", "dividend_pending"])
    def test_repeat_fetch_preserves_existing_method(self, test_db, event_type):
        fund = _env(test_db, "KEEP")
        assert _run(test_db, fund, [_div()]) == (1, [])
        event = _rows(test_db, fund)[0]
        event.event_type = event_type
        test_db.commit()
        assert _run(test_db, fund, [_div()]) == (0, [])
        assert _rows(test_db, fund)[0].event_type == event_type

    def test_same_day_announcement_still_skipped(self, test_db):
        fund = _env(test_db, "SAME")
        assert _run(test_db, fund, [_div(record_date=EX.strftime("%Y%m%d"))]) == (0, [])
        assert _rows(test_db, fund) == []

    def test_repeat_fetch_does_not_duplicate_event(self, test_db):
        """重复抓取幂等：第二次同公告不再建（`existing` 判据未被本次改动绕过）。"""
        fund = _env(test_db, "IDEM")
        assert _run(test_db, fund, [_div()])[0] == 1
        assert _run(test_db, fund, [_div()]) == (0, [])
        assert len(_rows(test_db, fund)) == 1

    def test_fixed_div_cash_creates_event_after_skip(self, test_db):
        """跳过不留残留行，故金额修复后重跑能正常补建（「不建事件」是可恢复的）。"""
        fund = _env(test_db, "REPAIR")
        assert _run(test_db, fund, [_div(div_cash=0)]) == (0, [_skip(fund, 0)])
        assert _run(test_db, fund, [_div(div_cash=0.0039)])[0] == 1
        assert len(_rows(test_db, fund)) == 1
