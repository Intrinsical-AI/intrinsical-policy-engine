# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Strict Jinja environment and explicit calendar date filters."""

from datetime import date, timedelta

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from intrinsical_policy_engine.domain.contract_models import canonical_date
from intrinsical_policy_engine.domain.services.duedate_service import resolve_calendar_date


def add_days(value: str, days: int) -> str:
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValueError("days must be an integer")
    return (date.fromisoformat(canonical_date(value)) + timedelta(days=days)).isoformat()


def resolve_relative_date(value: str, base_date: str | None = None) -> str:
    base = date.fromisoformat(canonical_date(base_date)) if base_date is not None else None
    return resolve_calendar_date(value, base).isoformat()


def to_bool(value: bool) -> str:
    if not isinstance(value, bool):
        raise ValueError("Expected a boolean")
    return "true" if value else "false"


def create_jinja_env(templates_dir: str) -> Environment:
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        undefined=StrictUndefined,
        autoescape=select_autoescape(("html", "xml")),
    )
    env.filters.update(add_days=add_days, resolve_date=resolve_relative_date, to_bool=to_bool)
    return env
