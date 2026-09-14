# ============ 集成测试：产品管理·维度适用校验与字典（自 test_products.py 拆分，issue #469） ============
# 原「集成测试：产品管理」（test_products.py）的五维分类部分；其余三个文件同源拆分。
# 本文件覆盖：
#   - TestDimensionMatrixValidation（#128/#135）：维度适用矩阵，非法组合 422 INVALID_DIMENSION_TAGS
#   - TestValueLevelApplicability（#135）：值级适用（维度值必须关联产品的 asset_class）
#   - TestIsActiveValidation（#135）：is_active 软失效，新赋值校验 active、存量引用不阻断
#   - TestAssetClassificationsEndpoint（#128）：维度字典只读端点
# 模块级 _seed_dims 仅本文件使用（原属 #128 章节，保留原位）。

from tests.factories import create_product, create_asset_classification

from app.models.asset_classification import (
    AssetClassification,
    AssetClassDimensionRule,
    AssetDimensionApplicability,
)


def _seed_dims(test_db):
    """种子矩阵校验所需维度值（issue #128）"""
    create_asset_classification(test_db, code="ASSET_STOCK")
    create_asset_classification(test_db, code="ASSET_BOND")
    create_asset_classification(test_db, code="ASSET_COMMODITY")
    create_asset_classification(test_db, code="ASSET_CASH")
    create_asset_classification(test_db, code="REGION_CN", dimension="region")
    create_asset_classification(test_db, code="STYLE_GROWTH", dimension="style")
    create_asset_classification(test_db, code="STYLE_BALANCED", dimension="style")
    create_asset_classification(test_db, code="SIZE_LARGE", dimension="size")
    create_asset_classification(test_db, code="SEG_COMPOSITE", dimension="segment")
    create_asset_classification(test_db, code="SEG_BOND_SHORT", dimension="segment")
    create_asset_classification(test_db, code="SEG_GOLD", dimension="segment")


class TestDimensionMatrixValidation:
    """维度适用矩阵校验（issue #128）：非法组合 422 INVALID_DIMENSION_TAGS"""

    def _post(self, client, admin_headers, **dims):
        json = {
            "code": "999002.OF",
            "market": "CN_OTC",
            "name": "矩阵测试基金",
            "product_type": "OEF",
            "is_qdii": False,
        }
        json.update(dims)
        return client.post("/api/products", json=json, headers=admin_headers)

    def test_stock_missing_region_422(self, client, admin_headers, test_db):
        """股票缺 region → 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            style_code="STYLE_BALANCED",
            size_code="SIZE_LARGE",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_stock_missing_style_422(self, client, admin_headers, test_db):
        """股票缺 style → 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="REGION_CN",
            size_code="SIZE_LARGE",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_bond_with_style_422(self, client, admin_headers, test_db):
        """债券带 style → 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_BOND",
            region_code="REGION_CN",
            segment_code="SEG_BOND_SHORT",
            style_code="STYLE_BALANCED",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_commodity_with_region_422(self, client, admin_headers, test_db):
        """商品带 region → 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_COMMODITY",
            segment_code="SEG_GOLD",
            region_code="REGION_CN",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_cash_with_region_422(self, client, admin_headers, test_db):
        """现金带 region → 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_CASH",
            region_code="REGION_CN",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_dimension_mismatch_422(self, client, admin_headers, test_db):
        """维度错位（region 字段填 style 值）→ 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="STYLE_GROWTH",
            style_code="STYLE_BALANCED",
            size_code="SIZE_LARGE",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_nonexistent_code_422(self, client, admin_headers, test_db):
        """引用不存在的维度值 → 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="REGION_MOON",
            style_code="STYLE_BALANCED",
            size_code="SIZE_LARGE",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_other_dims_without_asset_class_422(self, client, admin_headers, test_db):
        """未指定 asset_class 却带其他维度 → 422"""
        _seed_dims(test_db)
        resp = self._post(client, admin_headers, region_code="REGION_CN")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_unruled_asset_class_defaults_cash_like(self, client, admin_headers, test_db):
        """#135 矩阵落库：无规则行的大类 = 现金型全 forbidden（替代旧「矩阵未定义」422）"""
        _seed_dims(test_db)
        create_asset_classification(test_db, code="ASSET_ALTERNATIVE")
        # 不带其他维度 → 合法（现金型语义，新建大类默认态）
        resp = self._post(client, admin_headers, asset_class_code="ASSET_ALTERNATIVE")
        assert resp.status_code in (200, 201)
        # 带任一其他维度 → 422（全 forbidden）
        resp = self._post(
            client, admin_headers,
            code="999004.OF",
            asset_class_code="ASSET_ALTERNATIVE",
            region_code="REGION_CN",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_valid_stock_ok(self, client, admin_headers, test_db):
        """合法股票组合 → 创建成功且维度落库"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="REGION_CN",
            style_code="STYLE_GROWTH",
            size_code="SIZE_LARGE",
            segment_code="SEG_COMPOSITE",
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["region_code"] == "REGION_CN"
        assert data["style_code"] == "STYLE_GROWTH"
        assert data["size_code"] == "SIZE_LARGE"
        assert data["segment_code"] == "SEG_COMPOSITE"

    def test_valid_bond_ok(self, client, admin_headers, test_db):
        """合法债券组合（region+segment，无 style/size）→ 创建成功"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_BOND",
            region_code="REGION_CN",
            segment_code="SEG_BOND_SHORT",
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["style_code"] is None
        assert data["size_code"] is None

    def test_update_to_invalid_combo_422(self, client, admin_headers, test_db):
        """更新为非法组合（合并校验）→ 422 且不落库"""
        _seed_dims(test_db)
        create_product(test_db, code="999003.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/999003.OF/CN_OTC",
            json={"asset_class_code": "ASSET_BOND"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"


class TestValueLevelApplicability:
    """值级适用校验（issue #135）：维度值必须关联产品的 asset_class"""

    def _post(self, client, admin_headers, code="999002.OF", **dims):
        json = {
            "code": code,
            "market": "CN_OTC",
            "name": "值级校验测试基金",
            "product_type": "OEF",
            "is_qdii": False,
        }
        json.update(dims)
        return client.post("/api/products", json=json, headers=admin_headers)

    def test_stock_with_commodity_segment_422(self, client, admin_headers, test_db):
        """股票传 segment=SEG_GOLD：维度级通过（segment 选填），值级拒绝"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="REGION_CN",
            style_code="STYLE_BALANCED",
            size_code="SIZE_LARGE",
            segment_code="SEG_GOLD",
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_DIMENSION_TAGS"
        assert detail["details"]["applicable_asset_classes"] == ["ASSET_COMMODITY"]

    def test_stock_with_bond_segment_422(self, client, admin_headers, test_db):
        """股票传 segment=SEG_BOND_SHORT → 422"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="REGION_CN",
            style_code="STYLE_BALANCED",
            size_code="SIZE_LARGE",
            segment_code="SEG_BOND_SHORT",
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_bond_only_segment_passes(self, client, admin_headers, test_db):
        """仅关联债券的 SEG 值：债券产品引用成功"""
        _seed_dims(test_db)
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_BOND",
            region_code="REGION_CN",
            segment_code="SEG_BOND_SHORT",
        )
        assert resp.status_code in (200, 201)

    def test_new_bond_only_value_rejected_for_stock(self, client, admin_headers, test_db):
        """新建仅关联债券的 SEG 值 → 股票 422、债券通过（验收断言）"""
        _seed_dims(test_db)
        create_asset_classification(test_db, code="SEG_BOND_CONVERT", dimension="segment")
        test_db.add(AssetDimensionApplicability(
            dimension_value_code="SEG_BOND_CONVERT", asset_class_code="ASSET_BOND",
        ))
        test_db.commit()
        stock = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="REGION_CN",
            style_code="STYLE_BALANCED",
            size_code="SIZE_LARGE",
            segment_code="SEG_BOND_CONVERT",
        )
        assert stock.status_code == 422
        bond = self._post(
            client, admin_headers,
            code="999005.OF",
            asset_class_code="ASSET_BOND",
            region_code="REGION_CN",
            segment_code="SEG_BOND_CONVERT",
        )
        assert bond.status_code in (200, 201)

    def test_rules_db_driven(self, client, admin_headers, test_db):
        """规则读库证明：把股票 segment 改为 required 后，缺 segment → 422"""
        _seed_dims(test_db)
        rule = test_db.query(AssetClassDimensionRule).filter_by(
            asset_class_code="ASSET_STOCK", dimension="segment",
        ).first()
        rule.rule = "required"
        test_db.commit()
        resp = self._post(
            client, admin_headers,
            asset_class_code="ASSET_STOCK",
            region_code="REGION_CN",
            style_code="STYLE_BALANCED",
            size_code="SIZE_LARGE",
        )
        assert resp.status_code == 422
        assert "segment_code" in resp.json()["detail"]["details"]["missing"]

    def test_update_path_value_level(self, client, admin_headers, test_db):
        """update merged 校验同样过值级：改为不适用值 → 422"""
        _seed_dims(test_db)
        create_product(test_db, code="999006.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/999006.OF/CN_OTC",
            json={"segment_code": "SEG_GOLD"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"


class TestIsActiveValidation:
    """is_active 软失效（issue #135）：新赋值校验 active，存量引用不阻断"""

    def test_create_with_inactive_value_422(self, client, admin_headers, test_db):
        """create 引用已停用维度值 → 422"""
        _seed_dims(test_db)
        test_db.query(AssetClassification).filter_by(code="STYLE_GROWTH").first().is_active = False
        test_db.commit()
        resp = client.post(
            "/api/products",
            json={
                "code": "999007.OF", "market": "CN_OTC", "name": "停用值测试",
                "product_type": "OEF",
                "asset_class_code": "ASSET_STOCK", "region_code": "REGION_CN",
                "style_code": "STYLE_GROWTH", "size_code": "SIZE_LARGE",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"

    def test_update_unchanged_inactive_value_passes(self, client, admin_headers, test_db):
        """存量引用停用值：改其他维度字段（style 未变）不阻断"""
        _seed_dims(test_db)
        create_product(test_db, code="999008.OF", market="CN_OTC", style_code="STYLE_GROWTH")
        test_db.query(AssetClassification).filter_by(code="STYLE_GROWTH").first().is_active = False
        test_db.commit()
        resp = client.put(
            "/api/products/999008.OF/CN_OTC",
            json={"segment_code": "SEG_DIVIDEND"},
            headers=admin_headers,
        )
        assert resp.status_code == 200

    def test_update_change_to_inactive_value_422(self, client, admin_headers, test_db):
        """update 改赋停用值（字段实际变化）→ 422"""
        _seed_dims(test_db)
        create_product(test_db, code="999009.OF", market="CN_OTC", style_code="STYLE_BALANCED")
        test_db.query(AssetClassification).filter_by(code="STYLE_GROWTH").first().is_active = False
        test_db.commit()
        resp = client.put(
            "/api/products/999009.OF/CN_OTC",
            json={"style_code": "STYLE_GROWTH"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DIMENSION_TAGS"


class TestAssetClassificationsEndpoint:
    """维度字典只读端点（issue #128）"""

    def test_list_all(self, client, admin_headers):
        """全量字典：conftest 种子 34 条，按 dimension+sort_order 排序"""
        resp = client.get("/api/asset-classifications", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(data["items"]) > 0
        keys = [(i["dimension"], i["sort_order"]) for i in data["items"]]
        assert keys == sorted(keys)
        asset_classes = [i for i in data["items"] if i["dimension"] == "asset_class"]
        assert [i["code"] for i in asset_classes] == [
            "ASSET_STOCK", "ASSET_BOND", "ASSET_COMMODITY", "ASSET_CASH",
        ]

    def test_filter_by_dimension(self, client, admin_headers):
        """dimension 过滤"""
        resp = client.get(
            "/api/asset-classifications?dimension=region", headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        assert all(i["dimension"] == "region" for i in data["items"])

    def test_items_carry_applicability_and_active(self, client, admin_headers):
        """#135：每项含 applicable_asset_classes 与 is_active；asset_class 维度恒空"""
        resp = client.get("/api/asset-classifications", headers=admin_headers)
        assert resp.status_code == 200
        items = {i["code"]: i for i in resp.json()["items"]}
        assert items["REGION_CN"]["applicable_asset_classes"] == ["ASSET_STOCK", "ASSET_BOND"]
        assert items["SEG_GOLD"]["applicable_asset_classes"] == ["ASSET_COMMODITY"]
        assert items["STYLE_GROWTH"]["applicable_asset_classes"] == ["ASSET_STOCK"]
        assert items["ASSET_STOCK"]["applicable_asset_classes"] == []
        assert items["ASSET_CASH"]["is_active"] is True
        # 原有字段与排序不回归
        assert items["ASSET_STOCK"]["sort_order"] == 1

    def test_dimension_rules_top_level(self, client, admin_headers):
        """#135 矩阵落库：顶层 dimension_rules 与常量一致，无行维度 = forbidden"""
        resp = client.get("/api/asset-classifications", headers=admin_headers)
        assert resp.status_code == 200
        rules = resp.json()["dimension_rules"]
        assert rules["ASSET_STOCK"] == {
            "region": "required", "style": "required",
            "size": "required", "segment": "optional",
        }
        assert rules["ASSET_BOND"] == {"region": "required", "segment": "required"}
        assert rules["ASSET_COMMODITY"] == {"segment": "optional"}
        # ASSET_CASH 无规则行（全 forbidden）
        assert "ASSET_CASH" not in rules
