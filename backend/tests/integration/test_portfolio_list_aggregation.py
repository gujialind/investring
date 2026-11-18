# ============================================================================
# 集成测试：列表聚合读侧按 kind 分流（#905 PR3 B，R8 仪表盘跨口径合并）
# ============================================================================
# 三条硬条件（编排侧 2026-11-18 定案）的可测形态：
#   条件1 nav 逐字不变 + 列表 total_profit 与详情 _derive_portfolio_totals 同源；
#   条件2 ledger 缺失值显式定义（draft→None；active/closed 无记录→0，SPEC §5）；
#   条件3 台账批量聚合与逐组合同源，且查询次数不随台账组合数 N 增长（非 N+1）。
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event

from tests.factories import (
    create_portfolio, create_investor, create_platform, create_subscription,
    create_value_snapshot, create_investor_holding,
)
from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.services.portfolio_service import list_portfolios
from app.services.ledger_profit_service import (
    compute_ledger_totals, compute_ledger_totals_batch,
)


def _entry(db, code, platform, d, mv):
    db.add(LedgerEntry(portfolio_code=code, platform_code=platform, value_date=d,
                       market_value=Decimal(mv), asset_kind="non_cash"))
    db.flush()


def _flow(db, code, platform, d, direction, amt):
    db.add(LedgerFlow(portfolio_code=code, platform_code=platform, flow_date=d,
                      direction=direction, amount=Decimal(amt)))
    db.flush()


def _item(db, code):
    res = list_portfolios(db, page_size=100)
    return next(i for i in res["items"] if i["code"] == code)


class TestNavListUnchanged:
    """条件1：nav 列表 total_value/cumulative_return/investor_count 逐字不变，total_profit 与详情同源。"""

    def test_nav_list_values_and_profit_same_source_as_detail(self, test_db, client, admin_headers):
        create_investor(test_db, code="INV_N")
        create_platform(test_db, code="PLAT_N")
        create_portfolio(test_db, code="NAV_L", status="active")
        create_subscription(test_db, "NAV_L", "INV_N", sub_type="subscribe", amount=10000.0,
                            confirm_date=date(2026, 1, 5), status="confirmed", platform_code="PLAT_N")
        create_value_snapshot(test_db, "NAV_L", date(2026, 1, 5), 10000.0, 10000.0, 1.0)
        create_value_snapshot(test_db, "NAV_L", date(2026, 1, 10), 12000.0, 10000.0, 1.2)
        create_investor_holding(test_db, "NAV_L", "INV_N", date(2026, 1, 10), shares=10000.0)

        item = _item(test_db, "NAV_L")
        # 改动前口径：total_value=最新快照、cumulative_return=首末净值百分数、investor_count=最新日份额>0 人数
        assert item["total_value"] == 12000.0
        assert item["cumulative_return"] == 20.0  # (1.2-1.0)/1.0*100
        assert item["investor_count"] == 1
        # PR3 新增 total_profit，与详情同源（最新市值 − confirmed 净投入）
        assert item["total_profit"] == 2000.0  # 12000 − 10000
        detail = client.get("/api/portfolios/NAV_L", headers=admin_headers).json()
        assert item["total_value"] == detail["total_value"]
        assert item["total_profit"] == detail["total_profit"]

    def test_nav_no_snapshot_none(self, test_db):
        create_portfolio(test_db, code="NAV_EMPTY", status="draft")
        item = _item(test_db, "NAV_EMPTY")
        assert item["total_value"] is None
        assert item["total_profit"] is None
        assert item["cumulative_return"] is None
        assert item["investor_count"] == 0


class TestLedgerListSemantics:
    """条件2：ledger 缺失值显式定义，与详情 compute_ledger_totals 同源。"""

    def test_ledger_draft_none(self, test_db):
        create_investor(test_db, code="INV_LD")
        create_portfolio(test_db, code="LED_D", kind="ledger",
                         owner_investor_code="INV_LD", status="draft")
        item = _item(test_db, "LED_D")
        assert item["total_value"] is None
        assert item["total_profit"] is None
        assert item["cumulative_return"] is None
        assert item["investor_count"] == 0

    def test_ledger_active_no_records_zero(self, test_db):
        create_investor(test_db, code="INV_LA")
        create_portfolio(test_db, code="LED_A0", kind="ledger",
                         owner_investor_code="INV_LA", status="active")
        item = _item(test_db, "LED_A0")
        # SPEC §5：active 无记录 → 市值0收益0（已启用尚未记录资产，区别于 nav 无快照 None）
        assert item["total_value"] == 0.0
        assert item["total_profit"] == 0.0
        assert item["cumulative_return"] is None
        assert item["investor_count"] == 0

    def test_ledger_active_with_records_matches_detail(self, test_db, client, admin_headers):
        create_investor(test_db, code="INV_LR")
        create_platform(test_db, code="PLAT_R")
        create_portfolio(test_db, code="LED_R", kind="ledger",
                         owner_investor_code="INV_LR", status="active")
        _flow(test_db, "LED_R", "PLAT_R", date(2026, 1, 5), "inflow", "100")
        _entry(test_db, "LED_R", "PLAT_R", date(2026, 1, 5), "100")
        _entry(test_db, "LED_R", "PLAT_R", date(2026, 1, 6), "110")
        item = _item(test_db, "LED_R")
        assert item["total_value"] == 110.0
        assert item["total_profit"] == 10.0  # 110 − 100
        detail = client.get("/api/portfolios/LED_R", headers=admin_headers).json()
        assert item["total_value"] == detail["total_value"]
        assert item["total_profit"] == detail["total_profit"]

    def test_ledger_closed_with_records_computes(self, test_db):
        create_investor(test_db, code="INV_LC")
        create_platform(test_db, code="PLAT_C")
        create_portfolio(test_db, code="LED_C", kind="ledger",
                         owner_investor_code="INV_LC", status="closed")
        _flow(test_db, "LED_C", "PLAT_C", date(2026, 1, 5), "inflow", "100")
        _entry(test_db, "LED_C", "PLAT_C", date(2026, 1, 5), "130")
        item = _item(test_db, "LED_C")
        assert item["total_value"] == 130.0
        assert item["total_profit"] == 30.0


class TestBatchSameSourceAndQueryCount:
    """条件1+3：批量与逐组合同源；台账查询次数不随 N 增长（非 N+1）。"""

    def _build_three(self, db):
        create_investor(db, code="INV_B")
        create_platform(db, code="PLAT_B1")
        create_platform(db, code="PLAT_B2")
        for code in ("LED_B1", "LED_B2", "LED_B3"):
            create_portfolio(db, code=code, kind="ledger",
                             owner_investor_code="INV_B", status="active")
        # 多平台 + 多日 + 进出，覆盖 step-forward 与净入金
        _entry(db, "LED_B1", "PLAT_B1", date(2026, 1, 5), "100")
        _entry(db, "LED_B1", "PLAT_B1", date(2026, 1, 10), "200")
        _entry(db, "LED_B1", "PLAT_B2", date(2026, 1, 8), "50")
        _flow(db, "LED_B1", "PLAT_B1", date(2026, 1, 5), "inflow", "150")
        _entry(db, "LED_B2", "PLAT_B1", date(2026, 1, 6), "80")
        _flow(db, "LED_B2", "PLAT_B1", date(2026, 1, 6), "inflow", "100")
        _flow(db, "LED_B2", "PLAT_B1", date(2026, 1, 7), "outflow", "30")
        # LED_B3 无记录 → 0

    def test_batch_equals_per_portfolio(self, test_db):
        self._build_three(test_db)
        codes = ["LED_B1", "LED_B2", "LED_B3"]
        D = date(2026, 1, 10)
        batch = compute_ledger_totals_batch(test_db, codes, as_of_date=D)
        for code in codes:
            single = compute_ledger_totals(test_db, code, as_of_date=D)
            assert batch[code]["total_value"] == single["total_value"], code
            assert batch[code]["total_profit"] == single["total_profit"], code

    def test_ledger_queries_do_not_scale_with_n(self, test_db):
        self._build_three(test_db)
        engine = test_db.get_bind()
        counts = {"ledger_entry": 0, "ledger_flow": 0}

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if "ledger_entry" in statement:
                counts["ledger_entry"] += 1
            if "ledger_flow" in statement:
                counts["ledger_flow"] += 1

        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            list_portfolios(test_db, page_size=100)
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)
        # 3 个台账组合，市值/进出各只查 1 次（批量），不随 N 增长
        assert counts["ledger_entry"] == 1, counts
        assert counts["ledger_flow"] == 1, counts