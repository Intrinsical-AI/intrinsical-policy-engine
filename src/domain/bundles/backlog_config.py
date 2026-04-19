# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure backlog configuration helpers for declarative keyword filtering."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from src.domain.bundles.models import BacklogConfig, BacklogSplitRule

_DEFAULT_ENGINEERING_KEYWORDS = [
    "LOG",
    "SEC",
    "DATA",
    "TECHDOC",
    "API",
    "PAYLOAD",
    "JSON",
    "YAML",
    "MONITOR",
    "METRIC",
    "ROBUST",
    "CYBER",
    "INCIDENT",
    "TEST",
    "BIAS",
]

DEFAULT_BACKLOG_CONFIG = BacklogConfig(
    version="1.0.0",
    default_due_days=30,
    fallback_split_id="governance",
    splits=[
        BacklogSplitRule(
            id="engineering",
            filename="engineering_backlog.csv",
            keywords=_DEFAULT_ENGINEERING_KEYWORDS,
        ),
        BacklogSplitRule(
            id="governance",
            filename="governance_backlog.csv",
            keywords=[],
        ),
    ],
)


def build_backlog_config(data: Any) -> BacklogConfig:
    """Parse raw backlog config data into the domain model."""
    if data is None:
        return DEFAULT_BACKLOG_CONFIG
    if not isinstance(data, dict):
        raise ValueError("backlog_config root must be a mapping")
    try:
        return BacklogConfig(**data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def get_keywords_for_split(config: BacklogConfig, split_id: str) -> set[str]:
    """Get uppercased keyword set for a specific split."""
    for rule in config.splits:
        if rule.id == split_id:
            return {k.upper() for k in rule.keywords}
    return set()


def filter_actions_by_keywords(
    actions: list[dict],
    keywords: set[str],
    *,
    include_unmatched: bool = False,
) -> list[dict]:
    """Filter actions by keyword matching in id/title."""
    if not keywords:
        return actions if include_unmatched else []

    result = []
    for action in actions:
        text = (str(action.get("id", "")) + " " + str(action.get("title", ""))).upper()
        matches = any(k in text for k in keywords)
        if matches or include_unmatched:
            result.append(action)
    return result
