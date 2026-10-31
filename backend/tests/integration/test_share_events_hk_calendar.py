"""#784：HK_MUTUAL 份额事件日期校验按产品市场日历（HKEX）。

覆盖（SPEC issue-784-spec.md v1.3 §⑤）：
- T1 误拒方向：SSE 休（行缺失）+ HKEX 开 → HK_MUTUAL 创建通过；同日期 CN 产品按 SSE 拒。
- T2 误接受方向：SSE 开 + HKEX 休（显式 is_open=False 行）→ HK_MUTUAL 拒；CN 同日期通过。
- T3 share_confirm_date 同步按产品市场日历；CN 路径回归不变。
- T4 HKEX 日历行完全缺失：按「非交易日」拒（INVALID_ENTITLEMENT_DATE），与
  CALENDAR_NOT_SYNCED 区分（is_trading_day 对缺行返回 False，trading_utils.py:44-56）。
- T5 半开形态·份额生效日：ex_date 落 SSE 休市日 → pending 闸门阻断下一 SSE 快照日，
  人工确认后闸门翻转、快照恢复生成（证明「人工一步」而非死锁）。
- T5b 半开形态·权益登记日：entitlement_date 落 SSE 休市/HKEX 开市日 → 创建成功，
  confirm 与 preview 均 MISSING_POSITION_SNAPSHOT（同码同消息）、零写入。
  钉 Q2=改 A 决策口径：不得改为创建期拒绝或权益日自动回退。
- T6 创建路径优先序（A-1a，顾佳林 2026-10-31 Q3）：组合存在性 / market 解析先于
  日期校验；(code, market) 行精查保留在日期校验之后（与 trade_service 同构）。

HKEX 行一律用 ensure_trading_day / mirror 构造并**用后删除**（calendar_helpers
共享库契约）；SSE 行只读不改动。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.trading_calendar import TradingCalendar
from app.services import share_change_event_service as service
from app.services.exceptions import BusinessError
from app.services.snapshot_service import (
    _check_share_change_events,
    generate_daily_snapshots,
)
from app.utils.business_time import business_today
from tests.factories import (
    create_investor_holding, create_portfolio, create_position_snapshot,
    create_price_record, create_product, create_value_snapshot, ensure_trading_day,
)

HK = "HK_MUTUAL"
SEED_PLAT = "MYCF"  # tests/seed_base.py 平台种子

# 种子日历覆盖 = 2025-01-01 .. business_today()+365（seed_base，#468）。
# 取 +400/+405 天保证 SSE 行缺失（is_trading_day → False），HKEX 行按需自建。
D_HK_ONLY = business_today() + timedelta(days=400)          # SSE 无行、HKEX 自建开市
D_HK_ONLY_2 = business_today() + timedelta(days=401)
D_HK_SHUT = business_today() + timedelta(days=405)          # HKEX 显式休市行（T3）
D_HK_OPEN_LATE = business_today() + timedelta(days=406)     # HKEX 开市（T3 通过侧）


def _sse_open_days(db, start, count=2):
    """从 start 起取 count 个 SSE 开市日（种子形态，只读）。"""
    rows = (
        db.query(TradingCalendar.calendar_date)
        .filter(
            TradingCalendar.exchange == "SSE",
            TradingCalendar.calendar_date >= start,
            TradingCalendar.is_open == True,  # noqa: E712
        )
        .order_by(TradingCalendar.calendar_date)
        .limit(count)
        .all()
    )
    return [r[0] for r in rows]


def _body(port, product, ent, ex, *, market=None, event_type="forced_adjustment",
          shares_change=1.0, **extra):
    body = {
        "portfolio_code": port,
        "product_code": product,
        "event_type": event_type,
        "ex_date": ex.isoformat(),
        "entitlement_date": ent.isoformat(),
        "platform_code": SEED_PLAT,
    }
    if market is not None:
        body["market"] = market
    if shares_change is not None:
        body["shares_change"] = shares_change
    body.update(
        {k: (v.isoformat() if isinstance(v, date) else v) for k, v in extra.items()}
    )
    return body


class TestHkEventDateValidation:
    """T1/T2/T4：双日期校验按产品市场日历，双向差异（issue 验收断言 1、2）"""

    def test_t1_sse_shut_hkex_open_hk_allowed_cn_rejected(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="HK784_T1", status="active")
        create_product(test_db, code="HK784_F1", market=HK, product_type="OEF")
        create_product(test_db, code="HK784_F2", market="CN_OTC", product_type="OEF")
        try:
            ensure_trading_day(test_db, D_HK_ONLY, is_open=True, exchange="HKEX")
            ensure_trading_day(test_db, D_HK_ONLY_2, is_open=True, exchange="HKEX")

            resp = client.post(
                "/api/share-change-events",
                json=_body("HK784_T1", "HK784_F1", D_HK_ONLY, D_HK_ONLY_2, market=HK),
                headers=admin_headers,
            )
            assert resp.status_code in (200, 201), resp.text
            assert resp.json()["status"] == "pending"

            # 同日期 CN 产品：SSE 无行 → 仍按 SSE 拒（A 股行为不变）
            resp = client.post(
                "/api/share-change-events",
                json=_body("HK784_T1", "HK784_F2", D_HK_ONLY, D_HK_ONLY_2, market="CN_OTC"),
                headers=admin_headers,
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["error"] == "INVALID_ENTITLEMENT_DATE"
        finally:
            _remove_hkex(test_db, D_HK_ONLY, D_HK_ONLY_2)

    def test_t2_sse_open_hkex_shut_hk_rejected_cn_allowed(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="HK784_T2", status="active")
        create_product(test_db, code="HK784_F3", market=HK, product_type="OEF")
        create_product(test_db, code="HK784_F4", market="CN_OTC", product_type="OEF")
        ent, ex = _sse_open_days(test_db, business_today() + timedelta(days=1), 2)
        try:
            ensure_trading_day(test_db, ent, is_open=False, exchange="HKEX")
            ensure_trading_day(test_db, ex, is_open=False, exchange="HKEX")

            resp = client.post(
                "/api/share-change-events",
                json=_body("HK784_T2", "HK784_F3", ent, ex, market=HK),
                headers=admin_headers,
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["error"] == "INVALID_ENTITLEMENT_DATE"

            resp = client.post(
                "/api/share-change-events",
                json=_body("HK784_T2", "HK784_F4", ent, ex, market="CN_OTC"),
                headers=admin_headers,
            )
            assert resp.status_code in (200, 201), resp.text
        finally:
            _remove_hkex(test_db, ent, ex)

    def test_t4_hkex_calendar_missing_rows_rejected_as_non_trading(self, client, admin_headers, test_db):
        """HKEX 行缺失按「非交易日」拒绝（不是 CALENDAR_NOT_SYNCED）——T4 口径成文。"""
        create_portfolio(test_db, code="HK784_T4", status="active")
        create_product(test_db, code="HK784_F6", market=HK, product_type="OEF")
        ent, ex = _sse_open_days(test_db, business_today() + timedelta(days=1), 2)
        resp = client.post(
            "/api/share-change-events",
            json=_body("HK784_T4", "HK784_F6", ent, ex, market=HK),
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_ENTITLEMENT_DATE"

    def test_t3_share_confirm_date_follows_product_calendar(self, client, admin_headers, test_db):
        """T3：reinvest 的 share_confirm_date 按产品市场日历；CN 回归不变（断言 3）。"""
        create_portfolio(test_db, code="HK784_T3", status="active")
        create_product(test_db, code="HK784_F5", market=HK, product_type="OEF")
        create_product(test_db, code="HK784_F7", market="CN_OTC", product_type="OEF")
        try:
            ensure_trading_day(test_db, D_HK_ONLY, is_open=True, exchange="HKEX")
            ensure_trading_day(test_db, D_HK_ONLY_2, is_open=True, exchange="HKEX")
            ensure_trading_day(test_db, D_HK_SHUT, is_open=False, exchange="HKEX")
            ensure_trading_day(test_db, D_HK_OPEN_LATE, is_open=True, exchange="HKEX")

            # HK_MUTUAL：确认日落 HKEX 休市 → 拒
            resp = client.post(
                "/api/share-change-events",
                json=_body(
                    "HK784_T3", "HK784_F5", D_HK_ONLY, D_HK_ONLY_2, market=HK,
                    event_type="reinvest_dividend", shares_change=None, div_cash=0.1,
                    share_confirm_date=D_HK_SHUT,
                ),
                headers=admin_headers,
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["error"] == "INVALID_SHARE_CONFIRM_DATE"

            # HK_MUTUAL：确认日落 HKEX 开市（SSE 无行）→ 通过
            resp = client.post(
                "/api/share-change-events",
                json=_body(
                    "HK784_T3", "HK784_F5", D_HK_ONLY, D_HK_ONLY_2, market=HK,
                    event_type="reinvest_dividend", shares_change=None, div_cash=0.1,
                    share_confirm_date=D_HK_OPEN_LATE,
                ),
                headers=admin_headers,
            )
            assert resp.status_code in (200, 201), resp.text

            # CN_OTC 回归：确认日走 SSE（同日 HKEX 开、SSE 无行）→ 仍拒
            resp = client.post(
                "/api/share-change-events",
                json=_body(
                    "HK784_T3", "HK784_F7", D_HK_ONLY, D_HK_ONLY_2, market="CN_OTC",
                    event_type="reinvest_dividend", shares_change=None, div_cash=0.1,
                    share_confirm_date=D_HK_OPEN_LATE,
                ),
                headers=admin_headers,
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["error"] == "INVALID_ENTITLEMENT_DATE"
        finally:
            _remove_hkex(test_db, D_HK_ONLY, D_HK_OPEN_LATE)

    def test_t6_create_order_precedence(self, client, admin_headers, test_db):
        """T6（A-1a 优先序）：组合存在性 / market 解析先于日期校验；
        market 显式 + 行缺失仍日期错误先报（与 trade_service 同构的边界）。"""
        create_portfolio(test_db, code="HK784_T6", status="active")
        # LOF 一码两市场（供 MARKET_AMBIGUOUS）
        create_product(test_db, code="HK784_LOF", market="CN_OTC", product_type="LOF")
        create_product(test_db, code="HK784_LOF", market="CN_EXCHANGE", product_type="LOF")
        bad_ent, bad_ex = D_HK_ONLY, D_HK_ONLY_2  # 两市场均无 SSE 行、未建 HKEX 行 → 日期非法

        # ① 非法日期 + 组合不存在 → 组合 NOT_FOUND 先报（404）
        resp = client.post(
            "/api/share-change-events",
            json=_body("HK784_NOPE", "HK784_LOF", bad_ent, bad_ex),
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "NOT_FOUND"

        # ② 非法日期 + market 省略且 code 无任何行 → PRODUCT_NOT_FOUND 先报
        resp = client.post(
            "/api/share-change-events",
            json=_body("HK784_T6", "HK784_NOCODE", bad_ent, bad_ex),
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "PRODUCT_NOT_FOUND"

        # ③ 非法日期 + market 省略 + LOF 多市场 → MARKET_AMBIGUOUS 先报（422）
        resp = client.post(
            "/api/share-change-events",
            json=_body("HK784_T6", "HK784_LOF", bad_ent, bad_ex),
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "MARKET_AMBIGUOUS"

        # ④ 非法日期 + market 显式 HK_MUTUAL 且 (code, market) 行不存在 →
        #    仍日期错误先报（行精查保留在日期校验之后）
        resp = client.post(
            "/api/share-change-events",
            json=_body("HK784_T6", "HK784_LOF", bad_ent, bad_ex, market=HK),
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_ENTITLEMENT_DATE"


def _remove_hkex(db, start, end):
    db.query(TradingCalendar).filter(
        TradingCalendar.exchange == "HKEX",
        TradingCalendar.calendar_date >= start,
        TradingCalendar.calendar_date <= end,
    ).delete(synchronize_session=False)
    db.commit()


class TestHkHalfOpenStates:
    """T5/T5b：半开形态为 Q2=改 A 的既定成文行为——创建放行、确认/闸门显式失败。"""

    def test_t5b_entitlement_hk_only_creates_but_confirm_preview_fail(self, test_db):
        """权益登记日仅 HKEX 开市：创建成功；confirm/preview 均 MISSING_POSITION_SNAPSHOT、零写入。"""
        create_portfolio(test_db, code="HK784_B", status="active")
        create_product(test_db, code="HK784_BF", market=HK, product_type="OEF")
        try:
            ensure_trading_day(test_db, D_HK_ONLY, is_open=True, exchange="HKEX")
            ensure_trading_day(test_db, D_HK_ONLY_2, is_open=True, exchange="HKEX")
            event = service.create_share_change_event(
                test_db,
                portfolio_code="HK784_B", product_code="HK784_BF", market=HK,
                platform_code=SEED_PLAT, event_type="cash_dividend",
                entitlement_date=D_HK_ONLY, ex_date=D_HK_ONLY_2,
                div_cash=Decimal("0.1"),
            )
            test_db.commit()
            before = {c.key: getattr(event, c.key) for c in event.__table__.columns}

            for call in (
                service.compute_share_change_event_preview,
                service.confirm_share_change_event,
            ):
                with pytest.raises(BusinessError) as exc:
                    call(test_db, event)
                assert exc.value.code == "MISSING_POSITION_SNAPSHOT"
                assert before == {c.key: getattr(event, c.key) for c in event.__table__.columns}
                assert event.status == "pending"
                assert event.entitlement_shares is None
                assert not test_db.dirty
        finally:
            _remove_hkex(test_db, D_HK_ONLY, D_HK_ONLY_2)

    def test_t5_ex_sse_shut_pending_gates_then_manual_confirm(self, test_db):
        """除息日落 SSE 休市/HKEX 开市：auto-confirm 序列外 → pending 闸门阻断下一
        SSE 快照日；人工确认后闸门翻转、快照恢复生成（「人工一步」闭环）。"""
        create_portfolio(test_db, code="HK784_A", status="active")
        create_product(test_db, code="HK784_AF", market=HK, product_type="OEF", nav_lag_days=0)
        # ENT = 最近 SSE 开市日；EX = ENT 之后第一个 SSE 休市日；
        # D = EX 之后第一个 SSE 开市日（下一快照日）
        sse_days = (
            test_db.query(TradingCalendar)
            .filter(
                TradingCalendar.exchange == "SSE",
                TradingCalendar.calendar_date > business_today() - timedelta(days=10),
            )
            .order_by(TradingCalendar.calendar_date)
            .limit(30)
            .all()
        )
        ent = next(r.calendar_date for r in sse_days if r.is_open)
        ex = next(r.calendar_date for r in sse_days if not r.is_open and r.calendar_date > ent)
        target = next(r.calendar_date for r in sse_days if r.is_open and r.calendar_date > ex)
        try:
            ensure_trading_day(test_db, ent, is_open=True, exchange="HKEX")
            ensure_trading_day(test_db, ex, is_open=True, exchange="HKEX")
            ensure_trading_day(test_db, target, is_open=True, exchange="HKEX")

            event = service.create_share_change_event(
                test_db,
                portfolio_code="HK784_A", product_code="HK784_AF", market=HK,
                platform_code=SEED_PLAT, event_type="cash_dividend",
                entitlement_date=ent, ex_date=ex, div_cash=Decimal("0.1"),
            )
            test_db.commit()

            # ENT 日基线（手法同 dividend_method 的 BASE：手工持仓/市值/投资人行，
            # generate 逐日推进时以行情严格取价）
            create_position_snapshot(
                test_db, "HK784_A", "HK784_AF", HK, snapshot_date=ent, platform_code=SEED_PLAT,
                shares=1000, unit_price=2.1, cost_price=2.1, market_value=2100,
            )
            create_position_snapshot(
                test_db, "HK784_A", "CASH", "", snapshot_date=ent, platform_code=SEED_PLAT,
                cash_amount=0, market_value=0,
            )
            create_value_snapshot(test_db, "HK784_A", ent, total_value=2100, total_shares=1000, unit_price=2.1)
            create_investor_holding(test_db, "HK784_A", "VIEWER", ent, shares=1000)

            # ① 确认前：pending 闸门按 share_effective_date(=ex) <= target 阻断
            check = _check_share_change_events(test_db, "HK784_A", target)
            assert check["status"] == "failed"
            assert check["check_type"] == "share_change_events"

            # ② 人工确认可走通（权益登记日快照存在，确认不重跑日历校验）
            service.confirm_share_change_event(test_db, event)
            test_db.commit()
            assert event.status == "confirmed"

            # ③ 闸门翻转 + 快照恢复生成（份额/现金按范围窗落入 target 日）。
            # ent 与 target 之间的 SSE 交易日须逐日生成（连续性校验，#182）。
            check = _check_share_change_events(test_db, "HK784_A", target)
            assert check["status"] == "passed"
            for d in [r.calendar_date for r in sse_days
                      if r.is_open and ent < r.calendar_date <= target]:
                create_price_record(test_db, "HK784_AF", HK, d, 2.1)
                generate_daily_snapshots(test_db, "HK784_A", d)
            assert test_db.query(PortfolioValueSnapshot).filter_by(
                portfolio_code="HK784_A", snapshot_date=target,
            ).one() is not None
        finally:
            _remove_hkex(test_db, ent, target)
