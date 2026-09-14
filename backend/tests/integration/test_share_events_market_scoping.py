# ============ 集成测试：份额变动事件 market 收窄与列表/契约（自 test_share_events.py 拆分，issue #469） ============
# 覆盖类（原文件顺序）：
#   - TestShareChangeEventMarketScoping（#461）：LOF 双市场下确认/预览按 event.market 收窄
#   - TestShareChangeEventListFilter（#274）：列表服务端筛选 + 分页
#   - TestShareEventListProductName（#342）：list 读取侧派生 product_name
#   - TestShareEventOpenApiContract（#342）：openapi 分页响应契约守护
# ---- issue #461：LOF 一码多市场测试基线（同 code 双市场产品 + 双平台 + ENT/EX 交易日） ----
# 基线常量与 helper 由 share_event_helpers.py 承载：LOF461_CODE = "LOF461.SZ" 在 CN_EXCHANGE
# 与 CN_OTC 各建一条 LOF 产品记录；LOF461_ENT = 2025-12-08（权益登记日/基线快照日）、
# LOF461_EX = 2025-12-10（除息日）均置为交易日；平台为 MYCF / HBZQ。
# 事件持仓口径按 event.market 收窄：确认与预览共用同一 market 边界，另一市场的持仓与已录
# 事件不参与本市场的份额计算与平台覆盖校验，故同一 LOF 的两市场须分别录入事件。
# 本文件是 #461 回归断言的落点：TestShareChangeEventMarketScoping 全部用例基于该基线（由
# _setup_lof_baseline 构建）；#258/#343 的 market 补全与入参归一语义见 market_semantics.py。

import pytest
from datetime import date
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_product, create_platform,
    create_share_change_event, ensure_trading_day,
)
from app.models.share_change_event import ShareChangeEvent
from app.schemas.share_change_event import ShareChangeEventResponse

from tests.integration.share_event_helpers import (
    LOF461_CODE, LOF461_ENT, LOF461_EX, _setup_lof_baseline,
)


class TestShareChangeEventMarketScoping:
    """issue #461：LOF 一码多市场时事件持仓口径按 `event.market` 收窄。

    确认（_confirm_fund_level_event）、预览（resolve_entitlement_shares）两处必须是
    同一 market 边界——快照应用侧按 (产品, market, 平台) 精确匹配持仓行，跨市场聚合
    会份额错摊（同平台）或快照生成 POSITION_NOT_FOUND（跨平台）。
    """

    def _create(self, test_db, portfolio_code, *, event_type, market="CN_OTC", **kwargs):
        return create_share_change_event(
            test_db, portfolio_code, LOF461_CODE, market,
            event_type=event_type, ex_date=LOF461_EX, entitlement_date=LOF461_ENT,
            **{"status": "pending", **kwargs},
        )

    def _preview(self, client, admin_headers, event_id):
        resp = client.get(
            f"/api/share-change-events/{event_id}/preview", headers=admin_headers
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        return resp.json()["preview"]

    def _children(self, test_db, event_id):
        return test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == event_id
        ).all()

    def test_fund_level_split_same_platform_scopes_event_market(self, client, admin_headers, test_db):
        """同平台双市场（EX 100 / OTC 50）：拆分只摊派 event.market 持仓，子记录恰 1 条"""
        _setup_lof_baseline(test_db, "SPV_M1")  # 两市场同平台 MYCF
        event = self._create(test_db, "SPV_M1", event_type="share_split", ratio=Decimal("2"))

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()

        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert confirmed.entitlement_shares == Decimal("50.00")   # 仅 OTC，修复前 150
        assert confirmed.shares_change == Decimal("50.00")
        assert confirmed.shares_after == Decimal("100.00")

        children = self._children(test_db, event.id)
        assert len(children) == 1
        assert children[0].market == "CN_OTC"
        assert children[0].platform_code == "MYCF"
        assert children[0].entitlement_shares == Decimal("50.00")
        assert children[0].shares_after == Decimal("100.00")

    def test_fund_level_split_cross_platform_confirm_succeeds(self, client, admin_headers, test_db):
        """跨平台双市场（EX/MYCF、OTC/HBZQ）：子记录只覆盖 event.market 的行

        修复前子记录 platform 取自另一市场持仓行（CN_OTC/MYCF），快照应用时
        fund_key 不命中 → POSITION_NOT_FOUND 硬炸。"""
        _setup_lof_baseline(test_db, "SPV_M2", exch_platform="MYCF", otc_platform="HBZQ")
        event = self._create(test_db, "SPV_M2", event_type="share_split", ratio=Decimal("2"))

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()

        children = self._children(test_db, event.id)
        assert {(c.market, c.platform_code) for c in children} == {("CN_OTC", "HBZQ")}
        assert children[0].entitlement_shares == Decimal("50.00")
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert confirmed.entitlement_shares == Decimal("50.00")

    def test_fund_level_preview_scopes_event_market(self, client, admin_headers, test_db):
        """预览只含 event.market 持仓（修复前 150），且与确认落库值逐一相等"""
        _setup_lof_baseline(test_db, "SPV_M3")
        event = self._create(test_db, "SPV_M3", event_type="share_split", ratio=Decimal("2"))

        preview = self._preview(client, admin_headers, event.id)
        assert preview["entitlement_shares"] == 50.0   # 修复前 150（跨市场合计）
        assert preview["shares_change"] == 50.0
        assert preview["shares_after"] == 100.0

        client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert preview == {
            "entitlement_shares": float(confirmed.entitlement_shares),
            "shares_change": float(confirmed.shares_change),
            "shares_after": float(confirmed.shares_after),
            "cash_change": float(confirmed.cash_change),
        }

    @pytest.mark.parametrize(
        "event_type,expected",
        [
            ("cash_dividend", {"cash_change": 25.0, "shares_change": 0.0}),
            ("reinvest_dividend", {"cash_change": 0.0, "shares_change": 25.0}),
        ],
    )
    def test_platform_level_dividend_uses_event_market_shares(
        self, client, admin_headers, test_db, event_type, expected
    ):
        """平台级分红按 (market, platform) 读行：同平台双市场取 OTC 50，不是 EX 100"""
        _setup_lof_baseline(test_db, "SPV_M4")
        event = self._create(
            test_db, "SPV_M4", event_type=event_type, platform_code="MYCF",
            div_cash=Decimal("0.5"), reinvest_nav=Decimal("1"),
        )

        preview = self._preview(client, admin_headers, event.id)
        assert preview["entitlement_shares"] == 50.0   # 修复前 .first() 取 CN_EXCHANGE 100
        assert preview["cash_change"] == expected["cash_change"]
        assert preview["shares_change"] == expected["shares_change"]

        resp = client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        test_db.expire_all()
        confirmed = test_db.query(ShareChangeEvent).filter(ShareChangeEvent.id == event.id).one()
        assert float(confirmed.entitlement_shares) == preview["entitlement_shares"]
        assert float(confirmed.cash_change) == preview["cash_change"]
        assert float(confirmed.shares_change) == preview["shares_change"]

    def test_fund_level_event_missing_position_in_target_market(self, client, admin_headers, test_db):
        """event.market 无持仓、但另一市场有持仓时预览/确认均报 MISSING_POSITION_SNAPSHOT"""
        _setup_lof_baseline(test_db, "SPV_M5", otc_platform=None)  # 只建 CN_EXCHANGE 持仓
        event = self._create(test_db, "SPV_M5", event_type="share_split", ratio=Decimal("2"))

        preview = client.get(
            f"/api/share-change-events/{event.id}/preview", headers=admin_headers
        )
        assert preview.status_code == 422
        assert preview.json()["detail"]["error"] == "MISSING_POSITION_SNAPSHOT"

        confirm = client.post(
            f"/api/share-change-events/{event.id}/confirm", headers=admin_headers
        )
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "MISSING_POSITION_SNAPSHOT"

    def test_forced_adjustment_position_not_found_for_target_market(self, client, admin_headers, test_db):
        """forced_adjustment 按 (market, platform) 精查，目标 market 无持仓报 POSITION_NOT_FOUND"""
        _setup_lof_baseline(test_db, "SPV_M6", otc_platform=None)  # 只建 CN_EXCHANGE 持仓
        event = self._create(
            test_db, "SPV_M6", event_type="forced_adjustment",
            platform_code="MYCF", shares_change=Decimal("-10"),
        )

        preview = client.get(
            f"/api/share-change-events/{event.id}/preview", headers=admin_headers
        )
        assert preview.status_code == 422
        assert preview.json()["detail"]["error"] == "POSITION_NOT_FOUND"

        confirm = client.post(
            f"/api/share-change-events/{event.id}/confirm", headers=admin_headers
        )
        assert confirm.status_code == 422
        assert confirm.json()["detail"]["error"] == "POSITION_NOT_FOUND"


class TestShareChangeEventListFilter:
    """列表服务端筛选 + 分页（#274，形态对齐调仓列表 #126/#155）"""

    PORT = "SCE_FLT"

    def _seed(self, test_db):
        create_portfolio(test_db, code=self.PORT, status="active")
        create_product(test_db, code="FUND_FLTA", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_product(test_db, code="FUND_FLTB", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="FLT_PA")
        create_platform(test_db, code="FLT_PB")
        # e1：平台级现金分红，pending
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTA", "CN_OTC", event_type="cash_dividend",
            ex_date=date(2025, 11, 3), entitlement_date=date(2025, 10, 31),
            status="pending", platform_code="FLT_PA", div_cash=Decimal("0.1"))
        # e2：基金级拆分，confirmed，platform 为空
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTA", "CN_OTC", event_type="share_split",
            ex_date=date(2025, 11, 10), entitlement_date=date(2025, 11, 7),
            status="confirmed")
        # e3：平台级强制调整，pending，另一产品/平台
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTB", "CN_EXCHANGE", event_type="forced_adjustment",
            ex_date=date(2025, 12, 1), entitlement_date=date(2025, 11, 28),
            status="pending", platform_code="FLT_PB", shares_change=Decimal("10"))
        # e4：平台级现金分红，cancelled
        create_share_change_event(
            test_db, self.PORT, "FUND_FLTB", "CN_EXCHANGE", event_type="cash_dividend",
            ex_date=date(2025, 12, 5), entitlement_date=date(2025, 12, 4),
            status="cancelled", platform_code="FLT_PA", div_cash=Decimal("0.2"))

    def _list(self, client, admin_headers, query=""):
        resp = client.get(
            f"/api/share-change-events?portfolio_code={self.PORT}{query}",
            headers=admin_headers)
        assert resp.status_code == 200
        return resp.json()

    def test_filter_by_status(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&status=pending")
        assert data["total"] == 2
        assert all(i["status"] == "pending" for i in data["items"])

    def test_filter_by_event_type(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&event_type=cash_dividend")
        assert data["total"] == 2

    def test_filter_by_product_code(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&product_code=FUND_FLTA")
        assert data["total"] == 2

    def test_filter_products_multi_pairs(self, client, admin_headers, test_db):
        """products 复合多选命中 (code, market) 精确对；串市场不命中"""
        self._seed(test_db)
        data = self._list(
            client, admin_headers,
            "&products=FUND_FLTA|CN_OTC,FUND_FLTB|CN_EXCHANGE")
        assert data["total"] == 4
        assert self._list(client, admin_headers, "&products=FUND_FLTA|CN_EXCHANGE")["total"] == 0

    def test_filter_products_conflict_with_product_code(self, client, admin_headers, test_db):
        self._seed(test_db)
        resp = client.get(
            "/api/share-change-events?products=FUND_FLTA|CN_OTC&product_code=FUND_FLTA",
            headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PRODUCTS_PARAM_CONFLICT"

    def test_filter_by_platform_excludes_fund_level(self, client, admin_headers, test_db):
        """平台筛选只命中平台级记录，基金级父记录（platform 空）不在结果内"""
        self._seed(test_db)
        data = self._list(client, admin_headers, "&platform_code=FLT_PA")
        assert data["total"] == 2
        assert all(i["platform_code"] == "FLT_PA" for i in data["items"])

    def test_filter_ex_date_range_closed(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&ex_date_start=2025-12-01&ex_date_end=2025-12-31")
        assert data["total"] == 2
        assert {i["event_type"] for i in data["items"]} == {"forced_adjustment", "cash_dividend"}

    def test_filter_ex_date_range_invalid(self, client, admin_headers, test_db):
        self._seed(test_db)
        resp = client.get(
            "/api/share-change-events?ex_date_start=2025-12-31&ex_date_end=2025-12-01",
            headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_RANGE"

    def test_combined_filters(self, client, admin_headers, test_db):
        self._seed(test_db)
        data = self._list(client, admin_headers, "&status=pending&product_code=FUND_FLTA")
        assert data["total"] == 1
        assert data["items"][0]["event_type"] == "cash_dividend"

    def test_pagination_total_is_filtered(self, client, admin_headers, test_db):
        self._seed(test_db)
        page1 = self._list(client, admin_headers, "&status=pending&page_size=1")
        assert page1["total"] == 2 and len(page1["items"]) == 1
        page2 = self._list(client, admin_headers, "&status=pending&page_size=1&page=2")
        assert len(page2["items"]) == 1
        assert page1["items"][0]["id"] != page2["items"][0]["id"]


class TestShareEventListProductName:
    """list 响应读侧派生 product_name（#342，同调仓 #175 口径）"""

    ENT = date(2025, 11, 10)
    EX = date(2025, 11, 11)

    def _create_split(self, client, admin_headers, portfolio_code, product_code):
        resp = client.post(
            "/api/share-change-events",
            json={
                "portfolio_code": portfolio_code,
                "product_code": product_code,
                "market": "CN_OTC",
                "event_type": "share_split",
                "ex_date": self.EX.isoformat(),
                "entitlement_date": self.ENT.isoformat(),
                "ratio": 2.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"

    def test_list_events_includes_product_name(self, client, admin_headers, test_db):
        """两个不同产品的基金级事件，list 每条 item 均应带各自 product_name"""
        create_portfolio(test_db, code="EPN_P1", status="active")
        create_product(test_db, code="EPN_F1", market="CN_OTC", name="测试基金一",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_product(test_db, code="EPN_F2", market="CN_OTC", name="测试基金二",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)
        self._create_split(client, admin_headers, "EPN_P1", "EPN_F1")
        self._create_split(client, admin_headers, "EPN_P1", "EPN_F2")

        resp = client.get("/api/share-change-events?portfolio_code=EPN_P1", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        name_by_product = {item["product_code"]: item["product_name"] for item in data["items"]}
        assert name_by_product == {"EPN_F1": "测试基金一", "EPN_F2": "测试基金二"}
        # 字段完整性（#183 口径）：挂 response_model 后响应键与 schema 声明一一对应
        assert set(data["items"][0].keys()) == set(ShareChangeEventResponse.model_fields.keys())


class TestShareEventOpenApiContract:
    """openapi 契约守护（#342，同 #183 口径）：事件列表分页响应结构化"""

    def test_share_events_list_openapi_references_paginated_schema(self, client):
        """openapi.json 中 /api/share-change-events GET 200 应引用
        PaginatedShareEventResponse，且 items 元素指向 ShareChangeEventResponse
        （含 product_name），而非裸 ORM 空 schema。"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()

        get_op = spec["paths"]["/api/share-change-events"]["get"]
        schema_ref = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema_ref == {"$ref": "#/components/schemas/PaginatedShareEventResponse"}

        schemas = spec["components"]["schemas"]
        paginated = schemas["PaginatedShareEventResponse"]
        assert set(paginated["required"]) == {"items", "total", "page", "page_size"}
        assert set(paginated["properties"].keys()) == {
            "items", "total", "page", "page_size",
        }
        assert paginated["properties"]["items"]["items"] == {
            "$ref": "#/components/schemas/ShareChangeEventResponse"
        }

        event_props = schemas["ShareChangeEventResponse"]["properties"]
        assert "product_name" in event_props  # 防止误删读侧派生字段声明
