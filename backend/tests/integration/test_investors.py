# ============================================================================
# 集成测试：投资人管理 (test_investors.py)
# ============================================================================

import pytest
from tests.factories import create_investor
from app.models.investor import Investor


class TestInvestorCRUD:
    """投资人 CRUD API 测试"""

    def test_create_investor(self, client, admin_headers, test_db):
        """admin 创建投资人"""
        resp = client.post(
            "/api/investors",
            json={"code": "NEW_INV1", "name": "新投资人", "password": "inv_pass123"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "NEW_INV1"
        assert data["name"] == "新投资人"
        assert data["role"] == "viewer"

    def test_create_duplicate_investor_fails(self, client, admin_headers, test_db):
        """创建重复投资人应失败"""
        create_investor(test_db, code="DUP_INV")
        resp = client.post(
            "/api/investors",
            json={"code": "DUP_INV", "name": "重复", "password": "pass"},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_list_investors(self, client, admin_headers, test_db):
        """获取投资人列表（issue #487：响应须经分页响应模型收窄，不得泄漏 password_hash）"""
        resp = client.get("/api/investors", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"items", "total", "page", "page_size"}
        assert data["items"], "种子数据应至少有一个投资人（ADMIN）"
        item = data["items"][0]
        assert set(item.keys()) == {
            "code",
            "name",
            "role",
            "phone",
            "email",
            "last_login_at",
            "created_at",
            "updated_at",
        }
        # 兜底：任意嵌套层（含 future 新增包装）都不得出现凭据字段
        assert "password_hash" not in resp.text

    def test_get_investor_detail(self, client, admin_headers, test_db):
        """获取单个投资人详情"""
        create_investor(test_db, code="DETAIL_INV", name="详情投资人")
        resp = client.get("/api/investors/DETAIL_INV", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["code"] == "DETAIL_INV"

    def test_get_nonexistent_investor_404(self, client, admin_headers):
        """获取不存在的投资人应返回 404"""
        resp = client.get("/api/investors/NO_SUCH_CODE", headers=admin_headers)
        assert resp.status_code == 404

    def test_update_investor(self, client, admin_headers, test_db):
        """更新投资人信息"""
        create_investor(test_db, code="UPD_INV", name="旧名称")
        resp = client.put(
            "/api/investors/UPD_INV",
            json={"name": "新名称"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名称"

    def test_update_explicit_null_rejected(self, client, admin_headers, test_db):
        """显式 null 收口（#573）：role 落 NULL 会让 InvestorResponse 校验 500 且此后
        该行 GET 恒 500（computed 先于响应序列化 commit，NULL 持久化不可自愈）"""
        create_investor(test_db, code="UPD_NULL", name="原名称", role="viewer")
        resp = client.put(
            "/api/investors/UPD_NULL",
            json={"role": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        # 拒绝即零写入：role 保持原值，且该行仍可读（修复前一次 PUT 即持久 500）
        test_db.expire_all()
        row = test_db.query(Investor).filter(Investor.code == "UPD_NULL").first()
        assert row.role == "viewer"
        assert client.get("/api/investors/UPD_NULL", headers=admin_headers).status_code == 200

    def test_update_nullable_fields_null_clears(self, client, admin_headers, test_db):
        """phone/email 是 allow 例外：列可空且响应 Optional，显式 null = 清除（#573）"""
        create_investor(test_db, code="UPD_CLEAR", name="清除测试")
        investor = test_db.query(Investor).filter(Investor.code == "UPD_CLEAR").first()
        investor.phone, investor.email = "13800000000", "old@example.com"
        test_db.commit()

        resp = client.put(
            "/api/investors/UPD_CLEAR",
            json={"phone": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        # null 的字段清除，未传的字段不动（不传 = 不动）
        assert resp.json()["phone"] is None
        assert resp.json()["email"] == "old@example.com"
        assert resp.json()["name"] == "清除测试"

    def test_viewer_cannot_create_investor(self, client, viewer_headers):
        """viewer 不能创建投资人"""
        resp = client.post(
            "/api/investors",
            json={"code": "V_INV", "name": "X", "password": "pass"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_password_is_hashed(self, client, admin_headers, test_db):
        """创建投资人时密码应被 bcrypt 哈希存储"""
        client.post(
            "/api/investors",
            json={"code": "HASH_INV", "name": "哈希测试", "password": "plain_text"},
            headers=admin_headers,
        )
        investor = test_db.query(Investor).filter(Investor.code == "HASH_INV").first()
        assert investor is not None
        assert investor.password_hash != "plain_text"
        assert investor.password_hash.startswith("$2b$")
