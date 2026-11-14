"""连通检测单点单测（issue #880，SPEC v1.1 V3）：上游全 mock，CI 不跑真实网络。"""
from datetime import date
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
        probe.probe_data_source("000001.OF", "CN_OTC", "foo")
    assert e.value.code == "INVALID_DATA_SOURCE"


def test_tushare_no_token_rejected_before_fetch(monkeypatch):
    def _no_token():
        raise TushareNotConfiguredError("Tushare token 未配置")

    monkeypatch.setattr("app.services.tushare_client._get_tushare_pro", _no_token)
    fetch = MagicMock(side_effect=AssertionError("无 token 不应发起上游取数"))
    monkeypatch.setattr(probe, "_fetch_tushare_values", fetch)
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source("000001.OF", "CN_OTC", "tushare")
    assert e.value.code == "INVALID_DATA_SOURCE"
    assert e.value.details["reason"] == "token_missing"
    fetch.assert_not_called()


def test_tushare_otc_success(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [1.0, 1.01])
    assert probe.probe_data_source("000001.OF", "CN_OTC", "tushare") is None


def test_tushare_exchange_success(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [4.083])
    assert probe.probe_data_source("510300.SH", "CN_EXCHANGE", "tushare") is None


def test_tushare_no_data(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [])
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source("000001.OF", "CN_OTC", "tushare")
    assert e.value.code == "DATA_SOURCE_PROBE_FAILED"
    assert e.value.details["reason"] == "no_data"


def test_tushare_unusable_values_no_data(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(probe, "_fetch_tushare_values", lambda *a: [0.0, None, -1.0])
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source("000001.OF", "CN_OTC", "tushare")
    assert e.value.details["reason"] == "no_data"


def test_tushare_transient_retry_then_success(monkeypatch):
    _token_ok(monkeypatch)
    fetch = MagicMock(side_effect=[ConnectionError("reset"), [1.0]])
    monkeypatch.setattr(probe, "_fetch_tushare_values", fetch)
    assert probe.probe_data_source("000001.OF", "CN_OTC", "tushare") is None
    assert fetch.call_count == 2


def test_tushare_unreachable_after_retries(monkeypatch):
    _token_ok(monkeypatch)
    fetch = MagicMock(side_effect=ConnectionError("connection refused"))
    monkeypatch.setattr(probe, "_fetch_tushare_values", fetch)
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source("000001.OF", "CN_OTC", "tushare")
    assert e.value.code == "DATA_SOURCE_PROBE_FAILED"
    assert e.value.details["reason"] == "unreachable"
    assert fetch.call_count == probe.PROBE_MAX_ATTEMPTS


def test_tushare_auth_error_classified(monkeypatch):
    _token_ok(monkeypatch)
    monkeypatch.setattr(
        probe, "_fetch_tushare_values", MagicMock(side_effect=Exception("token 权限不足"))
    )
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source("000001.OF", "CN_OTC", "tushare")
    assert e.value.details["reason"] == "auth_error"


def test_akshare_otc_success(monkeypatch):
    monkeypatch.setattr(probe, "_fetch_akshare_values", lambda *a: [0.988])
    assert probe.probe_data_source("000001", "CN_OTC", "akshare") is None


def test_akshare_hk_success(monkeypatch):
    monkeypatch.setattr(probe, "_fetch_akshare_values", lambda *a: [1.0])
    assert probe.probe_data_source("9680500001", "HK_MUTUAL", "akshare") is None


def test_akshare_no_data(monkeypatch):
    monkeypatch.setattr(probe, "_fetch_akshare_values", lambda *a: [])
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source("000001", "CN_OTC", "akshare")
    assert e.value.details["reason"] == "no_data"


def test_akshare_unreachable_retries(monkeypatch):
    fetch = MagicMock(side_effect=ConnectionError("reset"))
    monkeypatch.setattr(probe, "_fetch_akshare_values", fetch)
    with pytest.raises(BusinessError) as e:
        probe.probe_data_source("000001", "CN_OTC", "akshare")
    assert e.value.details["reason"] == "unreachable"
    assert fetch.call_count == probe.PROBE_MAX_ATTEMPTS
