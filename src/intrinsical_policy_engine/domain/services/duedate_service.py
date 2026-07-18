# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Due date service: applies due date hints and flattens calendar entries.

Supports both absolute dates (ISO 8601) and relative offsets (T+Xm, T+Xd).
Red Team Fix H1: Dates should be computed relative to a base_date to avoid
expiration issues when running the tool after hardcoded dates.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from intrinsical_policy_engine.domain.constants import (
    PRIORITY_FULL_APPLICATION,
    PRIORITY_MODEL_GOVERNANCE,
    PRIORITY_REVIEW,
    PRIORITY_TRANSPARENCY,
)
from intrinsical_policy_engine.domain.contract_models import DueRulesContract, FrameworkRuntime
from intrinsical_policy_engine.domain.types import ActionId

# Regex for relative date offsets: T+24m (months), T+30d (days)
RELATIVE_DATE_PATTERN = re.compile(r"^T\+(\d+)(m|d)$", re.IGNORECASE)


def resolve_calendar_date(value: str, base_date: date) -> date:
    """Resolve a calendar date value, supporting both absolute and relative formats.

    Formats supported:
    - Absolute: "2026-08-02" (ISO 8601)
    - Relative months: "T+24m" (24 months from base_date)
    - Relative days: "T+30d" (30 days from base_date)

    Args:
        value: Date string in absolute or relative format
        base_date: Reference date for computing relative offsets

    Returns:
        Resolved date object

    Raises:
        ValueError: If the format is not recognized

    Examples:
        >>> resolve_calendar_date("2026-08-02", date(2025, 1, 1))
        date(2026, 8, 2)
        >>> resolve_calendar_date("T+24m", date(2025, 1, 1))
        date(2027, 1, 1)
        >>> resolve_calendar_date("T+30d", date(2025, 1, 1))
        date(2025, 1, 31)
    """
    # Try relative format first
    match = RELATIVE_DATE_PATTERN.match(value.strip())
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()

        if unit == "m":
            # Add months: handle year rollover
            new_month = base_date.month + amount
            new_year = base_date.year + (new_month - 1) // 12
            new_month = ((new_month - 1) % 12) + 1
            # Handle day overflow (e.g., Jan 31 + 1 month)
            try:
                return date(new_year, new_month, base_date.day)
            except ValueError:
                # Day doesn't exist in target month, use last day
                if new_month == 12:
                    next_month_year = new_year + 1
                    next_month = 1
                else:
                    next_month_year = new_year
                    next_month = new_month + 1
                return date(next_month_year, next_month, 1) - timedelta(days=1)
        elif unit == "d":
            return base_date + timedelta(days=amount)

    # Try absolute ISO 8601 format
    try:
        return date.fromisoformat(value.strip())
    except ValueError as e:
        raise ValueError(f"Invalid date format: {value}. Expected ISO 8601 or T+Xm/T+Xd") from e


def resolve_calendar(calendar: dict[str, str], base_date: date | None = None) -> dict[str, str]:
    """Resolve all calendar entries, converting relative dates to absolute ISO 8601.

    This enables dynamic date computation based on execution time rather than
    hardcoded dates that become stale.

    Args:
        calendar: Dict mapping calendar keys to date values (absolute or relative)
        base_date: Reference date for relative offsets. Defaults to today.

    Returns:
        New dict with all dates resolved to ISO 8601 format (YYYY-MM-DD)

    Example:
        >>> resolve_calendar({"review": "T+24m", "model": "2025-08-02"}, date(2025, 1, 1))
        {"review": "2027-01-01", "model": "2025-08-02"}
    """
    if base_date is None:
        base_date = date.today()

    resolved: dict[str, str] = {}
    for key, value in calendar.items():
        if not isinstance(value, str):
            # Skip non-string values (version, etc.)
            continue
        try:
            resolved_date = resolve_calendar_date(value, base_date)
            resolved[key] = resolved_date.isoformat()
        except ValueError:
            # Keep original value if parsing fails (might be a placeholder like "YYYY-MM-DD")
            resolved[key] = value

    return resolved


def flatten_calendar(calendar: dict | None) -> dict:
    """Return a dict view of the calendar, supporting nested 'calendar' key formats.

    Accepts either:
    - {<key>: <date>, ...}
    - {"calendar": {<key>: <date>, ...}}
    Returns an empty dict for invalid inputs.
    """
    if not isinstance(calendar, dict):
        return {}
    if "calendar" in calendar and not isinstance(calendar.get("calendar"), dict):
        return {}
    # Extract inner calendar, handling both nested and flat structures
    calendar_value = calendar.get("calendar")
    inner = calendar_value if isinstance(calendar_value, dict) else calendar
    return inner if isinstance(inner, dict) else {}


def apply_due_hints(
    action_ids: list[ActionId],
    due_rules: DueRulesContract,
    calendar: dict[str, Any],
    runtime: FrameworkRuntime | None = None,
) -> dict[ActionId, str]:
    """Apply due date hints to actions with explicit priority handling.

    Rules with higher priority/weight are applied first:
    - Explicit `priority` (numeric) takes precedence
    - `policy` (string) maps to predefined weights
    - Ties are resolved by original rule order (stable sort)

    Args:
        action_ids: List of action IDs to process
        due_rules: Typed DueRulesContract with rule entries and optional overrides
        calendar: Calendar with date values

    Returns:
        Dict mapping action IDs to due date strings
    """
    hints: dict[ActionId, str] = {}

    if not isinstance(due_rules, DueRulesContract):
        raise TypeError(
            "due_rules must be a DueRulesContract; legacy dict inputs are not supported"
        )

    rules = list(due_rules.rules)
    if not rules or not action_ids:
        return hints

    # Policy name to priority weight mapping (overridable from YAML)
    default_policy_weights = {
        "full_application": PRIORITY_FULL_APPLICATION,
        "transparency": PRIORITY_TRANSPARENCY,
        "model_governance": PRIORITY_MODEL_GOVERNANCE,
        "review": PRIORITY_REVIEW,
    }
    runtime_policy_weights = (
        runtime.policies.due_dates.policy_weights if runtime is not None else {}
    )
    policy_weights = due_rules.policy_weights or runtime_policy_weights or default_policy_weights

    # Rank rules by priority (higher priority first)
    ranked_rules = []
    for index, rule in enumerate(rules):
        weight = 0

        if isinstance(rule.priority, (int, float)):
            weight = int(rule.priority)
        elif isinstance(rule.policy, str):
            weight = policy_weights.get(rule.policy, 0)

        # Negative weight for descending sort, index for stable ordering
        ranked_rules.append((-weight, index, rule))

    ranked_rules.sort()

    runtime_alias_map = runtime.policies.due_dates.calendar_aliases if runtime is not None else {}
    alias_map = due_rules.calendar_aliases or runtime_alias_map

    # Apply rules to each action (first matching rule wins)
    for action_id in action_ids:
        for _, _, rule in ranked_rules:
            rule_ids = rule.ids or []
            rule_prefixes = rule.prefixes or []

            # Check if rule applies to this action
            matches = action_id in rule_ids or any(
                action_id.startswith(prefix) for prefix in rule_prefixes
            )

            if matches:
                # Find first valid calendar key (including alias fallbacks)
                found_dates = []
                for calendar_key in rule.calendar_keys or []:
                    keys_to_try = [calendar_key] + (alias_map.get(calendar_key, []) or [])
                    for ck in keys_to_try:
                        if ck in calendar and calendar.get(ck):
                            found_dates.append((ck, calendar[ck]))

                if found_dates:
                    # Use first match to maintain deterministic behavior based on precedence
                    chosen_key, chosen_date = found_dates[0]
                    hints[action_id] = chosen_date

                    # C-09: Log conflicts if aliases resolve to DIFFERENT dates
                    # This alerts admins to inconsistent calendar configurations
                    conflicts = [f"{k}='{d}'" for k, d in found_dates[1:] if d != chosen_date]
                    if conflicts:
                        logger = logging.getLogger(__name__)
                        logger.warning(
                            "duedate.calendar_alias_conflict",
                            {
                                "action_id": action_id,
                                "chosen": f"{chosen_key}='{chosen_date}'",
                                "conflicts": conflicts,
                            },
                        )
                    break

            # Stop at first matching rule
            if action_id in hints:
                break

    return hints
