# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Typed dataclasses describing canonical contract components."""

from dataclasses import dataclass, field
from typing import Literal

Applies = Literal["any", "provider", "deployer", "importer", "distributor"]

Priority = Literal["critical", "high", "medium", "low"]


@dataclass(frozen=True)
class Action:
    """Normalized action definition used throughout assessments."""

    id: str
    title: str
    applies_to: Applies = "any"
    priority: Priority = "medium"
    legal_refs: list[str] = field(default_factory=list)
    articles: set[str] = field(default_factory=set)
    when: dict | None = None  # AST compilado luego


@dataclass(frozen=True)
class Article:
    """Article metadata with backlinks to related articles."""

    id: str
    title: str
    cross_refs: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class DueRule:
    """Due date rule referencing IDs, prefixes, and calendar keys."""

    ids: set[str] = field(default_factory=set)
    prefixes: set[str] = field(default_factory=set)
    calendar_keys: list[str] = field(default_factory=list)
