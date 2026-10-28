# ============================================================================
# 集成测试：现金转移撤销（issue #788）+ 转移记录 cross_day 派生修正（issue #787）
# ============================================================================
# 覆盖 cancel_cash_transfer 的全部路径：
# - 当天完成转移撤销：两腿 cancelled、可用现金回退、现金流水两行消失、审计记录
# - 跨天在途转移撤销：sell confirmed / buy pending → 均 cancelled（buy 未入账，无现金守卫）
# - 已取消组幂等拒绝（TRANSFER_ALREADY_CANCELLED，零写入、无第二次审计）
# - 两腿状态混合（一条已 cancelled）保守拒绝（INVALID_STATUS）
# - rebal_ / sub_ 组拒绝（TRANSFER_NOT_FOUND，不碰调仓/申赎）
# - 组级快照保护（SNAPSHOT_DEPENDENCY）
# - 转入平台现金不足阻断（INSUFFICIENT_CASH）
# - viewer 403
# #787：已取消的当天转移不再被标为「跨天到账」（cross_day 按 buy.confirm_date > 转移日）
# ============================================================================

import json
from datetime import date
from decimal import Decimal

from app.models import AuditLog, Trade
from tests.factories import (
    create_portfolio,
    create_platform,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
    ensure_trading_day,
)

D0 = date(2025, 6, 6)   # 周五
T = date(2025, 6, 9)    # 周一
T1 = date(2025, 6, 10)  # 周二

PLAT_A = "CTC_PLAT_A"
PLAT_B = "CTC_PLAT_B"
INITIAL_CASH = 50000.0
TRANSFER_AMOUNT = 20000.0


def _ensure_trading_days(db):
    for d in [D0, T, T1]:
        ensure_trading_day(db, d, is_open=True)


def _setup_portfolio(db, code):
    """组合 + 两平台 + D0 快照（平台 A 持有全部现金）"""
    create_portfolio(db, code=code, status="active")
    create_platform(db, code=PLAT_A)
    create_platform(db, code=PLAT_B)
    create_position_snapshot(
        db, code, "CASH", "", D0,
        cash_amount=INITIAL_CASH, unit_price=None, cost_price=None,
        market_value=INITIAL_CASH, platform_code=PLAT_A,
    )
    create_value_snapshot(db, code, D0, total_value=INITIAL_CASH,
                          total_shares=INITIAL_CASH, unit_price=1.0)
    create_investor_holding(db, code, "VIEWER", D0, shares=INITIAL_CASH)


def _create_transfer(client, admin_headers, code, cross_day):
    resp = client.post(
        f"/api/portfolios/{code}/cash-transfer",
        json={
            "from_platform": PLAT_A,
            "to_platform": PLAT_B,
            "amount": TRANSFER_AMOUNT,
            "cross_day": cross_day,
            "transfer_date": T.isoformat(),
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, f"Transfer failed: {resp.status_code} {resp.text}"
    return resp.json()


def _group_legs(db, group):
    legs = {
        t.trade_type: t
        for t in db.query(Trade).filter(Trade.transfer_group == group).all()
    }
    assert set(legs) == {"sell", "buy"}, f"组 {group} 两腿结构异常: {set(legs)}"
    return legs


class TestCancelSameDayTransfer:
    """当天完成转移（两腿 confirmed）撤销：#788 主路径"""

    def test_cancel_restores_cash_and_hides_flows(
        self, client, admin_headers, test_db
    ):
        code = "CTC_SAME_1"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        created = _create_transfer(client, admin_headers, code, cross_day=False)
        assert created["sell_status"] == "confirmed"
        assert created["buy_status"] == "confirmed"
        group = created["transfer_group"]

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Cancel failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["sell_status"] == "cancelled"
        assert body["buy_status"] == "cancelled"
        assert body["transfer_date"] == T.isoformat()

        test_db.expire_all()
        legs = _group_legs(test_db, group)
        assert legs["sell"].status == "cancelled"
        assert legs["buy"].status == "cancelled"
        # trade 行保留（逻辑取消，不物理删除）
        assert legs["sell"].trade_date == T
        assert legs["buy"].trade_date == T

        # 可用现金回退到转移前
        from app.services.position_service import calculate_available_cash
        cash_a = calculate_available_cash(test_db, code, PLAT_A)
        cash_b = calculate_available_cash(test_db, code, PLAT_B)
        assert Decimal(str(cash_a)) == Decimal(str(INITIAL_CASH))
        assert Decimal(str(cash_b)) == Decimal("0")

        # 现金流水两行消失（cancelled 不进现金流水）
        flows = client.get(
            f"/api/portfolios/{code}/cash-flows?page=1&page_size=50",
            headers=admin_headers,
        )
        assert flows.status_code == 200
        flow_groups = {
            item["origin_ref"].get("transfer_group")
            for item in flows.json()["items"]
        }
        assert group not in flow_groups

        # 审计：cancel 动作记录组号与两腿原状态
        audit = test_db.query(AuditLog).filter_by(
            action="cancel", resource_type="cash_transfer", resource_id=group
        ).first()
        assert audit is not None, "撤销必须写审计"
        assert json.loads(audit.old_value) == {
            "sell_status": "confirmed",
            "buy_status": "confirmed",
        }

    def test_cancel_already_cancelled_rejected_idempotent(
        self, client, admin_headers, test_db
    ):
        code = "CTC_SAME_2"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        group = _create_transfer(client, admin_headers, code, cross_day=False)["transfer_group"]

        first = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        assert first.status_code == 200
        audit_count = test_db.query(AuditLog).filter_by(
            action="cancel", resource_type="cash_transfer", resource_id=group
        ).count()

        second = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        assert second.status_code == 422
        assert second.json()["detail"]["error"] == "TRANSFER_ALREADY_CANCELLED"
        # 零写入：无第二次审计、状态不变
        assert test_db.query(AuditLog).filter_by(
            action="cancel", resource_type="cash_transfer", resource_id=group
        ).count() == audit_count
        test_db.expire_all()
        legs = _group_legs(test_db, group)
        assert legs["sell"].status == "cancelled"
        assert legs["buy"].status == "cancelled"

    def test_cross_day_flag_false_for_cancelled_same_day_transfer(
        self, client, admin_headers, test_db
    ):
        """#787：已取消的当天转移不再被标为「跨天到账」"""
        code = "CTC_SAME_3"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        group = _create_transfer(client, admin_headers, code, cross_day=False)["transfer_group"]

        # 撤销前：当天完成，cross_day=False
        items = client.get(
            f"/api/portfolios/{code}/cash-transfers", headers=admin_headers
        ).json()["items"]
        row = next(i for i in items if i["transfer_group"] == group)
        assert row["cross_day"] is False

        client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        items = client.get(
            f"/api/portfolios/{code}/cash-transfers", headers=admin_headers
        ).json()["items"]
        row = next(i for i in items if i["transfer_group"] == group)
        assert row["sell_status"] == "cancelled"
        assert row["buy_status"] == "cancelled"
        assert row["cross_day"] is False, "已取消的当天转移不得标为跨天到账"


class TestCancelCrossDayInTransitTransfer:
    """跨天在途转移（sell confirmed / buy pending）撤销"""

    def test_cancel_in_transit_transfer(self, client, admin_headers, test_db):
        code = "CTC_XD_1"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        created = _create_transfer(client, admin_headers, code, cross_day=True)
        assert created["sell_status"] == "confirmed"
        assert created["buy_status"] == "pending"
        group = created["transfer_group"]

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Cancel failed: {resp.status_code} {resp.text}"
        assert resp.json()["sell_status"] == "cancelled"
        assert resp.json()["buy_status"] == "cancelled"

        test_db.expire_all()
        legs = _group_legs(test_db, group)
        assert legs["sell"].status == "cancelled"
        assert legs["buy"].status == "cancelled"
        # 原 confirm_date 语义保留（sell=转出日、buy=原预计到账日），撤销不改历史日期
        assert legs["sell"].confirm_date == T
        assert legs["buy"].confirm_date == T1

        # 转入腿未入账（pending），撤销后可用现金回到转移前
        from app.services.position_service import calculate_available_cash
        cash_a = calculate_available_cash(test_db, code, PLAT_A)
        assert Decimal(str(cash_a)) == Decimal(str(INITIAL_CASH))

        # 撤销后列表类型仍如实标记跨天（转移形态本身是跨天，#787 只修误标）
        items = client.get(
            f"/api/portfolios/{code}/cash-transfers", headers=admin_headers
        ).json()["items"]
        row = next(i for i in items if i["transfer_group"] == group)
        assert row["cross_day"] is True

    def test_cancel_confirmed_cross_day_transfer_runs_cash_guard(
        self, client, admin_headers, test_db
    ):
        """跨天转移确认到账（两腿 confirmed）后再撤销：走转入平台现金守卫"""
        code = "CTC_XD_2"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        created = _create_transfer(client, admin_headers, code, cross_day=True)
        group = created["transfer_group"]
        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/confirm",
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Confirm failed: {resp.text}"

        # 把转入平台 B 的现金全部转回 A（第二笔当天完成转移，两腿 confirmed），
        # 使 B 可用现金归零——注意须在 buy 腿到账日（T1）之后创建，
        # 否则按在途口径 B 的现金未入账，第二笔转移因验资失败
        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer",
            json={
                "from_platform": PLAT_B,
                "to_platform": PLAT_A,
                "amount": TRANSFER_AMOUNT,
                "cross_day": False,
                "transfer_date": T1.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Second transfer failed: {resp.text}"

        # 撤销第一笔转移：B 平台需减回 20000，但可用现金已 0 → 阻断
        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "INSUFFICIENT_CASH"
        assert detail["details"]["platform_code"] == PLAT_B

        # 零写入：两腿仍 confirmed
        test_db.expire_all()
        legs = _group_legs(test_db, group)
        assert legs["sell"].status == "confirmed"
        assert legs["buy"].status == "confirmed"


class TestCancelGuards:
    """撤销守卫：快照保护 / 现金不足 / 组类型 / 混合状态 / 权限"""

    def test_snapshot_protection_blocks_cancel(
        self, client, admin_headers, test_db
    ):
        code = "CTC_GUARD_1"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        group = _create_transfer(client, admin_headers, code, cross_day=False)["transfer_group"]

        # 转移日及之后生成快照（把转移计入历史）
        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": code, "target_date": T.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Snapshot failed: {resp.status_code} {resp.text}"
        assert resp.json()["success"] is True

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "SNAPSHOT_DEPENDENCY"
        assert detail["details"]["from_date"] == T.isoformat()

        test_db.expire_all()
        legs = _group_legs(test_db, group)
        assert legs["sell"].status == "confirmed"
        assert legs["buy"].status == "confirmed"

    def test_rebal_group_rejected(self, client, admin_headers, test_db):
        code = "CTC_GUARD_2"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        rebal_group = "rebal_abc123"
        from tests.factories import create_trade
        # 调仓组只有一条 CASH 腿（单腿即应拒绝，前缀守卫先于结构校验）
        create_trade(
            test_db, portfolio_code=code, product_code="CASH",
            market="", platform_code=PLAT_A, trade_type="sell",
            amount=1000.0, shares=None, price=1.0, fee=0.0,
            actual_amount=1000.0, trade_date=T, status="confirmed",
            transfer_group=rebal_group,
        )
        test_db.flush()

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{rebal_group}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "TRANSFER_NOT_FOUND"
        # 调仓腿不受影响
        test_db.expire_all()
        leg = test_db.query(Trade).filter_by(transfer_group=rebal_group).first()
        assert leg.status == "confirmed"

    def test_mixed_status_group_rejected(self, client, admin_headers, test_db):
        code = "CTC_GUARD_3"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        group = _create_transfer(client, admin_headers, code, cross_day=False)["transfer_group"]

        # 人为制造混合状态：sell 已 cancelled、buy confirmed
        test_db.expire_all()
        legs = _group_legs(test_db, group)
        legs["sell"].status = "cancelled"
        test_db.flush()

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_STATUS"
        # buy 腿不被波及
        test_db.expire_all()
        legs = _group_legs(test_db, group)
        assert legs["buy"].status == "confirmed"

    def test_viewer_forbidden(self, client, viewer_headers, admin_headers, test_db):
        code = "CTC_GUARD_4"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)
        group = _create_transfer(client, admin_headers, code, cross_day=False)["transfer_group"]

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/cancel",
            headers=viewer_headers,
        )
        assert resp.status_code == 403

        test_db.expire_all()
        legs = _group_legs(test_db, group)
        assert legs["sell"].status == "confirmed"
        assert legs["buy"].status == "confirmed"

    def test_unknown_group_404(self, client, admin_headers, test_db):
        code = "CTC_GUARD_5"
        _ensure_trading_days(test_db)
        _setup_portfolio(test_db, code)

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/no_such_group/cancel",
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "TRANSFER_NOT_FOUND"
