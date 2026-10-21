# ============ 集成测试：#719 读侧双向对手腿派生（paired_leg） ============
# 覆盖 issue #719 验收断言的后端部分：
# - 双向：基金腿↔现金腿互指、平台间转移的两条现金腿互指（原 #493 只有基金腿→现金腿单向）
# - 不受列表 status/platform/products/日期筛选影响：对手腿被筛掉时仍回填
# - 不补行：total 与分页口径恒按真实行数，派生只挂在既有行上
# - 边界：`sub_` 申赎单腿无对手 trade 行 → null；组内行数 ≠ 2 时整体跳过（宁可不补也不猜）
# - 契约时机：仅 list 端点填充，单对象端点恒 null；#493 的两个平铺字段行为不变

from datetime import date
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_platform, create_product, create_trade,
)

FUND = "FUND_719"
FUND_MKT = "CN_OTC"
FUND_NAME = "沪深300测试基"
CASH_PLAT = "P719A"
OTHER_PLAT = "P719B"
T = date(2025, 9, 1)
T1 = date(2025, 9, 2)


def _seed(db, code):
    """组合 + 两个平台 + 基金与 CASH 产品行。

    CASH 产品行必须显式建：现金腿的 (product_code, market) 是 ("CASH", "")，
    读侧名称/显示码走 build_product_label_maps 查产品表，无行则派生名为 None。
    """
    create_portfolio(db, code=code, status="active")
    create_platform(db, code=CASH_PLAT)
    create_platform(db, code=OTHER_PLAT)
    create_product(db, code=FUND, market=FUND_MKT, product_type="OEF", name=FUND_NAME)
    create_product(db, code="CASH", market="", product_type="CASH",
                   asset_class_code="ASSET_CASH", name="现金类资产", confirm_days=0)


def _rebal_pair(db, code, group, *, fund_type="buy", fund_date=T, cash_date=T,
                cash_platform=CASH_PLAT, cash_status="confirmed",
                fund_status="pending", amount=10000.0, fee=5.0, cash_amount=10005.0):
    """造一个 `rebal_` 组：1 条基金腿 + 1 条反向 CASH 腿（同组两腿）。"""
    fund = create_trade(
        db, code, FUND, FUND_MKT, trade_type=fund_type, platform_code=CASH_PLAT,
        transfer_group=group, status=fund_status, trade_date=fund_date,
        confirm_date=T1, amount=amount, shares=amount / 4, price=4.0, fee=fee,
        actual_amount=amount + fee if fund_type == "buy" else amount - fee,
    )
    cash = create_trade(
        db, code, "CASH", "", trade_type="sell" if fund_type == "buy" else "buy",
        platform_code=cash_platform, transfer_group=group, status=cash_status,
        trade_date=cash_date, confirm_date=cash_date, amount=cash_amount,
        actual_amount=cash_amount, price=None, shares=None,
    )
    return fund, cash


def _items(client, headers, query=""):
    resp = client.get(f"/api/trades{query}", headers=headers)
    assert resp.status_code == 200, resp.json()
    return {item["id"]: item for item in resp.json()["items"]}, resp.json()["total"]


class TestPairedLegBidirectional:
    """双向回填：锚点行不区分基金腿/现金腿"""

    def test_fund_leg_points_at_cash_leg(self, client, admin_headers, test_db):
        _seed(test_db, "PL719_A")
        fund, cash = _rebal_pair(test_db, "PL719_A", "rebal_719a0001")

        items, _total = _items(client, admin_headers, f"?portfolio_code=PL719_A")
        paired = items[fund.id]["paired_leg"]
        assert paired["id"] == cash.id
        assert paired["product_code"] == "CASH"
        assert paired["market"] == ""
        assert paired["trade_type"] == "sell"
        assert paired["platform_code"] == CASH_PLAT
        assert Decimal(str(paired["amount"])) == Decimal("10005.0")
        assert paired["confirm_date"] == "2025-09-01"
        assert paired["status"] == "confirmed"
        # 现金腿无产品名以外的字段：shares 为 null、price 落库为 1
        assert paired["shares"] is None

    def test_cash_leg_points_back_at_fund_leg_with_labels(self, client, admin_headers, test_db):
        """反向（本次新增的方向）：现金锚点行带出对手基金腿的产品名与显示码"""
        _seed(test_db, "PL719_B")
        fund, cash = _rebal_pair(test_db, "PL719_B", "rebal_719b0001",
                                 fund_type="sell", fund_status="confirmed")

        items, _total = _items(client, admin_headers, "?portfolio_code=PL719_B")
        paired = items[cash.id]["paired_leg"]
        assert paired["id"] == fund.id
        assert paired["product_code"] == FUND
        assert paired["market"] == FUND_MKT
        # 对手产品的名称/显示码与锚点行同批取，前端渲染派生子行不必再查产品表
        assert paired["product_name"] == FUND_NAME
        assert paired["display_code"] == FUND
        assert paired["trade_type"] == "sell"
        assert Decimal(str(paired["shares"])) == Decimal("2500.0")
        assert Decimal(str(paired["price"])) == Decimal("4.0")
        assert Decimal(str(paired["fee"])) == Decimal("5.0")

    def test_two_cash_transfer_legs_point_at_each_other(self, client, admin_headers, test_db):
        """平台间转移：裸 12 位 hex 组、两条腿均 CASH、按 trade_type 区分 → 互指"""
        _seed(test_db, "PL719_C")
        out_leg = create_trade(test_db, "PL719_C", "CASH", "", trade_type="sell",
                               platform_code=CASH_PLAT, transfer_group="a1b2c3d4e5f6",
                               status="confirmed", trade_date=T, confirm_date=T,
                               amount=30000.0, actual_amount=30000.0)
        in_leg = create_trade(test_db, "PL719_C", "CASH", "", trade_type="buy",
                              platform_code=OTHER_PLAT, transfer_group="a1b2c3d4e5f6",
                              status="confirmed", trade_date=T, confirm_date=T,
                              amount=30000.0, actual_amount=30000.0)

        items, _total = _items(client, admin_headers, "?portfolio_code=PL719_C")
        assert items[out_leg.id]["paired_leg"]["id"] == in_leg.id
        assert items[in_leg.id]["paired_leg"]["id"] == out_leg.id
        # 对手平台是"钱去了哪"的唯一线索，必须带上
        assert items[out_leg.id]["paired_leg"]["platform_code"] == OTHER_PLAT

    def test_cash_platform_flat_fields_unchanged(self, client, admin_headers, test_db):
        """#493 回归护栏：新增 paired_leg 不改写两个平铺字段的既有语义"""
        _seed(test_db, "PL719_D")
        fund, cash = _rebal_pair(test_db, "PL719_D", "rebal_719d0001")

        items, _total = _items(client, admin_headers, "?portfolio_code=PL719_D")
        assert items[fund.id]["cash_platform_code"] == CASH_PLAT
        assert items[fund.id]["cash_confirm_date"] == "2025-09-01"
        # 现金腿自身仍不回填平铺字段（历史契约：只服务基金腿）
        assert items[cash.id]["cash_platform_code"] is None
        assert items[cash.id]["cash_confirm_date"] is None


class TestPairedLegCrossesFilters:
    """派生通道不受列表筛选影响（与 #493 单向版同一不变量）"""

    def test_survives_product_filter(self, client, admin_headers, test_db):
        """核心场景：筛基金产品 → 现金腿不在结果集，但基金行的 paired_leg 指向它"""
        _seed(test_db, "PL719_E")
        fund, cash = _rebal_pair(test_db, "PL719_E", "rebal_719e0001")

        items, total = _items(client, admin_headers,
                              f"?portfolio_code=PL719_E&products={FUND}|{FUND_MKT}")
        # 不补行：total 与行数都只有基金腿这一条
        assert total == 1
        assert list(items) == [fund.id]
        assert items[fund.id]["paired_leg"]["id"] == cash.id

    def test_survives_cash_product_filter(self, client, admin_headers, test_db):
        """筛现金（products=CASH|，空市场段合法）→ 现金行带出对手基金腿"""
        _seed(test_db, "PL719_F")
        fund, cash = _rebal_pair(test_db, "PL719_F", "rebal_719f0001")

        items, total = _items(client, admin_headers,
                              "?portfolio_code=PL719_F&products=CASH|")
        assert total == 1
        assert list(items) == [cash.id]
        assert items[cash.id]["paired_leg"]["id"] == fund.id

    def test_survives_status_and_platform_filters(self, client, admin_headers, test_db):
        """半确认组（基金 pending / 现金 confirmed）与跨平台扣款都能穿透"""
        _seed(test_db, "PL719_G")
        fund, cash = _rebal_pair(test_db, "PL719_G", "rebal_719g0001",
                                 fund_status="pending", cash_status="confirmed",
                                 cash_platform=OTHER_PLAT)

        by_status, _ = _items(client, admin_headers,
                              "?portfolio_code=PL719_G&status=pending")
        assert list(by_status) == [fund.id]
        assert by_status[fund.id]["paired_leg"]["id"] == cash.id
        assert by_status[fund.id]["paired_leg"]["platform_code"] == OTHER_PLAT

        by_platform, _ = _items(client, admin_headers,
                                f"?portfolio_code=PL719_G&platform_code={CASH_PLAT}")
        assert list(by_platform) == [fund.id]
        assert by_platform[fund.id]["paired_leg"]["id"] == cash.id

    def test_counterpart_excluded_by_date_filter_still_filled(self, client, admin_headers, test_db):
        """日期筛选不吞对手腿：对手腿不在结果集时，本页行仍指向它。

        （#734）原构造用「跨日 + page_size 分页把两腿切到两页」——该前提建在旧排序
        键把同组拆页的缺陷之上；组锚点排序修复后同组两腿恒相邻，改用日期筛选构造
        「对手腿不在结果集」的同一语义，与排序解耦。
        """
        _seed(test_db, "PL719_H")
        fund, cash = _rebal_pair(test_db, "PL719_H", "rebal_719h0001",
                                 fund_date=T, cash_date=T1)

        # trade_date_end=T 把 T1 的现金腿筛出结果集
        items, total = _items(client, admin_headers,
                              f"?portfolio_code=PL719_H&trade_date_end={T.isoformat()}")
        assert total == 1
        assert list(items) == [fund.id]
        assert items[fund.id]["paired_leg"]["id"] == cash.id
        assert items[fund.id]["paired_leg"]["id"] not in items


class TestPairedLegBoundaries:
    """无对手与不猜对手"""

    def test_subscription_leg_has_no_counterpart(self, client, admin_headers, test_db):
        """`sub_` 申赎腿组内只有它自己（对手方是 subscription 记录）→ null"""
        _seed(test_db, "PL719_I")
        sub_leg = create_trade(test_db, "PL719_I", "CASH", "", trade_type="sell",
                               platform_code=CASH_PLAT, transfer_group="sub_719",
                               status="confirmed", trade_date=T, confirm_date=T,
                               amount=50000.0, actual_amount=50000.0)

        items, _total = _items(client, admin_headers, "?portfolio_code=PL719_I")
        assert items[sub_leg.id]["paired_leg"] is None

    def test_pending_sell_has_no_counterpart(self, client, admin_headers, test_db):
        """待确认卖出尚无到账腿 → null（不是"缺数据"，是业务上还没发生）"""
        _seed(test_db, "PL719_J")
        pending_sell = create_trade(
            test_db, "PL719_J", FUND, FUND_MKT, trade_type="sell",
            platform_code=CASH_PLAT, transfer_group="rebal_719j0001",
            status="pending", trade_date=T, confirm_date=T1,
            amount=20000.0, shares=5000.0, price=4.0, fee=5.0,
        )

        items, _total = _items(client, admin_headers, "?portfolio_code=PL719_J")
        assert items[pending_sell.id]["paired_leg"] is None

    def test_group_without_transfer_group_has_no_counterpart(self, client, admin_headers, test_db):
        """组号 NOT NULL，但独占一组的行没有对手（如手工补录的单边账）→ null"""
        _seed(test_db, "PL719_K")
        solo = create_trade(test_db, "PL719_K", FUND, FUND_MKT, trade_date=T,
                            transfer_group="rebal_719k0001")

        items, _total = _items(client, admin_headers, "?portfolio_code=PL719_K")
        assert items[solo.id]["paired_leg"] is None

    def test_ambiguous_group_is_skipped_not_guessed(self, client, admin_headers, test_db):
        """组内 3 条腿（违反 uq_trade_transfer_group 下的正常形态）→ 两边都不回填。

        真实写入路径造不出这个形态，只能直接插行；钉住"宁可不补也不猜对手"这条
        取舍，避免以后有人把它改成"取第一条"而静默错配。
        """
        _seed(test_db, "PL719_L")
        fund, cash = _rebal_pair(test_db, "PL719_L", "rebal_719l0001")
        extra = create_trade(test_db, "PL719_L", FUND, FUND_MKT, trade_type="sell",
                             platform_code=CASH_PLAT, transfer_group="rebal_719l0001",
                             status="confirmed", trade_date=T, confirm_date=T1,
                             amount=1000.0, shares=100.0, price=10.0)

        items, _total = _items(client, admin_headers, "?portfolio_code=PL719_L")
        assert items[fund.id]["paired_leg"] is None
        assert items[cash.id]["paired_leg"] is None
        assert items[extra.id]["paired_leg"] is None

    def test_same_group_code_across_portfolios_not_matched(self, client, admin_headers, test_db):
        """组号只在组合内有意义：跨组合同组号不得互指。

        `uq_trade_transfer_group` 是 (transfer_group, product_code, trade_type) 的全局
        唯一约束、不按组合分列，所以这里用不同 trade_type 造出同组号跨组合的形态
        （真实写入路径靠 uuid 组号不会撞，本例只钉住查询的 portfolio 收窄）。
        """
        _seed(test_db, "PL719_M")
        _seed(test_db, "PL719_N")
        fund_m, cash_m = _rebal_pair(test_db, "PL719_M", "rebal_shared1")
        stray_n = create_trade(test_db, "PL719_N", "CASH", "", trade_type="buy",
                               platform_code=CASH_PLAT, transfer_group="rebal_shared1",
                               status="confirmed", trade_date=T, confirm_date=T,
                               amount=1.0, actual_amount=1.0)

        # 不带 portfolio_code：同组号在两个组合下各有行，一次列表里同时看到三行
        items, total = _items(client, admin_headers)
        assert total == 3
        assert items[fund_m.id]["paired_leg"]["id"] == cash_m.id
        assert items[cash_m.id]["paired_leg"]["id"] == fund_m.id
        assert items[stray_n.id]["paired_leg"] is None


class TestPairedLegContractTiming:
    """填充时机与 #175/#705 的派生字段完全一致"""

    def test_single_object_endpoint_leaves_it_null(self, client, admin_headers, test_db):
        _seed(test_db, "PL719_O")
        fund, _cash = _rebal_pair(test_db, "PL719_O", "rebal_719o0001")

        resp = client.get(f"/api/trades/{fund.id}", headers=admin_headers)
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["id"] == fund.id
        assert body["paired_leg"] is None
        # 同端点的 product_name/display_code 也恒 None（#175/#705 既有口径），
        # paired_leg 不例外，避免消费方误以为单对象端点能拿到对手腿
        assert body["product_name"] is None
