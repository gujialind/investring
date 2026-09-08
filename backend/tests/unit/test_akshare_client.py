# ============================================================================
# 单元测试：akshare 客户端重试日志 (test_akshare_client.py) — issue #404
# ============================================================================
# `_retry` 是三个 fetcher（场外净值 / 场内日线 / 港互认）的唯一漏斗，
# 这里只测它的重试与日志行为，不碰网络。
# ============================================================================

import pytest

from app.services import akshare_client
from app.services.akshare_client import AkshareAPIError, _retry
from tests.conftest import log_lines


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """限流与退避都是真 sleep（默认 1s 起、指数翻倍），测试里没必要等"""
    monkeypatch.setattr(akshare_client, "_rate_limit_sleep", lambda: None)
    monkeypatch.setattr(akshare_client.time, "sleep", lambda seconds: None)


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
