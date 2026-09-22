# ============================================================================
# 集成测试：申赎写路径在交易日历耗尽时的拒绝与零残留（issue #591 裸消费点）
# ============================================================================
# 覆盖 subscription_service 的 4 个 get_next_trading_day 消费点：
#   create(:463) / update(:593) / confirm 与 confirm preview(:107 共用单点) /
#   unconfirm(:377)，以及 :377 的第二个调用方——快照级联回退
#   （snapshot_service._cascade_unconfirm_subscriptions）。
# 生产代码在本次无需改动：helper 抛 CALENDAR_NOT_SYNCED 后异常自然传播 →
# 全局 handler 出 422、请求事务未 commit 即随 get_db 关会话回滚。本文件把它钉成
# 回归测试（验收断言 1）。
# ============================================================================
from datetime import date

import pytest

from app.models import PortfolioValueSnapshot, Subscription
from tests.factories import (
    create_investor_holding,
    create_portfolio,
    create_position_snapshot,
    create_subscription,
    create_value_snapshot,
)
from tests.integration.calendar_exhaustion_helpers import (
    assert_rejected_without_residual,
    business_rows,
    make_last_open_day,
)

APPLY = date(2025, 6, 6)          # 受控日历里最后一个开市日
EARLIER = date(2025, 6, 5)        # 尚有后续交易日，用于造可提交的成功对照
BEYOND = date(2025, 6, 10)        # 日历裁剪后不再存在的日期（可挂快照行）


def _sub_payload(portfolio_code, apply_date=APPLY, sub_type="subscribe"):
    return {
        "portfolio_code": portfolio_code,
        "investor_code": "VIEWER",
        "platform_code": "MYCF",
        "sub_type": sub_type,
        "apply_date": apply_date.isoformat(),
        "amount" if sub_type == "subscribe" else "shares": 10000.0
        if sub_type == "subscribe" else 100.0,
    }


def _active_portfolio(db, code):
    create_portfolio(db, code=code, status="active")
    return code


class TestSubscriptionCreateRejects:
    def test_create_is_422_with_no_residual(self, client, admin_headers, test_db):
        """验收断言 1：只保留申请日一条开市记录 → 422 CALENDAR_NOT_SYNCED、零残留"""
        code = _active_portfolio(test_db, "SUB_EXH_C")
        make_last_open_day(test_db, APPLY)

        assert_rejected_without_residual(
            client.post, "/api/subscriptions", _sub_payload(code), admin_headers,
            test_db, code, "CALENDAR_NOT_SYNCED",
        )

    def test_control_create_earlier_apply_date_succeeds(
        self, client, admin_headers, test_db
    ):
        """正向对照：同一天不是最后开市日时，T+1 解析得出、创建成功（拒绝非普遍失败）。"""
        code = _active_portfolio(test_db, "SUB_EXH_OK")
        resp = client.post(
            "/api/subscriptions",
            json=_sub_payload(code, apply_date=EARLIER),
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"


class TestSubscriptionUpdateRejects:
    def test_update_apply_date_to_last_open_day_changes_nothing(
        self, client, admin_headers, test_db
    ):
        """改期到日历末尾 → 422，且 apply_date / confirm_date 均未被改写。"""
        code = _active_portfolio(test_db, "SUB_EXH_U")
        sub = create_subscription(
            test_db, code, "VIEWER", sub_type="subscribe",
            amount=10000.0, apply_date=EARLIER, confirm_date=APPLY, status="pending",
        )
        make_last_open_day(test_db, APPLY)
        before = business_rows(test_db, code)

        resp = client.put(
            f"/api/subscriptions/{sub.id}",
            json={"apply_date": APPLY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"
        test_db.flush()
        assert business_rows(test_db, code) == before
        test_db.refresh(sub)
        assert sub.apply_date == EARLIER and sub.confirm_date == APPLY


class TestConfirmAndPreviewShareTheGate:
    """confirm(:107) 与 confirm preview 共用同一单点 → 同码同消息、都不落库。"""

    def _pending_at_last_open_day(self, db, code):
        sub = create_subscription(
            db, code, "VIEWER", sub_type="subscribe", amount=10000.0,
            apply_date=APPLY, confirm_date=APPLY, status="pending",
        )
        make_last_open_day(db, APPLY)
        return sub

    def test_preview_and_confirm_same_code_and_message(
        self, client, admin_headers, test_db
    ):
        code = _active_portfolio(test_db, "SUB_EXH_PV")
        sub = self._pending_at_last_open_day(test_db, code)

        preview = client.get(
            f"/api/subscriptions/{sub.id}/preview", headers=admin_headers
        )
        confirm = client.post(
            f"/api/subscriptions/{sub.id}/confirm", headers=admin_headers
        )

        assert preview.status_code == 422, preview.text
        assert confirm.status_code == 422, confirm.text
        assert preview.json()["detail"] == confirm.json()["detail"], (
            "预览与确认必须同码同消息（同一实现单点）"
        )
        assert preview.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"
        test_db.refresh(sub)
        assert sub.status == "pending"
        assert sub.unit_price is None and sub.shares is None

    def test_unconfirm_is_rejected_and_keeps_confirmed(
        self, client, admin_headers, test_db
    ):
        """unconfirm 重算期望 confirm_date(:377) → 日历不足即 422，状态保持 confirmed。

        刻意不建快照行：否则先被 SNAPSHOT_DEPENDENCY（已被快照纳入）挡下，走不到
        :377 的交易日重算——本用例要测的是后者。
        """
        code = _active_portfolio(test_db, "SUB_EXH_UC")
        sub = create_subscription(
            test_db, code, "VIEWER", sub_type="subscribe", amount=10000.0,
            apply_date=APPLY, confirm_date=APPLY, status="confirmed",
            unit_price=1.0, shares=10000.0,
        )
        make_last_open_day(test_db, APPLY)

        resp = client.post(
            f"/api/subscriptions/{sub.id}/unconfirm", headers=admin_headers
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"
        # unconfirm 在 :377 重算 confirm_date 前已把 status 置 pending（ORM 未 flush）。
        # 生产出口是 get_db 在请求结束 close 会话 → 未提交改动当场丢弃；测试用共享
        # test_db，须显式 rollback 模拟同一出口后再断言零落库（直接 flush 会把测试
        # 夹具的共享性误当成生产行为）。该「先改后校验」次序属既存形态，非 #591 引入。
        test_db.rollback()
        test_db.refresh(sub)
        assert sub.status == "confirmed"
        assert sub.apply_date == APPLY


class TestCascadeUnconfirmViaSnapshotPaths:
    """:377 的第二个调用方：快照级联回退（_cascade_unconfirm_subscriptions）。"""

    def _confirmed_sub_at_apply(self, db, code, snapshot_days):
        create_portfolio(db, code=code, status="active")
        for d in snapshot_days:
            create_position_snapshot(
                db, code, "CASH", "", d,
                cash_amount=10000.0, unit_price=None, cost_price=None,
                market_value=10000.0, platform_code="MYCF",
            )
            create_value_snapshot(db, code, d, total_value=10000.0,
                                  total_shares=10000.0, unit_price=1.0)
            create_investor_holding(db, code, "VIEWER", d, shares=10000.0)
        sub = create_subscription(
            db, code, "VIEWER", sub_type="subscribe", amount=10000.0,
            apply_date=APPLY, confirm_date=APPLY, status="confirmed",
            unit_price=1.0, shares=10000.0,
        )
        make_last_open_day(db, APPLY)
        return sub

    def _snapshot_days(self, db, code):
        return sorted(
            row[0] for row in db.query(PortfolioValueSnapshot.snapshot_date).filter(
                PortfolioValueSnapshot.portfolio_code == code
            ).all()
        )

    def test_recalculate_reports_error_200_and_rolls_back(
        self, client, admin_headers, test_db
    ):
        """重算路径有逐日 try → 级联抛出落进 errors[]、响应 200、整体回滚（无半删状态）。"""
        code = "SUB_EXH_RC"
        sub = self._confirmed_sub_at_apply(test_db, code, [APPLY])

        resp = client.post(
            "/api/snapshots/recalculate",
            json={"portfolio_code": code,
                  "start_date": APPLY.isoformat(), "end_date": APPLY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        errors = [e for r in data["results"] for e in r["errors"]]
        assert errors, "级联回退失败必须出现在逐日 errors 里，不得静默"
        assert any("CALENDAR_NOT_SYNCED" == e.get("code") for e in errors), errors
        # 整体回滚：快照仍在、申购仍 confirmed
        assert self._snapshot_days(test_db, code) == [APPLY]
        test_db.refresh(sub)
        assert sub.status == "confirmed"

    def test_bulk_delete_is_422_and_keeps_already_deleted_days(
        self, client, admin_headers, test_db
    ):
        """批量删除逐日 commit，**不回滚**：后置日先删成功、申请日级联抛错 →
        422 且已成功日保留。这是本 issue 唯一合法的「部分状态」出口，显式断言之。
        """
        code = "SUB_EXH_BD"
        # BEYOND 在裁剪后的日历里不存在，但快照行可以先挂上（删除路径不校验取价日）
        sub = self._confirmed_sub_at_apply(test_db, code, [APPLY, BEYOND])
        assert self._snapshot_days(test_db, code) == [APPLY, BEYOND]

        resp = client.delete(
            f"/api/snapshots/{code}/bulk/{APPLY.isoformat()}",
            params={"confirm": True},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"

        remaining = self._snapshot_days(test_db, code)
        assert BEYOND not in remaining, "倒序中先行删除的后置日已 commit，须如实保留该部分状态"
        assert APPLY in remaining, "抛出日不得被删除"
        test_db.refresh(sub)
        assert sub.status == "confirmed"
