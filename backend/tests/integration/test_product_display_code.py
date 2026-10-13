# ============ 集成测试：产品显示码（issue #705） ============
# 覆盖：
#   - 显示码单点口径（剥后缀 / 港互认 6 位 / 映射缺失回退原码 / 系统码透传）
#   - 产品列表 keyword 命中 hk_sale_code（968050 搜到 10 位码产品）
#   - 行级读模型 display_code 与 product_name 同批填充（trades 列表）
#   - 港互认录入归一：6 位→10 位入库 + hk_sale_code 落 6 位；映射查不到 422；
#     排名表上游不可达同样 422（不漏 500）；10 位反查失败不阻断创建（hk_sale_code 落 NULL）

import sys
from types import SimpleNamespace

import pytest

from app.models.product import Product
from app.services import akshare_client
from app.services.exceptions import BusinessError
from app.services.product_service import create_product
from tests.factories import (
    create_portfolio,
    create_product as factory_create_product,
    create_trade,
)

# 维度值取 conftest 种子已存在的字典行（create_product 会校验适用矩阵与存在性）
DIMS = dict(
    asset_class_code="ASSET_STOCK",
    region_code="REGION_CN",
    style_code="STYLE_BALANCED",
    size_code="SIZE_LARGE",
    segment_code="SEG_COMPOSITE",
)

HK = "1001767344"
HK6 = "968050"
# 录入归一用例用非种子码（seed_base 已含 HK，create 会 ALREADY_EXISTS）
HK_NEW = "1001767388"


def _hk_product(test_db, code=HK, hk_sale_code=HK6):
    product = factory_create_product(
        test_db, code=code, market="HK_MUTUAL", name="摩根国际债券", product_type="OEF"
    )
    product.hk_sale_code = hk_sale_code
    test_db.commit()
    test_db.refresh(product)
    return product


class TestDisplayCodeDerivation:
    """显示码口径（app.utils.product_code.display_code 经 ProductResponse 单点导出）"""

    def test_suffix_stripped_for_tushare_markets(self, client, admin_headers, test_db):
        factory_create_product(test_db, code="510300.SH", market="CN_EXCHANGE")
        resp = client.get("/api/products?keyword=510300.SH", headers=admin_headers)
        item = resp.json()["items"][0]
        assert item["code"] == "510300.SH"
        assert item["display_code"] == "510300"

    def test_system_codes_pass_through(self, client, admin_headers):
        resp = client.get("/api/products?keyword=CASH&include_virtual=true", headers=admin_headers)
        codes = {i["code"]: i["display_code"] for i in resp.json()["items"]}
        assert codes["CASH"] == "CASH"

    def test_hk_uses_stored_sale_code(self, client, admin_headers, test_db):
        _hk_product(test_db)
        resp = client.get(f"/api/products/{HK}/HK_MUTUAL", headers=admin_headers)
        body = resp.json()
        assert body["code"] == HK
        assert body["hk_sale_code"] == HK6
        assert body["display_code"] == HK6

    def test_hk_falls_back_to_original_when_unmapped(self, client, admin_headers, test_db):
        _hk_product(test_db, code="1001767399", hk_sale_code=None)
        resp = client.get("/api/products/1001767399/HK_MUTUAL", headers=admin_headers)
        body = resp.json()
        assert body["hk_sale_code"] is None
        assert body["display_code"] == "1001767399"


class TestKeywordHitsSaleCode:
    """#705 验收：输入 6 位销售码能搜到库内 10 位码产品"""

    def test_keyword_matches_hk_sale_code(self, client, admin_headers, test_db):
        _hk_product(test_db)
        factory_create_product(test_db, code="510300.SH", market="CN_EXCHANGE")
        resp = client.get(f"/api/products?keyword={HK6}", headers=admin_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == HK

    def test_keyword_still_matches_code_and_name(self, client, admin_headers, test_db):
        _hk_product(test_db)
        resp = client.get("/api/products?keyword=1001767344", headers=admin_headers)
        assert resp.json()["total"] == 1
        resp = client.get("/api/products?keyword=摩根", headers=admin_headers)
        assert resp.json()["total"] == 1


class TestRowLevelDisplayCode:
    """行级读模型与 product_name 同批填充（仅 list 端点）"""

    def test_trade_list_carries_display_code(self, client, admin_headers, test_db):
        _hk_product(test_db)
        portfolio = create_portfolio(test_db, code="PF705")
        create_trade(
            test_db,
            portfolio_code=portfolio.code,
            product_code=HK,
            market="HK_MUTUAL",
        )
        resp = client.get(
            f"/api/trades?portfolio_code={portfolio.code}", headers=admin_headers
        )
        items = resp.json()["items"]
        assert items and items[0]["product_code"] == HK
        assert items[0]["display_code"] == HK6

    def test_single_trade_display_code_stays_null(
        self, client, admin_headers, test_db
    ):
        _hk_product(test_db)
        portfolio = create_portfolio(test_db, code="PF705B")
        trade = create_trade(
            test_db,
            portfolio_code=portfolio.code,
            product_code=HK,
            market="HK_MUTUAL",
        )
        resp = client.get(f"/api/trades/{trade.id}", headers=admin_headers)
        # 单对象端点不 join 产品表（同 product_name 口径）：消费方回退本地格式化
        assert resp.json()["display_code"] is None


class TestHkCreateNormalization:
    """港互认录入归一（create_product 单点）"""

    def test_six_digit_input_resolves_to_ten_digit(self, test_db, monkeypatch):
        monkeypatch.setattr(
            akshare_client, "_ensure_hk_code_map", lambda _ak: {HK6: HK_NEW}
        )
        product = create_product(
            test_db, code=HK6, market="HK_MUTUAL", name="摩根国际债券",
            product_type="OEF", **DIMS,
        )
        test_db.commit()
        assert product.code == HK_NEW
        assert product.hk_sale_code == HK6

    def test_unmapped_six_digit_rejected(self, test_db, monkeypatch):
        monkeypatch.setattr(akshare_client, "_ensure_hk_code_map", lambda _ak: {})
        with pytest.raises(BusinessError) as exc:
            create_product(
                test_db, code="969999", market="HK_MUTUAL", name="不存在",
                product_type="OEF", **DIMS,
            )
        assert exc.value.code == "HK_CODE_UNMAPPED"
        # 拒绝即零写入
        test_db.rollback()

    def test_ten_digit_input_reverse_fills_sale_code(self, test_db, monkeypatch):
        monkeypatch.setattr(
            akshare_client, "_ensure_hk_code_map", lambda _ak: {HK6: HK_NEW}
        )
        product = create_product(
            test_db, code=HK_NEW, market="HK_MUTUAL", name="摩根国际债券",
            product_type="OEF", **DIMS,
        )
        test_db.commit()
        assert product.hk_sale_code == HK6

    def test_reverse_lookup_failure_does_not_block_create(self, test_db, monkeypatch):
        def explode(_ak):
            raise AssertionError("反查失败不应阻断创建")

        monkeypatch.setattr(akshare_client, "_ensure_hk_code_map", explode)
        monkeypatch.setattr(akshare_client, "_hk_code_map", None)
        product = create_product(
            test_db, code=HK_NEW, market="HK_MUTUAL", name="摩根国际债券",
            product_type="OEF", **DIMS,
        )
        test_db.commit()
        assert product.code == HK_NEW
        assert product.hk_sale_code is None


class TestHkUpstreamUnavailable:
    """排名表上游不可达时的建档口径（#705 评审 Blocker）。

    与「表里查无此码」同归 422 `HK_CODE_UNMAPPED`：归一发生在 `akshare_client`
    的取数点（`_retry`），service 层不漏非领域异常，故不会冒到全局 handler 变 500。
    刻意打桩最外层上游（`akshare.fund_hk_rank_em`）而不是 `_ensure_hk_code_map`——
    桩在后者上就绕过了被测的归一环节，异常形态也不再是「上游原样抛出」。
    """

    @pytest.fixture
    def unreachable_rank_table(self, monkeypatch):
        def unreachable():
            raise RuntimeError("上游 fund_hk_rank_em 超时/连接失败")

        monkeypatch.setitem(
            sys.modules, "akshare", SimpleNamespace(fund_hk_rank_em=unreachable)
        )
        monkeypatch.setattr(akshare_client, "_hk_code_map", None)
        # `_retry` 的限流与指数退避是真 sleep（3 次尝试 ≈ 6s），本用例只关心归一结果
        monkeypatch.setattr(akshare_client, "_rate_limit_sleep", lambda: None)
        monkeypatch.setattr(akshare_client.time, "sleep", lambda seconds: None)

    def test_six_digit_create_returns_422_not_500(
        self, client, admin_headers, test_db, unreachable_rank_table
    ):
        resp = client.post(
            "/api/products",
            json={
                "code": HK6, "market": "HK_MUTUAL", "name": "摩根国际债券",
                "product_type": "OEF", **DIMS,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "HK_CODE_UNMAPPED"

    def test_rejected_create_writes_nothing(
        self, test_db, unreachable_rank_table
    ):
        with pytest.raises(BusinessError) as exc:
            create_product(
                test_db, code=HK6, market="HK_MUTUAL", name="摩根国际债券",
                product_type="OEF", **DIMS,
            )
        assert exc.value.code == "HK_CODE_UNMAPPED"
        test_db.rollback()
        assert test_db.query(Product).filter(Product.hk_sale_code == HK6).count() == 0
