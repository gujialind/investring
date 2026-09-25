# ============================================================================
# 集成测试：产品身份字段纠错 product_type / market (issue #232)
# ============================================================================
# 覆盖：
# - product_type 可改：枚举校验、系统虚拟产品守卫、pending 交易守卫
# - market 受控改：仅限无 trade/event/position 引用产品；price_record 随行迁移；
#   目标键查重、枚举校验、confirm_days 默认重推导
# - ProductUpdate extra=forbid：未知字段 422（根治静默忽略）
# ============================================================================

from datetime import date
from decimal import Decimal
import time

import pytest
from tests.factories import (
    create_portfolio,
    create_platform,
    create_price_record,
    create_position_snapshot,
    create_product,
    create_share_change_event,
    create_trade,
)

from app.models.price_record import PriceRecord
from app.services import product_service
from app.services.exceptions import BusinessError


class TestProductTypeUpdate:
    """product_type 修改：枚举校验 + 守卫"""

    def test_update_product_type_success(self, client, admin_headers, test_db):
        """场内 ETF → LOF：生效且 updated_at 变化"""
        create_product(test_db, code="164906.SZ", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0)
        before = client.get("/api/products/164906.SZ/CN_EXCHANGE", headers=admin_headers).json()
        time.sleep(1.1)  # updated_at 秒级精度，跨秒后 PUT 才能断言变化

        resp = client.put(
            "/api/products/164906.SZ/CN_EXCHANGE",
            json={"product_type": "LOF"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["product_type"] == "LOF"
        assert data["updated_at"] != before["updated_at"]

        got = client.get("/api/products/164906.SZ/CN_EXCHANGE", headers=admin_headers)
        assert got.json()["product_type"] == "LOF"

    def test_update_product_type_invalid_422(self, client, admin_headers, test_db):
        """非法类型 FOO → 422 INVALID_PRODUCT_TYPE，不落库（验收断言）"""
        create_product(test_db, code="PT001.OF", market="CN_OTC", product_type="OEF")
        resp = client.put(
            "/api/products/PT001.OF/CN_OTC",
            json={"product_type": "FOO"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_PRODUCT_TYPE"
        assert "valid" in detail["details"]

        got = client.get("/api/products/PT001.OF/CN_OTC", headers=admin_headers)
        assert got.json()["product_type"] == "OEF"

    def test_create_product_type_invalid_422(self, client, admin_headers):
        """创建侧同口径：非法类型 422（create/update 共用校验器）"""
        resp = client.post(
            "/api/products",
            json={"code": "PT002.OF", "market": "CN_OTC", "name": "非法类型",
                  "product_type": "FOO"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PRODUCT_TYPE"

    @pytest.mark.parametrize("code", ["CASH", "IN_TRANSIT_DIVIDEND"])
    def test_update_virtual_product_type_rejected(self, test_db, code):
        """系统虚拟产品禁改 product_type（market=''，service 层直测）。"""
        with pytest.raises(BusinessError) as exc:
            product_service.update_product(
                test_db, code=code, market="",
                updates={"product_type": "OEF"},
            )
        assert exc.value.code == "SYSTEM_PRODUCT_PROTECTED"

    def test_update_product_type_with_pending_trade_rejected(self, client, admin_headers, test_db):
        """存在 pending trade → 422 PENDING_TRANSACTIONS_EXIST（防确认口径半途翻转）"""
        create_product(test_db, code="PT003.SZ", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0)
        create_portfolio(test_db, code="PTPORT")
        create_platform(test_db, code="PTPLAT")
        create_trade(
            test_db, portfolio_code="PTPORT", product_code="PT003.SZ",
            market="CN_EXCHANGE", platform_code="PTPLAT", status="pending",
        )
        resp = client.put(
            "/api/products/PT003.SZ/CN_EXCHANGE",
            json={"product_type": "LOF"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "PENDING_TRANSACTIONS_EXIST"
        assert detail["details"]["pending_trades"] == 1

    def test_update_product_type_with_confirmed_trade_ok(self, client, admin_headers, test_db):
        """仅 confirmed trade 不阻断（历史确认已按旧口径完成，快照不存 type）"""
        create_product(test_db, code="PT004.SZ", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0)
        create_portfolio(test_db, code="PTPORT2")
        create_platform(test_db, code="PTPLAT2")
        create_trade(
            test_db, portfolio_code="PTPORT2", product_code="PT004.SZ",
            market="CN_EXCHANGE", platform_code="PTPLAT2", status="confirmed",
        )
        resp = client.put(
            "/api/products/PT004.SZ/CN_EXCHANGE",
            json={"product_type": "LOF"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["product_type"] == "LOF"

    def test_update_product_type_with_pending_event_rejected(self, client, admin_headers, test_db):
        """存在 pending 事件 → 422 PENDING_TRANSACTIONS_EXIST（守卫覆盖事件侧）"""
        create_product(test_db, code="PT005.SZ", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0)
        create_portfolio(test_db, code="PTPORT3")
        create_platform(test_db, code="PTPLAT3")
        create_share_change_event(
            test_db, portfolio_code="PTPORT3", product_code="PT005.SZ",
            market="CN_EXCHANGE", event_type="cash_dividend",
            platform_code="PTPLAT3", status="pending",
        )
        resp = client.put(
            "/api/products/PT005.SZ/CN_EXCHANGE",
            json={"product_type": "LOF"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "PENDING_TRANSACTIONS_EXIST"
        assert detail["details"]["pending_events"] == 1

    def test_rename_with_pending_trade_passes(self, client, admin_headers, test_db):
        """回归：前端编辑恒带 product_type，传原值不得进门禁——
        有 pending 交易的产品仅改名（product_type 未变化）应 200"""
        create_product(test_db, code="PT006.SZ", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0)
        create_portfolio(test_db, code="PTPORT4")
        create_platform(test_db, code="PTPLAT4")
        create_trade(
            test_db, portfolio_code="PTPORT4", product_code="PT006.SZ",
            market="CN_EXCHANGE", platform_code="PTPLAT4", status="pending",
        )
        resp = client.put(
            "/api/products/PT006.SZ/CN_EXCHANGE",
            json={"name": "改名不改类型", "product_type": "ETF"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["name"] == "改名不改类型"
        assert resp.json()["product_type"] == "ETF"


class TestProductUpdateForbidExtra:
    """extra=forbid：未知字段 422，根治「返回 ok 但零生效」的静默忽略"""

    def test_unknown_field_rejected(self, client, admin_headers, test_db):
        """issue 现象复现修复：product_typee 拼错 → 422 而非静默丢弃"""
        create_product(test_db, code="FB001.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/FB001.OF/CN_OTC",
            json={"product_typee": "LOF"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_known_fields_still_accepted(self, client, admin_headers, test_db):
        """白名单字段正常通过（回归防护）"""
        create_product(test_db, code="FB002.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/FB002.OF/CN_OTC",
            json={"name": "改名测试", "is_qdii": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["name"] == "改名测试"


class TestMarketUpdate:
    """market 受控修改：仅无引用产品；price_record 随行迁移"""

    def test_update_market_success_no_refs(self, client, admin_headers, test_db):
        """无引用产品 CN_EXCHANGE → CN_OTC：生效，confirm_days 默认重推导 0→1"""
        create_product(test_db, code="MK201.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        resp = client.put(
            "/api/products/MK201.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["market"] == "CN_OTC"
        assert data["confirm_days"] == 1

        # 新键可查，旧键 404
        assert client.get("/api/products/MK201.SZ/CN_OTC", headers=admin_headers).status_code == 200
        assert client.get("/api/products/MK201.SZ/CN_EXCHANGE", headers=admin_headers).status_code == 404

    def test_update_market_explicit_confirm_days_wins(self, client, admin_headers, test_db):
        """market 变更时显式 confirm_days 优先于默认推导"""
        create_product(test_db, code="MK202.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        resp = client.put(
            "/api/products/MK202.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC", "confirm_days": 3},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["confirm_days"] == 3

    def test_update_market_moves_price_records(self, client, admin_headers, test_db):
        """price_record 随行迁移：旧市场清零、新市场等量，响应带重同步提示"""
        create_product(test_db, code="MK203.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        create_price_record(test_db, "MK203.SZ", "CN_EXCHANGE", date(2026, 8, 20), 1.234)
        create_price_record(test_db, "MK203.SZ", "CN_EXCHANGE", date(2026, 8, 21), 1.250)

        resp = client.put(
            "/api/products/MK203.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert "随行迁移" in (resp.json().get("market_change_hint") or "")

        old = test_db.query(PriceRecord).filter_by(
            product_code="MK203.SZ", market="CN_EXCHANGE").count()
        new_records = test_db.query(PriceRecord).filter_by(
            product_code="MK203.SZ", market="CN_OTC").all()
        assert old == 0
        assert {str(r.price_date) for r in new_records} == {"2026-08-20", "2026-08-21"}
        assert {float(r.unit_price) for r in new_records} == {1.234, 1.25}

    def test_update_market_with_trade_rejected(self, client, admin_headers, test_db):
        """存在 trade 引用（任意状态）→ 422 MARKET_CHANGE_REFERENCED，details 带计数"""
        create_product(test_db, code="MK204.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        create_portfolio(test_db, code="MKPORT")
        create_platform(test_db, code="MKPLAT")
        create_trade(
            test_db, portfolio_code="MKPORT", product_code="MK204.SZ",
            market="CN_EXCHANGE", platform_code="MKPLAT", status="confirmed",
        )
        resp = client.put(
            "/api/products/MK204.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MARKET_CHANGE_REFERENCED"
        assert detail["details"]["references"]["trades"] == 1

    def test_update_market_with_position_rejected(self, client, admin_headers, test_db):
        """存在持仓快照引用 → 422 MARKET_CHANGE_REFERENCED"""
        create_product(test_db, code="MK205.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        create_portfolio(test_db, code="MKPORT2")
        create_position_snapshot(
            test_db, portfolio_code="MKPORT2", product_code="MK205.SZ",
            market="CN_EXCHANGE", snapshot_date=date(2026, 8, 21), shares=100.0,
            market_value=150.0,
        )
        resp = client.put(
            "/api/products/MK205.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MARKET_CHANGE_REFERENCED"
        assert detail["details"]["references"]["positions"] == 1

    def test_update_market_target_exists_rejected(self, client, admin_headers, test_db):
        """目标 (code, market) 已存在（LOF 双记录场景）→ 400 ALREADY_EXISTS"""
        create_product(test_db, code="MK206.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        create_product(test_db, code="MK206.SZ", market="CN_OTC", product_type="LOF")
        resp = client.put(
            "/api/products/MK206.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC"},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "ALREADY_EXISTS"

    def test_update_market_invalid_422(self, client, admin_headers, test_db):
        """非法市场值 → 422 INVALID_MARKET"""
        create_product(test_db, code="MK207.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        resp = client.put(
            "/api/products/MK207.SZ/CN_EXCHANGE",
            json={"market": "US_NASDAQ"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_MARKET"

    @pytest.mark.parametrize("code", ["IN_TRANSIT_BUY", "IN_TRANSIT_DIVIDEND"])
    def test_update_virtual_product_market_rejected(self, test_db, code):
        """系统虚拟产品禁改 market（service 层直测）"""
        with pytest.raises(BusinessError) as exc:
            product_service.update_product(
                test_db, code=code, market="",
                updates={"market": "CN_OTC"},
            )
        assert exc.value.code == "SYSTEM_PRODUCT_PROTECTED"

    def test_update_market_with_event_rejected(self, client, admin_headers, test_db):
        """存在事件引用（任意状态）→ 422 MARKET_CHANGE_REFERENCED，events 计数"""
        create_product(test_db, code="MK208.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        create_portfolio(test_db, code="MKPORT3")
        create_share_change_event(
            test_db, portfolio_code="MKPORT3", product_code="MK208.SZ",
            market="CN_EXCHANGE", event_type="share_split", status="confirmed",
        )
        resp = client.put(
            "/api/products/MK208.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MARKET_CHANGE_REFERENCED"
        assert detail["details"]["references"]["events"] == 1

    def test_update_market_with_is_qdii_derives_qdii_confirm_days(self, client, admin_headers, test_db):
        """同一 PUT 传 market+is_qdii：confirm_days 按**新** is_qdii 推导为 2"""
        create_product(test_db, code="MK209.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        resp = client.put(
            "/api/products/MK209.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC", "is_qdii": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["is_qdii"] is True
        assert resp.json()["confirm_days"] == 2

    def test_price_record_full_fields_preserved(self, client, admin_headers, test_db):
        """随行迁移逐字段搬运：全列值不丢不变"""
        create_product(test_db, code="MK210.SZ", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)
        test_db.add(PriceRecord(
            product_code="MK210.SZ", market="CN_EXCHANGE",
            price_date=date(2026, 8, 20),
            unit_price=Decimal("1.2340"), accumulated_nav=Decimal("2.3450"),
            pre_close=Decimal("1.2000"), pct_change=Decimal("2.8333"),
            net_asset=Decimal("12345678.9000"), source="tushare",
        ))
        test_db.commit()

        resp = client.put(
            "/api/products/MK210.SZ/CN_EXCHANGE",
            json={"market": "CN_OTC"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()

        moved = test_db.query(PriceRecord).filter_by(
            product_code="MK210.SZ", market="CN_OTC").one()
        assert moved.price_date == date(2026, 8, 20)
        assert moved.unit_price == Decimal("1.2340")
        assert moved.accumulated_nav == Decimal("2.3450")
        assert moved.pre_close == Decimal("1.2000")
        assert moved.pct_change == Decimal("2.8333")
        assert moved.net_asset == Decimal("12345678.9000")
        assert moved.source == "tushare"
