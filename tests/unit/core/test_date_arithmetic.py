# SPDX-License-Identifier: MPL-2.0
"""Framework-neutral contracts for relative-date parsing and arithmetic."""

from __future__ import annotations

from datetime import date

import pytest

from intrinsical_policy_engine.domain.services.date_arithmetic import (
    RelativeDateExpr,
    compute_date_offset,
    parse_relative_date,
)


@pytest.mark.parametrize(
    "expression, base_field, operator, amount, unit",
    [
        ("start_date + 15 days", "start_date", "+", 15, "days"),
        ("deadline - 7 days", "deadline", "-", 7, "days"),
        ("start_date + 2 weeks", "start_date", "+", 2, "weeks"),
        ("effective_date + 6 months", "effective_date", "+", 6, "months"),
        ("launch + 1 year", "launch", "+", 1, "years"),
        ("date + 1 day", "date", "+", 1, "days"),
    ],
)
def test_parse_relative_date(
    expression: str,
    base_field: str,
    operator: str,
    amount: int,
    unit: str,
) -> None:
    result = parse_relative_date(expression)

    assert result is not None
    assert result.base_field == base_field
    assert result.operator == operator
    assert result.amount == amount
    assert result.unit == unit


def test_parse_relative_date_tolerates_extra_whitespace() -> None:
    result = parse_relative_date("  base   +   10   days  ")

    assert result is not None
    assert result.amount == 10


@pytest.mark.parametrize("expression", ["invalid", "+ 5 days", "base + days", "base + 5"])
def test_invalid_relative_date_returns_none(expression: str) -> None:
    assert parse_relative_date(expression) is None


@pytest.mark.parametrize(
    "expression, base, expected",
    [
        (RelativeDateExpr("start", "+", 15, "days"), date(2025, 1, 1), date(2025, 1, 16)),
        (RelativeDateExpr("end", "-", 7, "days"), date(2025, 1, 15), date(2025, 1, 8)),
        (RelativeDateExpr("start", "+", 10, "days"), "2025-03-01", date(2025, 3, 11)),
        (RelativeDateExpr("start", "+", 2, "weeks"), date(2025, 1, 1), date(2025, 1, 15)),
    ],
)
def test_relative_date_expression_compute(
    expression: RelativeDateExpr,
    base: date | str,
    expected: date,
) -> None:
    assert expression.compute(base) == expected


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"days": 10}, date(2025, 1, 11)),
        ({"weeks": 2}, date(2025, 1, 15)),
        ({"months": 2}, date(2025, 3, 2)),
        ({"years": 1}, date(2026, 1, 1)),
        ({"days": -7}, date(2024, 12, 25)),
        ({"days": 5, "weeks": 1}, date(2025, 1, 13)),
    ],
)
def test_compute_date_offset(kwargs: dict[str, int], expected: date) -> None:
    assert compute_date_offset(date(2025, 1, 1), **kwargs) == expected


def test_compute_date_offset_accepts_iso_string() -> None:
    assert compute_date_offset("2025-01-01", days=10) == date(2025, 1, 11)


def test_date_offset_crosses_year_boundary() -> None:
    assert compute_date_offset(date(2025, 12, 25), days=15) == date(2026, 1, 9)
