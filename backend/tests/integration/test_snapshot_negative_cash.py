# ============================================================================
# 集成测试：快照负现金阻断（issue #203，原 #71 warning 语义已翻转）
# ============================================================================
# 覆盖：
# - generate_daily_snapshots 负现金日抛 NEGATIVE_CASH 阻断，快照不落库
# - 正常场景 warnings 为 None（负现金已从 warning 升级为硬阻断）
# - recalculate 区间含负现金日 → 单日 error（code=NEGATIVE_CASH）且整体
#   回滚「无变化」（原快照完整保留）
# - status 端点返回 negative_cash_platforms（负现金平台列出、正常为空）
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot
from app.services import snapshot_service
from app.services.exceptions import BusinessError
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
    create_subscription,
    create_trade,
)


def _setup_cash_snapshot(db, portfolio_code: str, snapshot_date: date, amount: float = 1000.0):
    """为组合制造指定日的完整三表快照（仅 CASH 持仓，无需行情数据）"""
    create_position_snapshot(
        db, portfolio_code, "CASH", "",
        snapshot_date=snapshot_date,
        cash_amount=amount, unit_price=None, cost_price=None,
        market_value=amount, platform_code="MYCF",
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=amount, total_shares=amount, unit_price=1.0,
    )
    create_investor_holding(
        db, portfolio_code, "VIEWER", snapshot_date, shares=amount,
    )


class TestGenerateNegativeCashBlock:
    """单日生成快照的负现金阻断（issue #203）

    日期基于 conftest 交易日历（工作日均为交易日）：
    - D0 = 2025-06-06（周五）
    - 下一交易日 = 2025-06-09（周一）
    """

    D0 = date(2025, 6, 6)
    NEXT_DAY = date(2025, 6, 9)

    def test_negative_cash_blocked_and_not_persisted(self, client, admin_headers, test_db):
        """confirmed CASH sell 使现金转负 → 422 NEGATIVE_CASH 阻断，快照不落库"""
        port = create_portfolio(test_db, code="NEGC_GEN", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0, amount=1000.0)
        # 窗口内 confirmed CASH sell 3000 → 1000 - 3000 = -2000
        create_trade(
            test_db, port.code, "CASH", "",
            trade_type="sell", amount=3000.0, price=None,
            platform_code="MYCF", trade_date=self.NEXT_DAY,
            confirm_date=self.NEXT_DAY, status="confirmed",
        )

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": self.NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "NEGATIVE_CASH"
        assert detail["details"]["portfolio_code"] == port.code
        assert detail["details"]["target_date"] == self.NEXT_DAY.isoformat()
        assert detail["details"]["negative_cash"] == [
            {"platform_code": "MYCF", "cash_amount": -2000.0}
        ]

        # 快照未落库（阻断生效）
        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.snapshot_date == self.NEXT_DAY,
        ).first()
        assert pos is None

    def test_normal_cash_warnings_none(self, client, admin_headers, test_db):
        """正常正现金场景 → warnings 为 None"""
        port = create_portfolio(test_db, code="NEGC_OK", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0, amount=1000.0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": self.NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["warnings"] is None


class TestRecalculateNegativeCashBlock:
    """区间重算含负现金日的阻断与整体回滚（issue #203）"""

    PREV_DAY = date(2025, 6, 5)  # 基线日（周四）
    D0 = date(2025, 6, 6)
    NEXT_DAY = date(2025, 6, 9)

    def test_recalculate_blocked_and_rolls_back(self, client, admin_headers, test_db):
        """区间含负现金日 → error code=NEGATIVE_CASH，整体回滚「无变化」"""
        port = create_portfolio(test_db, code="NEGC_REC", status="active")
        _setup_cash_snapshot(test_db, port.code, self.PREV_DAY, amount=1000.0)
        _setup_cash_snapshot(test_db, port.code, self.D0, amount=1000.0)
        _setup_cash_snapshot(test_db, port.code, self.NEXT_DAY, amount=1000.0)
        # D0 生效的 confirmed CASH sell 3000 → 重建 D0 时 1000 - 3000 = -2000
        create_trade(
            test_db, port.code, "CASH", "",
            trade_type="sell", amount=3000.0, price=None,
            platform_code="MYCF", trade_date=self.D0,
            confirm_date=self.D0, status="confirmed",
        )

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": port.code,
                "start_date": self.D0.isoformat(),
                "end_date": self.NEXT_DAY.isoformat(),
            },
            params={"confirm": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]

        # D0 重建即被负现金阻断：error 带 NEGATIVE_CASH，无 warnings
        assert result["total_processed"] == 0
        assert result["warnings"] == []
        assert len(result["errors"]) == 1
        err = result["errors"][0]
        assert err["date"] == self.D0.isoformat()
        assert err["code"] == "NEGATIVE_CASH"
        assert err["details"]["negative_cash"] == [
            {"platform_code": "MYCF", "cash_amount": -2000.0}
        ]

        # 整体回滚「无变化」：原快照完整保留（D0 现金仍为基线 1000）
        test_db.expire_all()
        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.snapshot_date == self.D0,
            PortfolioPosition.product_code == "CASH",
        ).first()
        assert pos is not None
        assert Decimal(str(pos.cash_amount)) == Decimal("1000")
        pos_next = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.snapshot_date == self.NEXT_DAY,
            PortfolioPosition.product_code == "CASH",
        ).first()
        assert pos_next is not None

    def test_recalculate_no_negative_cash_empty_warnings(self, client, admin_headers, test_db):
        """区间无负现金 → warnings 为空"""
        port = create_portfolio(test_db, code="NEGC_REC_OK", status="active")
        _setup_cash_snapshot(test_db, port.code, self.PREV_DAY, amount=1000.0)
        _setup_cash_snapshot(test_db, port.code, self.D0, amount=1000.0)

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": port.code,
                "start_date": self.D0.isoformat(),
                "end_date": self.D0.isoformat(),
            },
            params={"confirm": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        result = data["results"][0]
        assert result["errors"] == []
        assert not result["warnings"]


class TestCascadeAbortKeepsSnapshots:
    """级联回退失败 → 整体中止、不删除任何快照（issue #203 用户确认语义①）"""

    D0 = date(2025, 6, 6)

    def test_cascade_failure_propagates_and_snapshots_survive(self, test_db, monkeypatch):
        """级联回退申购抛 BusinessError → _delete_existing_snapshots 透传错误，
        三表快照一行不删（杜绝「快照已删但申购仍 confirmed」孤儿记录）"""
        port = create_portfolio(test_db, code="NEGC_CAS", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0, amount=1000.0)
        # apply_date == D0 的 confirmed 申购（用该日快照净值确认）→ 触发级联回退
        create_subscription(
            test_db, portfolio_code=port.code, investor_code="VIEWER",
            sub_type="subscribe", amount=1000.0,
            apply_date=self.D0, confirm_date=self.D0,
            status="confirmed",
        )

        def boom(db_, sub, **kwargs):
            raise BusinessError(
                code="SIMULATED_CASCADE_FAILURE",
                message="模拟级联回退失败",
            )

        monkeypatch.setattr(
            snapshot_service, "unconfirm_single_subscription", boom
        )

        with pytest.raises(BusinessError) as exc_info:
            snapshot_service._delete_existing_snapshots(test_db, port.code, self.D0)
        assert exc_info.value.code == "SIMULATED_CASCADE_FAILURE"

        # 快照未被删除（错误发生在任何 delete 之前）
        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.snapshot_date == self.D0,
        ).all()
        assert pos, "级联失败后持仓快照不应被删除"
        vs = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == self.D0,
        ).first()
        assert vs is not None, "级联失败后市值快照不应被删除"


class TestSnapshotStatusNegativeCash:
    """status 端点的 negative_cash_platforms 字段（issue #71）"""

    D0 = date(2025, 6, 6)

    def test_negative_cash_platform_listed(self, client, admin_headers, test_db):
        """最新快照日存在负现金 CASH 行 → 平台被列出，正现金平台不列"""
        port = create_portfolio(test_db, code="NEGC_ST", status="active")
        create_value_snapshot(
            test_db, port.code, self.D0,
            total_value=500, total_shares=1000, unit_price=0.5,
        )
        # MYCF 平台负现金，TTJJ 平台正现金
        create_position_snapshot(
            test_db, port.code, "CASH", "",
            snapshot_date=self.D0, cash_amount=-500.0, unit_price=None,
            cost_price=None, market_value=-500.0,
            platform_code="MYCF",
        )
        create_position_snapshot(
            test_db, port.code, "CASH", "",
            snapshot_date=self.D0, cash_amount=1000.0, unit_price=None,
            cost_price=None, market_value=1000.0,
            platform_code="TTJJ",
        )

        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/status",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["negative_cash_platforms"] == ["MYCF"]

    def test_normal_cash_empty_list(self, client, admin_headers, test_db):
        """最新快照日现金均为正 → negative_cash_platforms 为空列表"""
        port = create_portfolio(test_db, code="NEGC_ST_OK", status="active")
        _setup_cash_snapshot(test_db, port.code, self.D0, amount=1000.0)

        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/status",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["negative_cash_platforms"] == []

    def test_no_snapshot_empty_list(self, client, admin_headers, test_db):
        """无任何快照 → negative_cash_platforms 为空列表"""
        port = create_portfolio(test_db, code="NEGC_ST_NONE", status="active")

        resp = client.get(
            f"/api/snapshots/portfolios/{port.code}/status",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["negative_cash_platforms"] == []
