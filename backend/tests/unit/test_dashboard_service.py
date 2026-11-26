"""仪表盘汇总纯函数单测（#944）。"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import performance_service
from app.services.dashboard_service import _aggregate_xirr
from app.services.ledger_profit_service import ledger_xirr_flows
from app.services.performance_service import subscription_flows


def _sub(**overrides):
    values = {
        "status": "confirmed",
        "sub_type": "subscribe",
        "amount": Decimal("100.00"),
        "confirm_date": date(2025, 1, 1),
        "apply_date": date(2025, 1, 1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ledger_flow(when, direction, amount):
    return SimpleNamespace(flow_date=when, direction=direction, amount=Decimal(amount))


class TestSubscriptionFlows:
    def test_extracts_confirmed_flows_with_confirm_date_and_apply_fallback(self):
        rows = [
            _sub(),
            _sub(sub_type="redeem", amount="200.00", confirm_date=date(2025, 7, 1)),
            _sub(amount="50.00", confirm_date=None, apply_date=date(2025, 3, 1)),
        ]
        assert subscription_flows(rows) == [
            (date(2025, 1, 1), -100.0),
            (date(2025, 7, 1), 200.0),
            (date(2025, 3, 1), -50.0),
        ]

    @pytest.mark.parametrize("overrides", [
        {"status": "pending"},
        {"status": "cancelled"},
        {"amount": None},
        {"amount": "0"},
        {"amount": "-1"},
        {"confirm_date": None, "apply_date": None},
        {"sub_type": "unknown"},
    ])
    def test_invalid_rows_are_excluded(self, overrides):
        assert subscription_flows([_sub(**overrides)]) == []


class TestLedgerXirrFlows:
    def test_direction_signs_and_positive_terminal(self):
        rows = [
            _ledger_flow(date(2025, 1, 1), "inflow", "1000.00"),
            _ledger_flow(date(2025, 7, 1), "outflow", "200.00"),
        ]
        assert ledger_xirr_flows(rows, date(2026, 1, 1), 1300) == [
            (date(2025, 1, 1), -1000.0),
            (date(2025, 7, 1), 200.0),
            (date(2026, 1, 1), 1300.0),
        ]

    @pytest.mark.parametrize("end_value", [0, -1])
    def test_non_positive_terminal_is_not_appended(self, end_value):
        rows = [_ledger_flow(date(2025, 1, 1), "inflow", "1000.00")]
        assert ledger_xirr_flows(rows, date(2026, 1, 1), end_value) == [
            (date(2025, 1, 1), -1000.0),
        ]

    def test_helper_only_builds_flows_and_does_not_solve(self):
        assert ledger_xirr_flows([], date(2026, 1, 1), 100) == [
            (date(2026, 1, 1), 100.0),
        ]


class TestAggregateXirr:
    def test_no_external_flows_is_insufficient_even_with_terminal(self, monkeypatch):
        def fail(_flows):
            raise AssertionError("无外部现金流时不得求解 XIRR")

        monkeypatch.setattr(performance_service, "_xirr", fail)
        assert _aggregate_xirr([], [(date(2026, 1, 1), 100.0)]) == {
            "value": None,
            "reliable": False,
            "reason": "insufficient_flows",
            "cash_flow_count": 0,
            "holding_days": 0,
        }

    def test_unions_all_external_and_per_portfolio_terminals_once(self, monkeypatch):
        captured = []

        def solve(flows):
            captured.append(list(flows))
            return 0.123456

        monkeypatch.setattr(performance_service, "_xirr", solve)
        external = [
            (date(2025, 1, 1), -100.0),
            (date(2025, 7, 1), -900.0),
        ]
        terminals = [
            (date(2026, 1, 1), 110.0),
            (date(2026, 7, 1), 1170.0),
        ]
        result = _aggregate_xirr(external, terminals)
        assert captured == [[*external, *terminals]]
        assert result == {
            "value": 12.3456,
            "reliable": True,
            "reason": None,
            "cash_flow_count": 2,
            "holding_days": 546,
        }

    @pytest.mark.parametrize("days,reliable,reason", [
        (89, False, "short_history"),
        (90, True, None),
    ])
    def test_ninety_day_boundary_retains_diagnostic_value(
        self, monkeypatch, days, reliable, reason
    ):
        monkeypatch.setattr(performance_service, "_xirr", lambda _flows: 0.1)
        start = date(2025, 1, 1)
        result = _aggregate_xirr(
            [(start, -100.0)], [(start + timedelta(days=days), 110.0)]
        )
        assert result["value"] == 10.0
        assert result["reliable"] is reliable
        assert result["reason"] == reason
        assert result["holding_days"] == days

    def test_no_solution_reports_span_and_external_count(self, monkeypatch):
        monkeypatch.setattr(performance_service, "_xirr", lambda _flows: None)
        result = _aggregate_xirr(
            [(date(2025, 1, 1), -100.0)],
            [(date(2025, 7, 1), 0.0)],
        )
        assert result == {
            "value": None,
            "reliable": False,
            "reason": "no_solution",
            "cash_flow_count": 1,
            "holding_days": 181,
        }
