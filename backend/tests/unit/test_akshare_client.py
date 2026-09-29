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
            {"trade_date": "20260915", "unit_price": 10.54, "accumulated_nav": None},
            {"trade_date": "20260914", "unit_price": 10.55, "accumulated_nav": None},
        ]

    def test_result_is_independent_of_key_insertion_order(self, hk_pages):
        """键序再变一次也不影响结果——取数已不依赖位置。"""
        shuffled = [
            {"NAV": 10.55, "PDATE": "2026-09-14", "HKFCODE": HK, "CURRENCY": "元"},
        ]
        hk_pages([shuffled])

        assert akshare_client.get_fund_hk_mutual(HK, "20260914", "20260914") == [
            {"trade_date": "20260914", "unit_price": 10.55, "accumulated_nav": None}
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
    """最小 DataFrame 替身：被测代码只用到 `len()` 与 `iterrows()`。

    刻意不 import pandas——本仓测试此前零处构造 DataFrame，取数层改用 dict 替身后
    也不必为两条 CN 用例破例（#651 的解析改动同样不需要 pandas）。
    """

    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        return enumerate(self._rows)


class TestCnAkshareRowsAreScreened:
    """CN_OTC / CN_EXCHANGE 套用同一行级判据（akshare 的 CN 路径也是位置重命名）。

    差别要讲清：ETF 的 klines 是逗号分隔串、**无键名可取**，列语义由我们自行声明的
    fields2 钉住，键序漂移结构性不可能；且它无数据时显式返回空 DF，那个「空」是设计
    好的合法答复。故两条都只按行校验，绝不把「结果为空」当失败。
    """

    def test_otc_misbound_date_column_raises_instead_of_empty(self, monkeypatch, json_log_capture):
        """CN_OTC 若也发生 #651 那种错位（日期列变成币种），必须响亮失败。"""
        monkeypatch.setattr(
            "akshare.fund_open_fund_info_em",
            lambda **kw: _RowFrame([
                {"净值日期": "元", "单位净值": 342652919208.0},
                {"净值日期": "元", "单位净值": 342652919209.0},
            ]),
        )

        with pytest.raises(AkshareAPIError, match="无一行可解析"):
            akshare_client.get_fund_nav_otc("000051", "20260914", "20260915")

        only_log_line(json_log_capture, level="ERROR", operation="fetch_fund_nav_otc")

    def test_otc_keeps_good_rows_and_drops_bad_nav(self, monkeypatch, json_log_capture):
        monkeypatch.setattr(
            "akshare.fund_open_fund_info_em",
            lambda **kw: _RowFrame([
                {"净值日期": "2026-09-15", "单位净值": 1.234},
                {"净值日期": "2026-09-14", "单位净值": ""},
            ]),
        )

        rows = akshare_client.get_fund_nav_otc("000051", "20260914", "20260915")

        assert rows == [{"trade_date": "20260915", "unit_nav": 1.234, "accum_nav": None}]
        only_log_line(json_log_capture, level="WARNING", operation="fetch_fund_nav_otc")

    def test_otc_accepts_date_objects_from_pandas(self, monkeypatch):
        """akshare 把净值日期转成 date/Timestamp 对象——str() 后是 dashed，须照常接受。"""
        from datetime import date as _date

        monkeypatch.setattr(
            "akshare.fund_open_fund_info_em",
            lambda **kw: _RowFrame([{"净值日期": _date(2026, 9, 14), "单位净值": 1.0}]),
        )

        assert akshare_client.get_fund_nav_otc("000051") == [
            {"trade_date": "20260914", "unit_nav": 1.0, "accum_nav": None}
        ]

    def test_etf_empty_frame_is_benign(self, monkeypatch):
        """akshare 无 k 线时显式返回空 DF——那是设计好的答复，不是故障。"""
        monkeypatch.setattr("akshare.fund_etf_hist_em", lambda **kw: _RowFrame([]))

        assert akshare_client.get_fund_daily_exchange("510300", "20260914", "20260915") == []

    def test_etf_unparseable_dates_raise(self, monkeypatch):
        monkeypatch.setattr(
            "akshare.fund_etf_hist_em",
            lambda **kw: _RowFrame([
                {"日期": "元", "收盘": 4.6, "开盘": 4.5, "涨跌幅": 0.1},
            ]),
        )

        with pytest.raises(AkshareAPIError, match="无一行可解析"):
            akshare_client.get_fund_daily_exchange("510300", "20260914", "20260915")

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
