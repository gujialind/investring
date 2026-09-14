# ============ 集成测试：申购赎回（自 test_subscriptions.py 拆分，issue #469） ============
# 原「集成测试：申购赎回」套件（test_subscriptions.py）的创建/查询部分；其余三个文件同源拆分。
# 本文件覆盖：
#   - TestSubscriptionCreate / TestRedeemCreate：申赎创建（T+1 确认日、金额/份额与交易日校验）
#   - TestSubscriptionCancel / TestSubscriptionPermissions：pending 取消、viewer 权限
#   - TestAsOfDateRedeemValidation（#47）：补录历史赎回时 as_of_date 排除后续 confirmed 扣减
#   - TestUnconfirmSubscriptionSnapshotProtection：unconfirm 的 SNAPSHOT_DEPENDENCY 快照保护
#   - TestListSubscriptionFilters（#125）：状态/类型/平台/日期区间筛选与排序

from datetime import date

from tests.factories import (
    create_portfolio, create_investor, create_subscription,
    create_investor_holding, create_value_snapshot, ensure_trading_day,
    create_trade, create_platform,
)
from app.models.subscription import Subscription
from app.models.trade import Trade


class TestSubscriptionCreate:
    """申购创建测试"""

    def test_create_subscribe_pending(self, client, admin_headers, test_db):
        """申购提交后应为 pending 状态"""
        create_portfolio(test_db, code="SUB_P1", status="active")
        create_investor(test_db, code="SUB_I1")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "SUB_P1",
                "investor_code": "SUB_I1",
                "sub_type": "subscribe",
                "amount": 10000.0,
                "apply_date": "2025-09-01",
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["status"] == "pending"
        assert data["sub_type"] == "subscribe"

    def test_create_sets_confirm_date_next_trading_day(self, client, admin_headers, test_db):
        """#33改动1：创建时即设 confirm_date = 申请日下一交易日（恒 T+1）"""
        create_portfolio(test_db, code="SUB_CD", status="active")
        create_investor(test_db, code="SUB_ICD")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "SUB_CD",
                "investor_code": "SUB_ICD",
                "sub_type": "subscribe",
                "amount": 10000.0,
                "apply_date": "2025-09-01",
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        sub = test_db.query(Subscription).filter(
            Subscription.portfolio_code == "SUB_CD"
        ).first()
        assert sub.confirm_date == date(2025, 9, 2)

    def test_subscribe_zero_amount_rejected(self, client, admin_headers, test_db):
        """申购金额为 0 应被拒绝"""
        create_portfolio(test_db, code="SUB_Z", status="active")
        create_investor(test_db, code="SUB_IZ")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "SUB_Z",
                "investor_code": "SUB_IZ",
                "sub_type": "subscribe",
                "amount": 0,
                "apply_date": "2025-09-01",
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422)

    def test_subscribe_non_trading_day_rejected(self, client, admin_headers, test_db):
        """非交易日申购应被拒绝"""
        create_portfolio(test_db, code="SUB_NTD", status="active")
        create_investor(test_db, code="SUB_INTD")
        ensure_trading_day(test_db, date(2025, 9, 6), is_open=False)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "SUB_NTD",
                "investor_code": "SUB_INTD",
                "sub_type": "subscribe",
                "amount": 5000.0,
                "apply_date": "2025-09-06",
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422)


class TestRedeemCreate:
    """赎回创建测试"""

    def test_create_redeem_pending(self, client, admin_headers, test_db):
        """赎回提交后应为 pending 状态"""
        create_portfolio(test_db, code="RED_P1", status="active")
        create_investor(test_db, code="RED_I1")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)

        # 先创建持仓快照（有份额才能赎回）
        create_value_snapshot(test_db, "RED_P1", date(2025, 8, 29),
                              total_value=10000, total_shares=10000, unit_price=1.0)
        create_investor_holding(test_db, "RED_P1", "RED_I1", date(2025, 8, 29), shares=10000)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "RED_P1",
                "investor_code": "RED_I1",
                "sub_type": "redeem",
                "shares": 5000.0,
                "apply_date": "2025-09-01",
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["status"] == "pending"
        assert data["sub_type"] == "redeem"

    def test_redeem_exceeds_available_shares_rejected(self, client, admin_headers, test_db):
        """赎回份额超过可用份额应被拒绝"""
        create_portfolio(test_db, code="RED_EX", status="active")
        create_investor(test_db, code="RED_IEX")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)

        create_value_snapshot(test_db, "RED_EX", date(2025, 8, 29),
                              total_value=5000, total_shares=5000, unit_price=1.0)
        create_investor_holding(test_db, "RED_EX", "RED_IEX", date(2025, 8, 29), shares=5000)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "RED_EX",
                "investor_code": "RED_IEX",
                "sub_type": "redeem",
                "shares": 99999.0,
                "apply_date": "2025-09-01",
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422


class TestSubscriptionCancel:
    """申购赎回取消测试"""

    def test_cancel_pending_subscription(self, client, admin_headers, test_db):
        """取消 pending 申购"""
        create_portfolio(test_db, code="CAN_P", status="active")
        create_investor(test_db, code="CAN_I")
        sub = create_subscription(
            test_db, "CAN_P", "CAN_I", status="pending",
            apply_date=date(2025, 9, 1),
        )
        resp = client.post(
            f"/api/subscriptions/{sub.id}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        updated = test_db.query(Subscription).filter(Subscription.id == sub.id).first()
        assert updated.status == "cancelled"


class TestSubscriptionPermissions:
    """申购赎回权限测试"""

    def test_viewer_cannot_subscribe(self, client, viewer_headers, test_db):
        """viewer 不能提交申购"""
        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "X",
                "investor_code": "X",
                "sub_type": "subscribe",
                "amount": 1000,
                "apply_date": "2025-09-01",
                "platform_code": "MYCF",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_list_subscriptions(self, client, admin_headers, test_db):
        """获取申购赎回列表"""
        resp = client.get("/api/subscriptions", headers=admin_headers)
        assert resp.status_code == 200
        assert "items" in resp.json()


class TestAsOfDateRedeemValidation:
    """#47 补录历史赎回时 as_of_date 排除后续 confirmed"""

    def test_backfill_redeem_not_blocked_by_later_confirmed(self, client, admin_headers, test_db):
        """补录历史日赎回，后续 confirmed 赎回不应计入扣减"""
        create_portfolio(test_db, code="ASOF_RP", status="active")
        create_investor(test_db, code="ASOF_RI")
        ensure_trading_day(test_db, date(2025, 1, 6), is_open=True)
        ensure_trading_day(test_db, date(2025, 1, 7), is_open=True)
        ensure_trading_day(test_db, date(2025, 1, 8), is_open=True)
        ensure_trading_day(test_db, date(2025, 1, 9), is_open=True)
        # 快照：投资人份额=1000
        create_value_snapshot(test_db, "ASOF_RP", date(2025, 1, 6),
                              total_value=1000, total_shares=1000, unit_price=1.0)
        create_investor_holding(test_db, "ASOF_RP", "ASOF_RI", date(2025, 1, 6), shares=1000)
        # 后续 confirmed 赎回 800（confirm_date=1/8，快照后）
        create_subscription(
            test_db, "ASOF_RP", "ASOF_RI", sub_type="redeem",
            shares=800, apply_date=date(2025, 1, 7),
            confirm_date=date(2025, 1, 8), status="confirmed",
        )
        # 补录 1/7 赎回 500：as_of=1/7 时后续 confirm_date=1/8 不计入，可用=1000
        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "ASOF_RP",
                "investor_code": "ASOF_RI",
                "sub_type": "redeem",
                "shares": 500,
                "apply_date": "2025-01-07",
                "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        # 不应被 INSUFFICIENT_SHARES 拒绝
        assert resp.status_code in (200, 201), f"Expected success, got {resp.status_code}: {resp.json()}"


class TestUnconfirmSubscriptionSnapshotProtection:
    """unconfirm 申赎快照保护（SNAPSHOT_DEPENDENCY 检查已内嵌 subscription_service）"""

    def test_unconfirm_blocked_by_snapshot(self, client, admin_headers, test_db):
        """confirm_date 及之后已有快照时，unconfirm 返回 422 SNAPSHOT_DEPENDENCY 且状态不变"""
        create_portfolio(test_db, code="SUB_UC1", status="active")
        create_investor(test_db, code="SUB_UCI1")
        sub = create_subscription(
            test_db, "SUB_UC1", "SUB_UCI1",
            sub_type="subscribe", amount=10000.0, shares=10000.0,
            unit_price=1.0, apply_date=date(2025, 9, 1),
            confirm_date=date(2025, 9, 2), status="confirmed",
        )
        # 在 confirm_date 生成组合快照 → 快照依赖成立
        create_value_snapshot(
            test_db, "SUB_UC1", date(2025, 9, 2),
            total_value=10000, total_shares=10000, unit_price=1.0,
        )

        resp = client.post(
            f"/api/subscriptions/{sub.id}/unconfirm",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"
        # 检查发生在改字段之前，状态未被回退
        still = test_db.query(Subscription).filter(Subscription.id == sub.id).first()
        assert still.status == "confirmed"

    def test_unconfirm_ok_without_snapshot(self, client, admin_headers, test_db):
        """无快照依赖时 unconfirm 成功回退 pending 并级联删除配对 CASH trade"""
        create_portfolio(test_db, code="SUB_UC2", status="active")
        create_investor(test_db, code="SUB_UCI2")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        sub = create_subscription(
            test_db, "SUB_UC2", "SUB_UCI2",
            sub_type="subscribe", amount=10000.0, shares=10000.0,
            unit_price=1.0, apply_date=date(2025, 9, 1),
            confirm_date=date(2025, 9, 2), status="confirmed",
        )
        # 配对 CASH trade（transfer_group=sub_{id}），unconfirm 应级联物理删除
        create_trade(
            test_db, "SUB_UC2", "CASH", "",
            trade_type="buy", amount=10000.0, price=1.0,
            trade_date=date(2025, 9, 1), confirm_date=date(2025, 9, 2),
            actual_amount=10000.0, status="confirmed",
            transfer_group=f"sub_{sub.id}",
        )

        resp = client.post(
            f"/api/subscriptions/{sub.id}/unconfirm",
            headers=admin_headers,
        )
        assert resp.status_code == 200

        updated = test_db.query(Subscription).filter(Subscription.id == sub.id).first()
        assert updated.status == "pending"
        # confirm_date 不置 None，重算为 T+1，避免快照校验 NULL 漏检
        assert updated.confirm_date == date(2025, 9, 2)
        # 配对 CASH trade 已物理删除
        remaining = test_db.query(Trade).filter(
            Trade.transfer_group == f"sub_{sub.id}"
        ).count()
        assert remaining == 0


class TestListSubscriptionFilters:
    """申赎列表筛选/排序（issue #125）"""

    def test_filter_by_status(self, client, admin_headers, test_db):
        """三种状态各造 1 条，分别过滤只回目标状态"""
        create_portfolio(test_db, code="LS_P1", status="active")
        create_investor(test_db, code="LS_I1")
        create_subscription(test_db, "LS_P1", "LS_I1", status="pending",
                            apply_date=date(2025, 9, 1))
        create_subscription(test_db, "LS_P1", "LS_I1", status="confirmed",
                            apply_date=date(2025, 9, 2), confirm_date=date(2025, 9, 3))
        create_subscription(test_db, "LS_P1", "LS_I1", status="cancelled",
                            apply_date=date(2025, 9, 3))

        for st in ("pending", "confirmed", "cancelled"):
            resp = client.get(f"/api/subscriptions?status={st}", headers=admin_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert all(item["status"] == st for item in data["items"])

    def test_filter_by_sub_type_and_platform(self, client, admin_headers, test_db):
        """sub_type + platform_code 组合过滤取交集"""
        create_portfolio(test_db, code="LS_P2", status="active")
        create_investor(test_db, code="LS_I2")
        create_platform(test_db, code="LS_PLAT")
        create_subscription(test_db, "LS_P2", "LS_I2", sub_type="redeem", shares=100,
                            platform_code="LS_PLAT", apply_date=date(2025, 9, 1))
        create_subscription(test_db, "LS_P2", "LS_I2", sub_type="redeem", shares=100,
                            platform_code="MYCF", apply_date=date(2025, 9, 1))
        create_subscription(test_db, "LS_P2", "LS_I2", sub_type="subscribe",
                            platform_code="LS_PLAT", apply_date=date(2025, 9, 1))

        resp = client.get(
            "/api/subscriptions?sub_type=redeem&platform_code=LS_PLAT",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["sub_type"] == "redeem"
        assert data["items"][0]["platform_code"] == "LS_PLAT"

    def test_filter_apply_date_range_closed(self, client, admin_headers, test_db):
        """申请日期区间为闭区间：边界日记录包含"""
        create_portfolio(test_db, code="LS_P3", status="active")
        create_investor(test_db, code="LS_I3")
        create_subscription(test_db, "LS_P3", "LS_I3", apply_date=date(2025, 9, 1))
        create_subscription(test_db, "LS_P3", "LS_I3", apply_date=date(2025, 9, 5))
        create_subscription(test_db, "LS_P3", "LS_I3", apply_date=date(2025, 9, 10))

        resp = client.get(
            "/api/subscriptions?apply_date_start=2025-09-01&apply_date_end=2025-09-05",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        dates = sorted(item["apply_date"] for item in data["items"])
        assert dates == ["2025-09-01", "2025-09-05"]

    def test_filter_confirm_date_includes_pending_expected(self, client, admin_headers, test_db):
        """pending 记录按预计确认日命中确认日期区间（决策②）"""
        create_portfolio(test_db, code="LS_P4", status="active")
        create_investor(test_db, code="LS_I4")
        # pending：confirm_date 为预计确认日（创建时按 T+1 设定、保持非空）
        create_subscription(test_db, "LS_P4", "LS_I4", status="pending",
                            apply_date=date(2025, 9, 1), confirm_date=date(2025, 9, 2))
        create_subscription(test_db, "LS_P4", "LS_I4", status="confirmed",
                            apply_date=date(2025, 9, 3), confirm_date=date(2025, 9, 4))

        resp = client.get(
            "/api/subscriptions?confirm_date_start=2025-09-02&confirm_date_end=2025-09-02",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "pending"
        assert data["items"][0]["confirm_date"] == "2025-09-02"

    def test_inverted_range_returns_422(self, client, admin_headers, test_db):
        """start > end 返回 422 INVALID_DATE_RANGE（apply/confirm 两组同理）"""
        resp = client.get(
            "/api/subscriptions?apply_date_start=2025-09-10&apply_date_end=2025-09-01",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_RANGE"

        resp = client.get(
            "/api/subscriptions?confirm_date_start=2025-09-10&confirm_date_end=2025-09-01",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_DATE_RANGE"

    def test_sort_apply_date_desc(self, client, admin_headers, test_db):
        """排序 apply_date DESC, id DESC（同日期新记录在前）"""
        create_portfolio(test_db, code="LS_P5", status="active")
        create_investor(test_db, code="LS_I5")
        s1 = create_subscription(test_db, "LS_P5", "LS_I5", apply_date=date(2025, 9, 1))
        s2 = create_subscription(test_db, "LS_P5", "LS_I5", apply_date=date(2025, 9, 10))
        s3 = create_subscription(test_db, "LS_P5", "LS_I5", apply_date=date(2025, 9, 10))
        s4 = create_subscription(test_db, "LS_P5", "LS_I5", apply_date=date(2025, 9, 5))

        resp = client.get("/api/subscriptions", headers=admin_headers)
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        assert ids == [s3.id, s2.id, s4.id, s1.id]

    def test_viewer_restriction_with_filters(self, client, viewer_headers, test_db):
        """viewer 带 status 过滤仍只见自己记录；显式传他人 investor_code 被覆盖"""
        create_portfolio(test_db, code="LS_P6", status="active")
        create_investor(test_db, code="LS_I6")
        create_subscription(test_db, "LS_P6", "VIEWER", status="pending",
                            apply_date=date(2025, 9, 1))
        create_subscription(test_db, "LS_P6", "LS_I6", status="pending",
                            apply_date=date(2025, 9, 1))

        resp = client.get("/api/subscriptions?status=pending", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["investor_code"] == "VIEWER"

        # 显式传他人 investor_code 被 router 强制覆盖
        resp = client.get(
            "/api/subscriptions?status=pending&investor_code=LS_I6",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["investor_code"] == "VIEWER"
