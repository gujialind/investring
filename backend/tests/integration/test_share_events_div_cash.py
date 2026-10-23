# ============ 集成测试：每份分红金额正负校验（issue #764 / #771） ============
# 现场（#743 处置期间实测）：`div_cash = -0.0039` 的 reinvest_dividend 可以创建成功，
# compute_event_fields 折出 shares_change = -3.86（负向份额），确认时写进快照会把摊薄
# 成本与收益方向反转；`div_cash = 0` 则产出「0 份额 / 0 现金」的空事件污染列表与统计。
# 本文件钉住创建 / 更新（合并后值）/ 确认 preamble 三处同口径拒绝，且拒绝时零写入；
# #771 追加两处：计算单点 `compute_event_fields` 的同类兜底，以及 auto_confirm 对这类
# 存量脏行改为**跳过 + 补录指引**（旧行为是每次快照撞门禁、刷一条 auto_confirm_failed，
# 事件永久停在 pending）。

from datetime import date
from decimal import Decimal
import logging

import pytest

from app.models.audit_log import AuditLog
from app.models.share_change_event import ShareChangeEvent
from app.services.exceptions import BusinessError
from app.services.share_change_event_service import (
    MSG_DIV_CASH_NOT_POSITIVE, compute_event_fields, dividend_div_cash_invalid,
)
from app.services.snapshot_service import auto_confirm_after_snapshot
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
        """省略（None）刻意不在本条范围：允许先建事件、事后补金额（#771 另在检测口挡）。"""
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


# ---------------- NULL 放行的后果边界 ----------------

class TestNullDivCashYieldsEmptyEvent:
    """#764 的门禁挡的是**字面非正值**，不是「0 现金 / 0 份额的空事件」这个后果。

    `div_cash` 省略（NULL）刻意放行（人工录入允许「先建事件、金额待补」，见
    `_validate_dividend_div_cash` docstring 与 business-constraints #rule-event），
    而两条折算路径都是 `div_cash or 0` ⇒ 折出的正是本 issue 想避免的空事件，且确认能成功。
    钉住这条边界，防后来者把正文读成「不会再出现 0 值分红事件」，或反过来照它去加结果侧门禁。
    （#771 起 tushare 检测口不再建缺失金额的事件，本条只剩人工省略这一来源。）
    """

    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    def test_null_div_cash_confirms_as_empty_event(self, client, admin_headers, base, event_type):
        body = _create_body(event_type=event_type)
        body.pop("div_cash")
        resp = client.post("/api/share-change-events", json=body, headers=admin_headers)
        assert resp.status_code in (200, 201), resp.json()
        event = base.query(ShareChangeEvent).one()
        assert event.div_cash is None

        confirmed = client.post(
            f"/api/share-change-events/{event.id}/confirm", headers=admin_headers
        )
        assert confirmed.status_code == 200, confirmed.json()
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.status == "confirmed"
        assert row.div_cash is None
        # cash_dividend 折 0.00 现金、reinvest_dividend 折 0.00 份（红利金额先到分 ⇒ 0）
        assert row.cash_change == Decimal("0.00")
        assert row.shares_change == Decimal("0.00")


# ---------------- 计算单点的同类兜底（#771） ----------------

class TestComputeEventFieldsRejectsNonPositiveDivCash:
    """`compute_event_fields` 自己也得挡住非正 `div_cash`（#771）。

    确认 preamble 已先拒，本类钉的是**纵深**：绕过 preamble 的调用（新增调用点、
    重算/迁移脚本、未来复用该纯函数的路径）拿到的是拒绝，而不是 `shares_change =
    -3.86` 这种会写进快照的负向变动。判据与 preamble 同一函数（同码同消息），
    NULL 仍按 `TestNullDivCashYieldsEmptyEvent` 的既有口径折 0——**不得**在这里收紧。
    """

    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    @pytest.mark.parametrize("bad_div_cash", [Decimal("0"), Decimal("-0.0039")])
    def test_rejects_with_same_code_and_message(self, event_type, bad_div_cash):
        with pytest.raises(BusinessError) as exc:
            compute_event_fields(
                event_type, Decimal(str(SHARES)),
                div_cash=bad_div_cash, reinvest_nav=Decimal(str(NAV)),
            )
        assert exc.value.code == "INVALID_PARAM"
        assert exc.value.message == MSG_DIV_CASH_NOT_POSITIVE
        assert exc.value.details == {"field": "div_cash", "value": float(bad_div_cash)}

    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    def test_null_still_computes_zero(self, event_type):
        """NULL 不在本条范围：与 preamble、确认侧同口径（省略 ≠ 非法）。"""
        result = compute_event_fields(
            event_type, Decimal(str(SHARES)), div_cash=None, reinvest_nav=Decimal(str(NAV))
        )
        assert (result.shares_change, result.cash_change) == (Decimal("0"), Decimal("0.00"))

    def test_non_dividend_types_unaffected(self):
        """前端对所有类型都提交 `div_cash: 0`，非分红类不得被兜底误伤。"""
        result = compute_event_fields(
            "share_split", Decimal("1000"), ratio=Decimal("2"), div_cash=Decimal("0")
        )
        assert result.shares_change == Decimal("1000")


# ---------------- 判据单点对非有限值的防御（#771 评审） ----------------

NON_FINITE = [float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")]


class TestDivCashInvalidCriterionNonFinite:
    """`dividend_div_cash_invalid` 遇 NaN/Inf 返回 True 且**不抛异常**。

    有序比较 `Decimal("NaN") <= 0` 抛 `InvalidOperation`（与 `float('nan') <= 0` 返回
    False 不同）。本函数是三道出口的判据单点，其中 `auto_confirm_after_snapshot` 在
    `_auto_confirm_guarded` 的 savepoint **之外**调它（snapshot_service.py 的跳过检查），
    一条冒出的异常会掀掉整段自动确认；检测口同样复用本函数，故防御装在这里一处即可。
    取数层已把上游 NaN 归一为 None（`test_tushare_rate_limit.py::TestFundDivRowMapping`），
    本类钉的是防其他调用方传入的纵深。
    """

    @pytest.mark.parametrize("event_type", DIVIDEND_TYPES)
    @pytest.mark.parametrize("bad_div_cash", NON_FINITE)
    def test_non_finite_is_invalid(self, event_type, bad_div_cash):
        assert dividend_div_cash_invalid(event_type, bad_div_cash) is True

    @pytest.mark.parametrize("ok", [Decimal("0.0039"), 0.0039, Decimal("0"), 0, -0.0039])
    def test_explicit_values_keep_764_semantics(self, ok):
        """既有显式值口径不放宽也不收紧：正数放行，0/负数仍非法。"""
        expected = Decimal(str(ok)) <= 0
        assert dividend_div_cash_invalid("reinvest_dividend", ok) is expected

    def test_null_still_valid(self):
        """NULL 是「省略待补录」的既有口径，不得因加防御而顺带判非法。"""
        for event_type in DIVIDEND_TYPES:
            assert dividend_div_cash_invalid(event_type, None) is False

    @pytest.mark.parametrize("bad_div_cash", [float("nan"), Decimal("NaN")])
    def test_non_dividend_types_not_caught(self, bad_div_cash):
        """非分红类历史上恒提交 `div_cash: 0`，本判据对它们恒返回 False（既有边界）。"""
        assert dividend_div_cash_invalid("share_split", bad_div_cash) is False

    def test_compute_event_fields_rejects_non_finite(self):
        """计算单点走同一判据：拿到 NaN 是拒绝，不是折出 NaN 份额写进快照。"""
        with pytest.raises(BusinessError) as exc:
            compute_event_fields(
                "reinvest_dividend", Decimal(str(SHARES)),
                div_cash=Decimal("NaN"), reinvest_nav=Decimal(str(NAV)),
            )
        assert exc.value.code == "INVALID_PARAM"
        assert exc.value.message == MSG_DIV_CASH_NOT_POSITIVE


# ---------------- auto_confirm 跳过 + 补录指引（#771） ----------------

def _auto_confirm(db, day):
    """走真实后置钩子入口（判键 `share_effective_date`），结果与 #305 结构一致。"""
    results = auto_confirm_after_snapshot(db, PORT, day)
    db.commit()
    return results


def _entry_for(results, event_id):
    return [r for r in results if r.get("type") == "event" and r.get("id") == event_id]


class TestAutoConfirmSkipsBackfillNeededEvents:
    """事件仍 pending 时，自动确认改为「跳过 + 指引」，不再逐日撞门禁（#771）。

    旧路径：直造 ORM 的脏行（`div_cash <= 0`）在 auto_confirm 里被 preamble 拒绝 →
    `action=auto_confirm_failed` + WARNING，日复一日重试、永久停在 pending，界面上
    只有失败码、没有任何「去补录金额」的指引。
    """

    @pytest.mark.parametrize("bad_div_cash", [Decimal("0"), Decimal("-0.0039")])
    def test_skipped_with_actionable_hint_and_no_write(self, base, bad_div_cash):
        event = _dirty_event(base, "reinvest_dividend", bad_div_cash)

        results = _auto_confirm(base, ENT)
        entries = _entry_for(results, event.id)
        assert [e["action"] for e in entries] == ["auto_confirm_skipped"]
        assert entries[0]["code"] == "INVALID_PARAM"
        assert entries[0]["error"] == MSG_DIV_CASH_NOT_POSITIVE
        assert "补录" in entries[0]["hint"]

        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert (row.status, row.shares_change, row.cash_change) == ("pending", None, None)
        assert _audit_rows(base, event.id) == []

    def test_skip_is_not_counted_as_an_auto_confirm_failure(self, base):
        """`action` 必须是独立值：task_runner 按 `auto_confirm_failed` 归并计数（#715/#406），
        「等人工补录」不是失败，混进去会把任务终态谎报成 partial_success。"""
        _dirty_event(base, "reinvest_dividend", Decimal("-0.0039"))
        results = _auto_confirm(base, ENT)
        assert [r for r in results if r.get("action") == "auto_confirm_failed"] == []

    def test_skip_logs_info_not_warning(self, base, caplog):
        """不刷 WARNING（同一批每次都告警是本 issue 的现场症状）。"""
        event = _dirty_event(base, "reinvest_dividend", Decimal("-0.0039"))
        with caplog.at_level(logging.INFO, logger="app.services.snapshot_service"):
            _auto_confirm(base, ENT)
        service_records = [
            r for r in caplog.records if r.name == "app.services.snapshot_service"
        ]
        messages = [r.getMessage() for r in service_records]
        assert any("skipped" in m and str(event.id) in m for m in messages), messages
        assert not [r for r in service_records if r.levelno >= logging.WARNING], messages

    def test_positive_event_still_auto_confirms(self, base):
        """跳过不得扩大到正常事件：正金额照旧 auto_confirmed。"""
        event = _dirty_event(base, "reinvest_dividend", DIV_CASH)
        results = _auto_confirm(base, ENT)
        assert [e["action"] for e in _entry_for(results, event.id)] == ["auto_confirmed"]
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert row.status == "confirmed"
        # 8709.19 × 0.0039 = 33.97（先到分）→ / 1.0099 = 33.64 份
        assert row.shares_change == Decimal("33.64")

    def test_null_div_cash_still_auto_confirms(self, base):
        """NULL 不跳：与 #764 的放行同判据（省略可确认，只是折出空事件）。

        若把 NULL 也跳掉，「待补录」与「确认成空事件」两条路径会同时存在，
        反而不如旧行为可预期——本条钉住跳过条件严格等于 preamble 的拒绝条件。
        """
        event = _dirty_event(base, "reinvest_dividend", None)
        results = _auto_confirm(base, ENT)
        assert [e["action"] for e in _entry_for(results, event.id)] == ["auto_confirmed"]
        base.expire_all()
        assert base.get(ShareChangeEvent, event.id).shares_change == Decimal("0.00")

    def test_backfill_then_confirm_succeeds(self, client, admin_headers, base):
        """跳过路径必须可恢复：补录金额后同一事件正常确认（不是终态死信）。"""
        event = _dirty_event(base, "reinvest_dividend", Decimal("0"))
        assert [e["action"] for e in _entry_for(_auto_confirm(base, ENT), event.id)] == [
            "auto_confirm_skipped"
        ]

        resp = client.put(
            f"/api/share-change-events/{event.id}", headers=admin_headers,
            json={"div_cash": float(DIV_CASH)},
        )
        assert resp.status_code == 200, resp.json()

        assert [e["action"] for e in _entry_for(_auto_confirm(base, ENT), event.id)] == [
            "auto_confirmed"
        ]
        base.expire_all()
        row = base.get(ShareChangeEvent, event.id)
        assert (row.status, row.shares_change) == ("confirmed", Decimal("33.64"))


class TestSchedulingPathSurfacesSkipInTaskWarnings:
    """串联守门（#771 复审 S）：产生端的 skip 条目须经调度路径进任务终态。

    手动重算/追平/生成次日快照的响应本就透传 `auto_confirm_skipped`，但调度路径的
    `_generate_snapshots_for_date` 只收集 `auto_confirm_failed`，skip 被丢弃，任务终态
    `error_message` 对「有事件等人工补录」零记录——与检测口 `dividends_skipped` 落终态
    不对称。本用例真跑 runner（只把快照生成打桩成 no-op），钉住四条：skip 进 warnings、
    status 仍 success、不进 records_failed、终态 `error_message` 带组合与事件身份。
    """

    def test_skip_reaches_task_terminal_message_without_failing(self, base, monkeypatch):
        from app.models.portfolio import Portfolio
        from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
        from app.services import snapshot_service
        from app.services.task_runner import _derive_log_fields, _generate_snapshots_for_date

        event = _dirty_event(base, "reinvest_dividend", Decimal("-0.0039"))
        # 调度路径只处理 active + auto_snapshot_enabled 的组合，并从其最新快照日之后首个
        # 交易日逐日回补：把基线挪到 ENT 前一交易日，使回补循环覆盖 ENT——auto_confirm
        # 的判键是 share_effective_date == next_trading_day(ENT) == EX，脏行正在这天被试。
        prev_day = date(2026, 10, 15)
        ensure_trading_day(base, prev_day, is_open=True)
        base.query(Portfolio).filter_by(code=PORT).one().auto_snapshot_enabled = True
        base.query(PortfolioValueSnapshot).filter_by(portfolio_code=PORT).update(
            {"snapshot_date": prev_day}, synchronize_session=False,
        )
        base.commit()
        monkeypatch.setattr(
            snapshot_service, "generate_daily_snapshots",
            lambda db, portfolio_code, target_date: {"warnings": []},
        )

        outcome = _generate_snapshots_for_date(base, ENT)

        skips = [w for w in outcome["warnings"] if w.get("type") == "auto_confirm_skipped"]
        assert [w.get("event_id") for w in skips] == [event.id], outcome["warnings"]
        assert skips[0]["portfolio_code"] == PORT
        assert skips[0]["date"] == ENT.isoformat()
        assert "补录" in skips[0]["message"]
        # 「等人工补录」不是失败：不进 auto_confirm_failed / failed_portfolios
        assert outcome["auto_confirm_failed"] == []
        assert outcome["failed_portfolios"] == []
        # 终态归一：warnings 通道落 error_message 且不改 status、不改三计数
        status, total, success, failed, message = _derive_log_fields("snapshot_generate", outcome)
        assert status == "success"
        assert (total, success, failed) == (1, 1, 0)
        # #771 复审 S：终态须能定位到「哪个组合的哪条事件在等补录」，
        # 只剩 type 名时运维得翻运行时 INFO 日志或手工查 pending 事件
        assert message == f"warnings: auto_confirm_skipped({PORT}#{event.id})"
