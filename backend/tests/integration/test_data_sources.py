# ============================================================================
# 集成测试：数据源配置 (test_data_sources.py)
# ============================================================================
# 显式 null 收口（#579 无悔子集，与 #573 同口径）：api_key / is_enabled 显式
# null 此前被静默跳过并谎报「配置已更新」（零写入，akshare 还把 is_enabled: null
# 回显在 200 响应里），修后一律 422 INVALID_PARAM；无写入路径（未提供 / 空串）
# 的 message 如实报「未变更」，响应 is_enabled 如实回读 env 现值而非回显 None。
# .env 是真实文件：_update_env_file 经 monkeypatch 换成调用记录，env 键先在
# fixture 里登记以便 teardown 还原原值——测试全程不触碰真实环境。
# ============================================================================

import pytest

TUSHARE_URL = "/api/system/data-sources/tushare"
AKSHARE_URL = "/api/system/data-sources/akshare"


@pytest.fixture
def env_writer(monkeypatch):
    """替换 .env 写入为调用记录，并预登记两个 env 键（teardown 还原原值）"""
    calls: list = []
    monkeypatch.setattr(
        "app.routers.data_sources._update_env_file",
        lambda env_file, key, value: calls.append((env_file, key, value)),
    )
    monkeypatch.setenv("TUSHARE_TOKEN", "old-token")
    monkeypatch.setenv("AKSHARE_ENABLED", "true")
    return calls


class TestDataSourceNullGuard:
    """显式 null → 422，拒绝即零写入"""

    def test_tushare_explicit_null_api_key_rejected(self, client, admin_headers, env_writer):
        resp = client.put(TUSHARE_URL, json={"api_key": None}, headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        assert env_writer == []

    def test_akshare_explicit_null_is_enabled_rejected(self, client, admin_headers, env_writer):
        resp = client.put(AKSHARE_URL, json={"is_enabled": None}, headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        assert env_writer == []

    def test_unknown_source_404_precedes_guard(self, client, admin_headers, env_writer):
        """404 专用码先于通用收口（#576 评审 🟡3 教训：INVALID_PARAM 不抢占专用码）"""
        resp = client.put(
            "/api/system/data-sources/unknown",
            json={"api_key": None},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert env_writer == []

    def test_viewer_cannot_update(self, client, viewer_headers, env_writer):
        resp = client.put(TUSHARE_URL, json={"api_key": "x"}, headers=viewer_headers)
        assert resp.status_code == 403
        assert env_writer == []


class TestDataSourceHonestMessage:
    """无写入路径如实报「未变更」；写入路径行为不变"""

    def test_tushare_empty_body_reports_no_change(self, client, admin_headers, env_writer):
        resp = client.put(TUSHARE_URL, json={}, headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "未变更" in body["message"]
        assert env_writer == []
        # 如实回读 env 现值（old-token 仍生效），不再回显 None
        assert body["is_enabled"] is True

    def test_tushare_empty_string_reports_no_change(self, client, admin_headers, env_writer):
        """空串维持既有跳过行为（清空 token 不是已支持能力），但不再谎报「已更新」"""
        resp = client.put(TUSHARE_URL, json={"api_key": ""}, headers=admin_headers)
        assert resp.status_code == 200
        assert "未变更" in resp.json()["message"]
        assert env_writer == []

    def test_tushare_writes_token(self, client, admin_headers, env_writer):
        resp = client.put(TUSHARE_URL, json={"api_key": "new-token"}, headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "Tushare 配置已更新"
        assert body["is_enabled"] is True
        assert len(env_writer) == 1
        _env_file, key, value = env_writer[0]
        assert (key, value) == ("TUSHARE_TOKEN", "new-token")

    def test_akshare_writes_false(self, client, admin_headers, env_writer):
        """is_enabled=False 是正常值（falsy 但非 null），必须写入"""
        resp = client.put(AKSHARE_URL, json={"is_enabled": False}, headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "AkShare 配置已更新"
        assert body["is_enabled"] is False
        assert len(env_writer) == 1
        _env_file, key, value = env_writer[0]
        assert (key, value) == ("AKSHARE_ENABLED", "false")

    def test_akshare_empty_body_reports_no_change(self, client, admin_headers, env_writer):
        resp = client.put(AKSHARE_URL, json={}, headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "未变更" in body["message"]
        assert env_writer == []
        assert body["is_enabled"] is True
