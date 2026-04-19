# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Graph filtering system for flexible compliance graph construction.

This module provides a simple but powerful filtering mechanism that allows
building focused subgraphs based on node types, attributes, and relationships.

Design principles:
    - Simple: Easy to use with sensible defaults
    - Powerful: Covers common filtering needs (types, attributes, patterns)
    - Versatile: Composable filters, transitive relationships, wildcard patterns
    - Non-invasive: Optional, doesn't break existing code
    - Efficient: Optimized transitive closure using frontier-based BFS

Usage:
    >>> from src.domain.graph.filters import GraphFilter, filter_graph, apply_filters_or
    >>>
    >>> # Filter to review articles and their actions
    >>> filter_config = GraphFilter(
    ...     include_risk_levels={"high", "blocked"},
    ...     include_node_types={"Article", "Action"},
    ...     transitive=True
    ... )
    >>> filtered_graph = filter_graph(full_graph, filter_config)
    >>>
    >>> # Compose filters with OR logic (correct way)
    >>> filter_critical = GraphFilter(include_priorities={"critical"})
    >>> filter_provider = GraphFilter(include_applies_to={"provider"})
    >>> # Result: critical actions (any role) OR provider actions (any priority)
    >>> filtered_graph = apply_filters_or(full_graph, [filter_critical, filter_provider])
"""

from __future__ import annotations

import fnmatch
import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum

import networkx as nx

logger = logging.getLogger(__name__)


class FilterLogic(Enum):
    """Logic for combining multiple filter criteria."""

    AND = "AND"  # All criteria must match (default)
    OR = "OR"  # Any criterion can match


# Edge type to traversal direction mapping for transitive filtering
# Format: (source_node_type, edge_type, direction) -> target_node_type
# Direction: "forward" (source -> target), "backward" (target -> source)
_TRANSITIVE_RULES: dict[tuple[str, str, str], str] = {
    # Article -> Actions (via implements edges, backward)
    ("Article", "implements", "backward"): "Action",
    # Action -> Articles (via implements edges, forward)
    ("Action", "implements", "forward"): "Article",
    # Action -> Flags (via triggers edges, backward)
    ("Action", "triggers", "backward"): "Flag",
    # Flag -> Actions (via triggers edges, forward)
    ("Flag", "triggers", "forward"): "Action",
    # Action -> Evidence (via proves edges, backward)
    ("Action", "proves", "backward"): "Evidence",
    # Evidence -> Actions (via proves edges, forward)
    ("Evidence", "proves", "forward"): "Action",
}


@dataclass
class GraphFilter:
    """Filter configuration for compliance graph construction.

    All fields are optional. If None, that filter criterion is not applied.
    Multiple criteria are combined with AND logic by default (all must match).

    Pattern matching:
        - Supports wildcards using fnmatch: "TOPIC-8*", "SECTION-*"
        - Exact matches also supported: "TOPIC-8", "TOPIC-9"

    Transitive filtering:
        When transitive=True, uses optimized frontier-based BFS to include
        related nodes without re-processing already discovered nodes.

    Evidence state filtering:
        - exclude_completed_actions: Exclude actions that have evidence connected
        - include_only_missing_evidence: Include only actions without evidence

    Examples:
        # Only review articles and their actions
        GraphFilter(
            include_risk_levels={"high", "blocked"},
            include_node_types={"Article", "Action"},
            transitive=True
        )

        # Provider actions with critical/high priority
        GraphFilter(
            include_node_types={"Action"},
            include_priorities={"critical", "high"},
            include_applies_to={"provider"},
            transitive=True
        )

        # Actions without evidence (dashboard view)
        GraphFilter(
            include_node_types={"Action"},
            include_only_missing_evidence=True,
            transitive=True
        )
    """

    # Node type filtering
    include_node_types: set[str] | None = None
    """Include only these node types: {"Article", "Action", "Flag", "Evidence"}."""

    exclude_node_types: set[str] | None = None
    """Exclude these node types."""

    # Article filtering
    include_articles: set[str] | None = None
    """Include articles matching these IDs or patterns (supports wildcards)."""

    exclude_articles: set[str] | None = None
    """Exclude articles matching these IDs or patterns."""

    # Action attribute filtering
    include_priorities: set[str] | None = None
    """Include actions with these priorities: {"critical", "high", "medium", "low"}."""

    exclude_priorities: set[str] | None = None
    """Exclude actions with these priorities."""

    include_applies_to: set[str] | None = None
    """Include actions that apply to these roles: {"provider", "deployer", "any", ...}."""

    exclude_applies_to: set[str] | None = None
    """Exclude actions that apply to these roles."""

    include_categories: set[str] | None = None
    """Include actions with these categories.

    Examples: {"engineering", "legal", "compliance", "governance"}
    """

    exclude_categories: set[str] | None = None
    """Exclude actions with these categories."""

    # Article attribute filtering
    include_risk_levels: set[str] | None = None
    """Include articles with these risk levels: {"high", "standard", "blocked"}."""

    exclude_risk_levels: set[str] | None = None
    """Exclude articles with these risk levels."""

    # Flag filtering (affects which actions are included)
    include_flags: set[str] | None = None
    """Include actions triggered by these flags (supports wildcards)."""

    exclude_flags: set[str] | None = None
    """Exclude actions triggered by these flags."""

    # Evidence filtering
    include_evidence: bool = True
    """Whether to include Evidence nodes (default: True)."""

    # Evidence state filtering (for dashboard/progress views)
    exclude_completed_actions: bool = False
    """Exclude actions that have evidence connected (default: False)."""

    include_only_missing_evidence: bool = False
    """Include only actions without evidence (default: False).

    When True, only includes actions that have no Evidence nodes connected
    via 'proves' edges. Useful for progress dashboards.
    """

    # Transitive filtering
    transitive: bool = True
    """Include related nodes transitively (default: True).

    Uses optimized frontier-based BFS to avoid re-processing nodes.
    """

    transitive_node_types: set[str] | None = None
    """Restrict which node types can be reached transitively.

    If set, only nodes of these types will be included during transitive expansion.
    If None, all node types can be reached transitively.
    """

    transitive_bridge: bool = False
    """Allow traversing through nodes without including them (advanced mode).

    When True, nodes that don't match filter criteria can still be traversed
    to reach other nodes. Useful for complex filtering scenarios.
    """

    # Internal: Logic for combining criteria (used by combine_filters)
    _logic: FilterLogic = FilterLogic.AND

    def _matches_pattern(self, value: str, patterns: set[str]) -> bool:
        """Check if value matches any pattern in patterns (supports wildcards)."""
        return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)

    def _get_related_nodes(self, G: nx.DiGraph, node_id: str) -> set[str]:
        """Get nodes related to node_id via graph edges (for transitive filtering).

        Uses configurable transitive rules instead of hardcoded if/elif chains.
        This makes the system extensible to new node types without modifying
        the filter class (Open/Closed principle).
        """
        related = set()
        node_type = G.nodes[node_id].get("node_type", "")

        # Check all configured transitive rules
        for (source_type, edge_type, direction), target_type in _TRANSITIVE_RULES.items():
            if source_type != node_type:
                continue

            if direction == "forward":
                # Follow edges from source to target
                for target_id in G.successors(node_id):
                    edge_data = G.edges.get((node_id, target_id), {})
                    if edge_data.get("edge_type") == edge_type:
                        # Verify target node type matches rule
                        actual_target_type = G.nodes[target_id].get("node_type", "")
                        if actual_target_type == target_type:
                            related.add(target_id)

            elif direction == "backward":
                # Follow edges from target to source (reverse direction)
                for source_id in G.predecessors(node_id):
                    edge_data = G.edges.get((source_id, node_id), {})
                    if edge_data.get("edge_type") == edge_type:
                        # Verify source node type matches rule
                        # target_type is the related node type
                        actual_source_type = G.nodes[source_id].get("node_type", "")
                        if actual_source_type == target_type:
                            related.add(source_id)

        return related

    def _has_evidence(self, G: nx.DiGraph, action_id: str) -> bool:
        """Check if an action has evidence connected via 'proves' edges."""
        for evidence_id in G.predecessors(action_id):
            edge_data = G.edges.get((evidence_id, action_id), {})
            if edge_data.get("edge_type") == "proves":
                return True
        return False

    def _should_include_article(self, node_data: dict) -> bool:
        """Check if an Article node should be included."""
        article_id = node_data.get("id", "")
        risk_level = node_data.get("risk_level", "")

        # Exclusion checks (always enforced)
        if self.exclude_articles and self._matches_pattern(article_id, self.exclude_articles):
            return False
        if self.exclude_risk_levels and risk_level in self.exclude_risk_levels:
            return False

        # Inclusion checks (all must match if set, AND logic)
        if self.include_articles and not self._matches_pattern(article_id, self.include_articles):
            return False
        return not (self.include_risk_levels and risk_level not in self.include_risk_levels)

    def _should_include_action(self, node_id: str, node_data: dict, G: nx.DiGraph | None) -> bool:
        """Check if an Action node should be included."""
        priority = node_data.get("priority", "")
        applies_to = node_data.get("applies_to", "any")
        category = node_data.get("category", "")

        # Normalize applies_to (can be str, tuple, or list)
        if isinstance(applies_to, (tuple, list)):
            applies_to_set = {str(x).lower() for x in applies_to}
        else:
            applies_to_set = {str(applies_to).lower()}

        # Exclusion checks (always enforced)
        if self.exclude_priorities and priority in self.exclude_priorities:
            return False
        if self.exclude_applies_to:
            exclude_set = {str(x).lower() for x in self.exclude_applies_to}
            if exclude_set & applies_to_set:
                return False
        if self.exclude_categories and category and category in self.exclude_categories:
            return False

        # Inclusion checks (AND)
        if self.include_priorities and priority not in self.include_priorities:
            return False
        if self.include_applies_to:
            include_set = {str(x).lower() for x in self.include_applies_to}
            if not (include_set & applies_to_set or "any" in applies_to_set):
                return False
        if self.include_categories and category and category not in self.include_categories:
            return False

        # Flag filtering (now applies uniformly, including transitively discovered actions)
        if G is not None and not self._should_include_action_by_flags(G, node_id):
            return False

        # Evidence state filtering (requires graph access)
        if G is not None:
            has_evidence = self._has_evidence(G, node_id)
            if self.exclude_completed_actions and has_evidence:
                return False
            if self.include_only_missing_evidence and has_evidence:
                return False

        return True

    def _should_include_node(
        self, node_id: str, node_data: dict, G: nx.DiGraph | None = None
    ) -> bool:
        """Determine if a node should be included based on filter criteria.

        Args:
            node_id: Node identifier
            node_data: Node attributes dictionary
            G: Graph instance (required for evidence state filtering)

        Returns:
            True if node should be included, False otherwise
        """
        node_type = node_data.get("node_type", "")

        # Node type filtering (exclusion always takes precedence)
        if self.exclude_node_types and node_type in self.exclude_node_types:
            return False
        if self.include_node_types and node_type not in self.include_node_types:
            return False

        # Type-specific filtering
        if node_type == "Article":
            return self._should_include_article(node_data)
        elif node_type == "Action":
            return self._should_include_action(node_id, node_data, G)
        elif node_type == "Evidence":
            return self.include_evidence

        # Flag nodes and other types: include by default
        return True

    def _should_include_action_by_flags(self, G: nx.DiGraph, action_id: str) -> bool:
        """Check if action should be included based on flag filtering."""
        if not self.include_flags and not self.exclude_flags:
            return True

        # Get flags that trigger this action
        triggering_flags = set()
        for flag_id in G.predecessors(action_id):
            edge_data = G.edges.get((flag_id, action_id), {})
            if edge_data.get("edge_type") == "triggers":
                triggering_flags.add(flag_id)

        # If no flags trigger this action, include it if no flag filter is set
        if not triggering_flags:
            return True

        # Check include_flags
        if self.include_flags:
            matches_include = any(
                self._matches_pattern(flag_id, self.include_flags) for flag_id in triggering_flags
            )
            if not matches_include:
                return False

        # Check exclude_flags
        if self.exclude_flags:
            matches_exclude = any(
                self._matches_pattern(flag_id, self.exclude_flags) for flag_id in triggering_flags
            )
            if matches_exclude:
                return False

        return True


def apply_filters_or(G: nx.DiGraph, filters: list[GraphFilter]) -> nx.DiGraph:
    """Apply multiple filters with OR logic by unioning filtered subgraphs.

    This is the correct way to combine filters with OR logic for multidimensional
    criteria. Each filter is applied independently, and the resulting subgraphs
    are unioned together.

    Args:
        G: Full compliance graph to filter
        filters: List of filter configurations (any matching node is included)

    Returns:
        Union of all filtered subgraphs

    Example:
        >>> # "Critical priority actions" OR "Provider actions"
        >>> filter_critical = GraphFilter(include_priorities={"critical"})
        >>> filter_provider = GraphFilter(include_applies_to={"provider"})
        >>> result = apply_filters_or(full_graph, [filter_critical, filter_provider])
        >>> # Result includes: all critical actions (any role) + all provider actions (any priority)

    Note:
        This correctly handles multidimensional filters. For example:
        - Filter A: priority={critical}, applies_to=None (any role)
        - Filter B: priority=None (any), applies_to={provider}
        - Result: Actions that are critical (any role) OR provider (any priority)
        - A "low priority provider action" will match Filter B and be included.
    """
    if not filters:
        return G.copy()

    if len(filters) == 1:
        return filter_graph(G, filters[0])

    # Apply each filter independently and union the results
    result_graph = nx.DiGraph()

    for filter_config in filters:
        subgraph = filter_graph(G, filter_config)
        # nx.compose unions graphs (combines nodes and edges)
        result_graph = nx.compose(result_graph, subgraph)

    logger.info(
        f"Applied OR filters: {result_graph.number_of_nodes()} nodes, "
        f"{result_graph.number_of_edges()} edges "
        f"(from {G.number_of_nodes()} nodes, {G.number_of_edges()} edges)"
    )

    return result_graph


def _collect_filter_sets(filters: list[GraphFilter]) -> dict[str, set[str]]:
    """Collect all inclusion/exclusion sets from filters."""
    sets_dict: dict[str, set[str]] = {
        "include_node_types": set(),
        "exclude_node_types": set(),
        "include_articles": set(),
        "exclude_articles": set(),
        "include_priorities": set(),
        "exclude_priorities": set(),
        "include_applies_to": set(),
        "exclude_applies_to": set(),
        "include_categories": set(),
        "exclude_categories": set(),
        "include_risk_levels": set(),
        "exclude_risk_levels": set(),
        "include_flags": set(),
        "exclude_flags": set(),
    }

    # Map filter attribute names to dict keys
    attr_to_key = [
        ("include_node_types", "include_node_types"),
        ("exclude_node_types", "exclude_node_types"),
        ("include_articles", "include_articles"),
        ("exclude_articles", "exclude_articles"),
        ("include_priorities", "include_priorities"),
        ("exclude_priorities", "exclude_priorities"),
        ("include_applies_to", "include_applies_to"),
        ("exclude_applies_to", "exclude_applies_to"),
        ("include_categories", "include_categories"),
        ("exclude_categories", "exclude_categories"),
        ("include_risk_levels", "include_risk_levels"),
        ("exclude_risk_levels", "exclude_risk_levels"),
        ("include_flags", "include_flags"),
        ("exclude_flags", "exclude_flags"),
    ]

    for f in filters:
        for attr_name, dict_key in attr_to_key:
            attr_value = getattr(f, attr_name, None)
            if attr_value:
                sets_dict[dict_key].update(attr_value)

    return sets_dict


def combine_filters(
    filters: list[GraphFilter], logic: FilterLogic = FilterLogic.AND
) -> GraphFilter:
    """Combine multiple filters with AND logic by intersecting criteria.

    WARNING: This only works correctly for AND logic. For OR logic, use
    `apply_filters_or()` instead, which applies filters independently and unions
    the results.

    For AND logic, this merges inclusion/exclusion sets, which creates an
    intersection of criteria. This is only correct when you want ALL filters
    to match simultaneously.

    Args:
        filters: List of filter configurations to combine
        logic: Must be FilterLogic.AND (OR logic not supported here)

    Returns:
        Combined filter configuration (intersection of all criteria)

    Example:
        >>> # "Provider actions AND critical priority" (both must match)
        >>> filter_provider = GraphFilter(include_applies_to={"provider"})
        >>> filter_critical = GraphFilter(include_priorities={"critical"})
        >>> combined = combine_filters([filter_provider, filter_critical], logic=FilterLogic.AND)
        >>> result = filter_graph(full_graph, combined)
        >>> # Result: only provider actions that are also critical priority

    Note:
        For OR logic, use `apply_filters_or()` instead:
        >>> result = apply_filters_or(full_graph, [filter_provider, filter_critical])
    """
    if not filters:
        return GraphFilter()

    if len(filters) == 1:
        return filters[0]

    if logic == FilterLogic.OR:
        raise ValueError(
            "combine_filters() does not support OR logic. "
            "Use apply_filters_or() instead to correctly handle multidimensional OR filters."
        )

    # For AND logic: merge inclusion/exclusion sets (intersection)
    combined = GraphFilter(_logic=logic)
    sets_dict = _collect_filter_sets(filters)

    # Set combined values (non-empty sets only)
    set_attrs = [
        "include_node_types",
        "exclude_node_types",
        "include_articles",
        "exclude_articles",
        "include_priorities",
        "exclude_priorities",
        "include_applies_to",
        "exclude_applies_to",
        "include_categories",
        "exclude_categories",
        "include_risk_levels",
        "exclude_risk_levels",
        "include_flags",
        "exclude_flags",
    ]
    for attr_name in set_attrs:
        if sets_dict[attr_name]:
            setattr(combined, attr_name, sets_dict[attr_name])

    # For boolean flags, use AND logic (all must agree, or any sets it to True)
    combined.include_evidence = all(f.include_evidence for f in filters)
    combined.exclude_completed_actions = any(f.exclude_completed_actions for f in filters)
    combined.include_only_missing_evidence = any(f.include_only_missing_evidence for f in filters)
    combined.transitive = any(f.transitive for f in filters)

    return combined


def filter_graph(G: nx.DiGraph, filter_config: GraphFilter) -> nx.DiGraph:
    """Apply filter configuration to a compliance graph."""
    if not filter_config:
        return G.copy()

    # Phase 1: Direct matches
    matching_nodes: set[str] = set()
    for node_id, node_data in G.nodes(data=True):
        if filter_config._should_include_node(node_id, node_data, G):
            matching_nodes.add(node_id)

    # Phase 2: Transitive expansion (frontier-based BFS)
    if filter_config.transitive:
        frontier: deque[str] = deque(matching_nodes)
        discovered: set[str] = set(matching_nodes)
        expanded: set[str] = set()

        while frontier:
            current = frontier.popleft()
            if current in expanded:
                continue
            expanded.add(current)

            related = filter_config._get_related_nodes(G, current)
            for node_id in related:
                if node_id in discovered:
                    continue
                discovered.add(node_id)

                node_data = G.nodes[node_id]
                node_type = node_data.get("node_type", "")

                # Optional: restrict which node types can be reached transitively
                if (
                    filter_config.transitive_node_types is not None
                    and node_type not in filter_config.transitive_node_types
                ):
                    continue

                allowed = filter_config._should_include_node(node_id, node_data, G)

                if allowed:
                    matching_nodes.add(node_id)
                    frontier.append(node_id)
                    continue

                # Advanced mode: traverse-through without including
                if filter_config.transitive_bridge:
                    frontier.append(node_id)

    # Phase 3: Build subgraph
    filtered_G = G.subgraph(matching_nodes).copy()

    logger.info(
        "Filtered graph: %s nodes, %s edges (from %s nodes, %s edges)",
        filtered_G.number_of_nodes(),
        filtered_G.number_of_edges(),
        G.number_of_nodes(),
        G.number_of_edges(),
    )
    return filtered_G
