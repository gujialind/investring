# ============================================================================
# 集成测试：跨天现金转移在交易日历耗尽时的拒绝（issue #591 裸消费点）
# ============================================================================
# 覆盖 cash_transfer_service 的两个 get_next_trading_day 消费点：
#   create(:119，跨天转入腿的到账日) 与 confirm(:178，pending 腿 confirm_date 为空时兜底推导)。
# 两处抛出的**位置**决定了残留语义不同，分别显式断言：
#   - create 的抛出在 :109 的 db.flush() **之后**，两腿已 INSERT 进事务，
#     零残留完全依赖请求会话结束时的回滚（router 只在成功路径 commit）；
#   - confirm 的抛出必须先于 TRANSFER_NOT_READY 判定，否则「日历不足」会被
#     伪装成「尚未到确认日」——那是另一种静默误导。
# ============================================================================
from datetime import date

import pytest

from app.models import PortfolioValueSnapshot, Trade
from tests.factories import (
    create_investor_holding,
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
)
from tests.integration.calendar_exhaustion_helpers import (
    business_rows,
    make_last_open_day,
)

LAST = date(2025, 6, 6)      # 受控日历里最后一个开市日
BASELINE = date(2025, 6, 3)


def _portfolio_with_cash(db, code, amount=50000.0):
    create_portfolio(db, code=code, status="active")
    create_position_snapshot(
        db, code, "CASH", "", BASELINE,
        cash_amount=amount, unit_price=None, cost_price=None,
        market_value=amount, platform_code="MYCF",
    )
    create_value_snapshot(db, code, BASELINE, total_value=amount,
                          total_shares=amount, unit_price=1.0)
    create_investor_holding(db, code, "VIEWER", BASELINE, shares=amount)
    return code


class TestCashTransferCreateRejects:
    def _payload(self):
        return {
            "from_platform": "MYCF",
            "to_platform": "HBZQ",
            "amount": 1000.0,
            "transfer_date": LAST.isoformat(),
            "cross_day": True,
        }

    def test_cross_day_create_is_422_and_flushed_legs_are_rolled_back(
        self, client, admin_headers, test_db
    ):
        """抛出发生在两腿 flush 之后：残留全靠请求会话的回滚兜住，须显式钉住。"""
        code = _portfolio_with_cash(test_db, "CT_EXH_C")
        make_last_open_day(test_db, LAST)
        before = business_rows(test_db, code)

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer",
            json=self._payload(), headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "CALENDAR_NOT_SYNCED"
        # 此刻两腿仍留在测试夹具的共享会话里（生产由 get_db 的 close/rollback 丢弃），
        # 故先模拟同一出口再断言零落库——否则测的是夹具的共享性而非生产语义。
        test_db.rollback()
        # 基线（factories 内部 commit 过）必须还在：若回滚把整个外层事务抹掉，
        # 下面的「零残留」会因为「什么都不在」而空转通过。
        assert test_db.query(PortfolioValueSnapshot).filter_by(
            portfolio_code=code, snapshot_date=BASELINE
        ).first() is not None
        assert business_rows(test_db, code) == before, (
            "跨天腿已 flush 后抛出，回滚出口必须清干净，否则留下半笔转移"
        )

    def test_same_day_transfer_still_succeeds(self, client, admin_headers, test_db):
        """正向对照：cross_day=False 不需要下一交易日 → 最后一个日历日上照常成功。"""
        code = _portfolio_with_cash(test_db, "CT_EXH_OK")
        make_last_open_day(test_db, LAST)

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer",
            json={**self._payload(), "cross_day": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["sell_status"] == "confirmed"
        assert resp.json()["buy_status"] == "confirmed"


class TestCashTransferConfirmRejects:
    def test_null_confirm_date_leg_reports_calendar_not_transfer_not_ready(
        self, client, admin_headers, test_db
    ):
        """confirm_date 为 NULL 的 pending 腿：兜底推导(:178) 抛出必须**先于**
        TRANSFER_NOT_READY，否则日历缺失会被伪装成「还没到确认日」。"""
        code = _portfolio_with_cash(test_db, "CT_EXH_CF")
        group = "xfer_exh_null"
        for leg_type, status, confirm_date in (
            ("sell", "confirmed", LAST),
            ("buy", "pending", None),      # 待确认腿：confirm_date 空 → 走兜底推导
        ):
            test_db.add(Trade(
                portfolio_code=code, platform_code="MYCF",
                product_code="CASH", market="", trade_type=leg_type,
                transfer_group=group, amount=1000.0, price=1.0, fee=0.0,
                actual_amount=1000.0, trade_date=LAST,
                confirm_date=confirm_date, status=status,
            ))
        make_last_open_day(test_db, LAST)
        test_db.flush()

        resp = client.post(
            f"/api/portfolios/{code}/cash-transfer/{group}/confirm",
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "CALENDAR_NOT_SYNCED", (
            f"日历不足不得被伪装成其他码：{detail}"
        )
        # 与 create 相反：:178 的抛出点在 `for leg in pending_legs` 赋值循环**之前**，
        # 所以无需回滚即可断言「未写状态」——buy 腿既没被确认，也没被编造出到账日。
        legs = {
            t.trade_type: t
            for t in test_db.query(Trade).filter(Trade.transfer_group == group).all()
        }
        assert {k: v.status for k, v in legs.items()} == {
            "sell": "confirmed", "buy": "pending",
        }
        assert legs["buy"].confirm_date is None, (
            "确认日不得在日历不足时被编造出来"
        )
