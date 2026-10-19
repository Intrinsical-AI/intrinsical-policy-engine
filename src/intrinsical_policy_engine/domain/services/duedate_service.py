# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Validated dates resolved against an explicit base, with month-end clamping."""

from __future__ import annotations

import calendar as month_calendar
import re
from datetime import date, timedelta

from intrinsical_policy_engine.domain.contract_models import (
    CalendarContract,
    DueRulesContract,
    canonical_date,
)

RELATIVE_DATE_PATTERN = re.compile(r"^T\+(\d+)([md])$")


def resolve_calendar_date(value: str, base_date: date | None) -> date:
    value = canonical_date(value, relative=True)
    match = RELATIVE_DATE_PATTERN.fullmatch(value)
    if match is None:
        return date.fromisoformat(value)
    if base_date is None:
        raise ValueError("relative calendar dates require an explicit base_date")
    amount = int(match.group(1))
    if match.group(2) == "d":
        if amount > (date.max - base_date).days:
            raise ValueError(
                f"relative calendar date exceeds 9999-12-31 for base_date={base_date.isoformat()}"
            )
        return base_date + timedelta(days=amount)
    remaining_months = (date.max.year - base_date.year) * 12 + date.max.month - base_date.month
    if amount > remaining_months:
        raise ValueError(
            f"relative calendar date exceeds 9999-12-31 for base_date={base_date.isoformat()}"
        )
    offset = base_date.year * 12 + base_date.month - 1 + amount
    year, month = divmod(offset, 12)
    month += 1
    day = min(base_date.day, month_calendar.monthrange(year, month)[1])
    return date(year, month, day)


def resolve_calendar(calendar: CalendarContract, base_date: date | None = None) -> dict[str, str]:
    base = base_date or (date.fromisoformat(calendar.base_date) if calendar.base_date else None)
    resolved: dict[str, str] = {}
    for key, value in calendar.calendar.items():
        try:
            resolved[key] = resolve_calendar_date(value, base).isoformat()
        except ValueError as exc:
            raise ValueError(f"calendar.calendar.{key}: {exc}") from exc
    return resolved


def apply_due_hints(
    action_ids: list[str], due_rules: DueRulesContract, calendar: dict[str, str]
) -> dict[str, str]:
    hints: dict[str, str] = {}
    for rule in sorted(due_rules.rules, key=lambda item: -item.priority):
        deadline = min(calendar[key] for key in rule.calendar_keys)
        for action_id in action_ids:
            if action_id not in hints and (
                action_id in rule.ids
                or any(action_id.startswith(prefix) for prefix in rule.prefixes)
            ):
                hints[action_id] = deadline
    return hints
