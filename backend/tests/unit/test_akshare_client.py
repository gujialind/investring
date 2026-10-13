# ============================================================================
# 单元测试：akshare 客户端的重试日志与取数解析契约 (test_akshare_client.py)
# ============================================================================
# `_retry` 是三个 fetcher（场外净值 / 场内日线 / 港互认）的唯一漏斗，这里测它的
# 重试与日志行为。#651 起港互认改为自带请求 + 按 `PDATE`/`NAV` 键名解析，其信封
# 校验、分页与行级响亮判据同在此测。网络一律经 `_hk_nav_request_page` 或
# `requests.get` 打桩，不碰真网络。
# ============================================================================

import pytest

from app.services import akshare_client
from app.services.akshare_client import (
    AkshareAPIError,
    _hk_nav_request_page,
    _HK_MAX_PAGES,
    _HK_PAGE_SIZE,
    _retry,
)
from tests.conftest import log_lines, only_log_line

HK = "1001767344"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """限流与退避都是真 sleep（默认 1s 起、指数翻倍），测试里没必要等"""
    monkeypatch.setattr(akshare_client, "_rate_limit_sleep", lambda: None)
    monkeypatch.setattr(akshare_client.time, "sleep", lambda seconds: None)


def _hk_row(pdate, nav, **overrides):
    """2026-09 键序变动后的实测行形态。

    `CURRENCY:"元"` 与 `ESEQID:3.4e11` 是刻意留下的干扰列——它们正是旧的位置重命名
    误绑成「净值日期 / 单位净值」的那两列，摘掉它们这条套件就抓不住原故障。
    """
    row = {
        "_id": f"{HK}{pdate}",
        "ACCNAV": "",
        "BONUS": "",
        "CURRENCY": "元",
        "ESEQID": 342652919208.0,
        "FCODE": "968050",
        "HKFCODE": HK,
        "NAV": nav,
        "NAVCHG": -0.05,
        "NAVCHGRT": -0.4762,
        "PDATE": pdate,
    }
    row.update(overrides)
    return row


@pytest.fixture
def hk_pages(monkeypatch):
    """按 page_index 供页，并记录每次调用的线上参数供断言。"""
    calls = []

    def install(pages, *, total=None):
        def fake_page(hkfcode, page_index, date1, date2):
            calls.append(
                {"hkfcode": hkfcode, "pageindex": page_index, "date1": date1, "date2": date2}
            )
            rows = pages[page_index] if page_index < len(pages) else []
            return {
                "Code": "1",
                "Message": "Ok",
                "Data": rows,
                "TotalCount": total if total is not None else sum(len(p) for p in pages),
            }

        monkeypatch.setattr(akshare_client, "_hk_nav_request_page", fake_page)
        return calls

    return install


def _payload_response(monkeypatch, payload, status_code=200):
    """把 `_hk_nav_request_page` 底下的 requests.get 顶成直接返回给定信封。"""

    class _Resp:
        def raise_for_status(self):
            if status_code >= 400:
                raise akshare_client.requests.HTTPError(f"HTTP {status_code}")

        def json(self):
            return payload

    monkeypatch.setattr(
        akshare_client.requests, "get", lambda *args, **kwargs: _Resp()
    )


def test_retryable_failures_log_warning_then_succeed(json_log_capture):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("网络抖动")
        return [{"trade_date": "20310308"}]

    assert _retry(flaky, "获取场外基金净值失败") == [{"trade_date": "20310308"}]
    assert len(calls) == 3

    lines = log_lines(json_log_capture)
    assert [line["level"] for line in lines] == ["WARNING", "WARNING"]
    assert lines[0]["logger"] == "app.services.akshare_client"
    assert "获取场外基金净值失败" in lines[0]["message"]
    assert "网络抖动" in lines[0]["message"]


def test_final_failure_logs_error_with_stack(json_log_capture):
    def always_fail():
        raise ConnectionError("对端拒绝")

    with pytest.raises(AkshareAPIError, match="获取场内 ETF 日线失败: 对端拒绝"):
        _retry(always_fail, "获取场内 ETF 日线失败")

    lines = log_lines(json_log_capture)
    assert [line["level"] for line in lines] == ["WARNING", "WARNING", "ERROR"]
    # 原始异常此前只被折进 AkshareAPIError 的字符串里，堆栈丢失；现在留在 exception 字段
    assert "ConnectionError: 对端拒绝" in lines[-1]["exception"]


class TestHkMutualNavParsing:
    """#651 本体：按键名取数，且「上游有行而我解析不出」必须响亮。"""

    def test_reads_by_key_with_real_upstream_shape(self, hk_pages):
        """实测键序下取到正确的日期与净值——位置实现必然返回 '元' / 3.4e11"""
        hk_pages([[_hk_row("2026-09-15", 10.54), _hk_row("2026-09-14", 10.55)]])

        rows = akshare_client.get_fund_hk_mutual(HK, "20260914", "20260915")

        assert rows == [
            {"trade_date": "20260915", "unit_price": 10.54, "accumulated_nav": None,
             "pct_change": -0.4762},
            {"trade_date": "20260914", "unit_price": 10.55, "accumulated_nav": None,
             "pct_change": -0.4762},
        ]

    def test_result_is_independent_of_key_insertion_order(self, hk_pages):
        """键序再变一次也不影响结果——取数已不依赖位置。"""
        shuffled = [
            {"NAV": 10.55, "PDATE": "2026-09-14", "HKFCODE": HK, "CURRENCY": "元"},
        ]
        hk_pages([shuffled])

        # 这一行没有 NAVCHGRT：缺键只能是 None，不得被伪造成 0.0
        assert akshare_client.get_fund_hk_mutual(HK, "20260914", "20260914") == [
            {"trade_date": "20260914", "unit_price": 10.55, "accumulated_nav": None,
             "pct_change": None}
        ]

    def test_missing_pdate_raises_and_leaves_locatable_error(
        self, hk_pages, json_log_capture
    ):
        """整段缺 PDATE（键被改名）：抛错 + 一条含行数的 ERROR，绝不回落成空集。"""
        hk_pages([[_hk_row(None, 10.55, PDATE=None) for _ in range(3)]])

        with pytest.raises(AkshareAPIError, match="无一行可解析"):
            akshare_client.get_fund_hk_mutual(HK, "20260914", "20260922")

        line = only_log_line(
            json_log_capture, level="ERROR", operation="fetch_hk_mutual_nav"
        )
        assert line["upstream_rows"] == 3
        assert line["parsed"] == 0
        assert line["market"] == "HK_MUTUAL"

    def test_empty_nav_column_raises(self, hk_pages):
        """issue 要求的第三种形态：NAV 全为空串。空串不是价格。"""
        hk_pages([[_hk_row("2026-09-14", ""), _hk_row("2026-09-15", "")]])

        with pytest.raises(AkshareAPIError, match="无一行可解析"):
            akshare_client.get_fund_hk_mutual(HK, "20260914", "20260915")

    def test_partial_drift_keeps_good_rows_and_warns(self, hk_pages, json_log_capture):
        """局部坏值：可用行照常返回（不为几行坏值丢整段历史），坏行进 WARNING。"""
        hk_pages(
            [
                [
                    _hk_row("2026-09-15", 10.54),
                    _hk_row("2026-09-14", ""),
                    _hk_row("2026-09-11", 10.57),
                ]
            ]
        )

        rows = akshare_client.get_fund_hk_mutual(HK, "20260911", "20260915")

        assert [r["trade_date"] for r in rows] == ["20260915", "20260911"]
        line = only_log_line(
            json_log_capture, level="WARNING", operation="fetch_hk_mutual_nav"
        )
        assert line["parsed"] == 2
        assert line["skipped"] == ["1:price=''"]

    def test_rows_all_outside_window_are_benign_and_silent(
        self, hk_pages, json_log_capture
    ):
        """周末/停市：上游有行但都落在区间外 → 空集、不抛、不打日志。

        这条是「合法空窗」的锁：`run_nav_sync` 的增量窗 [本地最大+1, 昨天] 每个周一
        都会落进这里，判失败即假报警。
        """
        hk_pages([[_hk_row("2026-09-11", 10.57)]])

        rows = akshare_client.get_fund_hk_mutual(HK, "20260926", "20260927")

        assert rows == []
        assert log_lines(json_log_capture) == []

    def test_malformed_dates_never_escape_as_eight_digit_string(self, hk_pages):
        """坏日期只能进 malformed，绝不能带着非 YYYYMMDD 形态返回给写入侧。

        `market_data_service._bulk_upsert_prices` 在 try 之外做 `date(int(td[:4]), …)`，
        一个漏网的 "元" 会绕过失败标记、在 router 侧被读成 404。
        """
        hk_pages(
            [
                [
                    _hk_row("2026-9-4", 10.5),
                    _hk_row("09/04/2026", 10.5),
                    _hk_row(None, 10.5, PDATE=None),
                    _hk_row("NaT", 10.5),
                    _hk_row("2026-09-14", 10.55),
                ]
            ]
        )

        rows = akshare_client.get_fund_hk_mutual(HK, "20260901", "20260930")

        assert [r["trade_date"] for r in rows] == ["20260914"]
        assert all(len(r["trade_date"]) == 8 and r["trade_date"].isdigit() for r in rows)

    def test_compact_pdate_is_also_accepted(self, hk_pages):
        """上游若给 YYYYMMDD（本模块的内部口径）也照常接受，不因收紧而丢数据。"""
        hk_pages([[_hk_row("20260914", 10.55)]])

        assert akshare_client.get_fund_hk_mutual(HK)[0]["trade_date"] == "20260914"

    def test_non_numeric_nav_is_malformed_not_fatal(self, hk_pages):
        """净值列错位成币种串时逐行拒绝，但同行的正常行不受牵连。"""
        hk_pages([[_hk_row("2026-09-14", "元"), _hk_row("2026-09-15", 10.54)]])

        rows = akshare_client.get_fund_hk_mutual(HK, "20260914", "20260915")

        assert [r["trade_date"] for r in rows] == ["20260915"]

    def test_rows_after_end_date_are_dropped_benignly(self, hk_pages, json_log_capture):
        """晚于 end_date 的行是正常剔除，不算解析失败、不产生「无一行可解析」。"""
        hk_pages([[_hk_row("2026-09-28", 10.45)]])

        assert akshare_client.get_fund_hk_mutual(HK, "20260914", "20260922") == []
        assert log_lines(json_log_capture) == []


class TestHkMutualNavTransport:
    """信封、分页与线上参数形态。"""

    def test_error_envelope_raises_with_diagnostics(self, monkeypatch, json_log_capture):
        """紧凑日期这类上游错误的真实形态是 Code:"-1" 且**不带 Data 键**。

        写成 `payload.get("Data") or []` 就会把它读成合法空集，原地复刻 #651。
        """
        _payload_response(
            monkeypatch, {"Code": "-1", "Message": "103", "ErrorCode": "103"}
        )

        with pytest.raises(AkshareAPIError, match="Code=-1"):
            _hk_nav_request_page(HK, 0, "2026-09-08", "2026-09-25")

    def test_payload_without_data_list_raises(self, monkeypatch):
        _payload_response(monkeypatch, {"Code": "1", "Message": "Ok", "TotalCount": 0})

        with pytest.raises(AkshareAPIError, match="缺少 Data"):
            _hk_nav_request_page(HK, 0, "", "")

    def test_pagination_collects_to_total_count(self, hk_pages):
        """akshare 恒取第一页；实测 TotalCount=1768 → 早于 2022-07 的历史被静默截掉。"""
        pages = [
            [_hk_row("2026-09-14", 10.55)] * _HK_PAGE_SIZE,
            [_hk_row("2026-09-13", 10.56)] * (1768 - _HK_PAGE_SIZE),
        ]
        calls = hk_pages(pages, total=1768)

        rows = akshare_client.get_fund_hk_mutual(HK)

        assert len(rows) == 1768
        assert [c["pageindex"] for c in calls] == [0, 1]

    def test_pagination_stops_at_total_count_even_on_full_pages(self, hk_pages):
        """页数恰为整页倍数时靠 TotalCount 收敛，不能只会看短页。"""
        pages = [[_hk_row("2026-09-14", 10.55)] * _HK_PAGE_SIZE for _ in range(3)]
        calls = hk_pages(pages, total=_HK_PAGE_SIZE * 2)

        rows = akshare_client.get_fund_hk_mutual(HK)

        assert len(rows) == _HK_PAGE_SIZE * 2
        assert [c["pageindex"] for c in calls] == [0, 1]

    def test_each_extra_page_is_rate_limited(self, hk_pages, monkeypatch):
        """分页把一次尝试变成多次出网：页间必须限流，否则 `akshare_rate_interval`
        被稀释成 1/页数。
        """
        sleeps = []
        monkeypatch.setattr(akshare_client, "_rate_limit_sleep", lambda: sleeps.append(1))
        hk_pages(
            [
                [_hk_row("2026-09-14", 10.55)] * _HK_PAGE_SIZE,
                [_hk_row("2026-09-13", 10.56)] * 20,
            ],
            total=_HK_PAGE_SIZE + 20,
        )

        akshare_client.get_fund_hk_mutual(HK)

        # 2 页 → `_retry` 尝试前 1 次 + 第 2 页前 1 次
        assert len(sleeps) == 2

    def test_valid_envelope_is_returned_verbatim(self, monkeypatch):
        """`_hk_nav_request_page` 的正常出口本身也要有用例（其余用例打的是这个接缝）。"""
        _payload_response(
            monkeypatch,
            {"Code": "1", "Message": "Ok", "Data": [_hk_row("2026-09-14", 10.55)],
             "TotalCount": 1},
        )

        payload = _hk_nav_request_page(HK, 0, "2026-09-08", "2026-09-25")

        assert payload["TotalCount"] == 1
        assert payload["Data"][0]["PDATE"] == "2026-09-14"

    def test_page_cap_warns_instead_of_truncating_silently(self, hk_pages, json_log_capture):
        """取不完时响亮留 WARNING（truncated），不静默少给。"""
        endless = [[_hk_row("2026-09-14", 10.55)] * _HK_PAGE_SIZE] * (_HK_MAX_PAGES + 5)
        hk_pages(endless, total=999999)

        akshare_client.get_fund_hk_mutual(HK)

        line = only_log_line(
            json_log_capture, level="WARNING", operation="fetch_hk_mutual_nav"
        )
        assert line["truncated"] is True
        assert line["collected"] == _HK_PAGE_SIZE * _HK_MAX_PAGES

    def test_date_bounds_are_sent_dashed(self, hk_pages):
        """接口只认带横线格式（紧凑格式实测回错误信封），上线参数必须是 dashed。"""
        calls = hk_pages([[_hk_row("2026-09-14", 10.55)]])

        akshare_client.get_fund_hk_mutual(HK, "20260908", "20260925")

        assert calls[0]["date1"] == "2026-09-08"
        assert calls[0]["date2"] == "2026-09-25"

    def test_unwindowed_bound_sends_no_lower_limit(self, hk_pages):
        """start_date 为空＝不设下界，用空串（akshare 同源行为），不自造纪元日。"""
        calls = hk_pages([[_hk_row("2026-09-14", 10.55)]])

        akshare_client.get_fund_hk_mutual(HK)

        assert calls[0]["date1"] == ""
        assert calls[0]["date2"] == ""

    def test_bad_bound_raises_without_touching_upstream(self, hk_pages):
        """日期格式是调用方契约错误：当场拒绝，不进重试、不打上游。"""
        calls = hk_pages([[_hk_row("2026-09-14", 10.55)]])

        with pytest.raises(AkshareAPIError, match="需为 YYYYMMDD"):
            akshare_client.get_fund_hk_mutual(HK, "2026-09-08", None)

        assert calls == []

    def test_unwindowed_empty_is_an_error_but_windowed_empty_is_benign(self, hk_pages):
        """无效代码与周末返回逐字节同形，只能靠「有没有下界」区分。"""
        hk_pages([[]])
        assert akshare_client.get_fund_hk_mutual(HK, "20260926", "20260927") == []

        with pytest.raises(AkshareAPIError, match="全历史区间"):
            akshare_client.get_fund_hk_mutual(HK)

    def test_transport_error_is_retried_then_succeeds(self, monkeypatch, json_log_capture):
        """重试与限流仍包着港互认取数（重构没把它移出 _retry）。"""
        attempts = []

        def flaky_page(hkfcode, page_index, date1, date2):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("网络抖动")
            return {"Code": "1", "Message": "Ok", "Data": [_hk_row("2026-09-14", 10.55)],
                    "TotalCount": 1}

        monkeypatch.setattr(akshare_client, "_hk_nav_request_page", flaky_page)

        rows = akshare_client.get_fund_hk_mutual(HK, "20260914", "20260914")

        assert len(rows) == 1
        assert len(attempts) == 3
        assert [line["level"] for line in log_lines(json_log_capture)] == [
            "WARNING",
            "WARNING",
        ]


class _RowFrame:
    """最小 DataFrame 替身：被测代码只用到 `len()`、`iterrows()` 与 `columns`。

    刻意不 import pandas——本仓测试此前零处构造 DataFrame，取数层改用 dict 替身后
    也不必为两条 CN 用例破例（#651 的解析改动同样不需要 pandas）。

    `columns`（#692）按各行键的并集推导：真实 DataFrame 的列是矩形对齐的，替身从
    dict 行反推即可，且必须存在——场外路径靠 `_OTC_PCT_COLUMN in df.columns` 与
    `_OTC_ACCUM_COLUMN in df.columns`（#704）区分「上游没给这列」与「给了但这行是
    空值」，替身缺该属性会让漂移判据整条测不到。
    """

    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    @property
    def columns(self):
        seen = []
        for row in self._rows:
            for key in row:
                if key not in seen:
                    seen.append(key)
        return seen

    def iterrows(self):
        return enumerate(self._rows)


def _patch_otc(monkeypatch, unit_rows, accum_rows=None):
    """按 `indicator` 分发两个 DataFrame，顶掉 `fund_open_fund_info_em`（#704）。

    场外路径自 #704 起对同一接口函数打两次（「单位净值走势」+「累计净值走势」），故替身
    必须按指标分发——不分发就会把单位净值序列当成累计净值序列读，掩盖两条序列各自的
    缺日/坏值/列漂移行为。`accum_rows` 缺省为空序列，即「该品种上游没有累计净值」
    （货币基金形态），此时 `accum_nav` 全为 None 且不留日志。
    """
    calls = []

    def fake_em(**kwargs):
        calls.append(kwargs)
        if kwargs.get("indicator") == akshare_client._OTC_ACCUM_INDICATOR:
            return _RowFrame(accum_rows or [])
        return _RowFrame(unit_rows)

    monkeypatch.setattr("akshare.fund_open_fund_info_em", fake_em)
    return calls


class TestCnAkshareRowsAreScreened:
    """CN_OTC / CN_EXCHANGE 套用同一行级判据（akshare 的 CN 路径也是位置重命名）。

    差别要讲清：ETF 的 klines 是逗号分隔串，列顺序由 akshare 自己请求的 fields2 锁定，
    键序漂移结构性不可能。所以这两条只按行校验，外加港互认那条全历史判据——「带区间的
    空」良性，「不带下界却一行都没有」判故障（实测有效代码恒有行，只有无效代码才空）。
    """

    def test_otc_misbound_date_column_raises_instead_of_empty(self, monkeypatch, json_log_capture):
        """CN_OTC 若也发生 #651 那种错位（日期列变成币种），必须响亮失败。"""
        calls = _patch_otc(monkeypatch, [
            {"净值日期": "元", "单位净值": 342652919208.0},
            {"净值日期": "元", "单位净值": 342652919209.0},
        ])

        with pytest.raises(AkshareAPIError, match="无一行可解析"):
            akshare_client.get_fund_nav_otc("000051", "20260914", "20260915")

        only_log_line(json_log_capture, level="ERROR", operation="fetch_fund_nav_otc")
        # 单位净值整段解析不出来时本次同步已失败，不该再白打一次累计净值请求（#704）
        assert len(calls) == 1

    def test_otc_keeps_good_rows_and_drops_bad_nav(self, monkeypatch, json_log_capture):
        _patch_otc(monkeypatch, [
            {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.1215},
            {"净值日期": "2026-09-14", "单位净值": "", "日增长率": ""},
        ])

        rows = akshare_client.get_fund_nav_otc("000051", "20260914", "20260915")

        assert rows == [{"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": None,
                         "pct_change": 0.1215}]
        # 恰好一条：坏净值行的局部漂移 WARNING。列在、值坏，不得再叠一条列漂移告警。
        only_log_line(json_log_capture, level="WARNING", operation="fetch_fund_nav_otc")

    def test_otc_accepts_date_objects_from_pandas(self, monkeypatch):
        """akshare 把净值日期转成 date/Timestamp 对象——str() 后是 dashed，须照常接受。"""
        from datetime import date as _date

        _patch_otc(monkeypatch, [
            {"净值日期": _date(2026, 9, 14), "单位净值": 1.0, "日增长率": 0.5},
        ])

        assert akshare_client.get_fund_nav_otc("000051") == [
            {"trade_date": "20260914", "unit_nav": 1.0, "accum_nav": None, "pct_change": 0.5}
        ]

    def test_etf_windowed_empty_is_benign(self, monkeypatch):
        """带区间无 k 线时 akshare 返回空 DF——停市、假日都长这样，不是故障。"""
        monkeypatch.setattr("akshare.fund_etf_hist_em", lambda **kw: _RowFrame([]))

        assert akshare_client.get_fund_daily_exchange("510300", "20260914", "20260915") == []

    def test_etf_unwindowed_empty_raises(self, monkeypatch, json_log_capture):
        """全历史一行都没有不可能是良性的：实测有效代码恒有行（510300 为 3487 条），
        只有无效代码才返回空 DF（999999 为 0 条）。放行就是 #651 的静默换到 CN 侧。
        """
        monkeypatch.setattr("akshare.fund_etf_hist_em", lambda **kw: _RowFrame([]))

        with pytest.raises(AkshareAPIError, match="全历史区间未返回任何净值行"):
            akshare_client.get_fund_daily_exchange("510300")

        only_log_line(json_log_capture, level="ERROR", operation="fetch_fund_daily_exchange")

    def test_etf_unparseable_dates_raise(self, monkeypatch):
        monkeypatch.setattr(
            "akshare.fund_etf_hist_em",
            lambda **kw: _RowFrame([
                {"日期": "元", "收盘": 4.6, "开盘": 4.5, "涨跌幅": 0.1},
            ]),
        )

        with pytest.raises(AkshareAPIError, match="无一行可解析"):
            akshare_client.get_fund_daily_exchange("510300", "20260914", "20260915")

    def test_etf_bad_close_is_malformed_not_fatal(self, monkeypatch):
        """收盘列坏掉时只拒该行，同批正常行照常返回。"""
        monkeypatch.setattr(
            "akshare.fund_etf_hist_em",
            lambda **kw: _RowFrame([
                {"日期": "2026-09-14", "收盘": "元", "开盘": 4.5, "涨跌幅": 0.1},
                {"日期": "2026-09-15", "收盘": 4.6, "开盘": 4.5, "涨跌幅": 0.1},
            ]),
        )

        rows = akshare_client.get_fund_daily_exchange("510300", "20260914", "20260915")

        assert [r["trade_date"] for r in rows] == ["20260915"]

    def test_etf_rows_after_end_date_are_dropped(self, monkeypatch):
        """区间外剔除不计作解析失败——与港互认同一口径。"""
        monkeypatch.setattr(
            "akshare.fund_etf_hist_em",
            lambda **kw: _RowFrame([
                {"日期": "2026-09-28", "收盘": 4.6, "开盘": 4.5, "涨跌幅": 0.1},
            ]),
        )

        assert akshare_client.get_fund_daily_exchange("510300", "20260914", "20260922") == []

    def test_etf_window_drop_is_not_counted_as_malformed(self, monkeypatch):
        """整批落在区间外 → 空集且不算解析失败（与港互认同一口径）。"""
        monkeypatch.setattr(
            "akshare.fund_etf_hist_em",
            lambda **kw: _RowFrame([
                {"日期": "2026-09-11", "收盘": 4.6, "开盘": 4.5, "涨跌幅": 0.1},
            ]),
        )

        assert akshare_client.get_fund_daily_exchange("510300", "20260926", "20260927") == []

    def test_etf_returns_all_four_fields(self, monkeypatch):
        monkeypatch.setattr(
            "akshare.fund_etf_hist_em",
            lambda **kw: _RowFrame([
                {"日期": "2026-09-14", "收盘": 4.608, "开盘": 4.586, "涨跌幅": 0.57},
            ]),
        )

        assert akshare_client.get_fund_daily_exchange("510300") == [
            {
                "trade_date": "20260914",
                "close": 4.608,
                "pre_close": 4.586,
                "pct_change": 0.57,
            }
        ]


class TestFetchLayerDoesNotJudgeSign:
    """#701 的分层边界：取数层只判「是不是有限数」，正负由写入判据 `_usable_nav` 收口。

    这条不是多余的存在性检查。若有人在 `_finite_number` 里顺手加 `> 0`，0 价行会被计成
    malformed，#651 的「上游给了行却零可解析」响亮判据会把一次同步整体打成 **failed**；
    写侧判规则把同一批落成**跳过 + WARNING + 计 0 条**。两者都"正确"地拒绝了 0 价，但只有
    后者是设计意图——同步任务里 failed 会连带产品状态与告警，跳过不会。故本层职责单独钉。
    """

    def test_zero_and_negative_nav_stay_parseable_rows(self, monkeypatch):
        _patch_otc(monkeypatch, [
            {"净值日期": "2026-09-15", "单位净值": 0.0, "日增长率": -100.0},
            {"净值日期": "2026-09-14", "单位净值": -1.5, "日增长率": 0.0},
        ])

        rows = akshare_client.get_fund_nav_otc("000051")

        assert len(rows) == 2, "非正净值在本层仍是「解析得出的行」，拒绝发生在写侧"
        assert [r["unit_nav"] for r in rows] == [0.0, -1.5]
        assert [r["pct_change"] for r in rows] == [-100.0, 0.0]


class TestPctChangePassthrough:
    """#692：日涨跌走上游字段透传（场外「日增长率」/ 港互认 `NAVCHGRT`）。

    三件事必须分开：平盘 `0.0` 是真实涨跌、不得折成 None；坏值只能是 None、不得
    计入 malformed（否则一列展示字段的漂移就能打死整段可用历史）；整列缺失要响亮
    留一条 column_drift WARNING，但仍照常返回可用行。
    """

    def test_otc_flat_day_zero_is_preserved(self, monkeypatch):
        """平盘日 0.0 折成 None 会让前端读成「无数据」而不是 0%。"""
        _patch_otc(monkeypatch, [
            {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.0},
        ])

        assert akshare_client.get_fund_nav_otc("000051") == [
            {"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": None,
             "pct_change": 0.0}
        ]

    def test_otc_bad_pct_value_is_none_and_not_malformed(self, monkeypatch, json_log_capture):
        """坏值 → None，且不进 malformed：局部漂移 WARNING 只由净值/日期坏行触发。"""
        _patch_otc(monkeypatch, [
            {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": "元"},
            {"净值日期": "2026-09-14", "单位净值": 1.230, "日增长率": float("nan")},
        ])

        rows = akshare_client.get_fund_nav_otc("000051", "20260914", "20260915")

        assert [r["pct_change"] for r in rows] == [None, None]
        assert [r["trade_date"] for r in rows] == ["20260915", "20260914"]
        # 列在、只是值坏 ⇒ 一条日志都不该有（无 malformed、无列漂移）
        assert log_lines(json_log_capture) == []

    def test_otc_missing_column_warns_once_and_still_returns_rows(
        self, monkeypatch, json_log_capture
    ):
        """整列被改名：可用历史照常返回，另留一条 column_drift 线索。"""
        _patch_otc(monkeypatch, [
            {"净值日期": "2026-09-15", "单位净值": 1.234, "日涨幅": 0.12},
        ])

        rows = akshare_client.get_fund_nav_otc("000051")

        assert rows == [{"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": None,
                         "pct_change": None}]
        line = only_log_line(
            json_log_capture, level="WARNING", operation="fetch_fund_nav_otc",
            column_drift=True,
        )
        assert line["missing_column"] == akshare_client._OTC_PCT_COLUMN
        assert line["upstream_rows"] == 1
        assert line["market"] == "CN_OTC"

    def test_otc_empty_frame_does_not_warn_about_drift(self, monkeypatch, json_log_capture):
        """合法空窗（周末、净值未发布）不该顺带报一条列漂移——那是假报警。

        也不该再打一次累计净值请求（#704 评审）：没有行会消费它，而两个指标各自都要
        重新下载并解析整份 `pingzhongdata/{symbol}.js`，`run_nav_sync` 又是按产品串行，
        空窗日的无效成本会被产品数放大。
        """
        calls = _patch_otc(monkeypatch, [])

        assert akshare_client.get_fund_nav_otc("000051", "20260926", "20260927") == []
        assert log_lines(json_log_capture) == []
        assert [c["indicator"] for c in calls] == ["单位净值走势"]

    def test_otc_window_outside_history_skips_accum_request(
        self, monkeypatch, json_log_capture
    ):
        """上游有行但全落在请求窗口外，同样是合法空窗：不打第二次请求、不留日志。"""
        calls = _patch_otc(monkeypatch, [
            {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12},
        ])

        assert akshare_client.get_fund_nav_otc("000051", "20260926", "20260927") == []
        assert log_lines(json_log_capture) == []
        assert [c["indicator"] for c in calls] == ["单位净值走势"]

    def test_otc_drift_warning_survives_a_total_parse_failure(
        self, monkeypatch, json_log_capture
    ):
        """整段解析不出来时列漂移正是最可能的成因，必须先于抛错留痕。"""
        _patch_otc(monkeypatch, [
            {"净值日期": "元", "单位净值": 342652919208.0},
            {"净值日期": "元", "单位净值": 342652919209.0},
        ])

        with pytest.raises(AkshareAPIError, match="无一行可解析"):
            akshare_client.get_fund_nav_otc("000051", "20260914", "20260915")

        only_log_line(
            json_log_capture, level="WARNING", operation="fetch_fund_nav_otc",
            column_drift=True,
        )
        only_log_line(json_log_capture, level="ERROR", operation="fetch_fund_nav_otc")

    def test_hk_flat_day_zero_is_preserved(self, hk_pages):
        hk_pages([[_hk_row("2026-09-15", 10.54, NAVCHGRT=0.0)]])

        assert akshare_client.get_fund_hk_mutual(HK, "20260915", "20260915") == [
            {"trade_date": "20260915", "unit_price": 10.54, "accumulated_nav": None,
             "pct_change": 0.0}
        ]

    def test_hk_bad_pct_value_is_none_and_not_malformed(self, hk_pages, json_log_capture):
        """NAVCHGRT 坏值只让该字段为 None，不牵连同行的净值、不算解析失败。"""
        hk_pages([[_hk_row("2026-09-15", 10.54, NAVCHGRT="元")]])

        rows = akshare_client.get_fund_hk_mutual(HK, "20260915", "20260915")

        assert rows == [{"trade_date": "20260915", "unit_price": 10.54,
                         "accumulated_nav": None, "pct_change": None}]
        assert log_lines(json_log_capture) == []


class TestOtcAccumulatedNav:
    """#704：场外累计净值取自同接口的「累计净值走势」指标，按净值日期合并。

    三条语义各自独立钉住：合并靠日期映射而非位置对齐（缺日/日期不对齐落 NULL，**不拿
    单位净值顶替**）；坏值是展示列级事件（不计 malformed、不打死单位净值历史，与 #692
    日涨跌同族）；整段失效降级为全 NULL + 一条 WARNING，且该请求同样经 `_retry` 限流
    与重试。

    **整段失效必须与合法空序列分开**（评审 Blocker）：请求重试耗尽、缺「累计净值」列、
    有列但零条日期可解析，三种都在行上打 `accum_degraded`，写入侧据此在冲突行保留既有
    `accumulated_nav`；货币基金那种「上游 0 行」不打标，照既有语义落 NULL。
    """

    def test_accum_series_is_merged_by_date(self, monkeypatch):
        calls = _patch_otc(
            monkeypatch,
            [
                {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12},
                {"净值日期": "2026-09-14", "单位净值": 1.230, "日增长率": 0.10},
            ],
            accum_rows=[
                {"净值日期": "2026-09-15", "累计净值": 3.721},
                {"净值日期": "2026-09-14", "累计净值": 3.710},
            ],
        )

        rows = akshare_client.get_fund_nav_otc("000001", "20260914", "20260915")

        assert rows == [
            {"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": 3.721,
             "pct_change": 0.12},
            {"trade_date": "20260914", "unit_nav": 1.230, "accum_nav": 3.710,
             "pct_change": 0.10},
        ]
        assert [c["indicator"] for c in calls] == ["单位净值走势", "累计净值走势"]
        assert [c["symbol"] for c in calls] == ["000001", "000001"]

    def test_missing_accum_day_is_null_not_unit_nav(self, monkeypatch, json_log_capture):
        """两条序列日期不对齐：缺的那一天只能是 NULL，顶替等于伪造累计净值。"""
        _patch_otc(
            monkeypatch,
            [
                {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12},
                {"净值日期": "2026-09-14", "单位净值": 1.230, "日增长率": 0.10},
            ],
            accum_rows=[{"净值日期": "2026-09-15", "累计净值": 3.721}],
        )

        rows = akshare_client.get_fund_nav_otc("000001", "20260914", "20260915")

        assert [r["accum_nav"] for r in rows] == [3.721, None]
        assert [r["unit_nav"] for r in rows] == [1.234, 1.230]
        # 缺日是正常形态（净值发布时差），不该留任何日志
        assert log_lines(json_log_capture) == []

    def test_bad_accum_value_is_null_and_not_malformed(self, monkeypatch, json_log_capture):
        """坏值只丢这一列，不计 malformed——否则一列展示字段能打死整段可用历史。"""
        _patch_otc(
            monkeypatch,
            [
                {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12},
                {"净值日期": "2026-09-14", "单位净值": 1.230, "日增长率": 0.10},
            ],
            accum_rows=[
                {"净值日期": "2026-09-15", "累计净值": "元"},
                {"净值日期": "2026-09-14", "累计净值": float("nan")},
            ],
        )

        rows = akshare_client.get_fund_nav_otc("000001", "20260914", "20260915")

        assert [r["accum_nav"] for r in rows] == [None, None]
        assert [r["unit_nav"] for r in rows] == [1.234, 1.230]
        assert log_lines(json_log_capture) == []
        # 日期可解析、只是逐日坏值：不是整段失效，不得打上降级标记
        assert all("accum_degraded" not in r for r in rows)

    def test_empty_accum_series_is_silent(self, monkeypatch, json_log_capture):
        """货币基金等品种上游本就没有累计净值序列：空 DataFrame 不是漂移，不告警。"""
        _patch_otc(
            monkeypatch,
            [{"净值日期": "2026-09-15", "单位净值": 1.0, "日增长率": 0.01}],
            accum_rows=[],
        )

        rows = akshare_client.get_fund_nav_otc("000001")

        assert rows == [{"trade_date": "20260915", "unit_nav": 1.0, "accum_nav": None,
                         "pct_change": 0.01}]
        assert log_lines(json_log_capture) == []

    def test_missing_accum_column_warns_once(self, monkeypatch, json_log_capture):
        """「累计净值」整列被改名：可用历史照常返回，另留一条 column_drift 线索。"""
        _patch_otc(
            monkeypatch,
            [{"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12}],
            accum_rows=[{"净值日期": "2026-09-15", "累计净资": 3.721}],
        )

        rows = akshare_client.get_fund_nav_otc("000001")

        assert rows == [{"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": None,
                         "pct_change": 0.12, "accum_degraded": True}]
        line = only_log_line(
            json_log_capture, level="WARNING", operation="fetch_fund_nav_otc",
            column_drift=True,
        )
        assert line["missing_column"] == akshare_client._OTC_ACCUM_COLUMN
        assert line["market"] == "CN_OTC"

    def test_accum_date_column_drift_is_loud_and_degraded(
        self, monkeypatch, json_log_capture
    ):
        """有「累计净值」列、但零条日期可解析：整段失效，不得静默按合法空序列处理。

        真实 akshare 会把不可解析日期转成 `NaT`，故列名不变、日期列整体漂移时正是这个
        形态。此前它与货币基金那种「上游 0 行」共用 `{}` 且零日志，写入侧无从区分，
        重叠同步会把已有的 accumulated_nav 抹成 NULL。
        """
        _patch_otc(
            monkeypatch,
            [{"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12}],
            accum_rows=[
                {"净值日期": "NaT", "累计净值": 3.721},
                {"净值日期": "NaT", "累计净值": 3.710},
            ],
        )

        rows = akshare_client.get_fund_nav_otc("000001")

        assert rows == [{"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": None,
                         "pct_change": 0.12, "accum_degraded": True}]
        line = only_log_line(
            json_log_capture, level="WARNING", operation="fetch_fund_nav_otc",
            accum_date_drift=True,
        )
        assert line["upstream_rows"] == 2
        assert line["parsed"] == 0
        assert line["market"] == "CN_OTC"

    def test_accum_request_failure_degrades_without_killing_unit_nav(
        self, monkeypatch, json_log_capture
    ):
        """累计净值请求整段失败：单位净值行照常返回，降级留一条 accum_degraded WARNING。"""
        def fake_em(**kwargs):
            if kwargs.get("indicator") == akshare_client._OTC_ACCUM_INDICATOR:
                raise ConnectionError("对端拒绝")
            return _RowFrame([
                {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12},
            ])

        monkeypatch.setattr("akshare.fund_open_fund_info_em", fake_em)

        rows = akshare_client.get_fund_nav_otc("000001")

        assert rows == [{"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": None,
                         "pct_change": 0.12, "accum_degraded": True}]
        # _retry 自己的 2 条重试 WARNING + 1 条 ERROR 之外，降级线索恰好一条
        only_log_line(
            json_log_capture, level="WARNING", operation="fetch_fund_nav_otc",
            accum_degraded=True,
        )

    def test_accum_request_is_rate_limited_and_retried(self, monkeypatch):
        """第二次请求同样经 `_retry`：限流不稀释，瞬时故障重试后成功。"""
        attempts = []
        sleeps = []
        monkeypatch.setattr(
            akshare_client, "_rate_limit_sleep", lambda: sleeps.append(1)
        )

        def fake_em(**kwargs):
            if kwargs.get("indicator") == akshare_client._OTC_ACCUM_INDICATOR:
                attempts.append(1)
                if len(attempts) < 2:
                    raise ConnectionError("网络抖动")
                return _RowFrame([{"净值日期": "2026-09-15", "累计净值": 3.721}])
            return _RowFrame([
                {"净值日期": "2026-09-15", "单位净值": 1.234, "日增长率": 0.12},
            ])

        monkeypatch.setattr("akshare.fund_open_fund_info_em", fake_em)

        rows = akshare_client.get_fund_nav_otc("000001")

        assert rows[0]["accum_nav"] == 3.721
        assert len(attempts) == 2
        # 单位净值 1 次尝试 + 累计净值 2 次尝试，每次尝试前各限流一次
        assert len(sleeps) == 3


class TestHkMutualCodeResolution:
    """10 位码短路：港互认主路径不再经排名表，也不再被它的故障连坐。"""

    def test_ten_digit_code_never_consults_rank_map(self, hk_pages, monkeypatch):
        def explode(_ak):
            raise AssertionError("10 位码不该调用 fund_hk_rank_em")

        monkeypatch.setattr(akshare_client, "_ensure_hk_code_map", explode)
        calls = hk_pages([[_hk_row("2026-09-14", 10.55)]])

        akshare_client.get_fund_hk_mutual("1001767344")

        assert calls[0]["hkfcode"] == "1001767344"

    def test_suffix_is_stripped(self, hk_pages):
        calls = hk_pages([[_hk_row("2026-09-14", 10.55)]])

        akshare_client.get_fund_hk_mutual("1001767344.HK")

        assert calls[0]["hkfcode"] == "1001767344"

    def test_six_digit_code_still_maps(self, hk_pages, monkeypatch):
        """6 位内地销售代码的映射能力保留（当前库内无此类产品，但不是死代码）。"""
        monkeypatch.setattr(
            akshare_client, "_ensure_hk_code_map", lambda _ak: {"968050": HK}
        )
        calls = hk_pages([[_hk_row("2026-09-14", 10.55)]])

        akshare_client.get_fund_hk_mutual("968050")

        assert calls[0]["hkfcode"] == HK

    def test_unmappable_six_digit_code_raises(self, monkeypatch):
        monkeypatch.setattr(akshare_client, "_ensure_hk_code_map", lambda _ak: {})

        with pytest.raises(AkshareAPIError, match="未找到 6 位代码 123456"):
            akshare_client.get_fund_hk_mutual("123456")
