# ============================================================================
# 集成测试：issue #405 审计日志写入点 (test_audit_log.py)
# ============================================================================
# 覆盖验收断言：
# 1. 六类高风险资源（申赎 / 调仓 / 现金转移 / 份额事件 / 快照 / 现金重估）的
#    create·update·confirm·unconfirm·cancel·delete 各留一条审计，字段与 JSON 载荷合法
# 2. 事务归属：审计不 commit，随业务事务提交或回滚（业务回滚 → 无审计行）
# 3. actor 归属：请求上下文有 actor 记 actor；后台执行体（auto_confirm）落 SYSTEM 哨兵
# 4. router-inline 提取 service 后 REST 契约不变（detail.{error,message} + 422）
# 5. 未预期异常 → system_error_log（handler 经 scope 恢复 actor / IP）
# 6. 载荷口径：中文不转义、update 只含变化字段
# 7. issue #422：recalculate 收尾埋点的 flush 失败被守护（仍 200 + results[].errors
#    带真根因，不退化成 500 RECALCULATION_FAILED）；超长 request_path 在 stdout 与
#    handler 交接处保持完整、只在写入侧按列宽截断
# 日期基于 conftest 交易日历（工作日均为交易日）
# ============================================================================

import json
import logging
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.constants.audit_actions import (
    ACTION_CANCEL,
    ACTION_CASCADE_UNCONFIRM,
    ACTION_CONFIRM,
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_GENERATE,
    ACTION_RECALCULATE,
    ACTION_UNCONFIRM,
    ACTION_UPDATE,
    RESOURCE_CASH_TRANSFER,
    RESOURCE_MANUAL_MARKET_VALUE,
    RESOURCE_SHARE_CHANGE_EVENT,
    RESOURCE_SNAPSHOT,
    RESOURCE_SUBSCRIPTION,
    RESOURCE_TRADE,
    SYSTEM_ACTOR,
)
from app.context import RequestContext, request_context_var
from app.models import PortfolioValueSnapshot
from app.models.audit_log import AuditLog
from app.models.product import Product
from app.models.share_change_event import ShareChangeEvent
from app.models.system_error_log import SystemErrorLog
from app.models.trade import Trade
from app.services import snapshot_service
from app.services.audit_service import SYSTEM_ERROR_PATH_MAX
from app.services.cash_transfer_service import (
    confirm_cash_transfer,
    create_cash_transfer,
)
from app.services.position_service import (
    delete_manual_cash_override,
    update_cash_position,
)
from app.services.share_change_event_service import (
    FUND_LEVEL_TYPES,
    cancel_share_change_event,
    confirm_share_change_event,
    create_share_change_event as create_event_service,
    delete_share_change_event,
    unconfirm_share_change_event,
    update_share_change_event,
)
from app.services.snapshot_service import (
    _delete_existing_snapshots,
    auto_confirm_after_snapshot,
    generate_daily_snapshots,
    recalculate_snapshots,
)
from app.services.subscription_service import (
    cancel_subscription,
    confirm_single_subscription,
    create_subscription,
    delete_subscription,
    unconfirm_single_subscription,
    update_subscription,
)
from app.services.trade_service import (
    cancel_trade,
    confirm_single_trade,
    create_trade as create_trade_service,
    delete_trade,
    unconfirm_trade,
    update_trade,
)
from tests.factories import (
    create_investor,
    create_platform,
    create_portfolio,
    create_subscription as factory_subscription,
)
from tests.integration.test_snapshot_forced_adjustment import (
    D0,
    EX_DAY,
    FUND,
    _setup,
    _setup_real_history,
)
from tests.unit.test_audit_service import error_log_db  # noqa: F401  pytest 夹具复用

D1 = EX_DAY                 # 2025-06-09 周一：操作日（> 基线快照日 D0）
D2 = date(2025, 6, 10)      # 周二：跨天转移到账日 / 事件除息日
PLAT_B = "AUDIT_PLAT_B"


@contextmanager
def _as(actor="ADMIN", ip="10.0.0.9"):
    """绑定请求上下文，模拟「谁在操作」——service 级用例的 actor 来源。"""
    token = request_context_var.set(
        RequestContext(request_id="req-it", actor=actor, client_ip=ip)
    )
    try:
        yield
    finally:
        request_context_var.reset(token)


def _rows(db, **filters):
    query = db.query(AuditLog)
    for field, value in filters.items():
        query = query.filter(getattr(AuditLog, field) == value)
    return query.order_by(AuditLog.id).all()


def _one(db, **filters):
    rows = _rows(db, **filters)
    assert len(rows) == 1, f"期望恰好 1 条 {filters}，实得 {len(rows)}: {rows}"
    return rows[0]


def _payload(row):
    """审计载荷必须是合法 JSON（None 表示该侧无值）。"""
    return (
        json.loads(row.old_value) if row.old_value is not None else None,
        json.loads(row.new_value) if row.new_value is not None else None,
    )


def _seed_bare(db, port, investor="AUDIT_INV"):
    """无快照基线：active 组合 + 投资人（申赎首窗按 1.0000 计价）。"""
    create_portfolio(db, code=port, status="active")
    create_investor(db, code=investor)
    return investor


def _product(db, code=FUND, market="CN_OTC"):
    return db.query(Product).filter(
        Product.code == code, Product.market == market
    ).first()


class TestSubscriptionAudit:
    """申赎：create / update / confirm / unconfirm / cancel / delete"""

    def test_create_and_update(self, test_db):
        port = "AUD_SUB_CU"
        inv = _seed_bare(test_db, port)
        with _as():
            sub = create_subscription(
                test_db, portfolio_code=port, investor_code=inv,
                platform_code="MYCF", sub_type="subscribe",
                apply_date=D1, amount=Decimal("10000.00"),
                notes="首笔申购",
            )
            test_db.flush()
            created = _one(test_db, action=ACTION_CREATE,
                           resource_type=RESOURCE_SUBSCRIPTION,
                           resource_id=str(sub.id))
            assert created.investor_code == "ADMIN"
            assert created.ip_address == "10.0.0.9"
            old, new = _payload(created)
            assert old is None, "create 无旧值"
            assert new["sub_type"] == "subscribe"
            assert new["amount"] == "10000.00"
            assert new["platform_code"] == "MYCF"

            update_subscription(test_db, sub, {"notes": "改备注"})
            test_db.flush()
            updated = _one(test_db, action=ACTION_UPDATE,
                           resource_type=RESOURCE_SUBSCRIPTION,
                           resource_id=str(sub.id))
        old, new = _payload(updated)
        assert old == {"notes": "首笔申购"}, "diff-only：只含变化字段"
        assert new == {"notes": "改备注"}

    def test_confirm_and_unconfirm(self, test_db):
        port = "AUD_SUB_CF"
        inv = _seed_bare(test_db, port)
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=10000.0, apply_date=D1, status="pending",
        )
        with _as():
            confirm_single_subscription(test_db, sub)
            test_db.flush()
            confirmed = _one(test_db, action=ACTION_CONFIRM,
                             resource_type=RESOURCE_SUBSCRIPTION,
                             resource_id=str(sub.id))
            old, new = _payload(confirmed)
            assert old == {"status": "pending"}
            assert new["status"] == "confirmed"
            assert new["unit_price"] == "1.0000", "首窗净值 1.0000（#179）"
            # 副作用（配对 CASH 腿 / 组合激活）记进同一条，不另开审计
            assert new["cash_transfer_group"] == f"sub_{sub.id}"

            unconfirm_single_subscription(test_db, sub)
            test_db.flush()
            unconfirmed = _one(test_db, action=ACTION_UNCONFIRM,
                               resource_type=RESOURCE_SUBSCRIPTION,
                               resource_id=str(sub.id))
        old, new = _payload(unconfirmed)
        assert old["status"] == "confirmed"
        assert new["status"] == "pending"

    def test_cancel_and_delete(self, test_db):
        port = "AUD_SUB_CD"
        inv = _seed_bare(test_db, port)
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=5000.0, apply_date=D1, status="pending",
        )
        with _as():
            cancel_subscription(test_db, sub)
            test_db.flush()
            cancelled = _one(test_db, action=ACTION_CANCEL,
                             resource_type=RESOURCE_SUBSCRIPTION,
                             resource_id=str(sub.id))
            assert _payload(cancelled) == ({"status": "pending"},
                                           {"status": "cancelled"})

            delete_subscription(test_db, sub)
            test_db.flush()
            deleted = _one(test_db, action=ACTION_DELETE,
                           resource_type=RESOURCE_SUBSCRIPTION,
                           resource_id=str(sub.id))
        old, new = _payload(deleted)
        assert new is None, "delete 无新值"
        assert old["status"] == "cancelled"
        assert old["sub_type"] == "subscribe"


class TestSubscriptionRouterContract:
    """提取 service 后 REST 契约不变：状态码与 detail.{error,message}"""

    def test_cancel_pending_writes_audit(self, client, admin_headers, test_db):
        port = "AUD_SUB_RC"
        inv = _seed_bare(test_db, port)
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=5000.0, apply_date=D1, status="pending",
        )
        resp = client.post(f"/api/subscriptions/{sub.id}/cancel",
                           headers=admin_headers)
        assert resp.status_code == 200
        row = _one(test_db, action=ACTION_CANCEL,
                   resource_type=RESOURCE_SUBSCRIPTION, resource_id=str(sub.id))
        assert row.investor_code == "ADMIN", "actor 由 get_current_user 落到上下文"

    def test_cancel_confirmed_keeps_422_contract(self, client, admin_headers, test_db):
        port = "AUD_SUB_R422"
        inv = _seed_bare(test_db, port)
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=5000.0, apply_date=D1, status="confirmed",
        )
        resp = client.post(f"/api/subscriptions/{sub.id}/cancel",
                           headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_STATUS"
        assert _rows(test_db, action=ACTION_CANCEL) == [], "拒绝的操作不留审计"

    def test_delete_confirmed_keeps_422_contract(self, client, admin_headers, test_db):
        port = "AUD_SUB_RD"
        inv = _seed_bare(test_db, port)
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=5000.0, apply_date=D1, status="confirmed",
        )
        resp = client.delete(f"/api/subscriptions/{sub.id}", headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CANNOT_DELETE_CONFIRMED"

    def test_delete_pending_writes_audit(self, client, admin_headers, test_db):
        port = "AUD_SUB_RDP"
        inv = _seed_bare(test_db, port)
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=5000.0, apply_date=D1, status="pending",
        )
        resp = client.delete(f"/api/subscriptions/{sub.id}", headers=admin_headers)
        assert resp.status_code == 200
        _one(test_db, action=ACTION_DELETE,
             resource_type=RESOURCE_SUBSCRIPTION, resource_id=str(sub.id))


class TestTradeAudit:
    """调仓：create / update / confirm / unconfirm / cancel / delete（含配对腿级联）"""

    def test_create_records_transfer_group(self, test_db):
        port = "AUD_TRD_C"
        _setup(test_db, port)
        with _as():
            trade = create_trade_service(
                test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
                trade_type="buy", trade_date=D1,
                actual_amount=Decimal("300.00"), platform_code="MYCF",
            )
            test_db.flush()
            row = _one(test_db, action=ACTION_CREATE,
                       resource_type=RESOURCE_TRADE, resource_id=str(trade.id))
        _, new = _payload(row)
        assert new["product_code"] == FUND
        assert new["transfer_group"] == trade.transfer_group

    def test_update_is_diff_only(self, test_db):
        port = "AUD_TRD_U"
        _setup(test_db, port)
        trade = create_trade_service(
            test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
            trade_type="buy", trade_date=D1,
            actual_amount=Decimal("300.00"), platform_code="MYCF",
        )
        test_db.flush()
        with _as():
            update_trade(test_db, trade, {"notes": "调仓备注"})
            test_db.flush()
            row = _one(test_db, action=ACTION_UPDATE,
                       resource_type=RESOURCE_TRADE, resource_id=str(trade.id))
        old, new = _payload(row)
        assert old == {"notes": None}
        assert new == {"notes": "调仓备注"}

    def test_update_without_change_leaves_no_trace(self, test_db):
        """空更新不留痕：diff 为空则不调 record_audit，否则落一条 old/new 皆 NULL 的行。

        update_trade 自建 diff（不走 `_diff_fields`），故这道闸需单独锁；
        其数值入参已在函数开头经 `_dec()` 归一为 Decimal，Decimal-vs-float
        的跨类型比较在 `tests/unit/test_audit_service.py` 锁。
        """
        port = "AUD_TRD_NOOP"
        _setup(test_db, port)
        trade = create_trade_service(
            test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
            trade_type="buy", trade_date=D1,
            actual_amount=Decimal("300.00"), platform_code="MYCF",
        )
        test_db.flush()
        with _as():
            update_trade(test_db, trade, {"notes": "调仓备注"})
            test_db.flush()
            update_trade(test_db, trade, {"notes": "调仓备注"})
            test_db.flush()
            rows = _rows(test_db, action=ACTION_UPDATE,
                         resource_type=RESOURCE_TRADE, resource_id=str(trade.id))
        assert len(rows) == 1, "首次改 notes 留一条，原样重提交不再留痕"

    def test_confirm_and_unconfirm(self, test_db):
        port = "AUD_TRD_CF"
        _setup(test_db, port)
        trade = create_trade_service(
            test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
            trade_type="buy", trade_date=D1,
            actual_amount=Decimal("300.00"), platform_code="MYCF",
        )
        test_db.flush()
        product = _product(test_db)
        with _as():
            confirm_single_trade(test_db, trade, product)
            test_db.flush()
            confirmed = _one(test_db, action=ACTION_CONFIRM,
                             resource_type=RESOURCE_TRADE, resource_id=str(trade.id))
            _, new = _payload(confirmed)
            assert new["status"] == "confirmed"
            assert new["price"] == "1.0000", "场外确认取 T 日净值"

            unconfirm_trade(test_db, trade)
            test_db.flush()
            unconfirmed = _one(test_db, action=ACTION_UNCONFIRM,
                               resource_type=RESOURCE_TRADE, resource_id=str(trade.id))
        old, new = _payload(unconfirmed)
        assert old == {"status": "confirmed"}
        assert new["status"] == "pending"

    def test_cancel(self, test_db):
        port = "AUD_TRD_CA"
        _setup(test_db, port)
        trade = create_trade_service(
            test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
            trade_type="buy", trade_date=D1,
            actual_amount=Decimal("300.00"), platform_code="MYCF",
        )
        test_db.flush()
        with _as():
            cancel_trade(test_db, trade)
            test_db.flush()
            row = _one(test_db, action=ACTION_CANCEL,
                       resource_type=RESOURCE_TRADE, resource_id=str(trade.id))
        assert _payload(row) == ({"status": "pending"}, {"status": "cancelled"})

    def test_delete_cascades_paired_cash_leg(self, client, admin_headers, test_db):
        port = "AUD_TRD_D"
        _setup(test_db, port)
        trade = create_trade_service(
            test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
            trade_type="buy", trade_date=D1,
            actual_amount=Decimal("300.00"), platform_code="MYCF",
        )
        test_db.flush()
        group = trade.transfer_group
        resp = client.delete(f"/api/trades/{trade.id}", headers=admin_headers)
        assert resp.status_code == 200
        row = _one(test_db, action=ACTION_DELETE,
                   resource_type=RESOURCE_TRADE, resource_id=str(trade.id))
        old, new = _payload(row)
        assert new is None
        assert old["transfer_group"] == group
        assert test_db.query(Trade).filter(
            Trade.transfer_group == group
        ).count() == 0, "基金腿删除级联删配对 CASH 腿"

    def test_delete_confirmed_keeps_422_contract(self, client, admin_headers, test_db):
        port = "AUD_TRD_D422"
        _setup(test_db, port)
        trade = create_trade_service(
            test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
            trade_type="buy", trade_date=D1,
            actual_amount=Decimal("300.00"), platform_code="MYCF",
        )
        test_db.flush()
        confirm_single_trade(test_db, trade, _product(test_db))
        test_db.commit()
        resp = client.delete(f"/api/trades/{trade.id}", headers=admin_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CANNOT_DELETE_CONFIRMED"
        assert _rows(test_db, action=ACTION_DELETE,
                     resource_type=RESOURCE_TRADE) == []


class TestCashTransferAudit:
    """跨平台现金转移：resource_id 是 transfer_group（两腿共享，无单一主键）"""

    def _transfer(self, db, port, cross_day=True):
        _setup(db, port)
        create_platform(db, code=PLAT_B)
        return create_cash_transfer(
            db, portfolio_code=port, from_platform="MYCF", to_platform=PLAT_B,
            amount=Decimal("200.00"), transfer_date=D1, cross_day=cross_day,
        )

    def test_create_records_both_legs_status(self, test_db):
        port = "AUD_CT_C"
        with _as():
            result = self._transfer(test_db, port, cross_day=True)
            test_db.flush()
            row = _one(test_db, action=ACTION_CREATE,
                       resource_type=RESOURCE_CASH_TRANSFER,
                       resource_id=result["transfer_group"])
        _, new = _payload(row)
        assert new["from_platform"] == "MYCF"
        assert new["to_platform"] == PLAT_B
        assert new["cross_day"] is True
        assert (new["sell_status"], new["buy_status"]) == ("confirmed", "pending")

    def test_confirm_pending_leg(self, test_db):
        port = "AUD_CT_CF"
        with _as():
            result = self._transfer(test_db, port, cross_day=True)
            test_db.flush()
            confirmed = confirm_cash_transfer(
                test_db, portfolio_code=port,
                transfer_group=result["transfer_group"],
            )
            test_db.flush()
            row = _one(test_db, action=ACTION_CONFIRM,
                       resource_type=RESOURCE_CASH_TRANSFER,
                       resource_id=confirmed["transfer_group"])
        old, new = _payload(row)
        assert old == {"pending_count": 1}
        assert new == {"confirmed_count": 1, "confirm_date": "2025-06-10"}


class TestShareChangeEventAudit:
    """份额变动事件：create / update / confirm / unconfirm / cancel / delete"""

    def _event(self, db, port, event_type="cash_dividend", **kwargs):
        # 基金级事件（拆合/送股）不挂平台，平台级事件必传 platform_code
        platform_code = None if event_type in FUND_LEVEL_TYPES else "MYCF"
        defaults = {"div_cash": Decimal("0.5")} if event_type == "cash_dividend" else {}
        return create_event_service(
            db, portfolio_code=port, event_type=event_type,
            product_code=FUND, market="CN_OTC", platform_code=platform_code,
            ex_date=D2, entitlement_date=D0,
            **defaults, **kwargs,
        )

    def test_create_and_update(self, test_db):
        port = "AUD_EVT_CU"
        _setup(test_db, port)
        with _as():
            event = self._event(test_db, port, notes="分红")
            test_db.flush()
            created = _one(test_db, action=ACTION_CREATE,
                           resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                           resource_id=str(event.id))
            _, new = _payload(created)
            assert new["event_type"] == "cash_dividend"
            assert new["entitlement_date"] == "2025-06-06"

            update_share_change_event(test_db, event, {"notes": "改分红备注"})
            test_db.flush()
            updated = _one(test_db, action=ACTION_UPDATE,
                           resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                           resource_id=str(event.id))
        old, new = _payload(updated)
        assert old == {"notes": "分红"}
        assert new == {"notes": "改分红备注"}

    def test_update_resubmitting_same_numeric_value_leaves_no_trace(self, test_db):
        """PUT 整对象是编辑表单的常态：数值字段原样重提交不得产出审计行。

        上面那个用例只改 `notes`（字符串），故掩盖了数值字段的幻影 diff。
        `ShareChangeEventUpdate` 的数值字段是 `Optional[float]`，这里传
        `float(event.div_cash)` 复现 router 交给 service 的真实形态。
        SQLAlchemy 的 Numeric 在 SQLite 同样回 Decimal（实测），故本地即可复现。
        同时锁住「无实际变更不留痕」：diff 为空则不调 `record_audit`，
        否则会落一条 old/new 皆 NULL 的空载荷行。
        """
        port = "AUD_EVT_SAME"
        _setup(test_db, port)
        with _as():
            event = self._event(test_db, port)
            test_db.flush()
            update_share_change_event(
                test_db, event, {"div_cash": float(event.div_cash)}
            )
            test_db.flush()
            rows = _rows(test_db, action=ACTION_UPDATE,
                         resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                         resource_id=str(event.id))
        assert rows == [], f"原样重提交 div_cash 不应留审计痕，实得 {len(rows)} 条"

    def test_confirm_and_unconfirm(self, test_db):
        port = "AUD_EVT_CF"
        _setup(test_db, port)
        event = self._event(test_db, port)
        test_db.flush()
        with _as():
            confirm_share_change_event(test_db, event)
            test_db.flush()
            confirmed = _one(test_db, action=ACTION_CONFIRM,
                             resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                             resource_id=str(event.id))
            _, new = _payload(confirmed)
            assert new["status"] == "confirmed"
            assert new["entitlement_shares"] == "100.00"
            assert new["cash_change"] == "50.00"

            unconfirm_share_change_event(test_db, event)
            test_db.flush()
            unconfirmed = _one(test_db, action=ACTION_UNCONFIRM,
                               resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                               resource_id=str(event.id))
        assert _payload(unconfirmed) == ({"status": "confirmed"},
                                         {"status": "pending"})

    def test_cancel(self, test_db):
        port = "AUD_EVT_CA"
        _setup(test_db, port)
        event = self._event(test_db, port)
        test_db.flush()
        with _as():
            cancel_share_change_event(test_db, event)
            test_db.flush()
            row = _one(test_db, action=ACTION_CANCEL,
                       resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                       resource_id=str(event.id))
        assert _payload(row) == ({"status": "pending"}, {"status": "cancelled"})

    def test_delete_cascades_children(self, client, admin_headers, test_db):
        """基金级父事件删除：子记录一并删除，审计留父事件一条"""
        port = "AUD_EVT_D"
        _setup(test_db, port)
        event = self._event(test_db, port, event_type="share_split",
                            ratio=Decimal("2"))
        test_db.flush()
        confirm_share_change_event(test_db, event)
        test_db.flush()
        children = test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == event.id
        ).count()
        assert children >= 1, "基金级事件确认时按有持仓平台拆子记录"
        resp = client.delete(f"/api/share-change-events/{event.id}",
                             headers=admin_headers)
        assert resp.status_code == 200
        row = _one(test_db, action=ACTION_DELETE,
                   resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                   resource_id=str(event.id))
        old, new = _payload(row)
        assert new is None
        assert old["event_type"] == "share_split"
        assert test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == event.id
        ).count() == 0, "父事件删除级联删子记录"


class TestSnapshotAudit:
    """快照：generate / recalculate / delete / cascade_unconfirm"""

    def test_generate(self, test_db):
        port = "AUD_SNP_G"
        _setup(test_db, port)
        with _as():
            result = generate_daily_snapshots(test_db, port, EX_DAY)
            assert result["success"] is True, result
            test_db.flush()
            row = _one(test_db, action=ACTION_GENERATE,
                       resource_type=RESOURCE_SNAPSHOT,
                       resource_id=EX_DAY.isoformat())
        assert row.resource_name == port
        _, new = _payload(row)
        assert set(new) == {"total_value", "total_shares", "unit_price",
                            "positions", "investors"}
        # #421：构造点按列标度落 Decimal，载荷即保标度字符串（total_value /
        # unit_price 4 位、total_shares 2 位），不再是掉标度的 JSON 数字
        assert new["total_value"] == "1100.0000"
        assert new["total_shares"] == "1100.00"
        assert new["unit_price"] == "1.0000"

    def test_empty_generate_leaves_no_delete_trace(self, test_db):
        """generate 每次都先删旧快照；空删除不留痕，否则淹没审计日志"""
        port = "AUD_SNP_E"
        _setup(test_db, port)
        before = len(_rows(test_db, resource_type=RESOURCE_SNAPSHOT))
        generate_daily_snapshots(test_db, port, EX_DAY)
        test_db.flush()
        deletes = [r for r in _rows(test_db, resource_type=RESOURCE_SNAPSHOT)
                   if r.action == ACTION_DELETE]
        assert len(_rows(test_db, resource_type=RESOURCE_SNAPSHOT)) == before + 1
        assert deletes == []

    def test_delete_records_rowcounts_and_cascade(self, test_db):
        """删快照：三表行数 + 级联回退清单入 old_value，并逐条留级联痕迹"""
        port = "AUD_SNP_D"
        _setup(test_db, port)
        event = create_event_service(
            test_db, portfolio_code=port, event_type="cash_dividend",
            product_code=FUND, market="CN_OTC", platform_code="MYCF",
            ex_date=EX_DAY, entitlement_date=D0, div_cash=Decimal("0.5"),
        )
        test_db.flush()
        confirm_share_change_event(test_db, event)
        test_db.flush()

        with _as():
            _delete_existing_snapshots(test_db, port, D0)
            test_db.flush()

        deleted = _one(test_db, action=ACTION_DELETE,
                       resource_type=RESOURCE_SNAPSHOT, resource_id=D0.isoformat())
        old, new = _payload(deleted)
        assert new is None
        assert old["deleted"]["portfolio_position"] == 2
        assert old["deleted"]["portfolio_value_snapshot"] == 1
        assert old["deleted"]["investor_holding"] == 1
        assert old["cascaded_events"] == [event.id]

        cascaded = _one(test_db, action=ACTION_CASCADE_UNCONFIRM,
                        resource_type=RESOURCE_SHARE_CHANGE_EVENT,
                        resource_id=str(event.id))
        _, cascaded_new = _payload(cascaded)
        assert cascaded_new["status"] == "pending"
        assert cascaded_new["reason"] == "snapshot_deleted"
        assert cascaded_new["snapshot_date"] == "2025-06-06"

    def test_recalculate_records_summary(self, test_db):
        port = "AUD_SNP_R"
        inv = "AUD_SNP_R_INV"
        _setup_real_history(test_db, port, inv)
        # 级联回退只命中 apply_date == 快照日 的申赎，补一笔以覆盖该路径
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=200.0, apply_date=D0,
        )
        test_db.flush()
        confirm_single_subscription(test_db, sub)
        test_db.commit()

        with _as():
            result = recalculate_snapshots(test_db, port, D0, EX_DAY)
            test_db.flush()

        assert result["success"] is True, result
        row = _one(test_db, action=ACTION_RECALCULATE,
                   resource_type=RESOURCE_SNAPSHOT,
                   resource_id=f"{D0.isoformat()}..{EX_DAY.isoformat()}")
        assert row.resource_name == port
        old, new = _payload(row)
        assert old == {"start_date": "2025-06-06", "end_date": "2025-06-09"}
        summary = new["portfolios"][0]
        assert summary["portfolio_code"] == port
        assert summary["processed_dates"] == [D0.isoformat(), EX_DAY.isoformat()]
        assert summary["cascaded_unconfirmed"] >= 1
        # 级联回退的申购由 unconfirm_single_subscription 逐条留痕，不重复记 cascade
        assert _rows(test_db, action=ACTION_CASCADE_UNCONFIRM,
                     resource_type=RESOURCE_SUBSCRIPTION) == []
        assert _rows(test_db, action=ACTION_UNCONFIRM,
                     resource_type=RESOURCE_SUBSCRIPTION,
                     resource_id=str(sub.id))


class TestRecalculateAuditFlushGuard:
    """#422a：收尾埋点的 flush 失败不得把 router 承诺的 200+errors 变成 500

    逐日 except 刻意不 rollback（回滚交 router），故某日在 `db.add` 之后、`db.flush()`
    之前失败时，半截 ORM 对象留在 pending 态；收尾 `record_audit` 的 flush 因此要做
    真实工作，撞约束即抛。此前该异常逃出 service → router 的 `except Exception` →
    500 RECALCULATION_FAILED，前端/CLI 拿不到可展示的逐日错误清单。
    """

    def test_mid_day_failure_returns_200_with_root_cause(
        self, client, admin_headers, test_db, error_log_db, monkeypatch
    ):
        port = "AUD_SNP_FG"
        inv = "AUD_SNP_FG_INV"
        _setup_real_history(test_db, port, inv)
        test_db.commit()

        real_value_snapshot = snapshot_service._generate_portfolio_value_snapshot

        def value_snapshot_violating_not_null(*args, **kwargs):
            snap = real_value_snapshot(*args, **kwargs)
            # portfolio_code 是 NOT NULL 列：对象随当日失败留在 pending 态，
            # 等收尾埋点那次 flush 撞约束（issue 的可达路径②，与 #419 毒化无关）
            snap.portfolio_code = None
            return snap

        monkeypatch.setattr(
            snapshot_service, "_generate_portfolio_value_snapshot",
            value_snapshot_violating_not_null,
        )
        monkeypatch.setattr(
            snapshot_service, "_generate_investor_holding",
            MagicMock(side_effect=RuntimeError("投资人快照生成炸了")),
        )

        resp = client.post(
            "/api/snapshots/recalculate",
            headers=admin_headers,
            json={
                "portfolio_code": port,
                "start_date": D0.isoformat(),
                "end_date": EX_DAY.isoformat(),
            },
        )

        assert resp.status_code == 200, resp.text
        errors = resp.json()["results"][0]["errors"]
        assert errors, "逐日失败必须进 results[].errors"
        assert errors[0]["date"] == D0.isoformat()
        assert errors[0]["code"] == "RuntimeError", (
            "errors 携带当日真实根因，而非 500 的笼统 RECALCULATION_FAILED"
        )
        assert "投资人快照生成炸了" in errors[0]["error"]

        # 审计侧仍有痕：flush 被守护后响亮记录 + 落一条 system_error_log
        with error_log_db() as session:
            error = session.query(SystemErrorLog).one()
        assert error.error_type == "AuditWriteFailure"
        assert "portfolio_code" in error.error_message

        # 整体回滚：基线快照复原、无半截快照落库（§2.6「要么完整成功、要么无变化」）
        rows = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port
        ).all()
        assert [r.snapshot_date for r in rows] == [D0], rows


class TestManualCashOverrideAudit:
    """现金重估：manual_market_value 的 upsert 两分支 + 删除"""

    def test_create_then_update_then_delete(self, test_db):
        port = "AUD_MMV"
        _setup(test_db, port)
        with _as():
            update_cash_position(test_db, portfolio_code=port,
                                 platform_code="MYCF", amount=Decimal("888.88"),
                                 update_date=D1, created_by="ADMIN")
            test_db.flush()
            created = _one(test_db, action=ACTION_CREATE,
                           resource_type=RESOURCE_MANUAL_MARKET_VALUE)
            old, new = _payload(created)
            assert old is None
            assert new["value_date"] == "2025-06-09"
            override_id = created.resource_id

            update_cash_position(test_db, portfolio_code=port,
                                 platform_code="MYCF", amount=Decimal("999.99"),
                                 update_date=D1, created_by="ADMIN")
            test_db.flush()
            updated = _one(test_db, action=ACTION_UPDATE,
                           resource_type=RESOURCE_MANUAL_MARKET_VALUE,
                           resource_id=override_id)
            old, new = _payload(updated)
            # 按 Decimal 比：列是 Numeric(20,4)，旧值从库里读回带 4 位（888.8800），
            # 新值仍在会话中带输入刻度（999.99），字符串相等会把刻度差误判为值差
            assert Decimal(old["market_value"]) == Decimal("888.88"), "upsert 覆盖须留旧值"
            assert Decimal(new["market_value"]) == Decimal("999.99")

            delete_manual_cash_override(test_db, portfolio_code=port,
                                        platform_code="MYCF", value_date=D1)
            test_db.flush()
            deleted = _one(test_db, action=ACTION_DELETE,
                           resource_type=RESOURCE_MANUAL_MARKET_VALUE,
                           resource_id=override_id)
        old, new = _payload(deleted)
        assert new is None
        assert Decimal(old["market_value"]) == Decimal("999.99")
        assert old["created_by"] == "ADMIN"


class TestTransactionOwnership:
    """审计不 commit：随业务事务提交或回滚（§1.1 service 约定）"""

    def test_rollback_discards_audit(self, test_db):
        port = "AUD_TX_RB"
        inv = _seed_bare(test_db, port)
        with _as():
            create_subscription(
                test_db, portfolio_code=port, investor_code=inv,
                platform_code="MYCF", sub_type="subscribe",
                apply_date=D1, amount=Decimal("10000.00"),
            )
            test_db.flush()
            assert _rows(test_db, action=ACTION_CREATE) != []
        test_db.rollback()
        assert _rows(test_db) == [], "业务回滚 → 审计行一并回滚"

    def test_commit_persists_audit(self, test_db):
        port = "AUD_TX_CM"
        inv = _seed_bare(test_db, port)
        with _as():
            create_subscription(
                test_db, portfolio_code=port, investor_code=inv,
                platform_code="MYCF", sub_type="subscribe",
                apply_date=D1, amount=Decimal("10000.00"),
            )
        test_db.commit()
        assert len(_rows(test_db, action=ACTION_CREATE,
                         resource_type=RESOURCE_SUBSCRIPTION)) == 1


class TestActorAttribution:
    """后台执行体无请求主体 → SYSTEM 哨兵"""

    def test_auto_confirm_records_system_actor(self, test_db):
        port = "AUD_ACT_SYS"
        _setup(test_db, port)
        inv = "AUD_ACT_INV"
        create_investor(test_db, code=inv)
        sub = factory_subscription(
            test_db, portfolio_code=port, investor_code=inv,
            platform_code="MYCF", sub_type="subscribe",
            amount=200.0, apply_date=D0, status="pending",
        )
        test_db.flush()
        # 刻意不绑上下文：调度器 / 线程池路径没有请求主体
        auto_confirm_after_snapshot(test_db, port, D0)
        test_db.flush()
        row = _one(test_db, action=ACTION_CONFIRM,
                   resource_type=RESOURCE_SUBSCRIPTION, resource_id=str(sub.id))
        assert row.investor_code == SYSTEM_ACTOR
        assert row.ip_address is None

    def test_rest_actor_comes_from_token(self, client, admin_headers, test_db):
        port = "AUD_ACT_REST"
        inv = _seed_bare(test_db, port)
        resp = client.post(
            "/api/subscriptions",
            json={"portfolio_code": port, "investor_code": inv,
                  "sub_type": "subscribe", "amount": 10000.0,
                  "apply_date": D1.isoformat(), "platform_code": "MYCF"},
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        row = _one(test_db, action=ACTION_CREATE,
                   resource_type=RESOURCE_SUBSCRIPTION)
        assert row.investor_code == "ADMIN"
        assert row.ip_address, "访问日志与审计同源取 IP"


class TestPayloadConventions:
    def test_chinese_not_escaped(self, test_db):
        port = "AUD_PL_CN"
        _setup(test_db, port)
        trade = create_trade_service(
            test_db, portfolio_code=port, product_code=FUND, market="CN_OTC",
            trade_type="buy", trade_date=D1,
            actual_amount=Decimal("300.00"), platform_code="MYCF",
        )
        test_db.flush()
        with _as():
            update_trade(test_db, trade, {"notes": "建仓：沪深300"})
            test_db.flush()
        row = _one(test_db, action=ACTION_UPDATE, resource_type=RESOURCE_TRADE,
                   resource_id=str(trade.id))
        assert "建仓：沪深300" in row.new_value
        assert "\\u" not in row.new_value, "ensure_ascii=False：中文原样落库"
        assert json.loads(row.new_value)["notes"] == "建仓：沪深300"

    def test_non_json_types_stringified(self, test_db):
        """Decimal / date 不可 JSON 序列化，default=str 统一字符串化"""
        port = "AUD_PL_JSON"
        _setup(test_db, port)
        create_platform(test_db, code=PLAT_B)
        with _as():
            create_cash_transfer(
                test_db, portfolio_code=port, from_platform="MYCF",
                to_platform=PLAT_B, amount=Decimal("0.01"),
                transfer_date=D1, cross_day=False,
            )
            test_db.flush()
        row = _one(test_db, action=ACTION_CREATE,
                   resource_type=RESOURCE_CASH_TRANSFER)
        new = json.loads(row.new_value)
        assert new["transfer_date"] == "2025-06-09"
        assert new["amount"] == "0.01", "Decimal 字符串化后 2 位小数不变形（非 float 0.01）"


class TestSystemErrorLogOnUnhandledException:
    """未预期异常：handler 落 system_error_log，actor / IP 从 scope 恢复"""

    def test_handler_writes_error_log(self, client, admin_headers, monkeypatch):
        # 真实写入会另开连接，与 conftest 的 SQLite 写锁冲突；此处只验 handler 传参
        spy = MagicMock()
        monkeypatch.setattr("app.main.record_system_error", spy)
        monkeypatch.setattr(
            "app.routers.subscriptions.list_subscriptions",
            MagicMock(side_effect=RuntimeError("炸了")),
        )
        with pytest.raises(RuntimeError):
            client.get("/api/subscriptions", headers=admin_headers)

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["error_type"] == "RuntimeError"
        assert kwargs["error_message"] == "炸了"
        assert "RuntimeError: 炸了" in kwargs["error_stack"]
        assert kwargs["request_path"] == "/api/subscriptions"
        assert kwargs["request_method"] == "GET"
        # contextvar 在中间件 finally 已解绑，只能靠 get_current_user 暂存的 scope
        assert kwargs["investor_code"] == "ADMIN"
        assert kwargs["ip_address"]

    # #422c：路径长度无界（h11 请求行上限 16 KB），而列宽是 String(200)
    LONG_CODE = "P" * 300

    def _trigger_with_overlong_path(self, client, admin_headers, monkeypatch):
        """用带路径参数的端点构造 >200 字符的 path，并让它抛未预期异常"""
        monkeypatch.setattr(
            "app.services.performance_service.get_performance",
            MagicMock(side_effect=RuntimeError("绩效算炸了")),
        )
        path = f"/api/portfolios/{self.LONG_CODE}/performance"
        assert len(path) > SYSTEM_ERROR_PATH_MAX
        with pytest.raises(RuntimeError):
            client.get(path, headers=admin_headers)
        return path

    def test_overlong_path_kept_whole_in_stdout_and_handoff(
        self, client, admin_headers, monkeypatch, caplog
    ):
        """截断只发生在写入侧：stdout 与交给 record_system_error 的都仍是完整 path"""
        spy = MagicMock()
        monkeypatch.setattr("app.main.record_system_error", spy)

        with caplog.at_level(logging.ERROR, logger="app.main"):
            path = self._trigger_with_overlong_path(client, admin_headers, monkeypatch)

        logged = [r for r in caplog.records if r.getMessage() == "未预期异常"]
        assert logged, [r.getMessage() for r in caplog.records]
        assert getattr(logged[0], "path", None) == path, "stdout 必须留完整 path"
        assert spy.call_args.kwargs["request_path"] == path

    def test_overlong_path_still_lands_one_row(
        self, client, admin_headers, monkeypatch, error_log_db
    ):
        """真实写入路径：超长 path 仍落一行（不截断则 MySQL 严格模式下整行丢失）"""
        path = self._trigger_with_overlong_path(client, admin_headers, monkeypatch)

        with error_log_db() as session:
            error = session.query(SystemErrorLog).one()
        assert error.request_path == path[:SYSTEM_ERROR_PATH_MAX]
        assert len(error.request_path) <= SYSTEM_ERROR_PATH_MAX
        assert error.error_type == "RuntimeError"
        assert error.error_message == "绩效算炸了"
        assert error.error_stack
        assert error.request_method == "GET"
        assert error.investor_code == "ADMIN"

    # #427：异常文案里的 4 字节字符（emoji / CJK 扩展 B）在 utf8mb3 库上撞 errno 1366，
    # 被 record_system_error 的 best-effort except 吸收后整条记录静默消失。
    # 本用例走**真实**落库链路（不 mock record_system_error），端到端锁死该缺口；
    # 字符集只在 MySQL 成立，故 SQLite job 跳过、CI backend-test-mysql 才真验收。
    FOUR_BYTE_MESSAGE = "绩效算炸了 💥 扩展 𠀋"

    def test_four_byte_message_still_lands_one_row(
        self, client, admin_headers, monkeypatch, error_log_db
    ):
        from app.database import engine

        if engine.dialect.name != "mysql":
            pytest.skip("字符集只在 MySQL 成立（SQLite 无字符集概念）")

        monkeypatch.setattr(
            "app.services.performance_service.get_performance",
            MagicMock(side_effect=RuntimeError(self.FOUR_BYTE_MESSAGE)),
        )
        path = f"/api/portfolios/{self.LONG_CODE[:8]}/performance"
        with pytest.raises(RuntimeError):
            client.get(path, headers=admin_headers)

        with error_log_db() as session:
            error = session.query(SystemErrorLog).one()
        assert error.error_type == "RuntimeError"
        assert error.error_message == self.FOUR_BYTE_MESSAGE
        assert "💥" in error.error_stack
        assert error.request_path == path
        assert error.investor_code == "ADMIN"
