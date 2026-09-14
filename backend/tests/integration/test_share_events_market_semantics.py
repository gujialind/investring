# ============ 集成测试：份额变动事件 market 语义（自 test_share_events.py 拆分，issue #469） ============
# 覆盖类（原文件顺序）：
#   - TestShareEventMarketResolve（#258）：创建 market 省略/空串自动补全、LOF 一码多市场
#     MARKET_AMBIGUOUS（含 available_markets）、产品存在性 404
#   - TestShareEventInputNormalize（#343）：platform_code 空串归一 NULL（基金级）/
#     平台级必填 PLATFORM_REQUIRED、product_code 必填/空串
#
# 本文件用例属 #258/#343，不依赖 #461；LOF 一码多市场基线（#461）的完整背景与回归断言
# 见 test_share_events_market_scoping.py 头部（断言落点）。

from datetime import date

from tests.factories import (
    create_portfolio, create_product, ensure_trading_day,
)
from app.models.share_change_event import ShareChangeEvent


class TestShareEventMarketResolve:
    """issue #258：事件创建 market 省略补全 / 一码多市场 / 产品存在性（口径同 #83 调仓创建）"""

    ENT = date(2025, 11, 3)
    EX = date(2025, 11, 4)

    def _setup(self, test_db, portfolio_code):
        create_portfolio(test_db, code=portfolio_code, status="active")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)

    def _payload(self, portfolio_code, product_code, market="SENTINEL"):
        body = {
            "portfolio_code": portfolio_code,
            "product_code": product_code,
            "event_type": "forced_adjustment",
            "ex_date": self.EX.isoformat(),
            "entitlement_date": self.ENT.isoformat(),
            "platform_code": "MYCF",
            "shares_change": 1.0,
        }
        if market != "SENTINEL":
            body["market"] = market
        return body

    def test_create_event_market_omitted_auto_completed(self, client, admin_headers, test_db):
        """省略 market：按产品唯一市场自动补全，创建成功"""
        self._setup(test_db, "SMR_P1")
        create_product(test_db, code="SMR_F1", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P1", "SMR_F1"),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["status"] == "pending"
        assert data["market"] == "CN_OTC"

    def test_create_event_market_empty_string_auto_completed(self, client, admin_headers, test_db):
        """前端实际场景：market 传空串同样自动补全（原 500 复现路径）"""
        self._setup(test_db, "SMR_P2")
        create_product(test_db, code="SMR_F2", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P2", "SMR_F2", market=""),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["market"] == "CN_OTC"

    def test_create_event_lof_market_ambiguous(self, client, admin_headers, test_db):
        """一码多市场（LOF）且未传 market：422 MARKET_AMBIGUOUS + available_markets"""
        self._setup(test_db, "SMR_P3")
        create_product(test_db, code="SMR_LOF", market="CN_OTC", product_type="LOF")
        create_product(test_db, code="SMR_LOF", market="CN_EXCHANGE",
                       product_type="LOF", confirm_days=0)

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P3", "SMR_LOF"),
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MARKET_AMBIGUOUS"
        assert detail["details"]["product_code"] == "SMR_LOF"
        assert detail["details"]["available_markets"] == ["CN_EXCHANGE", "CN_OTC"]

    def test_create_event_product_not_found(self, client, admin_headers, test_db):
        """产品代码不存在：404 PRODUCT_NOT_FOUND（不再 500）"""
        self._setup(test_db, "SMR_P4")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P4", "SMR_MISSING"),
            headers=admin_headers,
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "PRODUCT_NOT_FOUND"
        assert detail["details"] == {"product_code": "SMR_MISSING"}

    def test_create_event_explicit_valid_market_unchanged(self, client, admin_headers, test_db):
        """显式传合法 (product_code, market)：行为不变"""
        self._setup(test_db, "SMR_P5")
        create_product(test_db, code="SMR_F5", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P5", "SMR_F5", market="CN_OTC"),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["market"] == "CN_OTC"

    def test_create_event_explicit_invalid_market_combo(self, client, admin_headers, test_db):
        """显式传不存在的 (product_code, market) 组合：404 NOT_FOUND + available_markets"""
        self._setup(test_db, "SMR_P6")
        create_product(test_db, code="SMR_F6", market="CN_OTC")

        resp = client.post(
            "/api/share-change-events",
            json=self._payload("SMR_P6", "SMR_F6", market="CN_EXCHANGE"),
            headers=admin_headers,
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "NOT_FOUND"
        assert detail["details"]["available_markets"] == ["CN_OTC"]


class TestShareEventInputNormalize:
    """issue #343：platform_code 空串归一为 NULL（基金级）；product_code 必填"""

    ENT = date(2025, 11, 5)
    EX = date(2025, 11, 6)

    def _setup(self, test_db, portfolio_code, product_code):
        create_portfolio(test_db, code=portfolio_code, status="active")
        create_product(test_db, code=product_code, market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)

    def test_fund_level_empty_platform_code_normalized(self, client, admin_headers, test_db):
        """前端实际场景：基金级事件传空串 platform_code → 归一为 NULL，创建成功（原 500 复现路径）"""
        self._setup(test_db, "SCN_P1", "SCN_F1")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P1",
                "product_code": "SCN_F1",
                "market": "CN_OTC",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
                "platform_code": "",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()["platform_code"] is None
        event = (
            test_db.query(ShareChangeEvent)
            .filter(ShareChangeEvent.portfolio_code == "SCN_P1")
            .first()
        )
        assert event is not None and event.platform_code is None

    def test_platform_level_empty_platform_code_required(self, client, admin_headers, test_db):
        """平台级事件传空串 platform_code：仍报 422 PLATFORM_REQUIRED 而非 500"""
        self._setup(test_db, "SCN_P2", "SCN_F2")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P2",
                "product_code": "SCN_F2",
                "market": "CN_OTC",
                "event_type": "cash_dividend",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "div_cash": 0.5,
                "platform_code": "",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PLATFORM_REQUIRED"

    def test_missing_product_code_required(self, client, admin_headers, test_db):
        """省略 product_code：422 PRODUCT_REQUIRED（原 NOT NULL 外键违约 500）"""
        self._setup(test_db, "SCN_P3", "SCN_F3")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P3",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCT_REQUIRED"

    def test_empty_product_code_required(self, client, admin_headers, test_db):
        """空串 product_code 同样报 PRODUCT_REQUIRED"""
        self._setup(test_db, "SCN_P4", "SCN_F4")

        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": "SCN_P4",
                "product_code": "",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCT_REQUIRED"
