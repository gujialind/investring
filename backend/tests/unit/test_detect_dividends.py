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
from datetime import date, timedelta
import math
from unittest.mock import patch

import pytest

from app.models.product import Product
from app.models.dividend_method_config import DividendMethodConfig
from app.models.share_change_event import ShareChangeEvent
from app.services.task_runner import _detect_dividends
from app.services.trading_utils import try_get_next_trading_day, is_trading_day
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


def _akdiv(**overrides):
    """一条 akshare 分红送配详情归一化公告（#881，键与 `get_fund_div_otc` 返回一致）。

    无 div_proc（该接口只列已实施分红）；pay_date 保留，#887 PR2 起 explicit 配置命中时
    由检测器消费（无配置时仍不消费，A3）。
    """
    row = {
        "ex_date": EX.strftime("%Y%m%d"),
        "record_date": ENT.strftime("%Y%m%d"),
        "div_cash": 0.0039,
        "pay_date": "20261021",
    }
    row.update(overrides)
    return row


def _env(db, suffix, *, positions=(("PF", "PL"),), hold_date=HOLD_DATE, shares=1000.0,
         data_source="tushare", market=MARKET):
    """建一只场外基金及若干 `(组合, 平台)` 持仓行，返回基金代码。

    持仓必须落在该组合**最新**快照日上：检测器按 `max(snapshot_date)` 选行，并要求
    `ex_date > 该行快照日`，否则本来就不会建事件（也就不该留痕）。
    `data_source` 供 #881 源路由用例；`market` 供 HK/场内排除用例。
    """
    fund = f"F771_{suffix}"
    product = create_product(db, code=fund, market=market, product_type="OEF")
    product.data_source = data_source
    for port, plat in positions:
        create_portfolio(db, code=f"{port}_{suffix}", status="active")
        create_platform(db, code=f"{plat}_{suffix}")
        create_position_snapshot(
            db, f"{port}_{suffix}", fund, market, snapshot_date=hold_date,
            platform_code=f"{plat}_{suffix}", shares=shares, market_value=shares,
        )
    db.commit()
    return fund


def _run(db, fund, dividends):
    """取回产品对象后跑检测（`get_fund_div` 打桩，不触网），返回 `(detected, skipped)`。

    本文件用例均无 explicit 配置行（#771/#881 口径），degraded 恒空，故只取前二元组；
    PR2 配置接线/degraded 专项见 TestConfigDrivenDetection（直接取三元组）。
    """
    product = db.query(Product).filter_by(code=fund, market=MARKET).one()
    with patch("app.services.tushare_client.get_fund_div", return_value=dividends):
        detected, skipped, _degraded = _detect_dividends(db, [product])
    return detected, skipped


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
        """公告本身无效（非「实施」/除息日早于登记日）时既不建也不留痕。

        #766：同日（ex_date == record_date）现为有效形态，只有逆序（ex < record）
        仍被过滤。
        """
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

    def test_same_day_announcement_creates_pending_event(self, test_db):
        """#766：同日公告（ex_date == record_date）建 dividend_pending，不再跳过。"""
        fund = _env(test_db, "SAME")
        assert _run(test_db, fund, [_div(record_date=EX.strftime("%Y%m%d"))]) == (1, [])
        event = _rows(test_db, fund)[0]
        assert event.event_type == "dividend_pending"
        assert (event.entitlement_date, event.ex_date) == (EX, EX)

    def test_reverse_order_announcement_still_skipped(self, test_db):
        """#766：逆序公告（ex_date < record_date）属脏数据，仍跳过、不留痕。"""
        fund = _env(test_db, "REV")
        assert _run(
            test_db, fund, [_div(ex_date="20261018", record_date="20261019", div_cash=0.0039)]
        ) == (0, [])
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


class TestAkshareSource:
    """#881：akshare 场外产品进入检测（源无关核心 + 双取数器）。

    A11 回归防线（分红方式配置需求，交叉引用 #881 验收断言 1）：数据源切到
    akshare 后分红公告仍被检测并产生事件——旧过滤条件（`data_source=="tushare"`）
    造成的静默失效由本类钉住关闭。共享核心判据（#771/#766）对两源同口径。
    """

    def _run_ak(self, db, fund, dividends):
        product = db.query(Product).filter_by(code=fund, market=MARKET).one()
        with patch("app.services.akshare_client.get_fund_div_otc", return_value=dividends):
            detected, skipped, _degraded = _detect_dividends(db, [product])
        return detected, skipped

    def test_akshare_product_creates_pending_event(self, test_db):
        fund = _env(test_db, "AK", data_source="akshare")
        detected, skipped = self._run_ak(test_db, fund, [_akdiv()])
        assert (detected, skipped) == (1, [])
        event = _rows(test_db, fund)[0]
        assert (event.status, event.event_type, event.event_source) == (
            "pending", "dividend_pending", "akshare",
        )
        assert "akshare" in event.notes
        # 无 explicit 配置：pay_date 不消费，建 pending 不带适用日期（A3；有 explicit 时消费见 TestConfigDrivenDetection）
        assert event.cash_pay_date is None and event.share_confirm_date is None
        assert float(event.div_cash) == 0.0039
        assert (event.entitlement_date, event.ex_date) == (ENT, EX)

    def test_source_routing_tushare_untouched(self, test_db):
        fund = _env(test_db, "TS")
        with patch("app.services.akshare_client.get_fund_div_otc") as ak_fetch:
            assert _run(test_db, fund, [_div()]) == (1, [])
            ak_fetch.assert_not_called()
        assert _rows(test_db, fund)[0].event_source == "tushare"

    def test_mixed_sources_one_batch(self, test_db):
        ak_fund = _env(test_db, "MIXAK", data_source="akshare")
        ts_fund = _env(test_db, "MIXTS")
        ak_prod = test_db.query(Product).filter_by(code=ak_fund, market=MARKET).one()
        ts_prod = test_db.query(Product).filter_by(code=ts_fund, market=MARKET).one()
        with patch("app.services.akshare_client.get_fund_div_otc", return_value=[_akdiv()]), \
             patch("app.services.tushare_client.get_fund_div", return_value=[_div()]):
            detected, skipped, _ = _detect_dividends(test_db, [ak_prod, ts_prod])
        assert (detected, skipped) == (2, [])
        assert _rows(test_db, ak_fund)[0].event_source == "akshare"
        assert _rows(test_db, ts_fund)[0].event_source == "tushare"

    def test_akshare_invalid_row_skipped_with_trace(self, test_db):
        fund = _env(test_db, "AKBAD", data_source="akshare")
        assert self._run_ak(test_db, fund, [_akdiv(div_cash=None)]) == (0, [_skip(fund, None)])
        assert _rows(test_db, fund) == []

    def test_akshare_missing_pay_date_skip_reason_distinguished(self, test_db):
        """S3：akshare 缺发放日 → 取数层归一 div_cash=None → skipped 原因「缺发放日」，
        与「金额缺失/非正」分立（仅文案区分，不产生 pending+degraded，取数归一逻辑不变）。"""
        fund = _env(test_db, "AKNOPAY", data_source="akshare")
        detected, skipped = self._run_ak(test_db, fund, [_akdiv(pay_date="", div_cash=None)])
        assert detected == 0
        assert "缺发放日" in skipped[0]["reason"]
        assert _rows(test_db, fund) == []

    def test_akshare_amount_invalid_skip_reason_generic(self, test_db):
        """S3：akshare 有发放日但金额无效 → skipped 原因仍「金额缺失/非正」（与缺发放日分立）。"""
        fund = _env(test_db, "AKAMT", data_source="akshare")
        detected, skipped = self._run_ak(test_db, fund, [_akdiv(div_cash=None)])  # pay_date 默认存在
        assert detected == 0
        assert "金额缺失或非正" in skipped[0]["reason"]
        assert "缺发放日" not in skipped[0]["reason"]

    def test_akshare_missing_record_date_silent_no_trace(self, test_db):
        """L2 #885 Nit 反例：缺权益登记日 → 检测器日期门静默跳过，无 dividends_skipped。

        日期门（`if not ex_date_str or not record_date_str`）先于留痕分支执行；
        取数层归一 div_cash=None 不改变这一去向。
        """
        fund = _env(test_db, "AKNOREC", data_source="akshare")
        assert self._run_ak(test_db, fund, [_akdiv(record_date="")]) == (0, [])
        assert _rows(test_db, fund) == []

    def test_akshare_same_day_announcement(self, test_db):
        fund = _env(test_db, "AKSAME", data_source="akshare")
        assert self._run_ak(
            test_db, fund, [_akdiv(record_date=EX.strftime("%Y%m%d"))]
        ) == (1, [])
        event = _rows(test_db, fund)[0]
        assert (event.entitlement_date, event.ex_date) == (EX, EX)

    def test_akshare_dedup_idempotent(self, test_db):
        fund = _env(test_db, "AKIDEM", data_source="akshare")
        assert self._run_ak(test_db, fund, [_akdiv()])[0] == 1
        assert self._run_ak(test_db, fund, [_akdiv()]) == (0, [])
        assert len(_rows(test_db, fund)) == 1

    def test_akshare_api_error_rolls_back_only_that_product(self, test_db):
        from app.services.akshare_client import AkshareAPIError
        bad = _env(test_db, "AKERR", data_source="akshare")
        good = _env(test_db, "AKERRTS")
        bad_prod = test_db.query(Product).filter_by(code=bad, market=MARKET).one()
        good_prod = test_db.query(Product).filter_by(code=good, market=MARKET).one()
        with patch("app.services.akshare_client.get_fund_div_otc",
                   side_effect=AkshareAPIError("结构漂移")), \
             patch("app.services.tushare_client.get_fund_div", return_value=[_div()]):
            detected, _, _ = _detect_dividends(test_db, [bad_prod, good_prod])
        assert detected == 1
        assert _rows(test_db, bad) == []
        assert len(_rows(test_db, good)) == 1

    @pytest.mark.parametrize("market", ["HK_MUTUAL", "CN_EXCHANGE"])
    def test_hk_and_exchange_not_detected(self, test_db, market):
        fund = _env(test_db, f"AK{market[:2]}", data_source="akshare", market=market)
        product = test_db.query(Product).filter_by(code=fund, market=market).one()
        with patch("app.services.akshare_client.get_fund_div_otc") as ak_fetch, \
             patch("app.services.tushare_client.get_fund_div") as ts_fetch:
            assert _detect_dividends(test_db, [product]) == (0, [], [])
        ak_fetch.assert_not_called()
        ts_fetch.assert_not_called()


class TestTsCodeShape:
    """hotfix/otc-dividend-ts-code-suffix：ts_code 拼码**形态**钉住。

    机器码按 #705 约定带 .OF（business-constraints rule-product、seed_base 同形）。
    本类以带后缀的机器码构造产品并断言取数入参——若剥后缀逻辑被去掉，入参变成
    "….OF.OF"，用例即红（不靠 mock 里的裸码悄悄通过）；裸码用例钉住向后兼容。
    """

    def test_suffixed_machine_code_normalized_once(self, test_db):
        fund = _env(test_db, "SUF.OF")  # 机器码 "F771_SUF.OF"，文档化形态
        product = test_db.query(Product).filter_by(code=fund, market=MARKET).one()
        with patch("app.services.tushare_client.get_fund_div", return_value=[_div()]) as fetch:
            detected, skipped, _ = _detect_dividends(test_db, [product])
        fetch.assert_called_once_with(fund)  # "F771_SUF.OF"，而非 "F771_SUF.OF.OF"
        assert (detected, skipped) == (1, [])
        event = _rows(test_db, fund)[0]
        assert event.product_code == fund  # 机器码原样落库，不改写不归一
        assert (event.status, event.event_type, event.event_source) == (
            "pending", "dividend_pending", "tushare",
        )
        assert fund in event.notes  # notes 用归一后的 ts_code

    def test_bare_code_still_appends_suffix(self, test_db):
        fund = _env(test_db, "BARE")
        product = test_db.query(Product).filter_by(code=fund, market=MARKET).one()
        with patch("app.services.tushare_client.get_fund_div", return_value=[_div()]) as fetch:
            detected, _, _ = _detect_dividends(test_db, [product])
        fetch.assert_called_once_with(f"{fund}.OF")
        assert detected == 1


class TestConfigDrivenDetection:
    """#887 PR2：explicit 配置接线 + A5/A5b 日期规则 + degraded 留痕 + A3 反向防线。

    检测器只决定建何种 pending 事件；确认/自动确认/闸门/重放仍走既有账务路径（不读配置）。
    degraded = 建了 pending 但未按 explicit 配置落地（与 skipped「不建事件」语义分立）。
    """

    def _cfg(self, db, fund, suffix, method, *, eff=date(2026, 10, 1), source="explicit"):
        row = DividendMethodConfig(
            portfolio_code=f"PF_{suffix}", product_code=fund, market=MARKET,
            platform_code=f"PL_{suffix}", dividend_method=method,
            effective_from=eff, source=source,
        )
        db.add(row)
        db.commit()
        return row

    def _detect(self, db, fund, dividends):
        product = db.query(Product).filter_by(code=fund, market=MARKET).one()
        with patch("app.services.tushare_client.get_fund_div", return_value=dividends):
            return _detect_dividends(db, [product])

    def test_explicit_cash_builds_typed_event(self, test_db):
        """A1：explicit 现金 + pay>=ex（非同日）→ cash_dividend，不产生 pending。"""
        fund = _env(test_db, "C1")
        self._cfg(test_db, fund, "C1", "cash_dividend")
        detected, skipped, degraded = self._detect(test_db, fund, [_div(pay_date="20261021")])
        assert (detected, skipped, degraded) == (1, [], [])
        event = _rows(test_db, fund)[0]
        assert (event.event_type, event.status) == ("cash_dividend", "pending")
        assert event.cash_pay_date == date(2026, 10, 21)
        assert event.share_confirm_date is None
        assert "explicit" in event.notes

    def test_explicit_reinvest_builds_typed_event(self, test_db):
        """A2：explicit 再投 + pay 为交易日且 >ex>=滞后 → reinvest_dividend；净值确认时取（不预填）。"""
        fund = _env(test_db, "C2")
        self._cfg(test_db, fund, "C2", "reinvest_dividend")
        pay = try_get_next_trading_day(test_db, EX, days=1, exchange="SSE")  # nav_lag_days=0 → 滞后 1
        detected, skipped, degraded = self._detect(test_db, fund, [_div(pay_date=pay.strftime("%Y%m%d"))])
        assert (detected, skipped, degraded) == (1, [], [])
        event = _rows(test_db, fund)[0]
        assert (event.event_type, event.status) == ("reinvest_dividend", "pending")
        assert event.share_confirm_date == pay
        assert event.reinvest_nav is None  # A2/#744：确认时取除息日精确净值

    def test_only_default_row_stays_pending_verbatim(self, test_db):
        """A3 反向防线（可独立变红）：仅 default 行 → 检测器视同无配置，建 dividend_pending。
        若检测器误读 default 建 typed，本用例翻红。"""
        fund = _env(test_db, "C3")
        self._cfg(test_db, fund, "C3", "reinvest_dividend", source="default")
        detected, skipped, degraded = self._detect(test_db, fund, [_div(pay_date="20261021")])
        assert (detected, skipped, degraded) == (1, [], [])
        event = _rows(test_db, fund)[0]
        assert event.event_type == "dividend_pending"
        assert event.cash_pay_date is None and event.share_confirm_date is None

    def test_no_config_stays_pending(self, test_db):
        """A3：无配置 → pending（与今日行为逐字一致）。"""
        fund = _env(test_db, "C4")
        detected, skipped, degraded = self._detect(test_db, fund, [_div(pay_date="20261021")])
        assert (detected, skipped, degraded) == (1, [], [])
        assert _rows(test_db, fund)[0].event_type == "dividend_pending"

    def test_cash_same_day_pay_equal_ex_degraded(self, test_db):
        """A5：同日现金 pay==ex → degraded pay_date_not_after_ex + pending（防互等死锁）。"""
        fund = _env(test_db, "C5")
        self._cfg(test_db, fund, "C5", "cash_dividend")
        detected, skipped, degraded = self._detect(
            test_db, fund, [_div(record_date=EX.strftime("%Y%m%d"), pay_date=EX.strftime("%Y%m%d"))])
        assert detected == 1 and skipped == []
        assert degraded[0]["reason"] == "pay_date_not_after_ex"
        event = _rows(test_db, fund)[0]
        assert event.event_type == "dividend_pending" and event.cash_pay_date is None

    def test_cash_non_same_day_pay_equal_ex_allowed(self, test_db):
        """A5/R8：非同日现金 pay==ex 允许（检测器不比人工路径更严）。"""
        fund = _env(test_db, "C6")
        self._cfg(test_db, fund, "C6", "cash_dividend")
        detected, skipped, degraded = self._detect(test_db, fund, [_div(pay_date=EX.strftime("%Y%m%d"))])
        assert (detected, skipped, degraded) == (1, [], [])
        event = _rows(test_db, fund)[0]
        assert event.event_type == "cash_dividend" and event.cash_pay_date == EX

    def test_cash_pay_before_ex_degraded(self, test_db):
        fund = _env(test_db, "C7")
        self._cfg(test_db, fund, "C7", "cash_dividend")
        _detected, _skipped, degraded = self._detect(test_db, fund, [_div(pay_date="20261019")])
        assert degraded[0]["reason"] == "pay_date_not_after_ex"
        assert _rows(test_db, fund)[0].event_type == "dividend_pending"

    def test_pay_date_missing_degraded(self, test_db):
        """pay_date 缺失 → pending + degraded pay_date_missing，不猜。"""
        fund = _env(test_db, "C8")
        self._cfg(test_db, fund, "C8", "cash_dividend")
        _detected, _skipped, degraded = self._detect(test_db, fund, [_div()])
        assert degraded[0]["reason"] == "pay_date_missing"
        assert _rows(test_db, fund)[0].event_type == "dividend_pending"

    def test_pay_date_invalid_degraded(self, test_db):
        fund = _env(test_db, "C9")
        self._cfg(test_db, fund, "C9", "cash_dividend")
        _detected, _skipped, degraded = self._detect(test_db, fund, [_div(pay_date="not-a-date")])
        assert degraded[0]["reason"] == "pay_date_invalid"

    def test_reinvest_non_trading_day_degraded(self, test_db):
        fund = _env(test_db, "C10")
        self._cfg(test_db, fund, "C10", "reinvest_dividend")
        non_td = EX + timedelta(days=1)
        while is_trading_day(test_db, non_td, exchange="SSE"):
            non_td += timedelta(days=1)
        _detected, _skipped, degraded = self._detect(test_db, fund, [_div(pay_date=non_td.strftime("%Y%m%d"))])
        assert degraded[0]["reason"] == "reinvest_non_trading_day"
        assert _rows(test_db, fund)[0].event_type == "dividend_pending"

    def test_reinvest_nav_lag_window_degraded(self, test_db):
        """A5b：nav_lag_days=1，pay=ex+1 交易日（< ex+2 滞后）→ degraded reinvest_nav_lag_window。"""
        fund = _env(test_db, "C11")
        product = test_db.query(Product).filter_by(code=fund, market=MARKET).one()
        product.nav_lag_days = 1
        test_db.commit()
        self._cfg(test_db, fund, "C11", "reinvest_dividend")
        pay = try_get_next_trading_day(test_db, EX, days=1, exchange="SSE")
        _detected, _skipped, degraded = self._detect(test_db, fund, [_div(pay_date=pay.strftime("%Y%m%d"))])
        assert degraded[0]["reason"] == "reinvest_nav_lag_window"
        assert _rows(test_db, fund)[0].event_type == "dividend_pending"

    def test_reinvest_nav_lag_satisfied(self, test_db):
        """A5b：nav_lag_days=1，pay=ex+2 交易日（>=滞后）→ reinvest_dividend。"""
        fund = _env(test_db, "C12")
        product = test_db.query(Product).filter_by(code=fund, market=MARKET).one()
        product.nav_lag_days = 1
        test_db.commit()
        self._cfg(test_db, fund, "C12", "reinvest_dividend")
        pay = try_get_next_trading_day(test_db, EX, days=2, exchange="SSE")
        detected, skipped, degraded = self._detect(test_db, fund, [_div(pay_date=pay.strftime("%Y%m%d"))])
        assert (detected, skipped, degraded) == (1, [], [])
        assert _rows(test_db, fund)[0].event_type == "reinvest_dividend"

    def test_akshare_pay_date_consumed_own_value(self, test_db):
        """双源 pay_date 各取本值（C1）：akshare explicit + akshare pay_date → typed 用该值。"""
        fund = _env(test_db, "C13", data_source="akshare")
        self._cfg(test_db, fund, "C13", "cash_dividend")
        product = test_db.query(Product).filter_by(code=fund, market=MARKET).one()
        with patch("app.services.akshare_client.get_fund_div_otc", return_value=[_akdiv(pay_date="20261022")]):
            detected, skipped, degraded = _detect_dividends(test_db, [product])
        assert (detected, skipped, degraded) == (1, [], [])
        event = _rows(test_db, fund)[0]
        assert event.event_type == "cash_dividend" and event.cash_pay_date == date(2026, 10, 22)
        assert event.event_source == "akshare"

    def test_config_change_after_detection_no_rewrite(self, test_db):
        """A7：检测后改配置不重写既有事件（existing 去重，事后补建不回溯）。"""
        fund = _env(test_db, "C14")
        self._cfg(test_db, fund, "C14", "cash_dividend")
        assert self._detect(test_db, fund, [_div(pay_date="20261021")])[0] == 1
        cfg = test_db.query(DividendMethodConfig).filter_by(product_code=fund).one()
        cfg.dividend_method = "reinvest_dividend"
        test_db.commit()
        detected, _skipped, _degraded = self._detect(test_db, fund, [_div(pay_date="20261021")])
        assert detected == 0
        assert _rows(test_db, fund)[0].event_type == "cash_dividend"
