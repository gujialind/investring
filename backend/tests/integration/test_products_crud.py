# ============ 集成测试：产品管理·CRUD 与确认参数更新（自 test_products.py 拆分，issue #469） ============
# 原「集成测试：产品管理」（test_products.py）的 CRUD / 确认参数更新部分；其余三个文件同源拆分
# （列表筛选 test_products_list_filters.py、维度适用 test_products_dimensions.py、
# 取值校验 test_products_validation.py）。
# 本文件覆盖：
#   - TestProductCRUD：产品列表 / 创建 / 详情 / viewer 权限 / 分页
#   - TestConfirmDaysAndNavLagDays（#228）：confirm_days / nav_lag_days 更新纯显式（is_qdii 不再联动重算），
#     创建时仍保留默认推导器

from tests.factories import create_product, create_asset_classification


class TestProductCRUD:
    """产品 CRUD API 测试"""

    def test_list_products(self, client, admin_headers):
        """获取产品列表"""
        resp = client.get("/api/products", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_create_product(self, client, admin_headers, test_db):
        """创建产品"""
        create_asset_classification(test_db, code="ASSET_STOCK")
        create_asset_classification(test_db, code="REGION_CN", dimension="region")
        create_asset_classification(test_db, code="STYLE_BALANCED", dimension="style")
        create_asset_classification(test_db, code="SIZE_LARGE", dimension="size")
        resp = client.post(
            "/api/products",
            json={
                "code": "999001.OF",
                "market": "CN_OTC",
                "name": "测试新基金",
                "product_type": "OEF",
                "asset_class_code": "ASSET_STOCK",
                "region_code": "REGION_CN",
                "style_code": "STYLE_BALANCED",
                "size_code": "SIZE_LARGE",
                "confirm_days": 1,
                "is_qdii": False,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["code"] == "999001.OF"
        assert data["product_type"] == "OEF"

    def test_get_product_detail(self, client, admin_headers, test_db):
        """获取产品详情"""
        create_product(test_db, code="888001.OF", market="CN_OTC")
        resp = client.get("/api/products/888001.OF/CN_OTC", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["code"] == "888001.OF"

    def test_viewer_cannot_create_product(self, client, viewer_headers):
        """viewer 不能创建产品"""
        resp = client.post(
            "/api/products",
            json={"code": "X", "market": "CN_OTC", "name": "X", "product_type": "OEF"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_list_products_pagination(self, client, admin_headers):
        """产品列表分页"""
        resp = client.get("/api/products?page=1&page_size=5", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert data["page"] == 1
        assert data["page_size"] == 5


class TestConfirmDaysAndNavLagDays:
    """issue #228：confirm_days / nav_lag_days 更新纯显式（is_qdii 不再联动重算），
    calculate_confirm_days 仅保留创建时默认推导器角色"""

    def test_update_is_qdii_keeps_confirm_days(self, client, admin_headers, test_db):
        """PUT {"is_qdii": true} 不改 confirm_days（存量 7 → 仍 7）"""
        create_product(test_db, code="LAG001.OF", market="CN_OTC", confirm_days=7)
        resp = client.put(
            "/api/products/LAG001.OF/CN_OTC",
            json={"is_qdii": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["is_qdii"] is True
        assert resp.json()["confirm_days"] == 7

        got = client.get("/api/products/LAG001.OF/CN_OTC", headers=admin_headers)
        assert got.json()["confirm_days"] == 7

    def test_update_is_qdii_with_explicit_confirm_days_wins(self, client, admin_headers, test_db):
        """PUT {"is_qdii": true, "confirm_days": 5} → 显式值生效（旧逻辑会被覆盖成 2）"""
        create_product(test_db, code="LAG002.OF", market="CN_OTC", confirm_days=1)
        resp = client.put(
            "/api/products/LAG002.OF/CN_OTC",
            json={"is_qdii": True, "confirm_days": 5},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["confirm_days"] == 5

    def test_update_nav_lag_days(self, client, admin_headers, test_db):
        """PUT {"nav_lag_days": 1} 生效（互认基金上线后手动置 1 的路径），不影响 confirm_days"""
        create_product(test_db, code="LAG003", market="HK_MUTUAL", confirm_days=1)
        resp = client.put(
            "/api/products/LAG003/HK_MUTUAL",
            json={"nav_lag_days": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["nav_lag_days"] == 1
        assert resp.json()["confirm_days"] == 1

        got = client.get("/api/products/LAG003/HK_MUTUAL", headers=admin_headers)
        assert got.json()["nav_lag_days"] == 1

    def test_create_otc_qdii_derives_confirm_days_2(self, client, admin_headers, test_db):
        """创建场外 QDII 不传 confirm_days → 后端推导 2（创建时默认推导器仍在）"""
        resp = client.post(
            "/api/products",
            json={
                "code": "LAG004.OF",
                "market": "CN_OTC",
                "name": "测试QDII基金",
                "product_type": "OEF",
                "asset_class_code": "ASSET_STOCK",
                "region_code": "REGION_CN",
                "style_code": "STYLE_BALANCED",
                "size_code": "SIZE_LARGE",
                "is_qdii": True,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        data = resp.json()
        assert data["confirm_days"] == 2
        # nav_lag_days 不做推导：创建未传即 0，需显式设置（迁移只回填存量场外 QDII）
        assert data["nav_lag_days"] == 0
