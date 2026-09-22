# ============================================================================
# 集成测试：平台管理 (test_platforms.py)
# ============================================================================

import pytest
from tests.factories import create_platform
from app.models.platform import Platform


class TestPlatformCRUD:
    """平台 CRUD API 测试"""

    def test_list_platforms(self, client, admin_headers):
        """获取平台列表"""
        resp = client.get("/api/platforms", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_create_platform(self, client, admin_headers):
        """创建平台"""
        resp = client.post(
            "/api/platforms",
            json={"code": "NEW_PLAT", "name": "新平台", "platform_type": "券商"},
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["code"] == "NEW_PLAT"

    def test_create_duplicate_platform_fails(self, client, admin_headers, test_db):
        """创建重复平台应失败"""
        create_platform(test_db, code="DUP_PLAT")
        resp = client.post(
            "/api/platforms",
            json={"code": "DUP_PLAT", "name": "重复"},
            headers=admin_headers,
        )
        assert resp.status_code in (400, 409)

    def test_get_platform_detail(self, client, admin_headers, test_db):
        """获取平台详情"""
        create_platform(test_db, code="DET_PLAT", name="详情平台")
        resp = client.get("/api/platforms/DET_PLAT", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["code"] == "DET_PLAT"

    def test_viewer_cannot_create_platform(self, client, viewer_headers):
        """viewer 不能创建平台"""
        resp = client.post(
            "/api/platforms",
            json={"code": "V_PLAT", "name": "X"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestPlatformUpdateNullGuard:
    """PUT 显式 null 收口（#579 无悔子集，与 #573 同口径）：
    name 拒绝（NOT NULL 列，修复前显式 null 直落 setattr → IntegrityError 500）；
    platform_type 进 allow（列可空且响应 Optional，null = 清除类型是既有合法路径）"""

    def test_update_platform(self, client, admin_headers, test_db):
        """正常更新基线（本文件此前无 PUT 用例）"""
        create_platform(test_db, code="UPD_OK", name="旧名称")
        resp = client.put(
            "/api/platforms/UPD_OK",
            json={"name": "新名称"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["name"] == "新名称"

    def test_update_explicit_null_name_rejected(self, client, admin_headers, test_db):
        create_platform(test_db, code="UPD_NULL", name="原名称")
        resp = client.put(
            "/api/platforms/UPD_NULL",
            json={"name": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        # 拒绝即零写入：行保持原值且仍可读
        test_db.expire_all()
        row = test_db.query(Platform).filter(Platform.code == "UPD_NULL").first()
        assert row.name == "原名称"
        assert client.get("/api/platforms/UPD_NULL", headers=admin_headers).status_code == 200

    def test_update_null_platform_type_clears(self, client, admin_headers, test_db):
        """allow 例外：显式 null = 清除类型（修复前后行为一致，不得破坏）"""
        create_platform(test_db, code="UPD_CLEAR", name="清除测试", platform_type="券商")
        resp = client.put(
            "/api/platforms/UPD_CLEAR",
            json={"platform_type": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["platform_type"] is None
        # 未传的字段不动（不传 = 不动）
        assert resp.json()["name"] == "清除测试"

    def test_update_mixed_null_rejected_zero_write(self, client, admin_headers, test_db):
        """allow 字段 + 非 allow 字段混合：整体拒绝，allow 侧同样零写入（#576 同口径）"""
        create_platform(test_db, code="UPD_MIX", name="原名称", platform_type="券商")
        resp = client.put(
            "/api/platforms/UPD_MIX",
            json={"name": None, "platform_type": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        test_db.expire_all()
        row = test_db.query(Platform).filter(Platform.code == "UPD_MIX").first()
        assert row.name == "原名称"
        assert row.platform_type == "券商"
