# ============ 集成测试：每份分红金额正负校验（issue #764） ============
# 现场（#743 处置期间实测）：`div_cash = -0.0039` 的 reinvest_dividend 可以创建成功，
# compute_event_fields 折出 shares_change = -3.86（负向份额），确认时写进快照会把摊薄
# 成本与收益方向反转；`div_cash = 0` 则产出「0 份额 / 0 现金」的空事件污染列表与统计。
# 本文件钉住创建 / 更新（合并后值）/ 确认 preamble 三处同口径拒绝，且拒绝时零写入。

from datetime import date
from decimal import Decimal

import pytest

from app.models.audit_log import AuditLog
from app.models.share_change_event import ShareChangeEvent
from tests.factories import (
    create_platform, create_portfolio, create_position_snapshot, create_price_record,
    create_product, create_share_change_event, create_value_snapshot, ensure_trading_day,
)

PORT = "P764"
FUND = "007169.OF"
PLAT = "YFFCJJ"
ENT = date(2026, 10, 16)   # 权益登记日（周五）
EX = date(2026, 10, 19)    # 除息日（周一）
SHARES = 8709.19
DIV_CASH = Decimal("0.0039")
NAV = 1.0099
DIVIDEND_TYPES = ("cash_dividend", "reinvest_dividend")


@pytest.fixture
def base(test_db):
    create_portfolio(test_db, code=PORT, status="active")
    create_product(test_db, code=FUND, market="CN_OTC", product_type="OEF")
    create_platform(test_db, code=PLAT)
    ensure_trading_day(test_db, ENT, is_open=True)
    ensure_trading_day(test_db, date(2026, 10, 17), is_open=False)
    ensure_trading_day(test_db, date(2026, 10, 18), is_open=False)
    ensure_trading_day(test_db, EX, is_open=True)
    create_position_snapshot(
        test_db, PORT, FUND, "CN_OTC", snapshot_date=ENT, shares=SHARES,
        unit_price=NAV, cost_price=1.0, market_value=SHARES * NAV, platform_code=PLAT,
    )
    create_value_snapshot(test_db, PORT, ENT, total_value=SHARES, total_shares=SHARES, unit_price=1.0)
    create_price_record(test_db, FUND, "CN_OTC", ENT, NAV)
    create_price_record(test_db, FUND, "CN_OTC", EX, NAV)
    test_db.commit()
    return test_db


def _dirty_event(db, event_type, div_cash):
    """直造存量脏行（绕开 service 校验），供确认侧兜底断言。"""
    event = create_share_change_event(
        db, PORT, FUND, "CN_OTC", event_type=event_type,
        ex_date=EX, entitlement_date=ENT, platform_code=PLAT, div_cash=div_cash,
    )
    db.commit()
    return event


def _create_body(**overrides):
    body = {
        "portfolio_code": PORT, "event_type": "reinvest_dividend",
        "ex_date": EX.isoformat(), "entitlement_date": ENT.isoformat(),
        "product_code": FUND, "market": "CN_OTC", "platform_code": PLAT,
        "div_cash": float(DIV_CASH),
    }
    body.update(overrides)
    return body


def _error(resp):
    assert resp.status_code == 422, resp.json()
    return resp.json()["detail"]


def _audit_rows(db, resource_id=None):
    query = db.query(AuditLog).filter(
        AuditLog.resource_type == "share_change_event"
    )
    if resource_id is not None:
        query = query.filter(AuditLog.resource_id == str(resource_id))
    return query.all()


# ---------------- 创建 ----------------

class TestCreateRejectsNonPositiveDivCash:
    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    @pytest.mark.parametrize("bad_div_cash", [0, -0.0039])
    def test_rejected_with_zero_write(self, client, admin_headers, base, event_type, bad_div_cash):
        resp = client.post(
            "/api/share-change-events",
            json=_create_body(event_type=event_type, div_cash=bad_div_cash),
            headers=admin_headers,
        )
        detail = _error(resp)
        assert detail["error"] == "INVALID_PARAM"
        assert "每份分红金额必须大于 0" in detail["message"]
        assert detail["details"] == {"field": "div_cash", "value": float(bad_div_cash)}
        base.expire_all()
        assert base.query(ShareChangeEvent).count() == 0
        assert _audit_rows(base) == []

    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    def test_positive_value_unchanged(self, client, admin_headers, base, event_type):
        resp = client.post(
            "/api/share-change-events",
            json=_create_body(event_type=event_type),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        row = base.query(ShareChangeEvent).one()
        assert row.div_cash == DIV_CASH
        assert row.status == "pending"

    def test_non_dividend_types_ignore_zero(self, client, admin_headers, base):
        """前端历史上对所有类型都提交 `div_cash: 0`，非分红类不得误伤。"""
        resp = client.post(
            "/api/share-change-events",
            json=_create_body(
                event_type="forced_adjustment", div_cash=0, cash_change=10,
            ),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()

    def test_omitted_div_cash_still_allowed(self, client, admin_headers, base):
        """省略（None）刻意不在本条范围：tushare 分红检测直造事件时该列可能缺失。"""
        body = _create_body()
        body.pop("div_cash")
        resp = client.post("/api/share-change-events", json=body, headers=admin_headers)
        assert resp.status_code in (200, 201), resp.json()
        assert base.query(ShareChangeEvent).one().div_cash is None


# ---------------- 更新（合并后值） ----------------

class TestUpdateRejectsNonPositiveDivCash:
    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    @pytest.mark.parametrize("bad_div_cash", [0, -0.5])
    def test_rejected_keeps_old_value_and_leaves_no_audit(
        self, client, admin_headers, base, event_type, bad_div_cash
    ):
        event = _dirty_event(base, event_type, DIV_CASH)
        resp = client.put(
            f"/api/share-change-events/{event.id}",
            json={"div_cash": bad_div_cash},
            headers=admin_headers,
        )
        assert _error(resp)["error"] == "INVALID_PARAM"
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).div_cash == DIV_CASH
        assert _audit_rows(base, event.id) == []

    def test_merged_value_rejected_even_when_only_notes_change(
        self, client, admin_headers, base
    ):
        """按合并后值校验：存量 0 值行改备注同样被拦（与 reinvest_nav 同口径）。"""
        event = _dirty_event(base, "cash_dividend", Decimal("0"))
        resp = client.put(
            f"/api/share-change-events/{event.id}", json={"notes": "改备注"}, headers=admin_headers
        )
        assert _error(resp)["error"] == "INVALID_PARAM"
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).notes is None

    def test_positive_update_accepted(self, client, admin_headers, base):
        event = _dirty_event(base, "cash_dividend", DIV_CASH)
        resp = client.put(
            f"/api/share-change-events/{event.id}", json={"div_cash": 0.005}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).div_cash == Decimal("0.005")

    def test_explicit_null_still_clears(self, client, admin_headers, base):
        """div_cash 是清除型字段（#579 allow 清单），显式 null 的既有语义不变。"""
        event = _dirty_event(base, "cash_dividend", DIV_CASH)
        resp = client.put(
            f"/api/share-change-events/{event.id}", json={"div_cash": None}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).div_cash is None


# ---------------- 确认 preamble（存量脏行兜底） ----------------

class TestConfirmRejectsNonPositiveDivCash:
    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    @pytest.mark.parametrize("bad_div_cash", [Decimal("0"), Decimal("-0.0039")])
    def test_preview_and_confirm_reject_with_zero_write(
        self, client, admin_headers, base, event_type, bad_div_cash
    ):
        """预览与确认共用 preamble，故同码同消息；负向份额不得写进事件或子记录。"""
        event = _dirty_event(base, event_type, bad_div_cash)
        for resp in (
            client.get(f"/api/share-change-events/{event.id}/preview", headers=admin_headers),
            client.post(f"/api/share-change-events/{event.id}/confirm", headers=admin_headers),
        ):
            detail = _error(resp)
            assert detail["error"] == "INVALID_PARAM"
            assert "每份分红金额必须大于 0" in detail["message"]

        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.status == "pending"
        assert row.shares_change is None
        assert row.cash_change is None
        assert row.div_cash == bad_div_cash
        assert base.query(ShareChangeEvent).filter(
            ShareChangeEvent.parent_event_id == event.id
        ).count() == 0
        assert _audit_rows(base, event.id) == []

    def test_positive_confirm_unchanged(self, client, admin_headers, base):
        event = _dirty_event(base, "reinvest_dividend", DIV_CASH)
        resp = client.post(
            f"/api/share-change-events/{event.id}/confirm", headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.status == "confirmed"
        # 8709.19 × 0.0039 = 33.97（先到分）→ 33.97 / 1.0099 = 33.64 份
        assert row.shares_change == Decimal("33.64")
