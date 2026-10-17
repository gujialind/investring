# ============================================================================
# 集成测试：现金流水 HTTP 契约 (test_cash_flows_api.py)
# ============================================================================
# issue #725 PR-1。走 TestClient 打 `GET /api/portfolios/{code}/cash-flows`，覆盖
# 只经 HTTP 层才成立的部分（口径本身在 tests/unit/test_cash_flow_service.py）：
#   - 鉴权与响应封装：401 / 404 / 422 的错误码与中文 detail；Decimal 以 JSON
#     字符串直出 4 位原值（契约调整 C8、取舍点 T3：读侧不量化）
#   - 断言 4/6：一笔平台间转移 = 两行、来源/去向互指、分页计数 +2、pair_ref 内容
#   - 断言 5/13/15：组合级与平台级 overview 的转移排除差、构成弹窗恒为全平台 +
#     组合口径合计、行和与合计之差 = 本期已生效转移腿
#   - 断言 7/12：group_total_count 解除类型筛选口径、dividend 值域含 forced_adjustment
#     且基金级父记录不出行
#   - 断言 1/10 的契约面：行内只有平台级余额、无「预计到账后余额」投影字段
#   - 取舍点 T4：status 只映射 settled/in_transit 两值，不透出 raw trade.status
# 余额取价与快照吸收（断言 1/2/3/9/11）在 test_cash_flows_balance.py。
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from tests.factories import (
    create_manual_market_value,
    create_portfolio,
    create_share_change_event,
    create_subscription,
    create_trade,
    ensure_trading_day,
)

D0 = date(2025, 6, 6)   # 周五：已生效行的现金日
D1 = date(2025, 6, 9)   # 周一：跨天转移转入腿的预计到账日
PORTFOLIO = "CF_API"
FUND = "510300.SH"      # 种子产品（场内 ETF）
OTC_FUND = "000300.OF"  # 种子产品（场外）

# 转移组：裸 12 位十六进制（models/trade.py 的 transfer_group 编码规则）
GROUP_SAME_DAY = "a1b2c3d4e5f6"
GROUP_CROSS_DAY = "b2c3d4e5f6a1"
GROUP_REBAL = "rebal_api000001"


@pytest.fixture
def api_portfolio(test_db) -> str:
    """造齐五类来源的组合 CF_API：D0 八行（已生效）+ D1 一行（在途）。"""
    ensure_trading_day(test_db, D0, is_open=True)
    ensure_trading_day(test_db, D1, is_open=True)
    create_portfolio(test_db, code=PORTFOLIO, name="现金流水接口组合", status="active")

    # ① 当天到账的平台间转移：两腿皆 confirmed
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="sell", amount=5000, platform_code="MYCF", trade_date=D0,
        confirm_date=D0, transfer_group=GROUP_SAME_DAY, status="confirmed",
    )
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="buy", amount=5000, platform_code="HBZQ", trade_date=D0,
        confirm_date=D0, transfer_group=GROUP_SAME_DAY, status="confirmed",
    )

    # ② 跨天转移：转出腿已生效、转入腿 pending（confirm_date = 下一交易日）
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="sell", amount=1500, platform_code="MYCF", trade_date=D0,
        confirm_date=D0, transfer_group=GROUP_CROSS_DAY, status="confirmed",
    )
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="buy", amount=1500, platform_code="HBZQ", trade_date=D0,
        confirm_date=D1, transfer_group=GROUP_CROSS_DAY, status="pending",
    )

    # ③ 申购确认的 CASH buy 腿（取舍点 T1：注资入账 = 流入）
    sub = create_subscription(
        test_db, portfolio_code=PORTFOLIO, investor_code="ADMIN",
        sub_type="subscribe", amount=20000, apply_date=D0, confirm_date=D0,
        status="confirmed", platform_code="HBZQ",
    )
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="buy", amount=20000, platform_code="HBZQ", trade_date=D0,
        confirm_date=D0, transfer_group=f"sub_{sub.id}", status="confirmed",
    )

    # ④ 调仓买入：基金腿 pending + CASH sell 腿（#493 创建即 confirmed）
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code=FUND, market="CN_EXCHANGE",
        trade_type="buy", amount=8200, platform_code="MYCF", trade_date=D0,
        confirm_date=D1, transfer_group=GROUP_REBAL, status="pending",
    )
    create_trade(
        test_db, portfolio_code=PORTFOLIO, product_code="CASH", market="",
        trade_type="sell", amount=8200, platform_code="MYCF", trade_date=D0,
        confirm_date=D0, transfer_group=GROUP_REBAL, status="confirmed",
    )

    # ⑤ 现金事件：平台级子记录出行、基金级父记录不出行（否则双计）、pending 不出行
    create_share_change_event(
        test_db, portfolio_code=PORTFOLIO, product_code=OTC_FUND, market="CN_OTC",
        event_type="cash_dividend", ex_date=D0, entitlement_date=D0,
        status="confirmed", platform_code=None, cash_change=Decimal("640"),
    )
    create_share_change_event(
        test_db, portfolio_code=PORTFOLIO, product_code=OTC_FUND, market="CN_OTC",
        event_type="cash_dividend", ex_date=D0, entitlement_date=D0,
        status="confirmed", platform_code="MYCF", cash_change=Decimal("320"),
        cash_pay_date=D0,
    )
    create_share_change_event(
        test_db, portfolio_code=PORTFOLIO, product_code=OTC_FUND, market="CN_OTC",
        event_type="forced_adjustment", ex_date=D0, entitlement_date=D0,
        status="confirmed", platform_code="MYCF", cash_change=Decimal("-50"),
    )
    create_share_change_event(
        test_db, portfolio_code=PORTFOLIO, product_code=OTC_FUND, market="CN_OTC",
        event_type="cash_dividend", ex_date=D0, entitlement_date=D0,
        status="pending", platform_code="MYCF", cash_change=Decimal("111"),
    )

    # ⑥ 手动重估（绝对值）
    create_manual_market_value(
        test_db, portfolio_code=PORTFOLIO, platform_code="HBZQ", product_code="CASH",
        record_date=D0, market_value=88000, computed_value=70000,
    )
    return PORTFOLIO


def _get(client, headers, code=PORTFOLIO, **params):
    return client.get(f"/api/portfolios/{code}/cash-flows", headers=headers, params=params)


class TestAccessAndEnvelope:
    def test_requires_authentication(self, client, api_portfolio):
        assert _get(client, headers={}).status_code == 401

    def test_viewer_can_read(self, client, viewer_headers, api_portfolio):
        assert _get(client, viewer_headers).status_code == 200

    def test_unknown_portfolio_returns_404(self, client, admin_headers):
        response = _get(client, admin_headers, code="CF_NOPE")
        assert response.status_code == 404
        assert response.json()["detail"] == {
            "error": "PORTFOLIO_NOT_FOUND", "message": "组合 CF_NOPE 不存在",
        }

    def test_envelope_and_decimal_strings(self, client, admin_headers, api_portfolio):
        response = _get(client, admin_headers, page_size=100)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "items", "total", "page", "page_size", "overview", "platform_breakdown",
        }
        assert set(body["overview"]) == {"available", "in_transit", "inflow", "outflow"}
        # C8/T3：金额 4 位原值以字符串直出（JS number 会丢精度），量化交前端
        assert all(isinstance(body["overview"][k], str) for k in body["overview"])
        row = body["items"][0]
        assert isinstance(row["amount"], str) and isinstance(
            row["platform_end_of_day_balance"], str
        )
        assert Decimal(row["amount"]) == Decimal(row["amount"]).quantize(
            Decimal("0.0001")
        )
        assert body["total"] == len(body["items"]) == 9
        assert body["page"] == 1 and body["page_size"] == 100

    def test_row_payload_has_no_portfolio_balance_or_projection(
        self, client, admin_headers, api_portfolio
    ):
        """断言 1/10 的契约面：余额列只有平台级，投影字段后端不出。"""
        (row,) = _get(client, admin_headers, flow_type="revalue").json()["items"]
        assert set(row) == {
            "source_kind", "flow_type", "flow_type_label", "cash_date",
            "cash_date_label", "cash_date_sub", "platform_code", "platform_name",
            "source_main", "source_sub", "amount", "is_override", "settled",
            "status", "action", "origin_ref", "pair_ref", "group_total_count",
            "platform_end_of_day_balance", "prev_day_balance",
        }
        assert not any("portfolio" in key for key in row)
        assert not any(
            token in key for key in row for token in ("projected", "projection")
        )


class TestParamValidation:
    @pytest.mark.parametrize(
        "params,error",
        [
            ({"status": "confirmed"}, "INVALID_PARAM"),
            ({"status": "pending"}, "INVALID_PARAM"),
            ({"flow_type": "cash_adjust"}, "INVALID_PARAM"),
            ({"from_date": D1, "to_date": D0}, "INVALID_DATE_RANGE"),
            ({"page": 0}, "INVALID_PARAM"),
            ({"page_size": 0}, "INVALID_PARAM"),
            ({"page_size": 101}, "INVALID_PARAM"),
        ],
    )
    def test_rejected_params_map_to_documented_codes(
        self, client, admin_headers, api_portfolio, params, error
    ):
        response = _get(client, admin_headers, **params)
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == error
        assert detail["message"], "detail.message 不得为空（中文文案铁律）"

    def test_invalid_status_message_names_the_domain(self, client, admin_headers, api_portfolio):
        detail = _get(client, admin_headers, status="confirmed").json()["detail"]
        assert "settled / in_transit" in detail["message"]

    def test_invalid_flow_type_message_lists_domain(self, client, admin_headers, api_portfolio):
        detail = _get(client, admin_headers, flow_type="cash_adjust").json()["detail"]
        assert "rebal_buy" in detail["message"] and "revalue" in detail["message"]

    def test_empty_platform_code_equals_omitted(self, client, admin_headers, api_portfolio):
        """PR #726 评审：`?platform_code=` 空串归一为「未筛选」，与省略参数完全等价。"""
        omitted = _get(client, admin_headers, page_size=100).json()
        empty = _get(client, admin_headers, platform_code="", page_size=100).json()
        assert empty == omitted
        assert empty["total"] == 9                    # 行集不按空串过滤成空
        # 可用现金与行集同 scope：不是「列表空 + 可用现金组合值」的矛盾组合
        assert Decimal(empty["overview"]["available"]) == Decimal(
            omitted["overview"]["available"]
        )


class TestTransferRows:
    """断言 4/6：一笔转移 = 两行、互指、分页计数 +2。"""

    def test_two_rows_per_transfer_and_total_counts_both(
        self, client, admin_headers, api_portfolio
    ):
        body = _get(client, admin_headers, flow_type="transfer", page_size=100).json()
        assert body["total"] == 4                      # 两笔转移各计 2
        groups = {}
        for row in body["items"]:
            groups.setdefault(row["origin_ref"]["transfer_group"], []).append(row)
        assert set(groups) == {GROUP_SAME_DAY, GROUP_CROSS_DAY}
        assert all(len(legs) == 2 for legs in groups.values())
        for legs in groups.values():
            assert {leg["platform_code"] for leg in legs} == {"MYCF", "HBZQ"}
            # 转出腿恒负、转入腿恒正，绝对值相等（现金账本内搬运，不改组合总额）
            by_side = {leg["platform_code"]: Decimal(leg["amount"]) for leg in legs}
            assert by_side["MYCF"] == -by_side["HBZQ"]
        # 当天到账的两腿皆已生效；跨天只有转出腿已生效（转入腿在途）
        settled_by_group = {
            group: {leg["settled"] for leg in legs} for group, legs in groups.items()
        }
        assert settled_by_group[GROUP_SAME_DAY] == {True}
        assert settled_by_group[GROUP_CROSS_DAY] == {True, False}

    def test_source_main_points_at_the_other_platform(
        self, client, admin_headers, api_portfolio
    ):
        body = _get(client, admin_headers, flow_type="transfer", page_size=100).json()
        for row in body["items"]:
            other = "华宝证券" if row["platform_code"] == "MYCF" else "蚂蚁财富"
            assert row["source_main"] == other
            assert row["pair_ref"]["platform_code"] != row["platform_code"]
            assert row["pair_ref"]["platform_name"] == other
            assert row["pair_ref"]["kind"] == "transfer_leg"
            assert Decimal(row["pair_ref"]["amount"]) == -Decimal(row["amount"])

    def test_cross_day_pair_exposes_settled_split(
        self, client, admin_headers, api_portfolio
    ):
        body = _get(
            client, admin_headers, flow_type="transfer", from_date=D1, page_size=100
        ).json()
        (in_leg,) = body["items"]
        assert in_leg["platform_code"] == "HBZQ"
        assert in_leg["cash_date"] == D1.isoformat()
        assert in_leg["cash_date_label"] == "到账日 · 预计"
        assert in_leg["settled"] is False
        assert in_leg["action"] == {"label": "确认到账", "kind": "confirm"}
        assert in_leg["pair_ref"]["settled"] is True
        assert in_leg["pair_ref"]["ref_date"] == D0.isoformat()
        assert in_leg["pair_ref"]["label"] == "转出 · 蚂蚁财富"

    def test_status_filter_maps_to_two_values_not_raw_trade_status(
        self, client, admin_headers, api_portfolio
    ):
        """取舍点 T4：不透出 raw trade.status（pending/confirmed/cancelled）。"""
        in_transit = _get(
            client, admin_headers, status="in_transit", page_size=100
        ).json()
        # 跨天转入腿（未到账）+ 未烘焙进快照的重估行（本组合无快照）
        assert in_transit["total"] == 2
        by_type = {row["flow_type"]: row for row in in_transit["items"]}
        assert set(by_type) == {"transfer", "revalue"}
        leg = by_type["transfer"]
        assert leg["origin_ref"]["transfer_group"] == GROUP_CROSS_DAY
        assert set(leg["status"]) == {"variant", "label", "note"}
        assert leg["status"]["label"] == "待到账"
        # 重估行落 in_transit 档（尚未生效）但对在途金额贡献为 0
        assert by_type["revalue"]["status"]["label"] == "待重算"
        assert Decimal(in_transit["overview"]["in_transit"]) == Decimal("1500")

        settled = _get(client, admin_headers, status="settled", page_size=100).json()
        assert settled["total"] == 7
        assert all(item["settled"] is True for item in settled["items"])
        assert {row["flow_type"] for row in settled["items"]} == {
            "transfer", "sub_buy", "rebal_buy", "dividend",
        }


class TestFlowTypeFilter:
    def test_dividend_domain_includes_forced_adjustment(
        self, client, admin_headers, api_portfolio
    ):
        """断言 12 + 取舍点 T5：forced_adjustment 复用 dividend 值域、label 分家。"""
        body = _get(client, admin_headers, flow_type="dividend", page_size=100).json()
        assert body["total"] == 2
        labels = {row["flow_type_label"] for row in body["items"]}
        assert labels == {"现金分红", "现金调整"}
        amounts = {Decimal(row["amount"]) for row in body["items"]}
        assert amounts == {Decimal("320"), Decimal("-50")}
        # 基金级父记录（640）与 pending 事件（111）都不出行
        assert Decimal("640") not in amounts and Decimal("111") not in amounts
        assert all(row["platform_code"] == "MYCF" for row in body["items"])

    def test_group_total_count_releases_type_filter(
        self, client, admin_headers, api_portfolio
    ):
        """断言 7 的后端面：N = 解除类型筛选后的同 (平台, 日) 总笔数。"""
        full = _get(
            client, admin_headers, platform_code="MYCF", from_date=D0, to_date=D0,
            page_size=100,
        ).json()
        expected = full["total"]
        filtered = _get(
            client, admin_headers, platform_code="MYCF", from_date=D0, to_date=D0,
            flow_type="dividend", page_size=100,
        ).json()
        assert filtered["total"] == 2 < expected
        assert {row["group_total_count"] for row in filtered["items"]} == {expected}
        assert {row["group_total_count"] for row in full["items"]} == {expected}

    def test_revalue_row_is_absolute_and_actionable(
        self, client, admin_headers, api_portfolio
    ):
        (row,) = _get(client, admin_headers, flow_type="revalue").json()["items"]
        assert Decimal(row["amount"]) == Decimal("88000")   # 绝对值，不是增量
        assert row["is_override"] is True
        assert row["cash_date_label"] == "覆盖日"
        assert row["source_main"] == "手工更新现金市值"
        assert row["action"] == {"label": "撤销", "kind": "revoke"}
        assert row["origin_ref"]["kind"] == "manual_market_value"


class TestOverviewAndBreakdown:
    """断言 5/13/15：两级排除规则、构成弹窗口径、recon 命名差。"""

    def test_portfolio_level_excludes_transfers_platform_level_includes(
        self, client, admin_headers, api_portfolio
    ):
        portfolio = _get(client, admin_headers, page_size=1).json()["overview"]
        # 已生效行：+20000（申购）、−8200（调仓买入）、+320（分红）、−50（现金调整）
        assert Decimal(portfolio["inflow"]) == Decimal("20320")
        assert Decimal(portfolio["outflow"]) == Decimal("-8250")
        # 在途：跨天转入腿 1500（按到账平台 HBZQ 归属）
        assert Decimal(portfolio["in_transit"]) == Decimal("1500")

        mycf = _get(client, admin_headers, platform_code="MYCF", page_size=1).json()[
            "overview"
        ]
        assert Decimal(mycf["inflow"]) == Decimal("320")
        # −8200（调仓）、−5000 与 −1500（两笔转出腿）、−50（现金调整）
        assert Decimal(mycf["outflow"]) == Decimal("-14750")
        assert Decimal(mycf["in_transit"]) == Decimal("0")

        hbzq = _get(client, admin_headers, platform_code="HBZQ", page_size=1).json()[
            "overview"
        ]
        # +20000（申购）、+5000（当天转入腿）；跨天转入腿在途不计
        assert Decimal(hbzq["inflow"]) == Decimal("25000")
        assert Decimal(hbzq["outflow"]) == Decimal("0")
        assert Decimal(hbzq["in_transit"]) == Decimal("1500")

    def test_breakdown_rows_equal_scoped_overview_and_total_equals_portfolio(
        self, client, admin_headers, api_portfolio
    ):
        """断言 13：构成弹窗行值 = 筛该平台时的四卡；合计 = 组合口径。"""
        body = _get(client, admin_headers, platform_code="MYCF", page_size=1).json()
        breakdown = body["platform_breakdown"]
        assert {row["platform_code"] for row in breakdown["rows"]} == {"MYCF", "HBZQ"}
        for row in breakdown["rows"]:
            scoped = _get(
                client, admin_headers, platform_code=row["platform_code"], page_size=1
            ).json()["overview"]
            assert {k: Decimal(str(row[k])) for k in scoped} == {
                k: Decimal(v) for k, v in scoped.items()
            }, row["platform_code"]
            assert row["platform_name"]

        unscoped = _get(client, admin_headers, page_size=1).json()
        assert unscoped["platform_breakdown"] == breakdown   # 不受平台筛选影响
        total = breakdown["total"]
        assert {k: Decimal(str(total[k])) for k in unscoped["overview"]} == {
            k: Decimal(v) for k, v in unscoped["overview"].items()
        }

    def test_recon_equals_settled_transfer_legs(self, client, admin_headers, api_portfolio):
        """断言 15：行和 − 合计 的差 = 本期已生效转移腿（转出计、在途转入不计）。"""
        breakdown = _get(client, admin_headers, page_size=1).json()["platform_breakdown"]
        rows_in = sum((Decimal(r["inflow"]) for r in breakdown["rows"]), Decimal("0"))
        rows_out = sum((Decimal(r["outflow"]) for r in breakdown["rows"]), Decimal("0"))
        total_in = Decimal(breakdown["total"]["inflow"])
        total_out = Decimal(breakdown["total"]["outflow"])
        assert Decimal(breakdown["recon"]["inflow"]) == rows_in - total_in
        assert Decimal(breakdown["recon"]["outflow"]) == rows_out - total_out
        # 已生效转移腿：+5000（HBZQ 转入）、−5000 与 −1500（MYCF 转出）
        assert Decimal(breakdown["recon"]["inflow"]) == Decimal("5000")
        assert Decimal(breakdown["recon"]["outflow"]) == Decimal("-6500")


class TestPagination:
    def test_total_is_stable_and_pages_are_disjoint(
        self, client, admin_headers, api_portfolio
    ):
        first = _get(client, admin_headers, page=1, page_size=4).json()
        second = _get(client, admin_headers, page=2, page_size=4).json()
        third = _get(client, admin_headers, page=3, page_size=4).json()
        assert first["total"] == second["total"] == third["total"] == 9
        assert len(first["items"]) == len(second["items"]) == 4
        assert len(third["items"]) == 1
        ids = [
            (row["origin_ref"]["kind"], row["origin_ref"]["id"])
            for page in (first, second, third)
            for row in page["items"]
        ]
        assert len(set(ids)) == 9
        # 现金日期降序跨页单调
        dates = [
            row["cash_date"] for page in (first, second, third)
            for row in page["items"]
        ]
        assert dates == sorted(dates, reverse=True)

    def test_date_range_is_inclusive(self, client, admin_headers, api_portfolio):
        body = _get(client, admin_headers, from_date=D0, to_date=D0, page_size=100).json()
        assert body["total"] == 8
        assert {row["cash_date"] for row in body["items"]} == {D0.isoformat()}
