"""compute_deadline (UT-BE-08) and X-Request-Received-At parsing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from solver.deadline import compute_deadline, parse_received_at

T0 = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)


def test_ut_be_08a_defaults():
    assert compute_deadline(T0, T0 + timedelta(milliseconds=2000)) == 5500


def test_ut_be_08b_custom_budget_and_margin():
    assert compute_deadline(T0, T0 + timedelta(milliseconds=1000), budget_ms=6000, response_margin_ms=300) == 4700


def test_budget_already_spent_is_not_positive():
    assert compute_deadline(T0, T0 + timedelta(seconds=9)) <= 0


def test_parse_received_at():
    assert parse_received_at("2026-10-26T05:00:00Z") == T0
    assert parse_received_at("2026-10-26T12:00:00+07:00") == T0
    assert parse_received_at("2026-10-26T05:00:00") == T0  # no zone: UTC
    assert parse_received_at("kemarin") is None and parse_received_at(None) is None
