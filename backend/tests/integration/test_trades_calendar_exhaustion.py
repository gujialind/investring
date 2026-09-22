# ============================================================================
# 集成测试：调仓写路径在交易日历耗尽时的拒绝与零残留（issue #591 裸消费点）
# ============================================================================
# 覆盖 trade_service 的 4 个 get_next_trading_day 消费点：
#   create(:1237) / update 改期(:1555) / unconfirm(:1856) / sync_transfer_group(:521)。
# 生产代码无需改动：helper 抛 CALENDAR_NOT_SYNCED → 异常自然传播 → 全局 handler 出
# 422，请求事务未 commit 即随 get_db 关会话回滚。
# 另含一条**正向对照**：最后日历日上 confirm_days=0 的场内 T+0 仍必须成功
# （days=0 是零查询路径，不得被 #591 的收紧误伤）。
# ============================================================================
from datetime import date

import pytest

from app.services.exceptions import BusinessError
from app.services.trade_service import sync_transfer_group
from tests.factories import (
    create_portfolio,
    create_product,
    create_trade,
)
from tests.integration.calendar_exhaustion_helpers import (
    assert_rejected_without_residual,
    business_rows,
    make_last_open_day,
)

LAST = date(2025, 6, 6)      # 受控日历里最后一个开市日
EARLIER = date(2025, 6, 5)


def _fund(db, code="TRD_EXH.OF", confirm_days=1):
    return create_product(
        db, code=code, market="CN_OTC", confirm_days=confirm_days,
        asset_class_code="ASSET_STOCK",
    )


def _with_cash(db, portfolio_code, amount=50000.0):
    """买入要过现金闸门：按 test_trades_lifecycle 的先例，落一笔 confirmed CASH 腿。"""
    create_trade(
        db, portfolio_code, "CASH", "", trade_type="buy",
        amount=amount, price=None, platform_code="MYCF",
        trade_date=EARLIER, confirm_date=EARLIER, status="confirmed",
    )
    return portfolio_code


def _payload(portfolio_code, product_code, trade_date, trade_type="buy"):
    return {
        "portfolio_code": portfolio_code,
        "product_code": product_code,
        "market": "CN_OTC",
        "platform_code": "MYCF",
        "trade_type": trade_type,
        "trade_date": trade_date.isoformat(),
        "price": 2.0,
        "amount" if trade_type == "buy" else "shares": 1000.0
        if trade_type == "buy" else 500.0,
    }


class TestTradeCreateRejects:
    def test_create_on_last_open_day_is_422_with_no_residual(
        self, client, admin_headers, test_db
    ):
        code = create_portfolio(test_db, "TRD_EXH_C", status="active").code
        _fund(test_db)
        make_last_open_day(test_db, LAST)

        assert_rejected_without_residual(
            client.post, "/api/trades",
            _payload(code, "TRD_EXH.OF", LAST), admin_headers,
            test_db, code, "CALENDAR_NOT_SYNCED",
        )

    def test_control_create_earlier_date_succeeds(self, client, admin_headers, test_db):
        """正向对照：同一天之后仍有交易日时创建成功（拒绝只发生在耗尽处）。"""
        code = _with_cash(test_db, create_portfolio(test_db, "TRD_EXH_OK", status="active").code)
        _fund(test_db)
        resp = client.post(
            "/api/trades", json=_payload(code, "TRD_EXH.OF", EARLIER),
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["confirm_date"] == LAST.isoformat()

    def test_t_plus_zero_on_last_open_day_still_succeeds(
        self, client, admin_headers, test_db, sample_etf_product
    ):
        """#591 验收：days=0 是零查询合法路径——最后一个日历日上的场内 T+0 必须成功。"""
        code = _with_cash(test_db, create_portfolio(test_db, "TRD_EXH_T0", status="active").code)
        make_last_open_day(test_db, LAST)
        payload = {
            "portfolio_code": code,
            "product_code": sample_etf_product.code,
            "market": sample_etf_product.market,
            "platform_code": "MYCF",
            "trade_type": "buy",
            "trade_date": LAST.isoformat(),
            "price": 4.0,
            "amount": 4000.0,
        }
        resp = client.post("/api/trades", json=payload, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        # confirm_date == 下单日（T+0），未被任何日历查询污染
        assert resp.json()["confirm_date"] == LAST.isoformat()


class TestTradeUpdateRejects:
    def test_change_date_to_last_open_day_leaves_trade_untouched(
        self, client, admin_headers, test_db
    ):
        """改期(:1555)：new_confirm_date 在任何 setattr 之前算出 → 抛出即零变更。"""
        code = create_portfolio(test_db, "TRD_EXH_U", status="active").code
        _fund(test_db)
        trade = create_trade(
            test_db, code, "TRD_EXH.OF", "CN_OTC", trade_type="buy",
            amount=1000.0, actual_amount=1000.0, price=2.0,
            trade_date=EARLIER, confirm_date=LAST, status="pending",
        )
        make_last_open_day(test_db, LAST)
        before = business_rows(test_db, code)

        resp = client.put(
            f"/api/trades/{trade.id}",
            json={"trade_date": LAST.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"
        test_db.flush()
        assert business_rows(test_db, code) == before
        test_db.refresh(trade)
        assert trade.trade_date == EARLIER and trade.confirm_date == LAST


class TestTradeUnconfirmRejects:
    def test_unconfirm_recompute_is_rejected(self, client, admin_headers, test_db):
        """unconfirm(:1856) 重算期望 confirm_date → 422；抛出先于 :1859 的 sync_transfer_group。"""
        code = create_portfolio(test_db, "TRD_EXH_UC", status="active").code
        _fund(test_db)
        trade = create_trade(
            test_db, code, "TRD_EXH.OF", "CN_OTC", trade_type="buy",
            amount=1000.0, actual_amount=1000.0, price=2.0,
            trade_date=LAST, confirm_date=LAST, status="confirmed",
        )
        make_last_open_day(test_db, LAST)

        resp = client.post(
            f"/api/trades/{trade.id}/unconfirm", headers=admin_headers
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"
        # 未进入 sync_transfer_group（配对 CASH 腿不会被连带改状态）
        test_db.rollback()   # 模拟生产 get_db 请求结束 close 未提交会话
        test_db.refresh(trade)
        assert trade.status == "confirmed"


class TestSyncTransferGroupRaisesBeforeFlush:
    def test_legacy_non_cash_pair_leg_propagates_and_rolls_back_savepoint(self, test_db):
        """:521 遗留形态（非 rebal 组 + 组内配对腿非 CASH）：抛出发生在 :530 的
        `db.flush()` 之前，且 :456 的 #37 连接级 savepoint 在 `except Exception` 里
        rollback + re-raise → 先前那行 `paired_trade.status = target_status` 不落库。
        只断言「库里没写进去」这一条：该函数自身明确不承诺失败后 session 继续可用。
        """
        code = create_portfolio(test_db, "TRD_EXH_GRP", status="active").code
        _fund(test_db, code="TRDA.OF")
        _fund(test_db, code="TRDB.OF")
        group = "xfer_legacy_pair"
        # 驱动腿的 trade_date 会先被同步给配对腿（:502），故它本身就得是最后开市日
        leg_a = create_trade(
            test_db, code, "TRDA.OF", "CN_OTC", trade_type="buy",
            amount=1000.0, actual_amount=1000.0, price=2.0,
            trade_date=LAST, confirm_date=LAST, status="confirmed",
            transfer_group=group,
        )
        leg_b = create_trade(
            test_db, code, "TRDB.OF", "CN_OTC", trade_type="buy",
            amount=1000.0, actual_amount=1000.0, price=2.0,
            trade_date=LAST, confirm_date=LAST, status="confirmed",
            transfer_group=group,
        )
        make_last_open_day(test_db, LAST)

        with pytest.raises(BusinessError) as ei:
            sync_transfer_group(test_db, leg_a, "pending")
        assert ei.value.code == "CALENDAR_NOT_SYNCED"
        # 断言走 refresh（绕开 session 缓存直读库），因为 savepoint 回滚只保证「库里没写」，
        # 内存里的 ORM 属性可能仍是脏的。
        test_db.flush()
        test_db.refresh(leg_b)
        assert leg_b.status == "confirmed"
        test_db.refresh(leg_a)
        assert leg_a.status == "confirmed"
