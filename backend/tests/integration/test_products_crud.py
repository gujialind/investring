# ============ 集成测试：产品管理·CRUD 与确认参数更新（自 test_products.py 拆分，issue #469） ============
# 原「集成测试：产品管理」（test_products.py）的 CRUD / 确认参数更新部分；其余三个文件同源拆分
# （列表筛选 test_products_list_filters.py、维度适用 test_products_dimensions.py、
# 取值校验 test_products_validation.py）。
# 本文件覆盖：
#   - TestProductCRUD：产品列表 / 创建 / 详情 / viewer 权限 / 分页
#   - TestConfirmDaysAndNavLagDays（#228）：confirm_days / nav_lag_days 更新纯显式（is_qdii 不再联动重算），
#     创建时仍保留默认推导器

import pytest

from tests.factories import create_product, create_asset_classification
from app.schemas.product import ProductResponse
# 模块导入期 autouse mock 尚未生效，此处捕获真实探测函数，供「窗外净值零写入」反例恢复真实探测
from app.services.data_source_probe import probe_data_source as _real_probe


class TestProductCRUD:
    """产品 CRUD API 测试"""

    def test_list_products(self, client, admin_headers):
        """获取产品列表（issue #487：响应须经分页响应模型收窄，不多带 ORM 独有列）"""
        resp = client.get("/api/products", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"items", "total", "page", "page_size"}
        assert data["items"]
        # 列表项口径 == ProductResponse：曾直吐 ORM 行多带 fallback_source / sync_error
        assert set(data["items"][0].keys()) == set(ProductResponse.model_fields)

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


class TestProductIsActive:
    """issue #856：产品停用标记 is_active 的更新/显式 null 收口/虚拟产品守卫"""

    def test_update_deactivate_and_reactivate(self, client, admin_headers, test_db):
        """PUT is_active=false → 200 且持久化；再 PUT true 恢复（停用可逆）"""
        create_product(test_db, code="ACT001.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/ACT001.OF/CN_OTC", json={"is_active": False}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["is_active"] is False
        got = client.get("/api/products/ACT001.OF/CN_OTC", headers=admin_headers)
        assert got.json()["is_active"] is False

        resp2 = client.put(
            "/api/products/ACT001.OF/CN_OTC", json={"is_active": True}, headers=admin_headers
        )
        assert resp2.status_code == 200, resp2.json()
        assert resp2.json()["is_active"] is True

    def test_update_null_rejected(self, client, admin_headers, test_db):
        """PUT is_active=null → 422 INVALID_PARAM（null_guard 收口，列 NOT NULL）"""
        create_product(test_db, code="ACT002.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/ACT002.OF/CN_OTC", json={"is_active": None}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_deactivate_keeps_other_fields(self, client, admin_headers, test_db):
        """停用是纯显式单字段更新：confirm_days / nav_lag_days 不传不改"""
        create_product(test_db, code="ACT003.OF", market="CN_OTC", confirm_days=1, nav_lag_days=0)
        resp = client.put(
            "/api/products/ACT003.OF/CN_OTC", json={"is_active": False}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["is_active"] is False
        assert resp.json()["confirm_days"] == 1
        assert resp.json()["nav_lag_days"] == 0

    @pytest.mark.parametrize("ptype", ["CASH", "IN_TRANSIT"])
    def test_virtual_deactivate_protected(self, client, admin_headers, test_db, ptype):
        """虚拟产品（CASH/IN_TRANSIT）停用 → 422 SYSTEM_PRODUCT_PROTECTED，写库前拒绝仍 active"""
        create_product(test_db, code=f"V{ptype}", market="CN_OTC", product_type=ptype)
        resp = client.put(
            f"/api/products/V{ptype}/CN_OTC", json={"is_active": False}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "SYSTEM_PRODUCT_PROTECTED"
        got = client.get(f"/api/products/V{ptype}/CN_OTC", headers=admin_headers)
        assert got.json()["is_active"] is True

    def test_virtual_other_edits_not_blocked(self, client, admin_headers, test_db):
        """守卫只挡停用，不扩及虚拟产品其他编辑（name 改照常 200）"""
        create_product(test_db, code="VCASH_EDIT", market="CN_OTC", product_type="CASH")
        resp = client.put(
            "/api/products/VCASH_EDIT/CN_OTC", json={"name": "改名现金"}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["name"] == "改名现金"


class TestProductDataSource:
    """issue #880：数据源切换（校验/连通检测前置/审计/状态重置/创建收紧）。

    连通检测由 integration/conftest.py autouse mock 为通过；失败用例自行覆写。
    """

    def test_switch_success_and_status_reset(self, client, admin_headers, test_db):
        from datetime import datetime

        from app.models.product import Product

        p = create_product(test_db, code="DS001.OF", market="CN_OTC")
        p.data_source_status = "success"
        p.sync_error = "旧错误"
        p.last_sync_at = datetime(2026, 11, 10, 7, 0, 0)
        test_db.commit()
        resp = client.put(
            "/api/products/DS001.OF/CN_OTC", json={"data_source": "akshare"}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["data_source"] == "akshare"
        assert resp.json()["data_source_status"] == "pending"
        got = test_db.query(Product).filter_by(code="DS001.OF", market="CN_OTC").first()
        assert got.data_source_status == "pending"
        assert got.sync_error is None
        # last_sync_at 保留（旧源历史事实，V5）
        assert got.last_sync_at == datetime(2026, 11, 10, 7, 0, 0)

    def test_switch_audit_diff_only(self, client, admin_headers, test_db):
        import json as _json

        from app.models.audit_log import AuditLog

        create_product(test_db, code="DS002.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/DS002.OF/CN_OTC", json={"data_source": "akshare"}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        rows = test_db.query(AuditLog).filter(AuditLog.resource_type == "product").all()
        assert len(rows) == 1
        assert rows[0].action == "update"
        assert _json.loads(rows[0].old_value) == {"data_source": "tushare"}
        assert _json.loads(rows[0].new_value) == {"data_source": "akshare"}

    def test_same_value_noop_no_audit(self, client, admin_headers, test_db):
        from app.models.audit_log import AuditLog

        create_product(test_db, code="DS003.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/DS003.OF/CN_OTC", json={"data_source": "tushare"}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        assert test_db.query(AuditLog).filter(AuditLog.resource_type == "product").count() == 0

    def test_null_rejected(self, client, admin_headers, test_db):
        create_product(test_db, code="DS004.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/DS004.OF/CN_OTC", json={"data_source": None}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_invalid_enum_rejected(self, client, admin_headers, test_db):
        create_product(test_db, code="DS005.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/DS005.OF/CN_OTC", json={"data_source": "foo"}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATA_SOURCE"

    def test_hk_tushare_rejected(self, client, admin_headers, test_db):
        # 真换源到 tushare（HK 现值 akshare → tushare）仍被硬拦（F1 后同值 no-op 不放宽真实换源）
        from app.models.product import Product

        p = create_product(test_db, code="DS006", market="HK_MUTUAL")
        p.data_source = "akshare"
        test_db.commit()
        resp = client.put(
            "/api/products/DS006/HK_MUTUAL", json={"data_source": "tushare"}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATA_SOURCE"
        assert test_db.query(Product).filter_by(code="DS006", market="HK_MUTUAL").first().data_source == "akshare"

    def test_hk_existing_tushare_row_rename_noop(self, client, admin_headers, test_db):
        """#880 L2 F1 反例：存量 HK_MUTUAL+tushare 行只改名（传原值/不传）→ 200、源不变、无审计、无状态重置。"""
        import json as _json

        from app.models.audit_log import AuditLog
        from app.models.product import Product

        p = create_product(test_db, code="DSHK1", market="HK_MUTUAL")
        p.data_source = "tushare"
        p.data_source_status = "skipped"
        p.sync_error = "tushare 不支持 HK_MUTUAL"
        test_db.commit()
        for payload in ({"name": "改名A", "data_source": "tushare"}, {"name": "改名B"}):
            resp = client.put("/api/products/DSHK1/HK_MUTUAL", json=payload, headers=admin_headers)
            assert resp.status_code == 200, resp.json()
            got = test_db.query(Product).filter_by(code="DSHK1", market="HK_MUTUAL").first()
            assert got.data_source == "tushare"
            assert got.data_source_status == "skipped"  # 无状态重置
            assert got.sync_error == "tushare 不支持 HK_MUTUAL"
        assert test_db.query(AuditLog).filter(AuditLog.resource_type == "product").count() == 0

    def test_same_value_probe_not_called(self, client, admin_headers, test_db, monkeypatch):
        """#880 L2 F2 反例：同值重提交即使探测被覆写为失败也 200（不探）、无状态重置、无审计。"""
        from app.models.audit_log import AuditLog
        from app.models.product import Product
        from app.services.exceptions import BusinessError

        def _fail(db, code, market, data_source):
            raise BusinessError("DATA_SOURCE_PROBE_FAILED", "数据源暂时不可达", details={"reason": "unreachable"})

        monkeypatch.setattr("app.services.data_source_probe.probe_data_source", _fail)
        p = create_product(test_db, code="DSSV1.OF", market="CN_OTC")
        p.data_source_status = "success"
        test_db.commit()
        resp = client.put(
            "/api/products/DSSV1.OF/CN_OTC", json={"data_source": "tushare"}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        got = test_db.query(Product).filter_by(code="DSSV1.OF", market="CN_OTC").first()
        assert got.data_source_status == "success"  # 无状态重置
        assert test_db.query(AuditLog).filter(AuditLog.resource_type == "product").count() == 0

    def test_virtual_rejected(self, client, admin_headers, test_db):
        create_product(test_db, code="DSCASH", market="CN_OTC", product_type="CASH")
        resp = client.put(
            "/api/products/DSCASH/CN_OTC", json={"data_source": "akshare"}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATA_SOURCE"

    def test_probe_failed_zero_write(self, client, admin_headers, test_db, monkeypatch):
        from app.services.exceptions import BusinessError

        def _fail(db, code, market, data_source):
            raise BusinessError(
                "DATA_SOURCE_PROBE_FAILED", "数据源暂时不可达，请稍后重试",
                details={"reason": "unreachable"},
            )

        monkeypatch.setattr("app.services.data_source_probe.probe_data_source", _fail)
        create_product(test_db, code="DS007.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/DS007.OF/CN_OTC", json={"data_source": "akshare"}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "DATA_SOURCE_PROBE_FAILED"
        got = client.get("/api/products/DS007.OF/CN_OTC", headers=admin_headers)
        assert got.json()["data_source"] == "tushare"  # 零写入

    def test_viewer_cannot_switch(self, client, viewer_headers, test_db):
        create_product(test_db, code="DS008.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/DS008.OF/CN_OTC", json={"data_source": "akshare"}, headers=viewer_headers
        )
        assert resp.status_code == 403

    def test_create_invalid_data_source_rejected(self, client, admin_headers, test_db):
        resp = client.post(
            "/api/products",
            json={"code": "DS009.OF", "market": "CN_OTC", "name": "x", "product_type": "OEF", "data_source": "foo"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATA_SOURCE"

    def test_create_hk_tushare_rejected(self, client, admin_headers, test_db, monkeypatch):
        # 港互认创建先经代码归一（akshare 排名表），mock 掉以隔离网络；数据源校验随后拒绝
        monkeypatch.setattr(
            "app.services.akshare_client.resolve_hk_code_pair", lambda code: (code, None)
        )
        resp = client.post(
            "/api/products",
            json={"code": "9680500001", "market": "HK_MUTUAL", "name": "x", "product_type": "OEF", "data_source": "tushare"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATA_SOURCE"

    def test_create_akshare_otc_out_of_window_zero_write(self, client, admin_headers, test_db, monkeypatch):
        """Blocker 集成反例：akshare 场外仅窗外有正净值 → 创建 422 no_data 且零写入。"""
        from datetime import date

        import akshare as ak
        import pandas as pd

        from app.models.product import Product
        from app.services import data_source_probe as probe_mod

        monkeypatch.setattr("app.services.data_source_probe.probe_data_source", _real_probe)
        monkeypatch.setattr(probe_mod.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(probe_mod, "business_today", lambda: date(2026, 11, 13))
        monkeypatch.setattr(
            probe_mod, "try_get_prev_trading_day",
            lambda db, d, days=1, exchange="SSE": date(2026, 11, 1),
        )
        monkeypatch.setattr(
            ak, "fund_open_fund_info_em",
            lambda **kw: pd.DataFrame({"净值日期": ["2026-09-28"], "单位净值": [1.0]}),
        )
        resp = client.post(
            "/api/products",
            json={"code": "DSW1.OF", "market": "CN_OTC", "name": "窗外净值",
                  "product_type": "OEF", "data_source": "akshare"},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.json()
        assert resp.json()["detail"]["error"] == "DATA_SOURCE_PROBE_FAILED"
        assert resp.json()["detail"]["details"]["reason"] == "no_data"
        assert test_db.query(Product).filter_by(code="DSW1.OF", market="CN_OTC").count() == 0

    def test_create_probe_failed_zero_write(self, client, admin_headers, test_db, monkeypatch):
        """#880 L2 S3：创建路径探测失败 → 422 且零写入（反例把守「create 必探」）。

        integration/conftest.py 的 autouse fixture 把探测整体 mock 成通过，
        本用例必须在用例内显式覆写为抛 DATA_SOURCE_PROBE_FAILED。
        有效性自证：临时移除 create_product 的探测调用后本用例须变红。
        """
        from app.models.product import Product
        from app.services.exceptions import BusinessError

        def _fail(db, code, market, data_source):
            raise BusinessError(
                "DATA_SOURCE_PROBE_FAILED", "数据源暂时不可达，请稍后重试",
                details={"reason": "unreachable"},
            )

        monkeypatch.setattr("app.services.data_source_probe.probe_data_source", _fail)
        resp = client.post(
            "/api/products",
            json={"code": "DSP01.OF", "market": "CN_OTC", "name": "探测失败建档",
                  "product_type": "OEF", "data_source": "akshare"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "DATA_SOURCE_PROBE_FAILED"
        assert test_db.query(Product).filter_by(code="DSP01.OF", market="CN_OTC").count() == 0

    def test_create_nav_without_market_rejected_accurate_message(self, client, admin_headers, test_db):
        """#880 L2 S5：缺市场创建净值型 → 422 且归因准确（不得误报「现金/虚拟」）。"""
        from app.models.product import Product

        resp = client.post(
            "/api/products",
            json={"code": "DSM01.OF", "name": "缺市场", "product_type": "OEF"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_DATA_SOURCE"
        assert "市场" in detail["message"]
        assert "现金" not in detail["message"]
        assert test_db.query(Product).filter_by(code="DSM01.OF").count() == 0
