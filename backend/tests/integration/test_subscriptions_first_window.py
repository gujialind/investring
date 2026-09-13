# ============ 集成测试：申购赎回·首窗定价与 started_at（自 test_subscriptions.py 拆分，issue #469） ============
# 原「集成测试：申购赎回」套件的首窗/started_at 部分；其余三个文件同源拆分。
# 本文件覆盖：
#   - TestFirstWindowPricingAndGate：首窗 1.0 定价与 CONFIRM_BEFORE_STARTED 闸门（章节 banner 见下方）
#   - TestStartedAtInvariant：started_at 重算不变量与回滚链防护（章节 banner 见下方）
# 模块级 _started_date 仅本文件使用（保留原位）；_confirmed_sub_with_cash_leg 已提取到
# tests/integration/subscription_helpers.py，供首窗/lifecycle/preview 三文件共用。

from datetime import date
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_investor, create_value_snapshot, ensure_trading_day,
    create_platform,
)
from tests.integration.subscription_helpers import _confirmed_sub_with_cash_leg
from app.models.subscription import Subscription
from app.models.trade import Trade


def _started_date(p):
    """started_at 为 DateTime 列但承载日期语义，读回可能是 date/datetime，归一后断言"""
    return p.started_at.date() if p.started_at else None


# ============================================================================
# issue #179：首窗 1.0 定价（三级决策）+ CONFIRM_BEFORE_STARTED 硬闸门
# ============================================================================

class TestFirstWindowPricingAndGate:
    """首日多平台申购不再死循环；乱序补录被闸门阻断"""

    def test_same_day_multi_platform_all_confirm_at_1(self, client, admin_headers, test_db):
        """原场景（PORT005）：同日多平台申购全部可确认，unit_price 均 1.0000，
        confirm_date == started_at 放行，各自生成配对 CASH buy"""
        create_portfolio(test_db, code="FW_P1")  # draft
        create_investor(test_db, code="FW_I1")
        create_platform(test_db, code="FW_PLAT2")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)

        sub_ids = []
        for plat, amount in (("MYCF", 143560.82), ("FW_PLAT2", 39105.99)):
            resp = client.post(
                "/api/subscriptions",
                json={
                    "portfolio_code": "FW_P1", "investor_code": "FW_I1",
                    "sub_type": "subscribe", "amount": amount,
                    "apply_date": "2025-09-01", "platform_code": plat,
                },
                headers=admin_headers,
            )
            assert resp.status_code in (200, 201)
            sub_ids.append(resp.json()["id"])

        for sid in sub_ids:
            resp = client.post(f"/api/subscriptions/{sid}/confirm", headers=admin_headers)
            assert resp.status_code == 200, resp.json()
            data = resp.json()
            assert Decimal(str(data["unit_price"])) == Decimal("1.0000")
            # 配对 CASH buy 落库
            legs = test_db.query(Trade).filter(
                Trade.transfer_group == f"sub_{sid}"
            ).all()
            assert len(legs) == 1
            assert legs[0].trade_type == "buy" and legs[0].status == "confirmed"

        from app.models.portfolio import Portfolio
        p = test_db.query(Portfolio).filter(Portfolio.code == "FW_P1").first()
        assert p.status == "active"
        assert _started_date(p) == date(2025, 9, 2)

    def test_apply_date_with_prior_arrival_needs_snapshot(self, client, admin_headers, test_db):
        """边界守卫：A apply=D-1 已 confirmed（confirm=D），B apply=D 且无快照 →
        NAV_NOT_AVAILABLE（当日已有资金到账，不适用首窗例外）"""
        create_portfolio(test_db, code="FW_P2")
        create_investor(test_db, code="FW_I2")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "FW_P2", "investor_code": "FW_I2",
                "sub_type": "subscribe", "amount": 10000.0,
                "apply_date": "2025-09-01", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        a_id = resp.json()["id"]
        assert client.post(f"/api/subscriptions/{a_id}/confirm", headers=admin_headers).status_code == 200

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "FW_P2", "investor_code": "FW_I2",
                "sub_type": "subscribe", "amount": 5000.0,
                "apply_date": "2025-09-02", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        b_id = resp.json()["id"]
        resp = client.post(f"/api/subscriptions/{b_id}/confirm", headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "NAV_NOT_AVAILABLE"

    def test_out_of_order_confirm_blocked(self, client, admin_headers, test_db):
        """乱序补录：confirm_date < started_at 被 CONFIRM_BEFORE_STARTED 阻断且不落库；
        首笔确认（started_at 尚空）豁免"""
        create_portfolio(test_db, code="FW_P3")
        create_investor(test_db, code="FW_I3")
        ensure_trading_day(test_db, date(2025, 8, 29), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)

        # 首笔（started_at 空）豁免阻断
        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "FW_P3", "investor_code": "FW_I3",
                "sub_type": "subscribe", "amount": 10000.0,
                "apply_date": "2025-09-01", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        s1_id = resp.json()["id"]
        assert client.post(f"/api/subscriptions/{s1_id}/confirm", headers=admin_headers).status_code == 200

        # 补录更早申购：confirm=09-01 < started_at=09-02 → 阻断
        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "FW_P3", "investor_code": "FW_I3",
                "sub_type": "subscribe", "amount": 8000.0,
                "apply_date": "2025-08-29", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        s0_id = resp.json()["id"]
        resp = client.post(f"/api/subscriptions/{s0_id}/confirm", headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CONFIRM_BEFORE_STARTED"
        still = test_db.query(Subscription).filter(Subscription.id == s0_id).first()
        assert still.status == "pending"

    def test_unconfirm_first_then_confirm_another(self, client, admin_headers, test_db):
        """首笔 unconfirm 后组合回 draft/started_at 清空（#180），
        再 confirm 另一笔仍走 1.0 且重设 started_at"""
        create_portfolio(test_db, code="FW_P4")
        create_investor(test_db, code="FW_I4")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "FW_P4", "investor_code": "FW_I4",
                "sub_type": "subscribe", "amount": 10000.0,
                "apply_date": "2025-09-01", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        s1_id = resp.json()["id"]
        assert client.post(f"/api/subscriptions/{s1_id}/confirm", headers=admin_headers).status_code == 200
        assert client.post(f"/api/subscriptions/{s1_id}/unconfirm", headers=admin_headers).status_code == 200

        from app.models.portfolio import Portfolio
        p = test_db.query(Portfolio).filter(Portfolio.code == "FW_P4").first()
        assert p.status == "draft" and p.started_at is None

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "FW_P4", "investor_code": "FW_I4",
                "sub_type": "subscribe", "amount": 6000.0,
                "apply_date": "2025-09-01", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        s2_id = resp.json()["id"]
        resp = client.post(f"/api/subscriptions/{s2_id}/confirm", headers=admin_headers)
        assert resp.status_code == 200
        assert Decimal(str(resp.json()["unit_price"])) == Decimal("1.0000")
        test_db.refresh(p)
        assert p.status == "active" and _started_date(p) == date(2025, 9, 2)

    def test_snapshot_nav_takes_precedence(self, client, admin_headers, test_db):
        """回归：申请日快照存在时取快照净值（三级决策 branch 1）"""
        create_portfolio(test_db, code="FW_P5", status="active")
        create_investor(test_db, code="FW_I5")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        create_value_snapshot(test_db, "FW_P5", date(2025, 9, 1),
                              total_value=12345, total_shares=10000, unit_price=1.2345)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "FW_P5", "investor_code": "FW_I5",
                "sub_type": "subscribe", "amount": 10000.0,
                "apply_date": "2025-09-02", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        sub_id = resp.json()["id"]
        # 创建后再补申请日快照（模拟快照生成先于确认的正常流程）
        create_value_snapshot(test_db, "FW_P5", date(2025, 9, 2),
                              total_value=12500, total_shares=10000, unit_price=1.25)

        resp = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert resp.status_code == 200
        assert Decimal(str(resp.json()["unit_price"])) == Decimal("1.25")


# ============================================================================
# issue #180：started_at = 现存最小 confirm_date（重算不变量）+ 回滚链防护
# ============================================================================

class TestStartedAtInvariant:
    """started_at 重算不变量（#180 定稿方案）"""

    def test_unconfirm_earliest_recomputes_to_next(self, client, admin_headers, test_db):
        """定稿反例：unconfirm 最早那笔（还有更晚 B）→ started_at = 次小 confirm_date"""
        from app.models.portfolio import Portfolio
        create_portfolio(test_db, code="SR_P1", status="active")
        create_investor(test_db, code="SR_I1")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        s1 = _confirmed_sub_with_cash_leg(
            test_db, "SR_P1", "SR_I1", 10000.0, date(2025, 9, 1), date(2025, 9, 2))
        _confirmed_sub_with_cash_leg(
            test_db, "SR_P1", "SR_I1", 5000.0, date(2025, 9, 2), date(2025, 9, 3))
        p = test_db.query(Portfolio).filter(Portfolio.code == "SR_P1").first()
        p.started_at = date(2025, 9, 2)
        test_db.commit()

        resp = client.post(f"/api/subscriptions/{s1.id}/unconfirm", headers=admin_headers)
        assert resp.status_code == 200, resp.json()
        test_db.refresh(p)
        assert _started_date(p) == date(2025, 9, 3)  # 不再悬空在已撤销交易的确认日
        assert p.status == "active"

    def test_unconfirm_non_earliest_keeps_started_at(self, client, admin_headers, test_db):
        """unconfirm 非最早笔 → started_at 不变，多笔时 status 保持 active"""
        from app.models.portfolio import Portfolio
        create_portfolio(test_db, code="SR_P2", status="active")
        create_investor(test_db, code="SR_I2")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        _confirmed_sub_with_cash_leg(
            test_db, "SR_P2", "SR_I2", 10000.0, date(2025, 9, 1), date(2025, 9, 2))
        s2 = _confirmed_sub_with_cash_leg(
            test_db, "SR_P2", "SR_I2", 5000.0, date(2025, 9, 2), date(2025, 9, 3))
        p = test_db.query(Portfolio).filter(Portfolio.code == "SR_P2").first()
        p.started_at = date(2025, 9, 2)
        test_db.commit()

        resp = client.post(f"/api/subscriptions/{s2.id}/unconfirm", headers=admin_headers)
        assert resp.status_code == 200
        test_db.refresh(p)
        assert _started_date(p) == date(2025, 9, 2)
        assert p.status == "active"

    def test_unconfirm_to_zero_reverts_draft(self, client, admin_headers, test_db):
        """unconfirm 至零确认申购 → draft + started_at NULL"""
        from app.models.portfolio import Portfolio
        create_portfolio(test_db, code="SR_P3", status="active")
        create_investor(test_db, code="SR_I3")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        s1 = _confirmed_sub_with_cash_leg(
            test_db, "SR_P3", "SR_I3", 10000.0, date(2025, 9, 1), date(2025, 9, 2))

        resp = client.post(f"/api/subscriptions/{s1.id}/unconfirm", headers=admin_headers)
        assert resp.status_code == 200
        p = test_db.query(Portfolio).filter(Portfolio.code == "SR_P3").first()
        assert p.status == "draft" and p.started_at is None

    def test_unconfirm_recomputes_started_at_without_autoflush(self, test_db):
        """回归：生产 session autoflush=False 时 started_at 重算不得脏读

        生产 SessionLocal 配置 autoflush=False（app/database.py），而测试会话默认
        autoflush=True 会掩盖「status=pending 未落库、min 聚合把本条仍算作
        confirmed」的脏读。本用例用同连接上的 autoflush=False 会话直连 service，
        模拟生产行为。
        """
        from sqlalchemy.orm import sessionmaker
        from app.models.portfolio import Portfolio
        from app.models.subscription import Subscription
        from app.services.subscription_service import unconfirm_single_subscription
        create_portfolio(test_db, code="SR_P5", status="active")
        create_investor(test_db, code="SR_I5")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        s1 = _confirmed_sub_with_cash_leg(
            test_db, "SR_P5", "SR_I5", 10000.0, date(2025, 9, 1), date(2025, 9, 2))
        test_db.commit()

        NoAutoflush = sessionmaker(
            bind=test_db.get_bind(), autoflush=False, expire_on_commit=False)
        db2 = NoAutoflush()
        try:
            sub = db2.query(Subscription).filter(Subscription.id == s1.id).first()
            unconfirm_single_subscription(db2, sub, check_snapshot=False)
            p = db2.query(Portfolio).filter(Portfolio.code == "SR_P5").first()
            assert p.started_at is None and p.status == "draft"
        finally:
            db2.close()

    def test_closed_not_reverted_by_cascade_unconfirm(self, test_db):
        """closed 组合经级联回退（check_snapshot=False）至零确认时保持 closed"""
        from app.models.portfolio import Portfolio
        from app.services.subscription_service import unconfirm_single_subscription
        create_portfolio(test_db, code="SR_P4", status="closed")
        create_investor(test_db, code="SR_I4")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        s1 = _confirmed_sub_with_cash_leg(
            test_db, "SR_P4", "SR_I4", 10000.0, date(2025, 9, 1), date(2025, 9, 2))

        unconfirm_single_subscription(test_db, s1, check_snapshot=False)
        test_db.commit()
        p = test_db.query(Portfolio).filter(Portfolio.code == "SR_P4").first()
        assert p.status == "closed" and p.started_at is None

    def test_reactivate_keeps_started_at_and_relaxed_write(self, client, admin_headers, test_db):
        """close/reactivate 不碰 started_at；空组合 reactivate 后新首购仍能写入
        （写入条件放宽为 started_at is None）"""
        from app.models.portfolio import Portfolio
        from app.services.subscription_service import unconfirm_single_subscription
        create_portfolio(test_db, code="RA_P1", status="active")
        create_investor(test_db, code="RA_I1")
        for d in (1, 2, 8, 9):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        s1 = _confirmed_sub_with_cash_leg(
            test_db, "RA_P1", "RA_I1", 10000.0, date(2025, 9, 1), date(2025, 9, 2))
        p = test_db.query(Portfolio).filter(Portfolio.code == "RA_P1").first()
        p.started_at = date(2025, 9, 2)
        test_db.commit()

        # 级联回退至零确认（closed 保持），再 close/reactivate 流转
        p.status = "closed"
        test_db.commit()
        unconfirm_single_subscription(test_db, s1, check_snapshot=False)
        test_db.commit()
        assert client.post("/api/portfolios/RA_P1/reactivate", headers=admin_headers).status_code == 200
        test_db.refresh(p)
        assert p.status == "active" and p.started_at is None

        # reactivate 后的新首购：status 已是 active，旧 draft 条件会漏设 started_at
        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "RA_P1", "investor_code": "RA_I1",
                "sub_type": "subscribe", "amount": 7000.0,
                "apply_date": "2025-09-08", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        new_id = resp.json()["id"]
        resp = client.post(f"/api/subscriptions/{new_id}/confirm", headers=admin_headers)
        assert resp.status_code == 200
        assert Decimal(str(resp.json()["unit_price"])) == Decimal("1.0000")
        test_db.refresh(p)
        assert p.started_at == date(2025, 9, 9) or _started_date(p) == date(2025, 9, 9)

    def test_close_reactivate_keeps_started_at_regression(self, client, admin_headers, test_db):
        """回归：close/reactivate 流转前后 started_at 完全不变"""
        from app.models.portfolio import Portfolio
        create_portfolio(test_db, code="RA_P2", status="active")
        create_investor(test_db, code="RA_I2")
        p = test_db.query(Portfolio).filter(Portfolio.code == "RA_P2").first()
        p.started_at = date(2025, 9, 2)
        test_db.commit()

        assert client.post("/api/portfolios/RA_P2/close", headers=admin_headers).status_code == 200
        assert client.post("/api/portfolios/RA_P2/reactivate", headers=admin_headers).status_code == 200
        test_db.refresh(p)
        assert _started_date(p) == date(2025, 9, 2)
