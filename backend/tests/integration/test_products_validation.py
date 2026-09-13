# ============ 集成测试：产品管理·确认参数取值校验（自 test_products.py 拆分，issue #469） ============
# 原「集成测试：产品管理」（test_products.py）的 confirm_days / nav_lag_days 取值校验部分；
# 其余三个文件同源拆分。
# 本文件覆盖：
#   - TestNavLagDaysValidation（#235/#240）：nav_lag_days >= 0、场内必须 0、显式 null 拒绝
#   - TestConfirmDaysValidation（#240）：confirm_days >= 0、场内必须 0、显式 null 拒绝，
#     创建路径显式优先、未传按 market+is_qdii 推导（#231/#236/#241）

from tests.factories import create_product


class TestNavLagDaysValidation:
    """issue #235/#240：nav_lag_days 取值校验——>=0；场内基金（CN_EXCHANGE）必须 0。

    #240 跟进 #5：负值校验收进 service 层（去掉 schema ge=0），
    同一业务规则统一 422 形状（detail.error=INVALID_NAV_LAG_DAYS）。

    覆盖：
    - 创建/更新负值 → 422 INVALID_NAV_LAG_DAYS（service 单一实现，统一形状）
    - 场内（CN_EXCHANGE）创建/更新 lag>0 → 422 INVALID_NAV_LAG_DAYS（service 跨字段）
    - 场外 QDII（CN_OTC lag=1）/ 互认（HK_MUTUAL lag=1）正常（回归，仅场内禁止）
    - market 迁移至 CN_EXCHANGE 但残留 lag>0 → 422（禁静默口径翻转）；同 PUT 显式置 0 → 成功
    """

    def test_create_nav_lag_days_negative_422(self, client, admin_headers):
        """>=0：创建传 -1 → 422 INVALID_NAV_LAG_DAYS（#240：service 层统一形状，非 pydantic 列表形状）"""
        resp = client.post(
            "/api/products",
            json={"code": "NL001.OF", "market": "CN_OTC", "name": "负值",
                  "product_type": "OEF", "nav_lag_days": -1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_NAV_LAG_DAYS"
        assert detail["details"]["nav_lag_days"] == -1

    def test_update_nav_lag_days_negative_422(self, client, admin_headers, test_db):
        """>=0：PUT 传 -1 → 422 INVALID_NAV_LAG_DAYS（#240：service 层统一形状，非 pydantic 列表形状）"""
        create_product(test_db, code="NL002.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/NL002.OF/CN_OTC",
            json={"nav_lag_days": -1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_NAV_LAG_DAYS"

    def test_create_exchange_nav_lag_positive_422(self, client, admin_headers):
        """场内（CN_EXCHANGE）创建 lag=1 → 422 INVALID_NAV_LAG_DAYS（跨字段）"""
        resp = client.post(
            "/api/products",
            json={"code": "NL003.SH", "market": "CN_EXCHANGE", "name": "场内滞后",
                  "product_type": "ETF", "nav_lag_days": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_NAV_LAG_DAYS"
        assert detail["details"]["market"] == "CN_EXCHANGE"

    def test_create_exchange_nav_lag_zero_ok(self, client, admin_headers):
        """场内创建 lag=0 → 成功（回归）"""
        resp = client.post(
            "/api/products",
            json={"code": "NL004.SH", "market": "CN_EXCHANGE", "name": "场内当日",
                  "product_type": "ETF", "nav_lag_days": 0},
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["nav_lag_days"] == 0

    def test_update_exchange_nav_lag_positive_422(self, client, admin_headers, test_db):
        """场内产品 PUT lag=1 → 422 INVALID_NAV_LAG_DAYS（跨字段）"""
        create_product(test_db, code="NL005.SH", market="CN_EXCHANGE",
                       product_type="ETF", nav_lag_days=0, confirm_days=0)
        resp = client.put(
            "/api/products/NL005.SH/CN_EXCHANGE",
            json={"nav_lag_days": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_NAV_LAG_DAYS"

    def test_update_otc_qdii_lag_one_ok(self, client, admin_headers, test_db):
        """场外 QDII lag=1 正常（回归，仅场内禁止；已由 test_update_nav_lag_days 覆盖 HK）"""
        create_product(test_db, code="NL006.OF", market="CN_OTC", nav_lag_days=1)
        resp = client.put(
            "/api/products/NL006.OF/CN_OTC",
            json={"nav_lag_days": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["nav_lag_days"] == 1

    def test_update_market_to_exchange_with_residual_lag_rejected(self, client, admin_headers, test_db):
        """CN_OTC (lag=1) → CN_EXCHANGE 未清 lag → 422（终态非法，禁静默口径翻转）"""
        create_product(test_db, code="NL007.OF", market="CN_OTC", nav_lag_days=1)
        resp = client.put(
            "/api/products/NL007.OF/CN_OTC",
            json={"market": "CN_EXCHANGE"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_NAV_LAG_DAYS"

    def test_update_market_to_exchange_with_lag_zero_ok(self, client, admin_headers, test_db):
        """CN_OTC (lag=1) → CN_EXCHANGE 且显式置 lag=0 → 成功（confirm_days 重推导 0）"""
        create_product(test_db, code="NL008.OF", market="CN_OTC", nav_lag_days=1)
        resp = client.put(
            "/api/products/NL008.OF/CN_OTC",
            json={"market": "CN_EXCHANGE", "nav_lag_days": 0},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["market"] == "CN_EXCHANGE"
        assert data["nav_lag_days"] == 0
        assert data["confirm_days"] == 0

    def test_update_nav_lag_days_null_422(self, client, admin_headers, test_db):
        """PUT 显式传 null → 422 INVALID_NAV_LAG_DAYS（service 拒绝：NOT NULL 列不允许以 null 清除）"""
        create_product(test_db, code="NL009.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/NL009.OF/CN_OTC",
            json={"nav_lag_days": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_NAV_LAG_DAYS"


class TestConfirmDaysValidation:
    """issue #240 跟进 #6：confirm_days 取值校验——>=0；场内（CN_EXCHANGE）必须 0；显式 null 拒绝。

    与 nav_lag_days 同决策（#240 跟进 #5）：校验收在 service 层（validate_confirm_days），
    统一 422 形状（detail.error=INVALID_CONFIRM_DAYS），schema 不加 ge 约束。
    创建路径（#231/#236/#241）：显式传入优先并校验，未传按 calculate_confirm_days 推导。

    覆盖：
    - PUT 负值 / 显式 null → 422 INVALID_CONFIRM_DAYS（此前负值 200 落库、读侧 or 0 静默当日确认）
    - 场内产品 PUT confirm_days>0 → 422（场内当天确认，与推导规则一致）
    - 场外合法值通过（回归）
    - market 迁移至 CN_EXCHANGE 未传 confirm_days → 重推导 0，不误报（回归）
    - market 迁移至 CN_EXCHANGE 且显式传非 0 → 422（终态校验）
    - 创建显式传合法值落库 / 不传按推导 / 显式非法值（负值、null、场内非 0）→ 422
    """

    def test_update_confirm_days_negative_422(self, client, admin_headers, test_db):
        """PUT 传 -1 → 422 INVALID_CONFIRM_DAYS（修复前负值 200 落库）"""
        create_product(test_db, code="CD001.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/CD001.OF/CN_OTC",
            json={"confirm_days": -1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_CONFIRM_DAYS"
        assert detail["details"]["confirm_days"] == -1

    def test_update_confirm_days_null_422(self, client, admin_headers, test_db):
        """PUT 显式传 null → 422 INVALID_CONFIRM_DAYS（列可空但读侧 or 0 静默当日确认，拒绝清除）"""
        create_product(test_db, code="CD002.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/CD002.OF/CN_OTC",
            json={"confirm_days": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CONFIRM_DAYS"

    def test_update_exchange_confirm_days_positive_422(self, client, admin_headers, test_db):
        """场内产品 PUT confirm_days=2 → 422 INVALID_CONFIRM_DAYS（场内当天确认，必须 0）"""
        create_product(test_db, code="CD003.SH", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0)
        resp = client.put(
            "/api/products/CD003.SH/CN_EXCHANGE",
            json={"confirm_days": 2},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_CONFIRM_DAYS"
        assert detail["details"]["market"] == "CN_EXCHANGE"

    def test_update_otc_confirm_days_positive_ok(self, client, admin_headers, test_db):
        """场外 PUT confirm_days=3 → 成功（回归：场外确认间隔可调）"""
        create_product(test_db, code="CD004.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/CD004.OF/CN_OTC",
            json={"confirm_days": 3},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["confirm_days"] == 3

    def test_update_market_to_exchange_rederives_confirm_days_ok(self, client, admin_headers, test_db):
        """CN_OTC(confirm_days=2) → CN_EXCHANGE 未传 confirm_days → 重推导 0，不误报（终态合法）"""
        create_product(test_db, code="CD005.OF", market="CN_OTC", confirm_days=2)
        resp = client.put(
            "/api/products/CD005.OF/CN_OTC",
            json={"market": "CN_EXCHANGE"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["market"] == "CN_EXCHANGE"
        assert data["confirm_days"] == 0

    def test_update_market_to_exchange_with_explicit_confirm_days_rejected(
        self, client, admin_headers, test_db
    ):
        """CN_OTC → CN_EXCHANGE 且显式传 confirm_days=1 → 422（合并终态：场内必须 0）"""
        create_product(test_db, code="CD006.OF", market="CN_OTC")
        resp = client.put(
            "/api/products/CD006.OF/CN_OTC",
            json={"market": "CN_EXCHANGE", "confirm_days": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CONFIRM_DAYS"

    def test_create_confirm_days_explicit_persisted(self, client, admin_headers):
        """issue #231/#236/#241：创建显式传合法值 → 落库为该值（显式优先，不再重推导）"""
        resp = client.post(
            "/api/products",
            json={"code": "CD007.OF", "market": "CN_OTC", "name": "创建显式",
                  "product_type": "OEF", "confirm_days": 2},
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["confirm_days"] == 2

    def test_create_confirm_days_negative_422(self, client, admin_headers):
        """创建显式传 -1 → 422 INVALID_CONFIRM_DAYS（修复前静默重推导落 1）"""
        resp = client.post(
            "/api/products",
            json={"code": "CD008.OF", "market": "CN_OTC", "name": "创建负值",
                  "product_type": "OEF", "confirm_days": -1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_CONFIRM_DAYS"
        assert detail["details"]["confirm_days"] == -1

    def test_create_confirm_days_null_422(self, client, admin_headers):
        """创建显式传 null → 422 INVALID_CONFIRM_DAYS（null 语义为清除，拒绝）"""
        resp = client.post(
            "/api/products",
            json={"code": "CD009.OF", "market": "CN_OTC", "name": "创建null",
                  "product_type": "OEF", "confirm_days": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CONFIRM_DAYS"

    def test_create_exchange_confirm_days_positive_422(self, client, admin_headers):
        """创建场内产品显式传 confirm_days=1 → 422（场内当天确认必须 0）"""
        resp = client.post(
            "/api/products",
            json={"code": "CD010.SH", "market": "CN_EXCHANGE", "name": "创建场内",
                  "product_type": "ETF", "confirm_days": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_CONFIRM_DAYS"
        assert detail["details"]["market"] == "CN_EXCHANGE"

    def test_create_confirm_days_omitted_derived(self, client, admin_headers):
        """创建不传 confirm_days → 按 market+is_qdii 推导（缺省推导，与修复前行为一致）"""
        resp = client.post(
            "/api/products",
            json={"code": "CD011.OF", "market": "CN_OTC", "name": "创建缺省",
                  "product_type": "OEF"},
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        # CN_OTC 非 QDII → 推导 1
        assert resp.json()["confirm_days"] == 1

    def test_create_hk_mutual_confirm_days_omitted_derived(self, client, admin_headers):
        """创建互认基金不传 confirm_days → 推导 1（其他市场分支，与 QDII=2 分支互补）"""
        resp = client.post(
            "/api/products",
            json={"code": "CD012.HK", "market": "HK_MUTUAL", "name": "创建互认",
                  "product_type": "OEF"},
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        assert resp.json()["confirm_days"] == 1
