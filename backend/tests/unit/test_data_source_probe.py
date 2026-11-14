"""连通检测单点单测（issue #880，SPEC v1.1 V3）：上游全 mock，CI 不跑真实网络。"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from app.services import data_source_probe as probe
from app.services.exceptions import BusinessError
from app.services.tushare_client import TushareNotConfiguredError


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """去真实 sleep / 业务日 / settings 依赖。"""
    monkeypatch.setattr(probe.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(probe, "business_today", lambda: date(2026, 11, 13))
    # S4：日历回看 helper 默认 mock（单测不连库）；长假反例自行覆写
    monkeypatch.setattr(
        probe, "try_get_prev_trading_day",
        lambda db, d, days=1, exchange="SSE": d - timedelta(days=14),
    )

    class _Settings:
        tushare_rate_interval = 0.0
        akshare_rate_interval = 0.0

    monkeypatch.setattr(probe, "get_settings", lambda: _Settings())


def _token_ok(monkeypatch):
    monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", lambda: object())


def test_budget_params_pinned():
    # 探测预算独立于同步重试（同步 3 次 + 10/30/60s 退避）；钉住参数防回退
    assert probe.PROBE_MAX_ATTEMPTS == 2
    assert probe.PROBE_RETRY_DELAY_SECONDS == 1.0


def test_invalid_source_rejected():
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001.OF", "CN_OTC", "foo")
    assert e.value.code == "INVALID_DATA_SOURCE"


def test_tushare_no_token_rejected_before_fetch(monkeypatch):
    def _no_token():
        raise TushareNotConfiguredError("Tushare token 未配置")

    monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", _no_token)
    fetch = MagicMock(side_effect=AssertionError("无 token 不应发起上游取数"))
    monkeypatch.setattr(probe, "_fetch_tushare_values", fetch)
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare")
    assert e.value.code == "INVALID_DATA_SOURCE"
    assert e.value.details["reason"] == "token_missing"
    fetch.assert_not_called()


def test_tushare_otc_success(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [1.0, 1.01])
    assert probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare") is None


def test_tushare_exchange_success(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [4.083])
    assert probe.probe_data_source(None, "510300.SH", "CN_EXCHANGE", "tushare") is None


def test_tushare_no_data(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [])
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare")
    assert e.value.code == "DATA_SOURCE_PROBE_FAILED"
    assert e.value.details["reason"] == "no_data"


def test_tushare_unusable_values_no_data(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [0.0, None, -1.0])
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare")
    assert e.value.details["reason"] == "no_data"


def test_tushare_transient_retry_then_success(monkeypatch):
    _token_ok(monkeypatch)
    fetch = MagicMock(side_effect=[ConnectionError("reset"), [1.0]])
    monkeypatch.setattr(probe, "_fetch_tushare_values", fetch)
    assert probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare") is None
    assert fetch.call_count == 2


def test_tushare_unreachable_after_retries(monkeypatch):
    _token_ok(monkeypatch)
    fetch = MagicMock(side_effect=ConnectionError("connection refused"))
    monkeypatch.setattr(probe, "_fetch_tushare_values", fetch)
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare")
    assert e.value.code == "DATA_SOURCE_PROBE_FAILED"
    assert e.value.details["reason"] == "unreachable"
    assert fetch.call_count == probe.PROBE_MAX_ATTEMPTS


def test_tushare_auth_error_classified(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(
        probe, "_fetch_tushare_values", MagicMock(side_effect=Exception("token 权限不足"))
    )
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare")
    assert e.value.details["reason"] == "auth_error"


def test_akshare_otc_success(monkeypatch):
    monkeypatch.setattr(probe, "_fetch_akshare_values", lambda *a: [0.988])
    assert probe.probe_data_source(None, "000001", "CN_OTC", "akshare") is None


def test_akshare_hk_success(monkeypatch):
    monkeypatch.setattr(probe, "_fetch_akshare_values", lambda *a: [1.0])
    assert probe.probe_data_source(None, "9680500001", "HK_MUTUAL", "akshare") is None


def test_akshare_no_data(monkeypatch):
    monkeypatch.setattr(probe, "_fetch_akshare_values", lambda *a: [])
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001", "CN_OTC", "akshare")
    assert e.value.details["reason"] == "no_data"


def test_akshare_unreachable_retries(monkeypatch):
    fetch = MagicMock(side_effect=ConnectionError("reset"))
    monkeypatch.setattr(probe, "_fetch_akshare_values", fetch)
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source(None, "000001", "CN_OTC", "akshare")
    assert e.value.details["reason"] == "unreachable"
    assert fetch.call_count == probe.PROBE_MAX_ATTEMPTS


class TestFetchFunctions:
    """直调 _fetch_tushare_values / _fetch_akshare_values（L2 CI 红① diff coverage 缺口）。

    stub 上游（假 pro / monkeypatch akshare 与 akshare_client HK helper），不跑真实网络；
    覆盖 CN_OTC / CN_EXCHANGE / HK_MUTUAL / 空 df / None df / 非法 market 各分支。
    """

    def test_tushare_otc_returns_unit_nav(self, monkeypatch):
        import pandas as pd

        df = pd.DataFrame({"unit_nav": [1.0, 1.01]})

        class Pro:
            def fund_nav(self, **kw):
                return df

        monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", lambda: Pro())
        assert probe._fetch_tushare_values("CN_OTC", "000001.OF", "20261101", "20261113") == [1.0, 1.01]

    def test_tushare_exchange_returns_close(self, monkeypatch):
        import pandas as pd

        df = pd.DataFrame({"close": [4.083]})

        class Pro:
            def fund_daily(self, **kw):
                return df

        monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", lambda: Pro())
        assert probe._fetch_tushare_values("CN_EXCHANGE", "510300.SH", "20261101", "20261113") == [4.083]

    def test_tushare_empty_df_returns_empty(self, monkeypatch):
        import pandas as pd

        class Pro:
            def fund_nav(self, **kw):
                return pd.DataFrame()

        monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", lambda: Pro())
        assert probe._fetch_tushare_values("CN_OTC", "000001.OF", "20261101", "20261113") == []

    def test_tushare_none_df_returns_empty(self, monkeypatch):
        class Pro:
            def fund_nav(self, **kw):
                return None

        monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", lambda: Pro())
        assert probe._fetch_tushare_values("CN_OTC", "000001.OF", "20261101", "20261113") == []

    def test_tushare_invalid_market_raises(self, monkeypatch):
        class Pro:
            pass

        monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", lambda: Pro())
        with pytest.raises(ValueError):
            probe._fetch_tushare_values("HK_MUTUAL", "1001767344", "20261101", "20261113")

    def test_akshare_otc_returns_unit_nav(self, monkeypatch):
        import akshare as ak
        import pandas as pd

        df = pd.DataFrame({"净值日期": ["2026-11-12"], "单位净值": [0.988]})
        monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kw: df)
        assert probe._fetch_akshare_values("CN_OTC", "000001", "20261101", "20261113") == [0.988]

    def test_akshare_otc_out_of_window_nav_rejected(self, monkeypatch):
        """Blocker 反例：仅窗外（2026-09-28）有正净值 → no_data 拒绝，不得因历史净值放行。"""
        import akshare as ak
        import pandas as pd

        monkeypatch.setattr(
            probe, "try_get_prev_trading_day",
            lambda db, d, days=1, exchange="SSE": date(2026, 11, 1),
        )
        df = pd.DataFrame({"净值日期": ["2026-09-28"], "单位净值": [1.0]})
        monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kw: df)
        with pytest.raises(BusinessError) as e:
            probe.probe_data_source(None, "000001.OF", "CN_OTC", "akshare")
        assert e.value.code == "DATA_SOURCE_PROBE_FAILED"
        assert e.value.details["reason"] == "no_data"

    def test_akshare_otc_in_window_nav_passes(self, monkeypatch):
        import akshare as ak
        import pandas as pd

        monkeypatch.setattr(
            probe, "try_get_prev_trading_day",
            lambda db, d, days=1, exchange="SSE": date(2026, 11, 1),
        )
        df = pd.DataFrame({"净值日期": ["2026-11-12"], "单位净值": [1.0]})
        monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kw: df)
        assert probe.probe_data_source(None, "000001.OF", "CN_OTC", "akshare") is None

    def test_akshare_otc_missing_date_column_rejected(self, monkeypatch):
        """净值日期列缺失 → 保守拒绝（no_data），不得默认通过（无法确认落在窗口内）。"""
        import akshare as ak
        import pandas as pd

        df = pd.DataFrame({"单位净值": [1.0]})
        monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kw: df)
        with pytest.raises(BusinessError) as e:
            probe.probe_data_source(None, "000001.OF", "CN_OTC", "akshare")
        assert e.value.details["reason"] == "no_data"

    def test_akshare_otc_unparseable_date_rejected(self, monkeypatch):
        """净值日期整列不可解析 → 保守拒绝（no_data），不得默认通过。"""
        import akshare as ak
        import pandas as pd

        df = pd.DataFrame({"净值日期": ["not-a-date"], "单位净值": [1.0]})
        monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kw: df)
        with pytest.raises(BusinessError) as e:
            probe.probe_data_source(None, "000001.OF", "CN_OTC", "akshare")
        assert e.value.details["reason"] == "no_data"

    def test_akshare_exchange_out_of_window_rejected(self, monkeypatch):
        """场内：上游即使忽略日期参数返回窗外行，本地二次过滤仍拒绝（窗口口径不依赖上游）。"""
        import akshare as ak
        import pandas as pd

        df = pd.DataFrame({"日期": ["2026-09-28"], "收盘": [4.0]})
        monkeypatch.setattr(ak, "fund_etf_hist_em", lambda **kw: df)
        with pytest.raises(BusinessError) as e:
            probe.probe_data_source(None, "510300", "CN_EXCHANGE", "akshare")
        assert e.value.details["reason"] == "no_data"

    def test_akshare_hk_out_of_window_rejected(self, monkeypatch):
        """港互认：上游即使返回窗外行，本地按 PDATE 二次过滤仍拒绝。"""
        monkeypatch.setattr("app.services.akshare_client._resolve_hk10", lambda code: "1001767344")
        monkeypatch.setattr(
            "app.services.akshare_client._hk_nav_request_page",
            lambda *a, **kw: {"Data": [{"PDATE": "2026-09-28", "NAV": 1.0}]},
        )
        with pytest.raises(BusinessError) as e:
            probe.probe_data_source(None, "1001767344", "HK_MUTUAL", "akshare")
        assert e.value.details["reason"] == "no_data"

    def test_akshare_exchange_returns_close(self, monkeypatch):
        import akshare as ak
        import pandas as pd

        df = pd.DataFrame({"日期": ["2026-11-12"], "收盘": [4.083]})
        monkeypatch.setattr(ak, "fund_etf_hist_em", lambda **kw: df)
        assert probe._fetch_akshare_values("CN_EXCHANGE", "510300", "20261101", "20261113") == [4.083]

    def test_akshare_hk_returns_nav(self, monkeypatch):
        monkeypatch.setattr("app.services.akshare_client._resolve_hk10", lambda code: "1001767344")
        monkeypatch.setattr(
            "app.services.akshare_client._hk_nav_request_page",
            lambda *a, **kw: {"Data": [{"PDATE": "2026-11-12", "NAV": 1.0}]},
        )
        assert probe._fetch_akshare_values("HK_MUTUAL", "1001767344", "20261101", "20261113") == [1.0]

    def test_akshare_empty_df_returns_empty(self, monkeypatch):
        import akshare as ak
        import pandas as pd

        monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **kw: pd.DataFrame())
        assert probe._fetch_akshare_values("CN_OTC", "000001", "20261101", "20261113") == []

    def test_akshare_exchange_empty_df_returns_empty(self, monkeypatch):
        import akshare as ak
        import pandas as pd

        monkeypatch.setattr(ak, "fund_etf_hist_em", lambda **kw: pd.DataFrame())
        assert probe._fetch_akshare_values("CN_EXCHANGE", "510300", "20261101", "20261113") == []

    def test_probe_window_trading_calendar_long_holiday(self, monkeypatch):
        """S4 反例：长假后首日自然日窗（10-09−10d=09-29）覆盖不到假期前净值（09-28）→
        旧自然日口径 no_data；交易日历窗（最近 10 开市日=09-28）覆盖→通过。
        有效性自证：临时把 probe 窗改回自然日，本用例须红；恢复后绿。"""
        import pandas as pd

        holiday_first = date(2026, 10, 9)
        monkeypatch.setattr(probe, "business_today", lambda: holiday_first)
        cal_start = date(2026, 9, 28)
        monkeypatch.setattr(
            probe, "try_get_prev_trading_day",
            lambda db, d, days=1, exchange="SSE": cal_start,
        )

        class Pro:
            def fund_nav(self, ts_code, start_date, end_date):
                nav = "20260928"
                if start_date <= nav <= end_date:
                    return pd.DataFrame({"unit_nav": [1.0]})
                return pd.DataFrame()

        monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", lambda: Pro())
        assert probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare") is None

    def test_unreachable_details_no_upstream_raw(self, monkeypatch):
        """F3：探测失败 422 的 details 不含上游原文（error 键/主机名），原文仅进日志。"""
        _token_ok(monkeypatch)
        monkeypatch.setattr(
            probe, "_fetch_tushare_values",
            MagicMock(side_effect=Exception("connect to secret-host.example.com failed")),
        )
        with pytest.raises(BusinessError) as e:
            probe.probe_data_source(None, "000001.OF", "CN_OTC", "tushare")
        assert e.value.code == "DATA_SOURCE_PROBE_FAILED"
        assert "error" not in e.value.details
        assert "secret-host" not in str(e.value.details)

    def test_akshare_invalid_market_raises(self, monkeypatch):
        with pytest.raises(ValueError):
            probe._fetch_akshare_values("UNKNOWN", "000001", "20261101", "20261113")
