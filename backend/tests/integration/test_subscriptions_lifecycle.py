# ============ 集成测试：申购赎回·生命周期与快照防护（自 test_subscriptions.py 拆分，issue #469） ============
# 原「集成测试：申购赎回」套件的 unconfirm/快照防护/编辑部分；其余三个文件同源拆分。
# 本文件覆盖：
#   - TestUnconfirmConsumedCashAllowed / TestRedeemConfirmCashCheck：issue #203 负现金两道防线
#   - TestSnapshotHistoryGapGuard（#180）：零快照 + 目标日前有确认交易 → 拒绝单日 generate（banner 见下）
#   - TestAutoConfirmOutOfOrder：乱序早期申购只记 auto_confirm_failed，不阻断整批
#   - TestSubscriptionUpdate（#202）：PUT /api/subscriptions/{id} 支持 apply_date 编辑
# 共享工厂 _confirmed_sub_with_cash_leg 见 tests/integration/subscription_helpers.py（原属 #180 章节）。

import pytest
from datetime import date
from decimal import Decimal

from tests.factories import (
    create_portfolio, create_investor, create_subscription,
    create_investor_holding, create_value_snapshot, ensure_trading_day,
    create_trade, create_position_snapshot,
)
from tests.integration.subscription_helpers import _confirmed_sub_with_cash_leg
from app.models.subscription import Subscription


class TestUnconfirmConsumedCashAllowed:
    """#203：入金已被消耗时 unconfirm 不再拦截（原 #180 守卫移除）。

    负现金防护改由①赎回确认消费点校验（INSUFFICIENT_CASH）②快照生成阻断
    （NEGATIVE_CASH）承担；unconfirm 放行，避免阻断快照删除级联回退。
    """

    def test_unconfirm_allowed_when_cash_consumed(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="RC_P1")
        create_investor(test_db, code="RC_I1")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "RC_P1", "investor_code": "RC_I1",
                "sub_type": "subscribe", "amount": 10000.0,
                "apply_date": "2025-09-01", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        s1_id = resp.json()["id"]
        assert client.post(f"/api/subscriptions/{s1_id}/confirm", headers=admin_headers).status_code == 200

        # 模拟现金被后续交易消耗（confirmed CASH sell 8000，余额仅剩 2000）
        create_trade(
            test_db, "RC_P1", "CASH", "",
            trade_type="sell", amount=8000.0, price=1.0,
            trade_date=date(2025, 9, 2), confirm_date=date(2025, 9, 2),
            actual_amount=8000.0, status="confirmed",
            transfer_group="rc_consume",
        )

        resp = client.post(f"/api/subscriptions/{s1_id}/unconfirm", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        still = test_db.query(Subscription).filter(Subscription.id == s1_id).first()
        assert still.status == "pending"


class TestRedeemConfirmCashCheck:
    """#203 消费点校验：赎回确认前校验平台可用现金足以支付赎回金额"""

    def _seed(self, test_db, port: str, investor: str):
        create_portfolio(test_db, code=port, status="active")
        create_investor(test_db, code=investor)
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        # 份额基线：价值快照 + 投资人持仓（申请日快照与现金基线由 _create_redeem 补）
        create_value_snapshot(test_db, port, date(2025, 8, 29),
                              total_value=10000, total_shares=10000, unit_price=1.0)
        create_investor_holding(test_db, port, investor, date(2025, 8, 29), shares=10000)

    def _create_redeem(self, test_db, client, admin_headers, port: str, investor: str,
                       cash: float):
        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": port, "investor_code": investor,
                "sub_type": "redeem", "shares": 4000.0,
                "apply_date": "2025-09-01", "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"Response: {resp.status_code} {resp.json()}"
        sub_id = resp.json()["id"]
        # 申请日快照：净值 1.25 → 赎回金额 = 4000 × 1.25 = 5000；
        # CASH 行同时作为可用现金基线（最新快照日即 calculate_available_cash 基线日）
        create_value_snapshot(test_db, port, date(2025, 9, 1),
                              total_value=12500, total_shares=10000, unit_price=1.25)
        create_position_snapshot(
            test_db, port, "CASH", "", snapshot_date=date(2025, 9, 1),
            cash_amount=cash, unit_price=None, cost_price=None,
            market_value=cash, platform_code="MYCF",
        )
        return sub_id

    def test_confirm_rejected_when_cash_insufficient(self, client, admin_headers, test_db):
        """可用 2000 < 赎回金额 5000 → INSUFFICIENT_CASH，记录保持 pending"""
        self._seed(test_db, "RCC_P1", "RCC_I1")
        sub_id = self._create_redeem(test_db, client, admin_headers, "RCC_P1", "RCC_I1",
                                     cash=2000.0)

        conf = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert conf.status_code == 422
        detail = conf.json()["detail"]
        assert detail["error"] == "INSUFFICIENT_CASH"
        assert detail["details"]["required"] == "5000.00"
        assert float(detail["details"]["available"]) == 2000.0
        assert float(detail["details"]["deficit"]) == 3000.0
        still = test_db.query(Subscription).filter(Subscription.id == sub_id).first()
        assert still.status == "pending"

    def test_confirm_allowed_when_cash_sufficient(self, client, admin_headers, test_db):
        """可用 8000 ≥ 赎回金额 5000 → 确认成功并生成配对 CASH sell 腿"""
        self._seed(test_db, "RCC_P2", "RCC_I2")
        sub_id = self._create_redeem(test_db, client, admin_headers, "RCC_P2", "RCC_I2",
                                     cash=8000.0)

        conf = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        confirmed = conf.json()
        assert confirmed["status"] == "confirmed"
        assert float(confirmed["amount"]) == 5000.0


# ============================================================================
# issue #180：零快照 + 目标日前有确认交易 → 拒绝单日 generate（防首快照失忆）
# ============================================================================

class TestSnapshotHistoryGapGuard:

    def test_generate_rejected_when_earlier_arrivals_exist(self, test_db):
        from app.services.snapshot_service import generate_daily_snapshots
        from app.services.exceptions import BusinessError
        create_portfolio(test_db, code="SG_P1", status="active")
        create_investor(test_db, code="SG_I1")
        for d in (1, 2, 3, 4):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        _confirmed_sub_with_cash_leg(
            test_db, "SG_P1", "SG_I1", 10000.0, date(2025, 9, 1), date(2025, 9, 2))

        with pytest.raises(BusinessError) as exc_info:
            generate_daily_snapshots(test_db, "SG_P1", date(2025, 9, 4))
        assert exc_info.value.code == "SNAPSHOT_REQUIRES_RECALCULATE"

    def test_generate_at_earliest_confirm_date_allowed(self, test_db):
        """目标日即最早到账日的真正首次生成不受守卫影响（须真实生成成功）"""
        from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
        from app.services.snapshot_service import generate_daily_snapshots
        create_portfolio(test_db, code="SG_P2", status="active")
        create_investor(test_db, code="SG_I2")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)
        _confirmed_sub_with_cash_leg(
            test_db, "SG_P2", "SG_I2", 10000.0, date(2025, 9, 1), date(2025, 9, 2))

        # 不捕获异常：任何 BusinessError 都直接使测试失败
        generate_daily_snapshots(test_db, "SG_P2", date(2025, 9, 2))

        snap = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "SG_P2",
            PortfolioValueSnapshot.snapshot_date == date(2025, 9, 2),
        ).first()
        assert snap is not None

    def test_recalculate_captures_history_cash(self, test_db):
        """recalculate 从最早 confirm_date 逐日重建，首张快照含历史 CASH 到账"""
        from app.models.portfolio_position import PortfolioPosition
        from app.services.snapshot_service import recalculate_snapshots
        create_portfolio(test_db, code="SG_P3", status="active")
        create_investor(test_db, code="SG_I3")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        _confirmed_sub_with_cash_leg(
            test_db, "SG_P3", "SG_I3", 10000.0, date(2025, 9, 1), date(2025, 9, 2))

        result = recalculate_snapshots(test_db, "SG_P3", date(2025, 9, 2), date(2025, 9, 3))
        assert not result.get("errors"), result
        test_db.commit()

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "SG_P3",
            PortfolioPosition.snapshot_date == date(2025, 9, 2),
            PortfolioPosition.product_code == "CASH",
        ).first()
        assert pos is not None
        assert Decimal(str(pos.cash_amount)) == Decimal("10000")


class TestAutoConfirmOutOfOrder:

    def test_out_of_order_sub_fails_not_blocking(self, test_db):
        """乱序早期申购在 auto_confirm 下 fail 为 auto_confirm_failed，不阻断整批"""
        from app.models.portfolio import Portfolio
        from app.services.snapshot_service import auto_confirm_after_snapshot
        create_portfolio(test_db, code="AC_P1", status="active")
        create_investor(test_db, code="AC_I1")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 8, d), is_open=True)
        for d in (1, 2, 3, 4):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        s1 = _confirmed_sub_with_cash_leg(
            test_db, "AC_P1", "AC_I1", 10000.0, date(2025, 9, 1), date(2025, 9, 2))
        p = test_db.query(Portfolio).filter(Portfolio.code == "AC_P1").first()
        p.started_at = s1.confirm_date
        test_db.commit()
        # 乱序 pending 早期申购：confirm=09-01 < started_at=09-02
        s0 = create_subscription(
            test_db, "AC_P1", "AC_I1", sub_type="subscribe", amount=8000.0,
            apply_date=date(2025, 8, 29), confirm_date=date(2025, 9, 1),
            status="pending",
        )

        results = auto_confirm_after_snapshot(test_db, "AC_P1", date(2025, 9, 2))
        failed = [r for r in results if r["id"] == s0.id]
        assert failed and failed[0]["action"] == "auto_confirm_failed"
        assert "首笔到账日" in failed[0]["error"]  # CONFIRM_BEFORE_STARTED 闸门文案
        test_db.refresh(s0)
        assert s0.status == "pending"


class TestSubscriptionUpdate:
    """申赎编辑测试（issue #202：PUT /api/subscriptions/{id} 支持 apply_date 编辑）"""

    def test_update_pending_amount_quantized(self, client, admin_headers, test_db):
        """pending 申购改金额：量化 2 位落库"""
        create_portfolio(test_db, code="UPD_P1", status="active")
        create_investor(test_db, code="UPD_I1")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        sub = create_subscription(
            test_db, "UPD_P1", "UPD_I1", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1),
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"amount": 12000.555},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.refresh(sub)
        assert sub.amount == Decimal("12000.56")  # ROUND_HALF_UP

    def test_update_apply_date_recomputes_confirm_date(self, client, admin_headers, test_db):
        """改申请日：预计确认日按 T+1 重算"""
        create_portfolio(test_db, code="UPD_P2", status="active")
        create_investor(test_db, code="UPD_I2")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        sub = create_subscription(
            test_db, "UPD_P2", "UPD_I2", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1),
            confirm_date=date(2025, 9, 2),
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"apply_date": "2025-09-02"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.refresh(sub)
        assert sub.apply_date == date(2025, 9, 2)
        assert sub.confirm_date == date(2025, 9, 3)

    def test_update_apply_date_non_trading_day_rejected(self, client, admin_headers, test_db):
        """改到非交易日拒绝 NON_TRADING_DAY"""
        create_portfolio(test_db, code="UPD_P3", status="active")
        create_investor(test_db, code="UPD_I3")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 6), is_open=False)
        sub = create_subscription(
            test_db, "UPD_P3", "UPD_I3", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1),
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"apply_date": "2025-09-06"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "NON_TRADING_DAY"

    def test_update_apply_date_before_snapshot_rejected(self, client, admin_headers, test_db):
        """改到最新快照日及之前拒绝 DATE_BEFORE_SNAPSHOT"""
        create_portfolio(test_db, code="UPD_P4", status="active")
        create_investor(test_db, code="UPD_I4")
        for d in (1, 2, 3):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        create_value_snapshot(test_db, "UPD_P4", date(2025, 9, 2), 10000, 10000, 1.0)
        sub = create_subscription(
            test_db, "UPD_P4", "UPD_I4", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 3),
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"apply_date": "2025-09-02"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "DATE_BEFORE_SNAPSHOT"

    def test_update_confirmed_rejected(self, client, admin_headers, test_db):
        """confirmed 拒绝修改 CANNOT_MODIFY_CONFIRMED"""
        create_portfolio(test_db, code="UPD_P5", status="active")
        create_investor(test_db, code="UPD_I5")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        sub = create_subscription(
            test_db, "UPD_P5", "UPD_I5", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1),
            confirm_date=date(2025, 9, 2), status="confirmed",
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"amount": 20000.0},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CANNOT_MODIFY_CONFIRMED"

    def test_update_cancelled_rejected(self, client, admin_headers, test_db):
        """cancelled 拒绝修改 INVALID_STATUS（终态不可复活改值，与 update_trade 同口径）"""
        create_portfolio(test_db, code="UPD_P7", status="active")
        create_investor(test_db, code="UPD_I7")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        sub = create_subscription(
            test_db, "UPD_P7", "UPD_I7", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1), status="cancelled",
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"apply_date": "2025-09-01"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_STATUS"

    def test_update_redeem_shares_adds_back_own_pending(self, client, admin_headers, test_db):
        """赎回改份额：加回自身 pending 旧份额后与可用份额精确比较"""
        create_portfolio(test_db, code="UPD_P6", status="active")
        create_investor(test_db, code="UPD_I6")
        for d in (1, 2):
            ensure_trading_day(test_db, date(2025, 9, d), is_open=True)
        create_investor_holding(
            test_db, "UPD_P6", "UPD_I6", snapshot_date=date(2025, 9, 1), shares=1000,
        )
        sub = create_subscription(
            test_db, "UPD_P6", "UPD_I6", sub_type="redeem",
            shares=500.0, apply_date=date(2025, 9, 1),
        )

        # 可用 = 1000 - 500(自身) + 500(加回) = 1000，改到 1000 应放行
        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"shares": 1000},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        # 超出 1 分即拒 INSUFFICIENT_SHARES（精确比较无容差）
        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"shares": 1000.01},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INSUFFICIENT_SHARES"

    def test_update_explicit_null_rejected(self, client, admin_headers, test_db):
        """显式 null 拒绝 INVALID_PARAM，不可绕过校验落库脏数据（PR #204 评审）"""
        create_portfolio(test_db, code="UPD_P8", status="active")
        create_investor(test_db, code="UPD_I8")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        sub = create_subscription(
            test_db, "UPD_P8", "UPD_I8", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1),
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"amount": None},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"
        test_db.refresh(sub)
        assert sub.amount == Decimal("10000.00")  # 零写入，原值不变

    def test_update_type_mismatch_rejected(self, client, admin_headers, test_db):
        """字段按申赎类型收口：subscribe 拒 shares、redeem 拒 amount（与创建同口径）"""
        create_portfolio(test_db, code="UPD_P9", status="active")
        create_investor(test_db, code="UPD_I9")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        sub_s = create_subscription(
            test_db, "UPD_P9", "UPD_I9", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1),
        )
        sub_r = create_subscription(
            test_db, "UPD_P9", "UPD_I9", sub_type="redeem",
            shares=100.0, apply_date=date(2025, 9, 1),
        )

        resp = client.put(
            f"/api/subscriptions/{sub_s.id}", json={"shares": 100},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

        resp = client.put(
            f"/api/subscriptions/{sub_r.id}", json={"amount": 5000},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PARAM"

    def test_update_notes_null_clears(self, client, admin_headers, test_db):
        """notes 传 null 清除备注（唯一放行 null 的字段，PR #204 评审）"""
        create_portfolio(test_db, code="UPD_P10", status="active")
        create_investor(test_db, code="UPD_I10")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        sub = create_subscription(
            test_db, "UPD_P10", "UPD_I10", sub_type="subscribe",
            amount=10000.0, apply_date=date(2025, 9, 1),
        )
        sub.notes = "原备注"
        test_db.commit()

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"notes": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        test_db.refresh(sub)
        assert sub.notes is None
