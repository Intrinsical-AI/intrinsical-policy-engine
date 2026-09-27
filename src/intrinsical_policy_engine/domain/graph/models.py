# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Domain models for compliance graph construction.

These dataclasses decouple graph building from the YAML contract structure,
enabling testability and extensibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArticleNode:
    """Immutable article node for graph construction.

    Attributes:
        id: Article identifier (e.g., "TOPIC-5", "SECTION-III")
        title: Human-readable title
        risk_level: Derived risk level ("high", "standard", "blocked")
        scope: Original scope text (preserved for backward compat)
    """

    id: str
    title: str = ""
    risk_level: str = "standard"
    scope: str = ""


@dataclass(frozen=True)
class ActionNode:
    """Immutable action node for graph construction.

    Attributes:
        id: Action identifier (e.g., "ACT-HR-01")
        title: Human-readable title
        articles: Tuple of related article IDs
        when_condition: Raw 'when' expression (string or dict DSL)
        priority: Priority level
        applies_to: Role applicability ("any", "provider", "deployer", etc.)
    """

    id: str
    title: str = ""
    articles: tuple[str, ...] = field(default_factory=tuple)
    when_condition: str | dict | None = None
    priority: str = "low"
    applies_to: str | tuple[str, ...] = "any"


@dataclass(frozen=True)
class EvidenceEntry:
    """Evidence file entry.

    Attributes:
        path: Relative path to evidence file
        required: Whether this evidence is mandatory
    """

    path: str
    required: bool = True


@dataclass
class GraphInput:
    """Aggregated input for graph construction, decoupled from ContractBundle.

    This model provides a clean interface between the YAML adapter layer
    and the graph builder, enabling:
    - Unit testing with mock data
    - Alternative data sources (DB, API, etc.)
    - Clear separation of concerns

    Attributes:
        articles: List of article nodes
        actions: List of action nodes
        evidence_map: Mapping of article_id -> evidence entries
        alias_to_canonical: Dedup mapping of alias action_id -> canonical action_id
    """

    articles: list[ArticleNode] = field(default_factory=list)
    actions: list[ActionNode] = field(default_factory=list)
    evidence_map: dict[str, list[EvidenceEntry]] = field(default_factory=dict)
    alias_to_canonical: dict[str, str] = field(default_factory=dict)

    def is_alias(self, action_id: str) -> bool:
        """Check if action_id is an alias."""
        return action_id in self.alias_to_canonical
