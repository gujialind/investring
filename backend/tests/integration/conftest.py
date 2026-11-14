"""集成测试共享夹具（issue #880）。"""
import pytest


@pytest.fixture(autouse=True)
def _mock_data_source_probe(monkeypatch):
    """#880：连通检测默认 mock 为通过。

    create 必探 / update 换源探会调上游（tushare/akshare），集成测试全程不跑真实网络
    （CI 同口径）。需模拟探测失败/无数据的用例自行 monkeypatch
    app.services.data_source_probe.probe_data_source 覆写本默认。
    探测单测（tests/unit/test_data_source_probe.py）不经本夹具，调真实函数。
    """
    monkeypatch.setattr(
        "app.services.data_source_probe.probe_data_source",
        lambda db, code, market, data_source: None,
    )
