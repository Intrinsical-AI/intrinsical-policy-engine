# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Date arithmetic for dynamic deadline calculation.

Supports expressions like:
- "awareness_date + 15 days" (Art. 73 general incidents)
- "awareness_date + 2 days" (Art. 73 death incidents)
- "awareness_date + 10 days" (Art. 73 serious harm)

Used by incident management and dynamic due date calculation.

Reference:
- Art. 73(1): 15 days for general incidents
- Art. 73(2): 2 days for death, 10 days for serious harm
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

# Supported time units
TimeUnit = Literal["days", "weeks", "months", "years"]

# Pattern for parsing relative date expressions
# Matches: "base_date + 15 days" or "base_date - 7 days"
_RELATIVE_PATTERN = re.compile(
    r"^\s*(?P<base>\w+)\s*(?P<op>[+-])\s*(?P<amount>\d+)\s*(?P<unit>days?|weeks?|months?|years?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RelativeDateExpr:
    """Parsed relative date expression."""

    base_field: str
    operator: Literal["+", "-"]
    amount: int
    unit: TimeUnit

    def compute(self, base_date: date | str) -> date:
        """Compute the target date from a base date.

        Args:
            base_date: The reference date (date object or ISO string)

        Returns:
            Computed date after applying the offset
        """
        if isinstance(base_date, str):
            base_date = _parse_date(base_date)

        delta = _compute_delta(self.amount, self.unit)
        if self.operator == "-":
            delta = -delta

        return base_date + delta


def parse_relative_date(expr: str) -> RelativeDateExpr | None:
    """Parse a relative date expression.

    Args:
        expr: Expression like "awareness_date + 15 days"

    Returns:
        RelativeDateExpr if valid, None otherwise
    """
    match = _RELATIVE_PATTERN.match(expr)
    if not match:
        return None

    unit_raw = match.group("unit").lower()
    # Normalize singular/plural
    unit: TimeUnit
    if unit_raw.startswith("day"):
        unit = "days"
    elif unit_raw.startswith("week"):
        unit = "weeks"
    elif unit_raw.startswith("month"):
        unit = "months"
    elif unit_raw.startswith("year"):
        unit = "years"
    else:
        return None

    return RelativeDateExpr(
        base_field=match.group("base"),
        operator=match.group("op"),  # type: ignore[arg-type]
        amount=int(match.group("amount")),
        unit=unit,
    )


def compute_incident_deadline(
    awareness_date: date | str,
    incident_type: str,
) -> date:
    """Compute incident reporting deadline per Art. 73.

    Args:
        awareness_date: Date when provider became aware of reportability
        incident_type: One of "death", "serious_harm", "general"

    Returns:
        Due date for reporting

    Reference deadlines (Art. 73):
    - death: 2 days
    - serious_harm_to_health: 10 days
    - general (other incidents): 15 days
    """
    if isinstance(awareness_date, str):
        awareness_date = _parse_date(awareness_date)

    days_map = {
        "death": 2,
        "serious_harm_to_health": 10,
        "serious_harm": 10,  # Alias
        "general": 15,
        "disruption_management_critical_infrastructure": 15,
        "disruption_operation_critical_infrastructure": 15,
        "fundamental_rights_infringement": 15,
        "harm_to_property": 15,
        "harm_to_environment": 15,
        "other_reportable_incident": 15,
    }

    days = days_map.get(incident_type.lower(), 15)
    return awareness_date + timedelta(days=days)


def compute_date_offset(
    base_date: date | str,
    days: int = 0,
    weeks: int = 0,
    months: int = 0,
    years: int = 0,
) -> date:
    """Compute a date offset from a base date.

    Args:
        base_date: Reference date
        days: Days to add (negative to subtract)
        weeks: Weeks to add
        months: Months to add (approximate: 30 days)
        years: Years to add (approximate: 365 days)

    Returns:
        Computed date
    """
    if isinstance(base_date, str):
        base_date = _parse_date(base_date)

    total_days = days + (weeks * 7) + (months * 30) + (years * 365)
    return base_date + timedelta(days=total_days)


def _parse_date(date_str: str) -> date:
    """Parse ISO date string to date object."""
    # Try ISO format first
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {date_str}")


def _compute_delta(amount: int, unit: TimeUnit) -> timedelta:
    """Compute timedelta from amount and unit."""
    if unit == "days":
        return timedelta(days=amount)
    if unit == "weeks":
        return timedelta(weeks=amount)
    if unit == "months":
        return timedelta(days=amount * 30)  # Approximate
    if unit == "years":
        return timedelta(days=amount * 365)  # Approximate
    return timedelta(days=0)
