# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Factory helpers for creating hardened Jinja environments.

This module lives in `common`/`app.config` so both domain and app layers can depend on it
without violating layering (docs/invariants/ENGINE-ARCHITECTURE-v1.md).

Keep all Jinja environment configuration here to avoid cross-layer imports.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined


class SilentEmpty(Undefined):
    """Undefined implementation that marks missing values.

    In compliance contexts, silently rendering empty strings is dangerous because
    it creates "valid-looking" artifacts with missing critical fields (owner, id,
    dates, etc.).

    This implementation marks missing fields with <<MISSING: key>> to make gaps
    visible while still allowing rendering to continue (useful for
    authoring/local development).
    """

    def _get_var_name(self) -> str:
        """Get the variable name from internal attributes."""
        var_name = getattr(self, "_undefined_name", None)
        if var_name is None:
            hint = getattr(self, "_undefined_hint", None)
            var_name = str(hint) if hint else "unknown"
        return var_name

    def _marker(self) -> str:
        """Return marker string used to signal missing fields."""
        return f"<<MISSING: {self._get_var_name()}>>"

    def _fail_with_undefined_error(self, *args: Any, **kwargs: Any) -> str:  # type: ignore[override]
        """Override Undefined behavior to mark missing fields."""
        return self._marker()

    def __str__(self) -> str:
        return self._marker()

    def __repr__(self) -> str:
        return f"SilentEmpty('{self._get_var_name()}')"

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 0

    def __iter__(self) -> Iterable[Any]:  # type: ignore[override]
        return iter(())

    def __call__(self, *args: Any, **kwargs: Any) -> str:  # type: ignore[override]
        return self._marker()

    def __getattr__(self, name: str) -> SilentEmpty:
        current_name = self._get_var_name()
        nested = type(self)()
        nested._undefined_name = f"{current_name}.{name}"  # type: ignore[attr-defined]
        return nested

    def __getitem__(self, key: Any) -> SilentEmpty:  # type: ignore[override]
        current_name = self._get_var_name()
        nested = type(self)()
        nested._undefined_name = f"{current_name}[{key}]"  # type: ignore[attr-defined]
        return nested

    def __contains__(self, item: Any) -> bool:
        return False

    def __eq__(self, other: Any) -> bool:
        return False

    def __ne__(self, other: Any) -> bool:
        return True

    def __add__(self, other: Any) -> str:  # type: ignore[override]
        return f"{self._marker()}{other}"

    def __radd__(self, other: Any) -> str:  # type: ignore[override]
        return f"{other}{self._marker()}"

    def __mul__(self, other: Any) -> str:  # type: ignore[override]
        return self._marker()

    def __rmul__(self, other: Any) -> str:  # type: ignore[override]
        return self._marker()

    def __int__(self) -> int:  # type: ignore[override]
        return 0

    def __float__(self) -> float:  # type: ignore[override]
        return 0.0


def make_tracking_undefined(collector: set[str]) -> type[SilentEmpty]:
    """Create an Undefined class that collects missing variable names.

    The returned Undefined class:
    - behaves like SilentEmpty (renders markers)
    - records missing variable names into `collector` during string conversion.

    Args:
        collector: Mutable set that collects missing variable names.

    Returns:
        A subclass of SilentEmpty that records missing fields into collector.
    """

    class TrackingUndefined(SilentEmpty):
        def __str__(self) -> str:
            name = self._get_var_name()
            collector.add(name)
            return super().__str__()

        def _fail_with_undefined_error(self, *args: Any, **kwargs: Any) -> str:  # type: ignore[override]
            name = self._get_var_name()
            collector.add(name)
            return super()._fail_with_undefined_error(*args, **kwargs)

    return TrackingUndefined


def add_days(date_input: _dt.date | _dt.datetime | str, days: int | str | float) -> str:
    """Add `days` to the given date/datetime/ISO string and return ISO date.

    Args:
        date_input: Date input (date, datetime, or ISO string).
        days: Number of days to add (will be converted to int).

    Returns:
        ISO date string (YYYY-MM-DD) with days added, or original string if parsing fails.
    """
    try:
        days_int = int(days)
    except (TypeError, ValueError):
        return str(date_input)

    if isinstance(date_input, _dt.datetime):
        base_date = date_input.date()
    elif isinstance(date_input, _dt.date):
        base_date = date_input
    else:
        date_str = str(date_input).strip()
        try:
            base_date = _dt.date.fromisoformat(date_str[:10])
        except ValueError:
            try:
                base_date = _dt.datetime.fromisoformat(date_str).date()
            except ValueError:
                return str(date_input)

    return (base_date + _dt.timedelta(days=days_int)).isoformat()


def to_bool(value: bool | str | int | float | Undefined | None) -> str:
    """Convert value to 'yes'/'no' string representation.

    Args:
        value: Value to convert. Can be bool, str, int, float, Undefined, or None.

    Returns:
        'yes' for truthy values, 'no' for falsy values.
    """
    if isinstance(value, Undefined):
        return "no"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return "yes" if value.strip().lower() in ("true", "yes", "1") else "no"
    return "yes" if value else "no"


def resolve_relative_date(
    date_str: str | Undefined | None,
    base_date: _dt.date | _dt.datetime | str | None = None,
) -> str:
    """Resolve relative date expressions (T+30d, T+6m) to ISO-8601 dates.

    Args:
        date_str: Date string, can be ISO date or relative (T+30d, T+6m).
        base_date: Base date for relative calculations (defaults to today).
            Can be date, datetime, or ISO string.

    Returns:
        ISO-8601 date string (YYYY-MM-DD). Returns empty string if input is
        undefined/invalid. Returns original string if not a recognized pattern.
    """
    if isinstance(date_str, Undefined) or not date_str:
        return ""

    s = str(date_str).strip()

    # Already an ISO date? Return as-is (date portion only).
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]

    match = re.match(r"^T\+(\d+)([dm])$", s, re.IGNORECASE)
    if not match:
        return s

    amount = int(match.group(1))
    unit = match.group(2).lower()

    # Parse base date
    if base_date is None:
        base = _dt.date.today()
    elif isinstance(base_date, _dt.datetime):
        base = base_date.date()
    elif isinstance(base_date, _dt.date):
        base = base_date
    else:
        try:
            base = _dt.date.fromisoformat(str(base_date)[:10])
        except ValueError:
            base = _dt.date.today()

    if unit == "d":
        return (base + _dt.timedelta(days=amount)).isoformat()

    # months
    new_month = base.month + amount
    new_year = base.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1

    import calendar

    max_day = calendar.monthrange(new_year, new_month)[1]
    new_day = min(base.day, max_day)
    return _dt.date(new_year, new_month, new_day).isoformat()


def create_jinja_env(
    root: str | Path,
    *,
    strict: bool,
    autoescape: bool | Callable[[str | None], bool] = False,
    undefined_cls: type[Undefined] | None = None,
) -> Environment:
    """Instantiate a configured Jinja environment rooted at `root`.

    Args:
        root: Root directory for template loading.
        strict: If True, use StrictUndefined (raises on missing vars).
            If False, use `undefined_cls` if provided, else SilentEmpty.
        autoescape: Enable auto-escaping for HTML/XML (default: False).
            Can be bool or a callable for selective escaping.
        undefined_cls: Optional Undefined class to use when strict=False.
            Use this to enable missing-field tracking (see make_tracking_undefined()).

    Returns:
        Configured Jinja2 Environment instance.
    """
    loader = FileSystemLoader(str(root))

    if strict:
        undefined_class: type[Undefined] = StrictUndefined
    else:
        undefined_class = undefined_cls or SilentEmpty

    env = Environment(
        loader=loader,
        autoescape=autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=undefined_class,
    )

    env.filters["add_days"] = add_days
    env.filters["bool"] = to_bool
    env.filters["resolve_date"] = resolve_relative_date
    env.tests["match"] = lambda value, pattern: bool(re.search(pattern, str(value)))
    return env


__all__ = [
    "SilentEmpty",
    "add_days",
    "create_jinja_env",
    "make_tracking_undefined",
    "resolve_relative_date",
    "to_bool",
]
