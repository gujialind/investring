# ============ 集成测试：产品管理·列表筛选与排序（自 test_products.py 拆分，issue #469） ============
# 原「集成测试：产品管理」（test_products.py）的产品列表查询部分；其余三个文件同源拆分。
# 本文件覆盖：
#   - TestProductKeywordFilter（#155）：code/name ilike OR 模糊匹配，含 % 字面化
#   - TestProductListAttrFilter（#238）：confirm_days / nav_lag_days / is_qdii 等值筛选，0/False 假值陷阱回归
#   - TestProductListVirtualFilter（#327）：虚拟产品默认排除，include_virtual=true 显式包含
#   - TestProductListDimFilter（#128）：五维等值筛选与 AND 叠加
#   - TestProductListOrder（#165）：created_at DESC + code ASC 确定性排序

import time

from tests.factories import create_product


class TestProductKeywordFilter:
    """产品列表 keyword 模糊筛选（issue #155）：code/name ilike OR 匹配"""

    def test_keyword_matches_code_fragment(self, client, admin_headers, test_db):
        """code 片段命中"""
        create_product(test_db, code="510300.SH", market="CN_EXCHANGE", name="沪深300ETF")
        create_product(test_db, code="000001.OF", market="CN_OTC", name="平安大华基金")
        resp = client.get("/api/products?keyword=5103", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == "510300.SH"

    def test_keyword_matches_name_fragment(self, client, admin_headers, test_db):
        """name 片段命中"""
        create_product(test_db, code="510300.SH", market="CN_EXCHANGE", name="沪深300ETF")
        create_product(test_db, code="000001.OF", market="CN_OTC", name="平安大华基金")
        resp = client.get("/api/products?keyword=大华", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "平安大华基金"

    def test_keyword_percent_literalized(self, client, admin_headers, test_db):
        """含 % 的输入被字面化，不触发通配"""
        create_product(test_db, code="900001.OF", market="CN_OTC", name="收益5%增强")
        create_product(test_db, code="900002.OF", market="CN_OTC", name="收益500增强")
        resp = client.get("/api/products?keyword=5%25", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        # % 若未转义，"收益500增强" 也会被通配命中
        assert data["total"] == 1
        assert data["items"][0]["name"] == "收益5%增强"

    def test_keyword_and_other_filters(self, client, admin_headers, test_db):
        """keyword 与既有筛选参数 AND 叠加"""
        create_product(test_db, code="510300.SH", market="CN_EXCHANGE",
                       name="沪深300ETF", product_type="ETF")
        create_product(test_db, code="510300.OF", market="CN_OTC",
                       name="沪深300联接", product_type="OEF")
        resp = client.get(
            "/api/products?keyword=510300&product_type=OEF",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == "510300.OF"


class TestProductListAttrFilter:
    """产品列表属性等值筛选（issue #238）：confirm_days / nav_lag_days / is_qdii。

    断言风格：谓词全称（结果集每项都满足等值）+ 自建 code 必在/必不在结果集，
    不对种子无关的绝对 total 下断言（种子见 tests/seed_base.py）。
    0 / False 为合法筛选值，覆盖「if param: 假值陷阱」回归。
    """

    def test_confirm_days_filter(self, client, admin_headers, test_db):
        """?confirm_days=2 → 结果每项 confirm_days==2 且自建 code 在列"""
        create_product(test_db, code="AF001.OF", market="CN_OTC", confirm_days=2)
        create_product(test_db, code="AF002.OF", market="CN_OTC", confirm_days=1)
        resp = client.get("/api/products?confirm_days=2", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert len(items) > 0
        # total 契约（评审 #244）：单页装得下时 total == len(items)，承重过滤后的 count
        assert data["total"] == len(items)
        assert all(i["confirm_days"] == 2 for i in items)
        codes = [i["code"] for i in items]
        assert "AF001.OF" in codes
        assert "AF002.OF" not in codes

    def test_confirm_days_zero_matches_virtual(self, client, admin_headers):
        """?confirm_days=0 验证 0 值不被 `if param:` 假值陷阱跳过；
        #327：默认排除虚拟产品，include_virtual=true 时种子 CASH/IN_TRANSIT 一并命中"""
        resp = client.get("/api/products?confirm_days=0", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert data["total"] == len(items)
        assert all(i["confirm_days"] == 0 for i in items)
        codes = [i["code"] for i in items]
        assert "510300.SH" in codes
        for virtual_code in ("CASH", "IN_TRANSIT_BUY", "IN_TRANSIT_SELL"):
            assert virtual_code not in codes

        resp_all = client.get(
            "/api/products?confirm_days=0&include_virtual=true", headers=admin_headers
        )
        assert resp_all.status_code == 200
        codes_all = [i["code"] for i in resp_all.json()["items"]]
        for seed_code in ("CASH", "IN_TRANSIT_BUY", "IN_TRANSIT_SELL", "510300.SH"):
            assert seed_code in codes_all

    def test_nav_lag_days_filter(self, client, admin_headers, test_db):
        """?nav_lag_days=1 → 谓词成立且自建 code 在列；?nav_lag_days=0 不含该 code"""
        create_product(test_db, code="AF003.OF", market="CN_OTC", nav_lag_days=1)
        resp = client.get("/api/products?nav_lag_days=1", headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) > 0
        assert resp.json()["total"] == len(items)
        assert all(i["nav_lag_days"] == 1 for i in items)
        assert "AF003.OF" in [i["code"] for i in items]

        resp0 = client.get("/api/products?nav_lag_days=0", headers=admin_headers)
        assert resp0.status_code == 200
        items0 = resp0.json()["items"]
        assert resp0.json()["total"] == len(items0)
        assert all(i["nav_lag_days"] == 0 for i in items0)
        assert "AF003.OF" not in [i["code"] for i in items0]

    def test_is_qdii_true_false(self, client, admin_headers, test_db):
        """?is_qdii=true 含自建 QDII；?is_qdii=false 不含（false 分支必测，防 `if is_qdii:` 回归）"""
        create_product(test_db, code="AF004.OF", market="CN_OTC", is_qdii=True, confirm_days=2)
        resp_true = client.get("/api/products?is_qdii=true", headers=admin_headers)
        assert resp_true.status_code == 200
        items_true = resp_true.json()["items"]
        assert resp_true.json()["total"] == len(items_true)
        assert all(i["is_qdii"] is True for i in items_true)
        assert "AF004.OF" in [i["code"] for i in items_true]

        resp_false = client.get("/api/products?is_qdii=false", headers=admin_headers)
        assert resp_false.status_code == 200
        items_false = resp_false.json()["items"]
        assert resp_false.json()["total"] == len(items_false)
        assert all(i["is_qdii"] is False for i in items_false)
        assert "AF004.OF" not in [i["code"] for i in items_false]

    def test_filters_and_combined(self, client, admin_headers, test_db):
        """三参数与既有参数 AND 叠加（仿 test_keyword_and_other_filters 风格）"""
        create_product(test_db, code="AF005.OF", market="CN_OTC",
                       product_type="OEF", confirm_days=1, is_qdii=False)
        create_product(test_db, code="AF006.OF", market="CN_OTC",
                       product_type="OEF", confirm_days=1, is_qdii=True)
        resp = client.get(
            "/api/products?confirm_days=1&is_qdii=false&product_type=OEF",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert data["total"] == len(items)
        assert all(
            i["confirm_days"] == 1 and i["is_qdii"] is False and i["product_type"] == "OEF"
            for i in items
        )
        codes = [i["code"] for i in items]
        assert "AF005.OF" in codes
        assert "AF006.OF" not in codes

    def test_filter_with_pagination(self, client, admin_headers, test_db):
        """筛选×分页组合（评审 #244）：页间不相交、total 跨页不变且等于未分页口径"""
        create_product(test_db, code="AF010.SH", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0, nav_lag_days=0)
        create_product(test_db, code="AF011.SH", market="CN_EXCHANGE",
                       product_type="ETF", confirm_days=0, nav_lag_days=0)
        baseline = client.get(
            "/api/products?confirm_days=0&page_size=100", headers=admin_headers
        ).json()
        expected_total = baseline["total"]
        assert expected_total >= 4  # 种子 confirm_days=0 非虚拟 2 只（510300.SH/161017.SZ）+ 自建 2 只（#327 起虚拟产品默认排除）

        page1 = client.get(
            "/api/products?confirm_days=0&page_size=2&page=1", headers=admin_headers
        ).json()
        page2 = client.get(
            "/api/products?confirm_days=0&page_size=2&page=2", headers=admin_headers
        ).json()
        last_page = (expected_total + 1) // 2
        page_last = client.get(
            f"/api/products?confirm_days=0&page_size=2&page={last_page}",
            headers=admin_headers,
        ).json()

        # total 契约：跨页不变，且与未分页口径一致；过滤谓词跨页成立
        for payload in (page1, page2, page_last):
            assert payload["total"] == expected_total
            assert all(i["confirm_days"] == 0 for i in payload["items"])
        # 页容量与页间不相交（(code, market) 为产品自然键）
        assert len(page1["items"]) == 2
        assert len(page2["items"]) == 2
        keys1 = {(i["code"], i["market"]) for i in page1["items"]}
        keys2 = {(i["code"], i["market"]) for i in page2["items"]}
        assert keys1.isdisjoint(keys2)
        # 末页恰好收尾：全集可被逐页不重不漏遍历
        assert len(page_last["items"]) == expected_total - 2 * (last_page - 1)

    def test_backward_compat_no_params(self, client, admin_headers, test_db):
        """不传三参数时不过滤：结果集混合多种 confirm_days / is_qdii 取值
        （#327：虚拟产品默认排除，confirm_days=0 假值由真实种子 510300.SH 承重）"""
        create_product(test_db, code="AF007.OF", market="CN_OTC", confirm_days=2, is_qdii=True)
        resp = client.get("/api/products?page_size=100", headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        codes = [i["code"] for i in items]
        assert "AF007.OF" in codes  # is_qdii=True 未被默认排除
        assert "510300.SH" in codes  # confirm_days=0 未被默认排除
        assert "CASH" not in codes  # 虚拟产品默认排除（#327）
        assert {i["is_qdii"] for i in items} == {True, False}
        assert len({i["confirm_days"] for i in items}) > 1


class TestProductListVirtualFilter:
    """虚拟产品排除（issue #327）：product_type ∈ {CASH, IN_TRANSIT} 的虚拟产品默认不出现在列表，
    include_virtual=true 显式包含；排除发生在服务端过滤（分页 total 随之变化，非客户端剔除）。"""

    VIRTUAL_CODES = ("CASH", "IN_TRANSIT_BUY", "IN_TRANSIT_SELL")

    def test_default_excludes_virtual(self, client, admin_headers):
        """无参默认：三个系统虚拟产品不在列、真实种子在列，total 为排除后口径"""
        resp = client.get("/api/products?page_size=100", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(data["items"])
        codes = [i["code"] for i in data["items"]]
        for code in self.VIRTUAL_CODES:
            assert code not in codes
        assert "510300.SH" in codes
        assert all(i["product_type"] not in ("CASH", "IN_TRANSIT") for i in data["items"])

    def test_include_virtual_true(self, client, admin_headers):
        """include_virtual=true：虚拟产品出现，total 含之"""
        resp = client.get(
            "/api/products?page_size=100&include_virtual=true", headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(data["items"])
        codes = [i["code"] for i in data["items"]]
        for code in self.VIRTUAL_CODES:
            assert code in codes

    def test_include_virtual_false_same_as_default(self, client, admin_headers):
        """显式 include_virtual=false 与不传等价"""
        default = client.get("/api/products?page_size=100", headers=admin_headers).json()
        explicit = client.get(
            "/api/products?page_size=100&include_virtual=false", headers=admin_headers
        ).json()
        assert explicit["total"] == default["total"]
        assert [i["code"] for i in explicit["items"]] == [i["code"] for i in default["items"]]

    def test_pagination_total_reflects_exclusion(self, client, admin_headers):
        """分页契约：排除发生在服务端，include_virtual 开/关的 total 差恰为种子虚拟产品数"""
        excluded = client.get("/api/products?page_size=100", headers=admin_headers).json()
        included = client.get(
            "/api/products?page_size=100&include_virtual=true", headers=admin_headers
        ).json()
        assert included["total"] - excluded["total"] == len(self.VIRTUAL_CODES)

    def test_keyword_does_not_resurrect_virtual(self, client, admin_headers):
        """keyword 命中虚拟产品 code 时默认仍排除；include_virtual=true 才命中"""
        resp = client.get("/api/products?keyword=CASH", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        resp_all = client.get(
            "/api/products?keyword=CASH&include_virtual=true", headers=admin_headers
        )
        assert "CASH" in [i["code"] for i in resp_all.json()["items"]]

    def test_explicit_product_type_cash_default_empty(self, client, admin_headers):
        """AND 语义：product_type=CASH 与默认排除叠加 → 空；include_virtual=true → 命中种子 CASH"""
        resp = client.get("/api/products?product_type=CASH", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        resp_all = client.get(
            "/api/products?product_type=CASH&include_virtual=true", headers=admin_headers
        )
        assert resp_all.json()["total"] == 1
        assert resp_all.json()["items"][0]["code"] == "CASH"


class TestProductListDimFilter:
    """五维 list 筛选（issue #128 遗留缺口，评审 #244）：asset_class/region/segment 等值与 AND 叠加。

    断言风格同 TestProductListAttrFilter：谓词全称 + 自建 code 必在/必不在 + total 契约。
    """

    def test_asset_class_filter(self, client, admin_headers, test_db):
        """?asset_class_code=ASSET_BOND → 自建债券在列、默认工厂产品（ASSET_STOCK）不在列"""
        create_product(test_db, code="AF012.OF", market="CN_OTC",
                       asset_class_code="ASSET_BOND", region_code="REGION_CN",
                       style_code=None, size_code=None, segment_code="SEG_BOND_SHORT")
        create_product(test_db, code="AF013.OF", market="CN_OTC")  # 默认 ASSET_STOCK
        resp = client.get("/api/products?asset_class_code=ASSET_BOND", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert len(items) > 0
        assert data["total"] == len(items)
        assert all(i["asset_class_code"] == "ASSET_BOND" for i in items)
        codes = [i["code"] for i in items]
        assert "AF012.OF" in codes
        assert "AF013.OF" not in codes

    def test_region_and_segment_combined(self, client, admin_headers, test_db):
        """region_code × segment_code AND 叠加；单维对照（SEG_COMPOSITE）不含自建 code"""
        create_product(test_db, code="AF014.OF", market="CN_OTC",
                       asset_class_code="ASSET_BOND", region_code="REGION_CN",
                       style_code=None, size_code=None, segment_code="SEG_BOND_SHORT")
        resp = client.get(
            "/api/products?region_code=REGION_CN&segment_code=SEG_BOND_SHORT",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert len(items) > 0
        assert data["total"] == len(items)
        assert all(
            i["region_code"] == "REGION_CN" and i["segment_code"] == "SEG_BOND_SHORT"
            for i in items
        )
        assert "AF014.OF" in [i["code"] for i in items]

        resp2 = client.get("/api/products?segment_code=SEG_COMPOSITE", headers=admin_headers)
        assert resp2.status_code == 200
        assert "AF014.OF" not in [i["code"] for i in resp2.json()["items"]]


class TestProductListOrder:
    """产品 list 确定性排序（issue #165）：created_at DESC + code ASC"""

    def test_new_product_on_first_page(self, client, admin_headers, test_db):
        """新建产品必然出现在 page_size=50 首页（#162 下拉验收前提）"""
        create_product(test_db, code="960001.OF", market="CN_OTC", name="排序测试基金")
        resp = client.get("/api/products?page_size=50", headers=admin_headers)
        assert resp.status_code == 200
        codes = [i["code"] for i in resp.json()["items"]]
        assert "960001.OF" in codes

    def test_created_desc_tiebreak_code_and_stable(self, client, admin_headers, test_db):
        """新建优先；同秒并列按 code 定序；重复请求顺序稳定"""
        create_product(test_db, code="960010.OF", market="CN_OTC", name="排序旧基金")
        time.sleep(1.1)  # 跨秒创建（created_at 为 NOW() 秒级精度）
        create_product(test_db, code="960012.OF", market="CN_OTC", name="排序新基金B")
        create_product(test_db, code="960011.OF", market="CN_OTC", name="排序新基金A")

        resp = client.get("/api/products?page_size=100", headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        codes = [i["code"] for i in items]
        # 新建优先：跨秒后创建的两个产品均排在旧产品之前
        assert codes.index("960012.OF") < codes.index("960010.OF")
        assert codes.index("960011.OF") < codes.index("960010.OF")
        # 同秒并列时按 code 升序定序
        by_code = {i["code"]: i for i in items}
        if by_code["960011.OF"]["created_at"] == by_code["960012.OF"]["created_at"]:
            assert codes.index("960011.OF") < codes.index("960012.OF")

        # 重复请求顺序稳定（确定性排序）
        resp2 = client.get("/api/products?page_size=100", headers=admin_headers)
        assert [i["code"] for i in resp2.json()["items"]] == codes
