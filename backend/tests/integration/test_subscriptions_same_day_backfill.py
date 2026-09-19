# ============ 集成测试：申赎当天补录（issue #495） ============
# 申赎创建闸门由「申请日晚于最新快照日」放宽为「确认日晚于最新快照日」：
#   - D 日快照已生成后允许补录 apply_date == D 的申购/赎回（confirm_date = D+1，
#     按 D 日净值计价）；apply_date < D 仍拒绝（其 confirm_date <= D，确认日落在
#     已冻结区间会静默漏记）
#   - 配套机制 auto_confirm_before_snapshot：生成 D+1 快照前自动确认
#     latest < confirm_date <= target 的到期 pending，否则该笔被
#     _check_pending_transactions 按 confirm_date <= target 阻断、且生成后
#     auto_confirm（按 apply_date 匹配、快照落地后才运行）救不到 → 死锁
#
# 覆盖验收断言：
#  1. apply_date == 最新快照日 D 创建申赎 → 成功、pending、confirm_date = next_trading_day(D) > D
#  2. apply_date < D → 拒绝 DATE_BEFORE_SNAPSHOT，文案「确认日必须晚于最新快照日」
#  3. 边界：D-1（交易日）confirm_date = D <= D 恒拒，不因交易日历间隙放行
#  4. 定价：申购 shares = quantize(amount / nav_D)；赎回 amount = quantize(shares × nav_D)
#  5. 快照纳入：补录后生成 D+1 无需手动确认，total_shares/total_value/CASH 含该笔
#  6. 级联：删 D 快照 → 回退 pending；删 D+1 快照 → 不回退（级联按 apply_date == 快照日）
#  7. PUT apply_date 改到 D 放行并重算 confirm_date；改更早拒绝
#  8. legacy pending（confirm_date <= D）仍阻断生成，不静默放行

from datetime import date
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot, Trade
from app.models.subscription import Subscription
from app.services import snapshot_service
from tests.factories import (
    create_portfolio,
    create_investor,
    create_position_snapshot,
    create_price_record,
    create_product,
    create_subscription,
    create_value_snapshot,
    create_investor_holding,
    ensure_trading_day,
)

# conftest 交易日历：工作日均为交易日；显式 ensure 以锁定本文件依赖的日期
PREV_DAY = date(2025, 6, 5)   # 周四（D-1，交易日）
D0 = date(2025, 6, 6)         # 周五（最新快照日 D）
NEXT_DAY = date(2025, 6, 9)   # 周一（D+1 = 生成目标日）
EARLY_DAY = date(2025, 6, 4)  # 周三（legacy pending 用）

FUND = "BK495.OF"
MARKET = "CN_OTC"


def _ensure_days(test_db):
    for d in (EARLY_DAY, PREV_DAY, D0, NEXT_DAY):
        ensure_trading_day(test_db, d, is_open=True)


def _setup_gate_baseline(test_db, port: str, investor: str):
    """纯闸门场景基线：active 组合 + D 日市值快照（get_latest_snapshot_date 依据）"""
    create_portfolio(test_db, code=port, status="active")
    create_investor(test_db, code=investor)
    _ensure_days(test_db)
    create_value_snapshot(test_db, port, D0, total_value=10000, total_shares=10000,
                          unit_price=1.0)


def _setup_fund_baseline(test_db, port: str, investor: str, cash: float = 0.0):
    """完整三表基线：D 日基金持仓 10000 份 @1.0（可选 CASH），净值 1.0"""
    create_portfolio(test_db, code=port, status="active")
    create_investor(test_db, code=investor)
    _ensure_days(test_db)
    create_product(test_db, code=FUND, market=MARKET,
                   product_type="OEF", asset_class_code="ASSET_STOCK")
    create_position_snapshot(
        test_db, port, FUND, MARKET, snapshot_date=D0,
        shares=10000, unit_price=1.0, cost_price=1.0,
        market_value=10000, platform_code="MYCF",
    )
    if cash:
        create_position_snapshot(
            test_db, port, "CASH", "", snapshot_date=D0,
            cash_amount=cash, unit_price=None, cost_price=None,
            market_value=cash, platform_code="MYCF",
        )
    total_value = 10000 + cash
    create_value_snapshot(test_db, port, D0, total_value=total_value,
                          total_shares=10000, unit_price=1.0)
    create_investor_holding(test_db, port, investor, D0, shares=10000)


class TestSameDayBackfillGate:
    """#495 闸门：申请日 == 最新快照日放行，更早恒拒"""

    def test_create_subscribe_on_latest_snapshot_day_allowed(self, client, admin_headers, test_db):
        """apply_date == D → 成功、pending、confirm_date = D+1 > D（验收 1）"""
        _setup_gate_baseline(test_db, "BG_P1", "BG_I1")

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "BG_P1", "investor_code": "BG_I1",
                "sub_type": "subscribe", "amount": 5000.0,
                "apply_date": D0.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"{resp.status_code}: {resp.json()}"
        data = resp.json()
        assert data["status"] == "pending"
        assert data["confirm_date"] == NEXT_DAY.isoformat()

    def test_create_redeem_on_latest_snapshot_day_allowed(self, client, admin_headers, test_db):
        """赎回同口径：申请日 == D 放行（可用份额按 D 日快照基线）"""
        _setup_gate_baseline(test_db, "BG_P2", "BG_I2")
        create_investor_holding(test_db, "BG_P2", "BG_I2", D0, shares=1000)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "BG_P2", "investor_code": "BG_I2",
                "sub_type": "redeem", "shares": 400.0,
                "apply_date": D0.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"{resp.status_code}: {resp.json()}"
        assert resp.json()["confirm_date"] == NEXT_DAY.isoformat()

    def test_create_before_latest_snapshot_rejected(self, client, admin_headers, test_db):
        """apply_date = D-1（交易日）→ confirm_date = D <= D 恒拒，新文案（验收 2、3）"""
        _setup_gate_baseline(test_db, "BG_P3", "BG_I3")

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "BG_P3", "investor_code": "BG_I3",
                "sub_type": "subscribe", "amount": 5000.0,
                "apply_date": PREV_DAY.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "DATE_BEFORE_SNAPSHOT"
        assert detail["message"] == f"确认日必须晚于最新快照日（{D0.isoformat()}）"

    def test_create_earlier_nonadjacent_rejected(self, client, admin_headers, test_db):
        """apply_date = D-2 → confirm_date = D-1 <= D 拒（交易日历间隙不放行）"""
        _setup_gate_baseline(test_db, "BG_P4", "BG_I4")

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "BG_P4", "investor_code": "BG_I4",
                "sub_type": "subscribe", "amount": 5000.0,
                "apply_date": EARLY_DAY.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "DATE_BEFORE_SNAPSHOT"

    def test_update_apply_date_to_latest_snapshot_day_allowed(self, client, admin_headers, test_db):
        """PUT apply_date 改到 D：放行并同步重算 confirm_date = D+1（验收 7）"""
        _setup_gate_baseline(test_db, "BG_P5", "BG_I5")
        sub = create_subscription(
            test_db, "BG_P5", "BG_I5", sub_type="subscribe",
            amount=5000.0, apply_date=NEXT_DAY, confirm_date=date(2025, 6, 10),
            status="pending",
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"apply_date": D0.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        test_db.refresh(sub)
        assert sub.apply_date == D0
        assert sub.confirm_date == NEXT_DAY

    def test_update_apply_date_earlier_rejected(self, client, admin_headers, test_db):
        """PUT apply_date 改到 D-1 → confirm_date = D <= D 拒绝（验收 7）"""
        _setup_gate_baseline(test_db, "BG_P6", "BG_I6")
        sub = create_subscription(
            test_db, "BG_P6", "BG_I6", sub_type="subscribe",
            amount=5000.0, apply_date=NEXT_DAY, confirm_date=date(2025, 6, 10),
            status="pending",
        )

        resp = client.put(
            f"/api/subscriptions/{sub.id}", json={"apply_date": PREV_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "DATE_BEFORE_SNAPSHOT"


class TestSameDayBackfillSnapshotInclusion:
    """#495 配套机制：生成前到期补确认 + D+1 快照自动纳入"""

    def test_subscribe_backfill_auto_confirmed_and_included(self, client, admin_headers, test_db):
        """D 日快照后补录 D 日申购 → 生成 D+1 无需手动确认：
        按 nav_D=1.0 计 2500 份；D+1 净值 1.2 → total_value=14500、total_shares=12500"""
        _setup_fund_baseline(test_db, "IN_P1", "IN_I1")

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "IN_P1", "investor_code": "IN_I1",
                "sub_type": "subscribe", "amount": 2500.0,
                "apply_date": D0.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"{resp.status_code}: {resp.json()}"
        sub_id = resp.json()["id"]

        create_price_record(test_db, FUND, MARKET, NEXT_DAY, 1.2)
        gen = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": "IN_P1", "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert gen.status_code == 200, f"{gen.status_code}: {gen.json()}"

        # 生成前到期补确认：无需手动 confirm，按 D 日净值计价
        sub = test_db.query(Subscription).filter(Subscription.id == sub_id).first()
        assert sub.status == "confirmed"
        assert Decimal(str(sub.unit_price)) == Decimal("1.0000")
        assert Decimal(str(sub.shares)) == Decimal("2500.00")
        leg = test_db.query(Trade).filter(Trade.transfer_group == f"sub_{sub_id}").first()
        assert leg is not None and leg.confirm_date == NEXT_DAY

        # D+1 快照三表均含该笔（验收 5）：份额 10000+2500，市值 10000×1.2+2500
        snap = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "IN_P1",
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first()
        assert Decimal(str(snap.total_shares)) == Decimal("12500.00")
        assert Decimal(str(snap.total_value)) == Decimal("14500.00")
        assert Decimal(str(snap.unit_price)) == Decimal("1.1600")
        cash_pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "IN_P1",
            PortfolioPosition.snapshot_date == NEXT_DAY,
            PortfolioPosition.product_code == "CASH",
        ).first()
        assert Decimal(str(cash_pos.cash_amount)) == Decimal("2500.00")

    def test_redeem_backfill_deducts_shares_and_cash(self, client, admin_headers, test_db):
        """D 日快照后补录 D 日赎回 2000 份（nav_D=1.0 → 金额 2000）：
        申赎不改基金持仓（份额只由调仓变动）；D+1 份额 8000、CASH 5000-2000=3000、
        市值 10000×1.2+3000=15000、净值 15000/8000=1.8750（验收 4、5）"""
        _setup_fund_baseline(test_db, "IN_P2", "IN_I2", cash=5000.0)

        resp = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "IN_P2", "investor_code": "IN_I2",
                "sub_type": "redeem", "shares": 2000.0,
                "apply_date": D0.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), f"{resp.status_code}: {resp.json()}"
        sub_id = resp.json()["id"]

        create_price_record(test_db, FUND, MARKET, NEXT_DAY, 1.2)
        gen = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": "IN_P2", "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert gen.status_code == 200, f"{gen.status_code}: {gen.json()}"

        sub = test_db.query(Subscription).filter(Subscription.id == sub_id).first()
        assert sub.status == "confirmed"
        assert Decimal(str(sub.unit_price)) == Decimal("1.0000")
        assert Decimal(str(sub.amount)) == Decimal("2000.00")

        snap = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "IN_P2",
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first()
        assert Decimal(str(snap.total_shares)) == Decimal("8000.00")
        assert Decimal(str(snap.total_value)) == Decimal("15000.00")
        assert Decimal(str(snap.unit_price)) == Decimal("1.8750")
        fund_pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "IN_P2",
            PortfolioPosition.snapshot_date == NEXT_DAY,
            PortfolioPosition.product_code == FUND,
        ).first()
        # 申赎不触发基金持仓变动（只有调仓改基金份额），赎回只动组合份额与 CASH
        assert Decimal(str(fund_pos.shares)) == Decimal("10000.00")
        cash_pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "IN_P2",
            PortfolioPosition.snapshot_date == NEXT_DAY,
            PortfolioPosition.product_code == "CASH",
        ).first()
        assert Decimal(str(cash_pos.cash_amount)) == Decimal("3000.00")

    def test_legacy_pending_still_blocks_generation(self, client, admin_headers, test_db):
        """legacy pending（confirm_date = D-1 <= D）不在补确认窗口，仍阻断生成（验收 8）"""
        from app.services.snapshot_service import generate_daily_snapshots
        _setup_fund_baseline(test_db, "IN_P3", "IN_I3")
        # 工厂直造（模拟历史/异常数据）：apply EARLY_DAY、confirm PREV_DAY <= D0
        create_subscription(
            test_db, "IN_P3", "IN_I3", sub_type="subscribe",
            amount=3000.0, apply_date=EARLY_DAY, confirm_date=PREV_DAY,
            status="pending",
        )
        create_price_record(test_db, FUND, MARKET, NEXT_DAY, 1.2)

        with pytest.raises(ValueError) as exc_info:
            generate_daily_snapshots(test_db, "IN_P3", NEXT_DAY)
        assert "请先确认" in str(exc_info.value)

    def test_delete_d0_cascades_backfill_to_pending(self, client, admin_headers, test_db):
        """删 D 快照 → 该笔（apply_date == D）级联回退 pending（验收 6）"""
        _setup_fund_baseline(test_db, "IN_P4", "IN_I4")
        sub_id = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "IN_P4", "investor_code": "IN_I4",
                "sub_type": "subscribe", "amount": 2500.0,
                "apply_date": D0.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        ).json()["id"]
        create_price_record(test_db, FUND, MARKET, NEXT_DAY, 1.2)
        gen = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": "IN_P4", "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert gen.status_code == 200, gen.json()

        snapshot_service._delete_existing_snapshots(test_db, "IN_P4", D0)
        sub = test_db.query(Subscription).filter(Subscription.id == sub_id).first()
        assert sub.status == "pending"
        assert sub.confirm_date == NEXT_DAY  # 回退按 T+1 重算预计确认日

    def test_delete_d1_keeps_backfill_confirmed(self, client, admin_headers, test_db):
        """删 D+1 快照 → 不回退该笔（级联按 apply_date == 快照日匹配，验收 6）"""
        _setup_fund_baseline(test_db, "IN_P5", "IN_I5")
        sub_id = client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": "IN_P5", "investor_code": "IN_I5",
                "sub_type": "subscribe", "amount": 2500.0,
                "apply_date": D0.isoformat(), "platform_code": "MYCF",
            },
            headers=admin_headers,
        ).json()["id"]
        create_price_record(test_db, FUND, MARKET, NEXT_DAY, 1.2)
        gen = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": "IN_P5", "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert gen.status_code == 200, gen.json()

        snapshot_service._delete_existing_snapshots(test_db, "IN_P5", NEXT_DAY)
        sub = test_db.query(Subscription).filter(Subscription.id == sub_id).first()
        assert sub.status == "confirmed"
