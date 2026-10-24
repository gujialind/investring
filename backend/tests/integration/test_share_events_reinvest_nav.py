# ============ 集成测试：红利再投资净值解析（issue #744） ============
# 生产现场：事件 22（007169 / 易方达基金平台）reinvest_nav 落库 0.0，旧逻辑 `or 1` 静默按 1.0
# 折算 → 33.97 份；按除息日净值 1.0099 应为 33.64 份。本文件钉住：
#   - 录入：reinvest_nav 可留空、显式 0/负数拒绝、场内份额不可再投资
#   - 预览/确认：恒取除息日数据源净值；手填只校验；缺净值 MISSING_NAV；存量 0 值拒绝
#   - 确认回写解析净值 + 审计；预览只读；sync_nav 回填重试
#   - 生成后自动确认遇缺净值：auto_confirm_failed，事件保持 pending

import json
from datetime import date
from decimal import Decimal

import pytest

from app.models.audit_log import AuditLog
from app.models.share_change_event import ShareChangeEvent
from app.services.snapshot_service import auto_confirm_after_snapshot
from tests.factories import (
    create_platform, create_portfolio, create_position_snapshot, create_price_record,
    create_product, create_share_change_event, create_value_snapshot, ensure_trading_day,
)

PORT = "P744"
FUND = "007169.OF"
PLAT = "YFFCJJ"
ENT = date(2026, 10, 16)   # 权益登记日（周五，现场规避录法）
EX = date(2026, 10, 19)    # 除息日（周一，公告再投资净值日）
SHARES = 8709.19
DIV_CASH = Decimal("0.0039")
NAV_ENT = 1.0136
NAV_EX = 1.0099


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
        unit_price=NAV_ENT, cost_price=1.0, market_value=SHARES * NAV_ENT, platform_code=PLAT,
    )
    create_value_snapshot(test_db, PORT, ENT, total_value=SHARES, total_shares=SHARES, unit_price=1.0)
    create_price_record(test_db, FUND, "CN_OTC", ENT, NAV_ENT)
    test_db.commit()
    return test_db


def _event(db, **kwargs):
    event = create_share_change_event(
        db, PORT, FUND, "CN_OTC", event_type="reinvest_dividend",
        ex_date=EX, entitlement_date=ENT, platform_code=PLAT,
        div_cash=DIV_CASH, **kwargs,
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


# ---------------- 录入 ----------------

class TestReinvestInput:
    def test_create_without_nav_persists_null(self, client, admin_headers, base):
        resp = client.post("/api/share-change-events", json=_create_body(), headers=admin_headers)
        assert resp.status_code in (200, 201), resp.json()
        row = base.query(ShareChangeEvent).one()
        assert row.reinvest_nav is None

    @pytest.mark.parametrize("bad_nav", [0, -1.0099])
    def test_create_non_positive_nav_rejected(self, client, admin_headers, base, bad_nav):
        resp = client.post(
            "/api/share-change-events", json=_create_body(reinvest_nav=bad_nav), headers=admin_headers
        )
        detail = _error(resp)
        assert detail["error"] == "INVALID_PARAM"
        assert "再投资净值必须大于 0" in detail["message"]
        assert base.query(ShareChangeEvent).count() == 0

    def test_create_on_exchange_rejected(self, client, admin_headers, base):
        create_product(base, code="510300.SH", market="CN_EXCHANGE", product_type="ETF")
        base.commit()
        resp = client.post(
            "/api/share-change-events",
            json=_create_body(product_code="510300.SH", market="CN_EXCHANGE"),
            headers=admin_headers,
        )
        detail = _error(resp)
        assert detail["error"] == "INVALID_PARAM"
        assert "场内份额只能现金分红" in detail["message"]
        assert base.query(ShareChangeEvent).count() == 0

    def test_other_types_ignore_zero_nav(self, client, admin_headers, base):
        resp = client.post(
            "/api/share-change-events",
            json=_create_body(event_type="cash_dividend", reinvest_nav=0),
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()

    def test_update_zero_rejected_null_clears(self, client, admin_headers, base):
        event = _event(base, reinvest_nav=Decimal("1.0099"))
        resp = client.put(
            f"/api/share-change-events/{event.id}", json={"reinvest_nav": 0}, headers=admin_headers
        )
        assert _error(resp)["error"] == "INVALID_PARAM"
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).reinvest_nav == Decimal("1.0099")

        resp = client.put(
            f"/api/share-change-events/{event.id}", json={"reinvest_nav": None}, headers=admin_headers
        )
        assert resp.status_code == 200, resp.json()
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).reinvest_nav is None


# ---------------- 预览 / 确认 ----------------

class TestReinvestNavResolution:
    def _preview(self, client, headers, event_id):
        return client.get(f"/api/share-change-events/{event_id}/preview", headers=headers)

    def _confirm(self, client, headers, event_id, **params):
        return client.post(
            f"/api/share-change-events/{event_id}/confirm", params=params, headers=headers
        )

    @pytest.mark.parametrize("stored_nav", [None, Decimal("1.0099")])
    def test_event22_regression_uses_ex_date_nav(self, client, admin_headers, base, stored_nav):
        """事件 22：留空或手填一致 → 33.97 元 / 1.0099 = 33.64 份（不取登记日 1.0136、不回退 1.0）。"""
        create_price_record(base, FUND, "CN_OTC", EX, NAV_EX)
        event = _event(base, reinvest_nav=stored_nav)

        resp = self._preview(client, admin_headers, event.id)
        assert resp.status_code == 200, resp.json()
        preview = resp.json()["preview"]
        assert preview == {
            "event_type": "reinvest_dividend",
            "cash_effective_date": EX.isoformat(),
            "share_effective_date": EX.isoformat(),
            "entitlement_shares": SHARES,
            "shares_change": 33.64,
            "shares_after": 8742.83,
            "cash_change": 0.0,
            "div_cash": 0.0039,
            "dividend_amount": 33.97,
            "reinvest_nav": NAV_EX,
            "nav_date": EX.isoformat(),
        }
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).reinvest_nav == stored_nav  # 预览只读

        resp = self._confirm(client, admin_headers, event.id)
        assert resp.status_code == 200, resp.json()
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.status == "confirmed"
        assert row.shares_change == Decimal("33.64")
        assert row.shares_after == Decimal("8742.83")
        assert row.reinvest_nav == Decimal("1.0099")

        audit = base.query(AuditLog).filter(
            AuditLog.resource_type == "share_change_event",
            AuditLog.resource_id == str(event.id),
            AuditLog.action == "confirm",
        ).one()
        assert json.loads(audit.new_value)["reinvest_nav"] == "1.0099"  # 审计载荷 Decimal 存字符串

    def test_mismatched_manual_nav_rejected(self, client, admin_headers, base):
        create_price_record(base, FUND, "CN_OTC", EX, NAV_EX)
        event = _event(base, reinvest_nav=Decimal("1.0"))
        for resp in (
            self._preview(client, admin_headers, event.id),
            self._confirm(client, admin_headers, event.id),
        ):
            detail = _error(resp)
            assert detail["error"] == "PRICE_NAV_MISMATCH"
            assert "1.0099" in detail["message"]
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.status == "pending"
        assert row.reinvest_nav == Decimal("1.0")

    @pytest.mark.parametrize("dirty_nav", [Decimal("0"), Decimal("-1")])
    def test_stored_non_positive_nav_rejected_not_resolved(
        self, client, admin_headers, base, dirty_nav
    ):
        """存量脏值（事件 22 的 0.0）不按「未填」静默洗成数据源值，须先清空。"""
        create_price_record(base, FUND, "CN_OTC", EX, NAV_EX)
        event = _event(base, reinvest_nav=dirty_nav)
        for resp in (
            self._preview(client, admin_headers, event.id),
            self._confirm(client, admin_headers, event.id),
        ):
            assert _error(resp)["error"] == "INVALID_PARAM"
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).status == "pending"

    def test_missing_ex_date_nav_rejected(self, client, admin_headers, base):
        """只有登记日净值时不得取它，报 MISSING_NAV（带除息日）。"""
        event = _event(base)
        for resp in (
            self._preview(client, admin_headers, event.id),
            self._confirm(client, admin_headers, event.id),
        ):
            detail = _error(resp)
            assert detail["error"] == "MISSING_NAV"
            assert str(EX) in detail["message"]
            assert detail["details"]["nav_date"] == EX.isoformat()
            assert detail["details"]["event_id"] == event.id
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.status == "pending"
        assert row.shares_change is None
        assert base.query(AuditLog).filter(AuditLog.action == "confirm").count() == 0

    def test_sync_nav_backfills_and_confirms(self, client, admin_headers, base, monkeypatch):
        calls = []

        def fake_sync(db, product_code, market, start_date=None, end_date=None):
            calls.append((product_code, market, start_date))
            create_price_record(db, product_code, market, EX, NAV_EX)
            return {"success": True, "synced_count": 1}

        monkeypatch.setattr("app.services.market_data_service.sync_price_data", fake_sync)
        event = _event(base)
        resp = self._confirm(client, admin_headers, event.id, sync_nav=True)
        assert resp.status_code == 200, resp.json()
        assert calls == [(FUND, "CN_OTC", EX)]
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.shares_change == Decimal("33.64")
        assert row.reinvest_nav == Decimal("1.0099")

    @pytest.mark.parametrize("outcome", ["raises", "soft_fail"])
    def test_sync_nav_failure_reports_missing_nav(
        self, client, admin_headers, base, monkeypatch, outcome
    ):
        def fake_sync(db, product_code, market, start_date=None, end_date=None):
            if outcome == "raises":
                raise RuntimeError("tushare 超时")
            return {"success": False, "message": "tushare 超时"}

        monkeypatch.setattr("app.services.market_data_service.sync_price_data", fake_sync)
        event = _event(base)
        detail = _error(self._confirm(client, admin_headers, event.id, sync_nav=True))
        assert detail["error"] == "MISSING_NAV"
        assert "自动同步净值失败: tushare 超时" in detail["message"]
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).status == "pending"

    def test_without_sync_nav_does_not_call_sync(self, client, admin_headers, base, monkeypatch):
        def fail_sync(*args, **kwargs):
            raise AssertionError("未显式 sync_nav 不得访问外部数据源")

        monkeypatch.setattr("app.services.market_data_service.sync_price_data", fail_sync)
        event = _event(base)
        assert _error(self._confirm(client, admin_headers, event.id))["error"] == "MISSING_NAV"


class TestAutoConfirmMissingNav:
    def test_after_snapshot_reports_failure_and_keeps_pending(self, base):
        """不加生成前钩子（维护者决策）：ex_date 前一日自动确认缺净值 → 记失败、事件保持 pending。"""
        event = _event(base)
        results = auto_confirm_after_snapshot(base, PORT, ENT)
        entries = [r for r in results if r.get("id") == event.id]
        assert entries and entries[0]["action"] == "auto_confirm_failed"
        assert entries[0].get("code") == "MISSING_NAV"
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).status == "pending"
