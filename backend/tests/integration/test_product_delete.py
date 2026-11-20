# ============================================================================
# 集成测试：产品删除统一引用校验（issue #906 修复 B，对齐 #820）
# ============================================================================
# 统一规则（维护者定稿）：任何产品未被引用才可删——CASH/IN_TRANSIT_* 系统
# 虚拟产品不加额外保护也不给豁免。有引用 → PRODUCT_HAS_DEPENDENT_DATA(409,
# details.dependencies 表名→计数)，消灭 MySQL 下未捕获 IntegrityError → 500。
#
# 判据 = 元数据派生 FK 引用表（trade/portfolio_position/share_change_event/
# price_record/dividend_method_config）+ 无 FK 的 manual_market_value 显式补查；
# nav_sync_detail（同步日志）显式豁免，名单守门见
# tests/unit/test_product_delete_dependents.py。
# ============================================================================

from datetime import date

import pytest

from app.models import AuditLog, DividendMethodConfig, Product
from tests.factories import (
    create_portfolio,
    create_product,
    create_price_record,
    create_position_snapshot,
    create_share_change_event,
    create_trade,
    create_manual_market_value,
)

REF_CODE = "DELREF.OF"
REF_MARKET = "CN_OTC"
D0 = date(2025, 6, 6)


@pytest.fixture
def ref_setup(test_db):
    """active 组合 + 待删产品（各用例自行追加引用行）。"""
    port = create_portfolio(test_db, code="PROD_DEL_PORT", status="active")
    create_product(test_db, code=REF_CODE, market=REF_MARKET)
    return port


def _add_reference(test_db, table: str, port) -> None:
    if table == "trade":
        create_trade(test_db, port.code, REF_CODE, REF_MARKET, trade_date=D0)
    elif table == "portfolio_position":
        create_position_snapshot(
            test_db, port.code, REF_CODE, REF_MARKET,
            snapshot_date=D0, shares=100.0, unit_price=1.0,
            cost_price=1.0, market_value=100.0, platform_code="MYCF",
        )
    elif table == "share_change_event":
        create_share_change_event(
            test_db, port.code, REF_CODE, REF_MARKET,
            event_type="cash_dividend", ex_date=D0, entitlement_date=D0,
        )
    elif table == "price_record":
        create_price_record(test_db, REF_CODE, REF_MARKET, D0, 1.0)
    elif table == "dividend_method_config":
        test_db.add(DividendMethodConfig(
            portfolio_code=port.code, product_code=REF_CODE, market=REF_MARKET,
            platform_code="MYCF", dividend_method="cash_dividend",
            effective_from=D0, source="explicit",
        ))
        test_db.commit()
    elif table == "manual_market_value":
        create_manual_market_value(
            test_db, port.code, "MYCF", REF_CODE, D0, 1000.0,
        )
    else:
        raise AssertionError(f"未知引用表: {table}")


class TestProductDeleteReferenced:
    @pytest.mark.parametrize("table", [
        "trade",
        "portfolio_position",
        "share_change_event",
        "price_record",
        "dividend_method_config",
        "manual_market_value",
    ])
    def test_referenced_product_rejected_409_zero_writes(
        self, client, admin_headers, test_db, ref_setup, table
    ):
        """验收断言：有引用 → 409 + 产品仍在 + 零写入（含无 FK 的 manual_market_value）。"""
        _add_reference(test_db, table, ref_setup)
        audit_before = test_db.query(AuditLog).count()

        resp = client.delete(
            f"/api/products/{REF_CODE}/{REF_MARKET}", headers=admin_headers
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "PRODUCT_HAS_DEPENDENT_DATA"
        assert detail["details"]["dependencies"].get(table, 0) >= 1

        # 产品仍在、引用行仍在、失败路径不落审计行
        assert test_db.query(Product).filter(
            Product.code == REF_CODE, Product.market == REF_MARKET
        ).first() is not None
        assert test_db.query(AuditLog).count() == audit_before

    def test_dependencies_counts_all_referencing_tables(
        self, client, admin_headers, test_db, ref_setup
    ):
        """多表同时引用：dependencies 逐表给计数（表名→计数契约同 #820）。"""
        _add_reference(test_db, "trade", ref_setup)
        _add_reference(test_db, "price_record", ref_setup)
        resp = client.delete(
            f"/api/products/{REF_CODE}/{REF_MARKET}", headers=admin_headers
        )
        assert resp.status_code == 409, resp.text
        deps = resp.json()["detail"]["details"]["dependencies"]
        assert deps["trade"] >= 1
        assert deps["price_record"] >= 1

    @pytest.mark.dialect
    def test_mysql_referenced_delete_is_409_not_500(
        self, client, admin_headers, test_db, ref_setup
    ):
        """MySQL 方言：FK RESTRICT 不再以未捕获 IntegrityError → 500 呈现。"""
        if test_db.get_bind().dialect.name != "mysql":
            pytest.skip("FK RESTRICT 的 IntegrityError 形态仅 MySQL 方言成立")
        _add_reference(test_db, "trade", ref_setup)
        resp = client.delete(
            f"/api/products/{REF_CODE}/{REF_MARKET}", headers=admin_headers
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "PRODUCT_HAS_DEPENDENT_DATA"


class TestProductDeleteUnreferenced:
    def test_unreferenced_product_delete_succeeds(
        self, client, admin_headers, test_db, ref_setup
    ):
        """无引用产品删除仍成功，回执逐字不变（既有 locale 守门同口径）。"""
        resp = client.delete(
            f"/api/products/{REF_CODE}/{REF_MARKET}", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"message": f"产品 {REF_CODE}({REF_MARKET}) 删除成功"}
        assert test_db.query(Product).filter(
            Product.code == REF_CODE, Product.market == REF_MARKET
        ).first() is None
        # 成功路径落审计（ACTION_DELETE / RESOURCE_PRODUCT，#906 下沉 service 补审计）
        audit = test_db.query(AuditLog).filter(
            AuditLog.action == "delete",
            AuditLog.resource_type == "product",
            AuditLog.resource_id == f"{REF_CODE}({REF_MARKET})",
        ).first()
        assert audit is not None

    def test_unreferenced_virtual_product_delete_succeeds(
        self, client, admin_headers, test_db
    ):
        """统一规则：系统虚拟产品无引用时可删，不加特殊保护（维护者决策 ③）。

        虚拟产品按 product_type 判定（与 #856 停用守卫同口径）；market 取
        常规值以便 URL 复合键可寻址（种子 CASH 的 market="" 不可经路由表达，
        先例见 test_products_crud.py 的 VCASH_EDIT）。
        """
        create_product(
            test_db, code="VIRT_DEL", market="CN_OTC", product_type="CASH",
            asset_class_code=None, region_code=None, style_code=None,
            size_code=None, segment_code=None,
        )
        resp = client.delete("/api/products/VIRT_DEL/CN_OTC", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert test_db.query(Product).filter(
            Product.code == "VIRT_DEL", Product.market == "CN_OTC"
        ).first() is None

    def test_referenced_cash_virtual_product_rejected(
        self, client, admin_headers, test_db, ref_setup
    ):
        """统一规则反向：CASH 型产品被 manual_market_value 引用 → 拒删（无豁免）。"""
        create_product(
            test_db, code="VCASH_REF", market="CN_OTC", product_type="CASH",
            asset_class_code=None, region_code=None, style_code=None,
            size_code=None, segment_code=None,
        )
        create_manual_market_value(
            test_db, ref_setup.code, "MYCF", "VCASH_REF", D0, 1000.0,
        )
        resp = client.delete("/api/products/VCASH_REF/CN_OTC", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "PRODUCT_HAS_DEPENDENT_DATA"
        assert detail["details"]["dependencies"].get("manual_market_value", 0) >= 1