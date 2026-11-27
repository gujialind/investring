"""仪表盘汇总端点集成测试（#944）。"""
from datetime import date, timedelta
from decimal import Decimal
import re

import pytest
from sqlalchemy import event

from app.models.ledger_entry import LedgerEntry
from app.models.ledger_flow import LedgerFlow
from app.models.subscription import Subscription
from app.services import dashboard_service
from app.services import ledger_profit_service, performance_service
from tests.factories import (
    create_investor,
    create_platform,
    create_portfolio,
    create_subscription,
    create_trade,
    create_value_snapshot,
)

TOP_KEYS = {
    "total_value", "total_profit", "aggregate_xirr",
    "pending_subscription_count", "pending_trade_count", "portfolios",
}
AGGREGATE_KEYS = {"value", "reliable", "reason", "cash_flow_count", "holding_days"}
ROW_KEYS = {
    "code", "name", "kind", "total_value", "total_profit", "cumulative_return",
    "mwr", "annualization_reliable", "holding_days",
}


def _base(db):
    create_investor(db, code="DASH_INV")
    create_platform(db, code="DASH_PLATFORM")


def _nav(db, code, *, status="active", snapshots=(), subscriptions=()):
    create_portfolio(db, code=code, name=code, status=status)
    for sub_type, amount, when in subscriptions:
        create_subscription(
            db, code, "DASH_INV", sub_type=sub_type, amount=amount,
            apply_date=when, confirm_date=when, status="confirmed",
            platform_code="DASH_PLATFORM",
        )
    for when, total_value, unit_price in snapshots:
        create_value_snapshot(
            db, code, when, total_value=total_value,
            total_shares=1000.0, unit_price=unit_price,
        )


def _ledger(db, code, *, status="active", entries=(), flows=()):
    create_portfolio(
        db, code=code, name=code, status=status, kind="ledger",
        owner_investor_code="DASH_INV",
    )
    for when, market_value in entries:
        db.add(LedgerEntry(
            portfolio_code=code, platform_code="DASH_PLATFORM", value_date=when,
            market_value=Decimal(str(market_value)), asset_kind="non_cash",
        ))
    for when, direction, amount in flows:
        db.add(LedgerFlow(
            portfolio_code=code, platform_code="DASH_PLATFORM", flow_date=when,
            direction=direction, amount=Decimal(str(amount)),
        ))
    db.flush()


def _by_code(payload):
    return {row["code"]: row for row in payload["portfolios"]}


class TestDashboardAuthAndShape:
    def test_empty_active_set_excludes_draft_and_closed(self, client, viewer_headers, test_db):
        _base(test_db)
        start = date(2025, 1, 1)
        end = date(2026, 1, 1)
        for code, status in (
            ("DASH_DRAFT_NAV", "draft"), ("DASH_CLOSED_NAV", "closed"),
        ):
            _nav(
                test_db, code, status=status,
                snapshots=[(start, 1000.0, 1.0), (end, 9999.0, 9.999)],
                subscriptions=[("subscribe", 1000.0, start)],
            )
        for code, status in (
            ("DASH_DRAFT_LEDGER", "draft"), ("DASH_CLOSED_LEDGER", "closed"),
        ):
            _ledger(
                test_db, code, status=status,
                entries=[(start, 1000.0), (end, 9999.0)],
                flows=[(start, "inflow", 1000.0)],
            )

        response = client.get("/api/dashboard/summary", headers=viewer_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == TOP_KEYS
        assert payload["total_value"] == 0.0
        assert payload["total_profit"] == 0.0
        assert payload["portfolios"] == []
        assert payload["aggregate_xirr"] == {
            "value": None,
            "reliable": False,
            "reason": "insufficient_flows",
            "cash_flow_count": 0,
            "holding_days": None,
        }
        assert payload["pending_subscription_count"] == 0
        assert payload["pending_trade_count"] == 0

    def test_requires_auth_and_returns_exact_shape(self, client, admin_headers, test_db):
        _base(test_db)
        _nav(test_db, "DASH_ACTIVE")
        assert client.get("/api/dashboard/summary").status_code == 401

        response = client.get("/api/dashboard/summary", headers=admin_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == TOP_KEYS
        assert set(payload["aggregate_xirr"]) == AGGREGATE_KEYS
        assert len(payload["portfolios"]) == 1
        assert set(payload["portfolios"][0]) == ROW_KEYS

    def test_pending_counts_and_endpoint_is_read_only(self, client, viewer_headers, test_db, monkeypatch):
        _base(test_db)
        _nav(test_db, "DASH_PENDING")
        create_portfolio(test_db, code="DASH_PENDING_DRAFT", status="draft")
        when = date(2026, 1, 5)
        create_subscription(
            test_db, "DASH_PENDING", "DASH_INV", amount=100.0,
            apply_date=when, confirm_date=None, status="pending",
            platform_code="DASH_PLATFORM",
        )
        create_subscription(
            test_db, "DASH_PENDING_DRAFT", "DASH_INV", amount=200.0,
            apply_date=when, confirm_date=None, status="pending",
            platform_code="DASH_PLATFORM",
        )
        create_subscription(
            test_db, "DASH_PENDING", "DASH_INV", amount=300.0,
            apply_date=when, confirm_date=when, status="confirmed",
            platform_code="DASH_PLATFORM",
        )
        create_trade(
            test_db, "DASH_PENDING", "CASH", "", trade_type="buy", amount=100.0,
            price=None, platform_code="DASH_PLATFORM", trade_date=when, status="pending",
        )
        create_trade(
            test_db, "DASH_PENDING", "CASH", "", trade_type="buy", amount=200.0,
            price=None, platform_code="DASH_PLATFORM", trade_date=when, status="confirmed",
        )
        payload = client.get("/api/dashboard/summary", headers=viewer_headers).json()
        # 与现有仪表盘一致：待处理数量取全量待处理行，不受 active 组合集合裁剪。
        assert payload["pending_subscription_count"] == 2
        assert payload["pending_trade_count"] == 1

        def fail_transaction_boundary(*_args, **_kwargs):
            raise AssertionError("只读端点不得 commit/rollback")

        monkeypatch.setattr(test_db, "commit", fail_transaction_boundary)
        monkeypatch.setattr(test_db, "rollback", fail_transaction_boundary)
        response = client.get("/api/dashboard/summary", headers=viewer_headers)
        assert response.status_code == 200, response.text


class TestDashboardAggregation:
    def test_nav_and_ledger_totals_mwr_and_holding_days(
        self, client, admin_headers, test_db, monkeypatch
    ):
        _base(test_db)
        D = date(2026, 10, 1)
        start = date(2026, 1, 1)
        middle = date(2026, 7, 1)
        ledger_latest = date(2026, 9, 1)
        monkeypatch.setattr(dashboard_service, "business_today", lambda: D)
        monkeypatch.setattr(ledger_profit_service, "business_today", lambda: D)

        _nav(
            test_db, "DASH_NAV",
            snapshots=[(start, 1000.0, 1.0), (middle, 1200.0, 1.2), (D, 1300.0, 1.3)],
            subscriptions=[("subscribe", 1000.0, start), ("redeem", 200.0, middle)],
        )
        _nav(test_db, "DASH_NAV_EMPTY")
        _ledger(
            test_db, "DASH_LEDGER",
            entries=[(start, 1000.0), (middle, 1200.0), (ledger_latest, 1250.0)],
            flows=[(start, "inflow", 1000.0), (middle, "outflow", 200.0)],
        )
        _ledger(test_db, "DASH_LEDGER_EMPTY")

        response = client.get("/api/dashboard/summary", headers=admin_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        rows = _by_code(payload)
        assert set(rows) == {"DASH_NAV", "DASH_NAV_EMPTY", "DASH_LEDGER", "DASH_LEDGER_EMPTY"}
        assert all(set(row) == ROW_KEYS for row in rows.values())
        assert payload["total_value"] == 2550.0  # nav 1300 + ledger step-forward 1250
        assert payload["total_profit"] == 950.0   # nav 500 + ledger 450

        nav = rows["DASH_NAV"]
        assert nav["total_value"] == 1300.0
        assert nav["total_profit"] == 500.0
        assert nav["cumulative_return"] == 30.0
        assert nav["holding_days"] == (D - start).days
        assert nav["annualization_reliable"] is True
        subs = test_db.query(Subscription).filter_by(portfolio_code="DASH_NAV").all()
        assert nav["mwr"] == performance_service.compute_mwr(subs, D, 1300.0)["mwr"]

        ledger = rows["DASH_LEDGER"]
        assert ledger["total_value"] == 1250.0
        assert ledger["total_profit"] == 450.0
        assert ledger["cumulative_return"] is None
        assert ledger["holding_days"] == (ledger_latest - start).days
        assert ledger["annualization_reliable"] is True
        assert ledger["mwr"] == ledger_profit_service.compute_ledger_performance(
            test_db, "DASH_LEDGER"
        )["mwr"]

        for code in ("DASH_NAV_EMPTY", "DASH_LEDGER_EMPTY"):
            assert rows[code]["mwr"] is None
            assert rows[code]["annualization_reliable"] is False
        assert rows["DASH_NAV_EMPTY"]["total_value"] is None
        assert rows["DASH_NAV_EMPTY"]["total_profit"] is None
        assert rows["DASH_NAV_EMPTY"]["holding_days"] is None
        assert rows["DASH_LEDGER_EMPTY"]["total_value"] == 0.0
        assert rows["DASH_LEDGER_EMPTY"]["total_profit"] == 0.0
        assert rows["DASH_LEDGER_EMPTY"]["holding_days"] is None

        expected_flows = [
            (start, -1000.0), (middle, 200.0),  # ledger 外部流
            (start, -1000.0), (middle, 200.0),  # nav 外部流
            (D, 1250.0), (D, 1300.0),          # 各组合自己的期末市值
        ]
        expected_rate = performance_service._xirr(expected_flows)
        assert payload["aggregate_xirr"] == {
            "value": round(expected_rate * 100, 4),
            "reliable": True,
            "reason": None,
            "cash_flow_count": 4,
            "holding_days": (D - start).days,
        }

    def test_union_xirr_is_not_average_and_uses_distinct_terminal_dates(
        self, client, admin_headers, test_db
    ):
        _base(test_db)
        first = date(2025, 1, 1)
        second = date(2025, 7, 1)
        end_a = date(2026, 1, 1)
        end_b = date(2026, 7, 1)
        _nav(
            test_db, "DASH_UNION_A",
            snapshots=[(first, 100.0, 1.0), (end_a, 110.0, 1.1)],
            subscriptions=[("subscribe", 100.0, first)],
        )
        _nav(
            test_db, "DASH_UNION_B",
            snapshots=[(second, 900.0, 1.0), (end_b, 1170.0, 1.3)],
            subscriptions=[("subscribe", 900.0, second)],
        )

        payload = client.get("/api/dashboard/summary", headers=admin_headers).json()
        rows = _by_code(payload)
        assert rows["DASH_UNION_A"]["mwr"] == pytest.approx(10.0, abs=0.01)
        assert rows["DASH_UNION_B"]["mwr"] == pytest.approx(30.0, abs=0.01)
        average = (rows["DASH_UNION_A"]["mwr"] + rows["DASH_UNION_B"]["mwr"]) / 2
        expected_flows = [
            (first, -100.0), (second, -900.0),
            (end_a, 110.0), (end_b, 1170.0),
        ]
        expected = round(performance_service._xirr(expected_flows) * 100, 4)
        assert payload["aggregate_xirr"]["value"] == expected
        assert payload["aggregate_xirr"]["value"] != pytest.approx(average, abs=0.01)
        assert payload["aggregate_xirr"]["cash_flow_count"] == 2
        assert payload["aggregate_xirr"]["holding_days"] == (end_b - first).days

    @pytest.mark.parametrize("days,reliable,reason", [
        (89, False, "short_history"),
        (90, True, None),
    ])
    def test_aggregate_ninety_day_boundary(
        self, client, admin_headers, test_db, days, reliable, reason
    ):
        _base(test_db)
        start = date(2025, 1, 1)
        end = start + timedelta(days=days)
        _nav(
            test_db, "DASH_BOUNDARY",
            snapshots=[(start, 1000.0, 1.0), (end, 1100.0, 1.1)],
            subscriptions=[("subscribe", 1000.0, start)],
        )
        payload = client.get("/api/dashboard/summary", headers=admin_headers).json()
        aggregate = payload["aggregate_xirr"]
        assert aggregate["value"] is not None
        assert aggregate["reliable"] is reliable
        assert aggregate["reason"] == reason
        assert aggregate["holding_days"] == days
        assert _by_code(payload)["DASH_BOUNDARY"]["annualization_reliable"] is reliable

    def test_active_portfolios_without_external_flows_are_insufficient(
        self, client, admin_headers, test_db, monkeypatch
    ):
        _base(test_db)
        D = date(2026, 10, 1)
        monkeypatch.setattr(dashboard_service, "business_today", lambda: D)
        _nav(
            test_db, "DASH_NO_FLOW_NAV",
            snapshots=[(date(2026, 1, 1), 1000.0, 1.0)],
        )
        _ledger(
            test_db, "DASH_NO_FLOW_LEDGER",
            entries=[(date(2026, 1, 1), 2000.0)],
        )
        payload = client.get("/api/dashboard/summary", headers=admin_headers).json()
        assert payload["aggregate_xirr"] == {
            "value": None,
            "reliable": False,
            "reason": "insufficient_flows",
            "cash_flow_count": 0,
            "holding_days": (D - date(2026, 1, 1)).days,
        }
        assert all(row["mwr"] is None for row in payload["portfolios"])

    @pytest.mark.parametrize("kind,terminal", [
        ("nav", "missing"),
        ("nav", "zero"),
        ("ledger", "zero"),
    ])
    def test_external_flows_without_positive_terminal_make_aggregate_insufficient(
        self, client, admin_headers, test_db, monkeypatch, kind, terminal
    ):
        _base(test_db)
        D = date(2026, 10, 1)
        start = date(2026, 1, 1)
        middle = date(2026, 7, 1)
        monkeypatch.setattr(dashboard_service, "business_today", lambda: D)
        monkeypatch.setattr(ledger_profit_service, "business_today", lambda: D)
        _nav(
            test_db, "DASH_VALUED",
            snapshots=[(start, 1000.0, 1.0), (D, 1100.0, 1.1)],
            subscriptions=[("subscribe", 1000.0, start)],
        )
        if kind == "nav":
            _nav(
                test_db, "DASH_UNVALUED",
                snapshots=[] if terminal == "missing" else [(middle, 0.0, 0.0)],
                subscriptions=[("subscribe", 100.0, start), ("redeem", 150.0, middle)],
            )
        else:
            _ledger(
                test_db, "DASH_UNVALUED",
                flows=[(start, "inflow", 100.0), (middle, "outflow", 150.0)],
            )

        calls = []
        solve = performance_service._xirr

        def capture(flows):
            calls.append(list(flows))
            return solve(flows)

        monkeypatch.setattr(performance_service, "_xirr", capture)
        response = client.get("/api/dashboard/summary", headers=admin_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["aggregate_xirr"] == {
            "value": None,
            "reliable": False,
            "reason": "insufficient_flows",
            "cash_flow_count": 3,
            "holding_days": (D - start).days,
        }
        unvalued = _by_code(payload)["DASH_UNVALUED"]
        assert unvalued["mwr"] is None
        assert unvalued["total_value"] == (
            None if kind == "nav" and terminal == "missing" else 0.0
        )
        assert len(calls) == 1
        assert (start, -100.0) not in calls[0]
        assert (middle, 150.0) not in calls[0]

    def test_external_flows_without_solution_report_no_solution(
        self, client, admin_headers, test_db
    ):
        _base(test_db)
        start = date(2025, 1, 1)
        end = date(2026, 1, 1)
        _nav(
            test_db, "DASH_NO_SOLUTION",
            snapshots=[(start, 1000.0, 1.0), (end, 1100.0, 1.1)],
            subscriptions=[("redeem", 1000.0, start)],
        )
        payload = client.get("/api/dashboard/summary", headers=admin_headers).json()
        assert payload["aggregate_xirr"] == {
            "value": None,
            "reliable": False,
            "reason": "no_solution",
            "cash_flow_count": 1,
            "holding_days": 365,
        }
        assert _by_code(payload)["DASH_NO_SOLUTION"]["mwr"] is None


class TestDashboardQueryCount:
    def test_query_count_does_not_grow_with_portfolio_count(
        self, test_db, monkeypatch
    ):
        _base(test_db)
        monkeypatch.setattr(dashboard_service, "business_today", lambda: date(2026, 10, 1))

        def fail_per_portfolio(*_args, **_kwargs):
            raise AssertionError("仪表盘不得调用逐组合绩效入口")

        monkeypatch.setattr(performance_service, "get_performance", fail_per_portfolio)
        monkeypatch.setattr(
            ledger_profit_service, "compute_ledger_performance", fail_per_portfolio
        )

        def build(prefix, count):
            start = date(2026, 1, 1)
            end = date(2026, 7, 1)
            for index in range(count):
                nav_code = f"{prefix}_NAV{index}"
                _nav(
                    test_db, nav_code,
                    snapshots=[(start, 1000.0, 1.0), (end, 1100.0, 1.1)],
                    subscriptions=[("subscribe", 1000.0, start)],
                )
                _ledger(
                    test_db, f"{prefix}_LEDGER{index}",
                    entries=[(start, 2000.0), (end, 2200.0)],
                    flows=[(start, "inflow", 2000.0)],
                )

        def capture_selects():
            statements = []

            def before_cursor_execute(_conn, _cursor, statement, _params, _context, _executemany):
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)

            engine = test_db.get_bind()
            event.listen(engine, "before_cursor_execute", before_cursor_execute)
            try:
                dashboard_service.get_dashboard_summary(test_db)
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor_execute)
            return statements

        build("Q1", 1)
        one_portfolio_kind_pair = capture_selects()
        build("Q3", 2)
        three_portfolio_kind_pairs = capture_selects()

        assert len(three_portfolio_kind_pairs) == len(one_portfolio_kind_pair)
        expected_table_counts = {
            "portfolio": 2,                 # active 集合 + 台账批量状态
            "portfolio_value_snapshot": 2,  # latest + first
            "investor_holding": 1,
            "subscription": 3,              # 列表净投入 + MWR + pending 计数
            "ledger_entry": 2,              # 批量市值 + 首末日期
            "ledger_flow": 2,               # 批量净入金 + MWR
            "trade": 1,
        }
        for table, expected in expected_table_counts.items():
            pattern = rf"\b{re.escape(table)}\b"
            actual = sum(
                bool(re.search(pattern, statement.lower()))
                for statement in three_portfolio_kind_pairs
            )
            assert actual == expected, (table, actual, expected, three_portfolio_kind_pairs)
