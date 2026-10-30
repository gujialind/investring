# ============================================================================
# 集成测试：认证模块 (test_auth.py)
# ============================================================================
# 覆盖 POST /api/auth/login, POST /api/auth/logout, PUT /api/auth/password
# ============================================================================

import os
import secrets
import pytest
from datetime import datetime, timedelta
from unittest import mock

from app.services.auth_service import create_session
from app.utils.security import create_access_token
from tests.factories import create_investor


class TestLogin:
    """登录 API 测试"""

    def test_login_success(self, client, test_db):
        """正确凭据登录应返回 token 和用户信息"""
        create_investor(test_db, code="LOGIN_OK", password="pass123")
        resp = client.post("/api/auth/login", json={"code": "LOGIN_OK", "password": "pass123"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["code"] == "LOGIN_OK"

    def test_login_wrong_password(self, client, test_db):
        """错误密码应返回 401"""
        create_investor(test_db, code="LOGIN_BAD", password="correct")
        resp = client.post("/api/auth/login", json={"code": "LOGIN_BAD", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client, test_db):
        """不存在的用户应返回 401"""
        resp = client.post("/api/auth/login", json={"code": "GHOST", "password": "pass"})
        assert resp.status_code == 401

    def test_login_account_locked_after_5_failures(self, client, test_db):
        """连续 5 次失败应锁定账户"""
        create_investor(test_db, code="LOCK_ME", password="right_pass")
        # 清空之前的失败记录
        from app.utils.security import login_failure_tracker
        login_failure_tracker.pop("LOCK_ME", None)

        for i in range(5):
            client.post("/api/auth/login", json={"code": "LOCK_ME", "password": "wrong"})

        # 第 6 次应该被锁定（403）
        resp = client.post("/api/auth/login", json={"code": "LOCK_ME", "password": "right_pass"})
        assert resp.status_code == 403
        assert "ACCOUNT_LOCKED" in str(resp.json().get("detail", ""))

        # 清理
        login_failure_tracker.pop("LOCK_ME", None)


class TestLogout:
    """登出 API 测试"""

    def test_logout_success(self, client, admin_headers):
        """登出应返回成功"""
        resp = client.post("/api/auth/logout", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "登出成功"

    def test_logout_without_token(self, client):
        """无 Token 登出应返回 401"""
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 401


class TestChangePassword:
    """修改密码 API 测试"""

    def test_change_own_password_with_old(self, client, test_db):
        """用户修改自己密码需提供旧密码"""
        investor = create_investor(test_db, code="CHG_PW", password="old_pass")
        token = create_session(test_db, investor)["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.put(
            "/api/auth/password",
            json={"old_password": "old_pass", "new_password": "new_pass"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "密码修改成功" in resp.json()["message"]

    def test_change_own_password_without_old_fails(self, client, test_db):
        """不传旧密码应被拒绝"""
        investor = create_investor(test_db, code="CHG_NO_OLD", password="old_pass")
        token = create_session(test_db, investor)["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.put(
            "/api/auth/password",
            json={"new_password": "new_pass"},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_admin_change_other_password(self, client, test_db, admin_headers):
        """admin 可修改其他用户密码（无需旧密码）"""
        create_investor(test_db, code="TARGET_U", password="original")
        resp = client.put(
            "/api/auth/password",
            json={"target_code": "TARGET_U", "new_password": "admin_set"},
            headers=admin_headers,
        )
        assert resp.status_code == 200

    def test_viewer_cannot_change_others_password(self, client, test_db, viewer_headers):
        """viewer 不能修改其他人密码"""
        create_investor(test_db, code="OTHER_U", password="pass")
        resp = client.put(
            "/api/auth/password",
            json={"target_code": "OTHER_U", "new_password": "hacked", "old_password": "pass"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestAccessControl:
    """权限控制测试"""

    def test_unauthenticated_request_401(self, client):
        """未认证请求应返回 401"""
        resp = client.get("/api/portfolios")
        assert resp.status_code == 401

    def test_invalid_token_401(self, client):
        """无效 Token 应返回 401"""
        headers = {"Authorization": "Bearer invalid.token.here"}
        resp = client.get("/api/portfolios", headers=headers)
        assert resp.status_code == 401

    def test_viewer_forbidden_on_admin_endpoint(self, client, viewer_headers):
        """viewer 访问 admin 接口应返回 403"""
        resp = client.post(
            "/api/investors",
            json={"code": "NEW_INV", "name": "New", "password": "pass"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_expired_token_401(self, client):
        """过期 Token 应返回 401"""
        token = create_access_token(
            {"sub": "EXPIRED"}, expires_delta=timedelta(seconds=-10)
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/api/portfolios", headers=headers)
        assert resp.status_code == 401


class TestSecretKeyStartupGuard:
    """issue #255: SECRET_KEY 默认占位值启动拒绝 + 默认串签名 token 失效"""

    def test_default_placeholder_secret_rejected(self):
        """SECRET_KEY 为默认占位值时应拒绝实例化（拒绝启动）"""
        from app.config import Settings, INSECURE_DEFAULT_SECRET_KEY

        with mock.patch.dict(os.environ, {"SECRET_KEY": INSECURE_DEFAULT_SECRET_KEY}):
            with pytest.raises(RuntimeError):
                Settings()

    def test_strong_random_secret_accepted(self):
        """随机强密钥（≥32 字节）应正常实例化"""
        from app.config import Settings

        with mock.patch.dict(os.environ, {"SECRET_KEY": secrets.token_hex(32)}):
            settings = Settings()
        assert len(settings.secret_key) >= 32

    def test_token_signed_with_default_secret_rejected_401(self, client):
        """默认占位串签名的 token（轮换前的旧 token）验签失败 → 401"""
        from jose import jwt
        from app.config import INSECURE_DEFAULT_SECRET_KEY

        token = jwt.encode(
            {"sub": "ADMIN", "role": "admin",
             "exp": datetime.utcnow() + timedelta(days=1)},
            INSECURE_DEFAULT_SECRET_KEY,
            algorithm="HS256",
        )
        resp = client.get(
            "/api/portfolios",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
