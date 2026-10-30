# ============================================================================
# 单元测试：安全模块 (test_security.py)
# ============================================================================
# 覆盖 app/utils/security.py 中的核心函数：
# - 密码哈希与验证
# - JWT Token 生成与解码
# - 持久化会话撤销
# - 登录失败追踪与账户锁定
# ============================================================================

import time
from datetime import timedelta
from unittest.mock import patch
import pytest


class TestPasswordHashing:
    """密码哈希与验证测试"""

    def test_hash_and_verify_correct_password(self):
        """正确密码验证应返回 True"""
        from app.utils.security import get_password_hash, verify_password
        password = "MySecureP@ss123"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_verify_wrong_password(self):
        """错误密码验证应返回 False"""
        from app.utils.security import get_password_hash, verify_password
        hashed = get_password_hash("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_hash_produces_different_salts(self):
        """同一密码每次哈希应产生不同的结果（随机 salt）"""
        from app.utils.security import get_password_hash
        hash1 = get_password_hash("same_password")
        hash2 = get_password_hash("same_password")
        assert hash1 != hash2

    def test_hash_uses_bcrypt(self):
        """哈希结果应以 bcrypt 前缀开头"""
        from app.utils.security import get_password_hash
        hashed = get_password_hash("test")
        assert hashed.startswith("$2b$")

    def test_empty_password(self):
        """空密码应能正常哈希和验证"""
        from app.utils.security import get_password_hash, verify_password
        hashed = get_password_hash("")
        assert verify_password("", hashed) is True
        assert verify_password("notempty", hashed) is False


class TestJWTToken:
    """JWT Token 生成与解码测试"""

    def test_create_and_decode_token(self):
        """生成的 Token 应能正确解码出 payload"""
        from app.utils.security import create_access_token, decode_token
        data = {"sub": "ADMIN", "role": "admin"}
        token = create_access_token(data)
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "ADMIN"
        assert payload["role"] == "admin"

    def test_token_contains_expiry(self):
        """Token payload 应包含 exp 过期时间"""
        from app.utils.security import create_access_token, decode_token
        token = create_access_token({"sub": "TEST"})
        payload = decode_token(token)
        assert "exp" in payload

    def test_decode_invalid_token(self):
        """无效 Token 应返回 None"""
        from app.utils.security import decode_token
        assert decode_token("invalid.token.here") is None

    def test_decode_tampered_token(self):
        """篡改后的 Token 应解码失败（签名不匹配）"""
        from app.utils.security import create_access_token, decode_token
        token = create_access_token({"sub": "ADMIN"})
        # 篡改 payload 部分
        tampered = token[:-5] + "XXXXX"
        assert decode_token(tampered) is None

    def test_token_with_custom_expiry(self):
        """使用自定义过期时间的 Token"""
        from app.utils.security import create_access_token, decode_token
        token = create_access_token(
            {"sub": "TEST"},
            expires_delta=timedelta(hours=1),
        )
        payload = decode_token(token)
        assert payload is not None
        assert "exp" in payload

    def test_expired_token_returns_none(self):
        """过期的 Token 解码应返回 None"""
        from app.utils.security import create_access_token, decode_token
        token = create_access_token(
            {"sub": "TEST"},
            expires_delta=timedelta(seconds=-1),  # 已过期
        )
        assert decode_token(token) is None


class TestSessionRevocation:
    def test_revoke_persists_across_sessions(self, test_db):
        from sqlalchemy.orm import Session
        from app.models.investor import Investor
        from app.services.auth_service import create_session, authenticate_session, revoke_session
        from app.services.exceptions import BusinessError

        token = create_session(test_db, test_db.get(Investor, "ADMIN"))["token"]
        session, _ = authenticate_session(test_db, token)
        revoke_session(test_db, session.id)
        test_db.flush()
        with Session(bind=test_db.connection()) as other:
            with pytest.raises(BusinessError) as exc:
                authenticate_session(other, token)
            assert exc.value.http_status == 401

    def test_revoke_does_not_affect_other_session(self, test_db):
        from app.models.investor import Investor
        from app.services.auth_service import create_session, authenticate_session, revoke_session

        investor = test_db.get(Investor, "ADMIN")
        first = create_session(test_db, investor)["token"]
        second = create_session(test_db, investor)["token"]
        assert first != second
        session, _ = authenticate_session(test_db, first)
        revoke_session(test_db, session.id)
        assert authenticate_session(test_db, second)[0].revoked_at is None


class TestLoginFailureTracking:
    """登录失败追踪与账户锁定测试"""

    def setup_method(self):
        """每个测试方法开始前清空失败追踪器"""
        from app.utils.security import login_failure_tracker
        login_failure_tracker.clear()

    def test_first_failure_not_locked(self):
        """第一次失败不应锁定"""
        from app.utils.security import record_login_failure, is_account_locked
        is_locked, _ = record_login_failure("LOCK_TEST_1")
        assert is_locked is False
        locked, _ = is_account_locked("LOCK_TEST_1")
        assert locked is False

    def test_four_failures_not_locked(self):
        """连续 4 次失败不应锁定（阈值为 5）"""
        from app.utils.security import record_login_failure
        for _ in range(4):
            is_locked, _ = record_login_failure("LOCK_TEST_4")
            assert is_locked is False

    def test_five_failures_triggers_lock(self):
        """连续 5 次失败应触发锁定"""
        from app.utils.security import record_login_failure, is_account_locked
        for i in range(4):
            record_login_failure("LOCK_TEST_5")
        is_locked, locked_until = record_login_failure("LOCK_TEST_5")
        assert is_locked is True
        assert locked_until is not None
        # 账户应该被锁定
        locked, _ = is_account_locked("LOCK_TEST_5")
        assert locked is True

    def test_clear_login_failure(self):
        """登录成功后应清除失败计数"""
        from app.utils.security import (
            record_login_failure, clear_login_failure, is_account_locked, login_failure_tracker
        )
        # 累积 3 次失败
        for _ in range(3):
            record_login_failure("CLEAR_TEST")
        assert "CLEAR_TEST" in login_failure_tracker

        # 登录成功，清除
        clear_login_failure("CLEAR_TEST")
        locked, _ = is_account_locked("CLEAR_TEST")
        assert locked is False

    def test_clear_nonexistent_user_no_error(self):
        """清除不存在的用户不应报错"""
        from app.utils.security import clear_login_failure
        clear_login_failure("NONEXISTENT_USER")  # 应无异常

    def test_different_users_tracked_independently(self):
        """不同用户的失败计数应独立追踪"""
        from app.utils.security import record_login_failure, is_account_locked
        # 用户 A 失败 5 次（锁定）
        for _ in range(5):
            record_login_failure("USER_A")
        # 用户 B 失败 2 次（未锁定）
        record_login_failure("USER_B")
        record_login_failure("USER_B")

        locked_a, _ = is_account_locked("USER_A")
        locked_b, _ = is_account_locked("USER_B")
        assert locked_a is True
        assert locked_b is False
