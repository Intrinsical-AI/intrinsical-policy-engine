# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Integrity auditing for compliance knowledge graph."""

import logging
from dataclasses import dataclass
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


@dataclass
class AuditViolation:
    """Structured representation of a graph integrity violation."""

    code: str
    target_id: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        """Return a compact `CODE: message` representation."""
        return f"{self.code}: {self.message}"


def audit_graph(G: nx.DiGraph, config: dict[str, Any] | None = None) -> list[AuditViolation]:
    """Run integrity checks on compliance graph.

    Args:
        G: Compliance graph from build_compliance_graph()
        config: Audit configuration dictionary (usually from audit.yml).
                Supported keys:
                - 'expected_gaps': list of article IDs to whitelist for REVIEW_GAP
                - 'registered_flags': list of flag IDs from flags.yml registry
                - 'expected_dead_flags': list of flag IDs to whitelist for DEAD_FLAG

    Returns:
        List of AuditViolation objects (empty if clean)
    """
    violations = []
    cfg = config or {}

    # Extract config sets (Compliance-as-Code)
    expected_gaps = set(cfg.get("expected_gaps") or [])
    registered_flags = set(cfg.get("registered_flags") or [])
    expected_dead_flags = set(cfg.get("expected_dead_flags") or [])

    violations.extend(_check_orphan_actions(G))
    violations.extend(_check_dead_ends(G))
    violations.extend(_check_broken_references(G))
    violations.extend(_check_review_gaps(G, expected_gaps))
    violations.extend(_check_dead_flags(G, registered_flags, expected_dead_flags))
    logger.info(f"Audit complete: {len(violations)} violations found")

    return violations


def _check_orphan_actions(G: nx.DiGraph) -> list[AuditViolation]:
    """Find Action nodes with no outgoing 'implements' edges."""
    violations = []
    for node_id in G.nodes():
        node_data = G.nodes[node_id]
        if node_data.get("node_type") != "Action":
            continue
        implements_count = sum(
            1
            for _, _, edge_data in G.out_edges(node_id, data=True)
            if edge_data.get("edge_type") == "implements"
        )
        if implements_count == 0:
            violations.append(
                AuditViolation(
                    code="ORPHAN_ACTION",
                    target_id=node_id,
                    message=f"{node_id} - No articles implemented",
                )
            )
    return violations


def _check_dead_ends(G: nx.DiGraph) -> list[AuditViolation]:
    """Find Action nodes with no incoming 'proves' edges from Evidence."""
    violations = []
    for node_id in G.nodes():
        node_data = G.nodes[node_id]
        if node_data.get("node_type") != "Action":
            continue
        proves_count = sum(
            1
            for _, _, edge_data in G.in_edges(node_id, data=True)
            if edge_data.get("edge_type") == "proves"
        )
        if proves_count == 0:
            violations.append(
                AuditViolation(
                    code="DEAD_END",
                    target_id=node_id,
                    message=f"{node_id} - No evidence documentation",
                )
            )
    return violations


def _check_broken_references(G: nx.DiGraph) -> list[AuditViolation]:
    """Check if Evidence nodes point to non-existent Actions."""
    violations = []
    for node_id in G.nodes():
        node_data = G.nodes[node_id]
        if node_data.get("node_type") != "Evidence":
            continue
        for _, target, edge_data in G.out_edges(node_id, data=True):
            if edge_data.get("edge_type") != "proves":
                continue
            if target not in G.nodes or "node_type" not in G.nodes[target]:
                violations.append(
                    AuditViolation(
                        code="BROKEN_REF",
                        target_id=node_id,
                        message=f"{node_id} -> {target} (Node missing)",
                    )
                )
            elif G.nodes[target].get("node_type") != "Action":
                violations.append(
                    AuditViolation(
                        code="INVALID_REF",
                        target_id=node_id,
                        message=f"{node_id} -> {target} (Target is not an Action)",
                    )
                )
    return violations


def _is_review(node_data: dict[str, Any]) -> bool:
    """Check if an Article node is Review."""
    # 1. Check structured attribute (preferred)
    if node_data.get("risk_level") == "high":
        return True

    # 2. Fallback to scope string matching (legacy/robustness)
    scope = str(node_data.get("scope", "")).lower()
    return "review" in scope


def _check_review_gaps(G: nx.DiGraph, expected_gaps: set[str]) -> list[AuditViolation]:
    """Check for Review articles that have no implementing actions.

    Uses expected_gaps set from configuration for whitelisting.
    """
    violations = []

    for node_id in G.nodes():
        node = G.nodes[node_id]
        if node.get("node_type") != "Article":
            continue

        # Check if Review
        if not _is_review(node):
            continue

        # Count incoming 'implements' edges
        implementing_actions = [
            n
            for n in G.predecessors(node_id)
            if G.edges[n, node_id].get("edge_type") == "implements"
        ]

        if not implementing_actions:
            if node_id in expected_gaps:
                logger.info(
                    f"Expected gap: Review Article {node_id} has no actions "
                    "(whitelisted in audit.yml)"
                )
            else:
                violations.append(
                    AuditViolation(
                        code="REVIEW_GAP",
                        target_id=node_id,
                        message=f"Review Article {node_id} has no implementing actions",
                    )
                )

    return violations


def _check_dead_flags(
    G: nx.DiGraph, registered_flags: set[str], expected_dead_flags: set[str]
) -> list[AuditViolation]:
    """Check for registered flags that are never used in any action's 'when' condition.

    A "dead flag" is a flag declared in the flags registry but that never appears
    in the graph (i.e., never triggers any action). This may indicate:
    - Obsolete flag definitions that should be removed
    - Missing action coverage for certain conditions
    - Typos in flag names

    Args:
        G: Compliance graph
        registered_flags: Set of flag IDs from flags.yml registry
        expected_dead_flags: Set of flag IDs to whitelist (known dead flags)

    Returns:
        List of DEAD_FLAG violations for flags that are registered but unused.
    """
    violations: list[AuditViolation] = []

    # Skip if no registry provided (audit without flag metadata)
    if not registered_flags:
        return violations

    # Collect flags actually used in the graph (have 'triggers' edges to actions)
    used_flags: set[str] = set()
    for node_id in G.nodes():
        node_data = G.nodes[node_id]
        if node_data.get("node_type") != "Flag":
            continue
        # Check if this flag has any outgoing 'triggers' edges
        has_triggers = any(
            G.edges[node_id, target].get("edge_type") == "triggers"
            for target in G.successors(node_id)
        )
        if has_triggers:
            used_flags.add(node_id)

    # Find dead flags (registered but not used)
    dead_flags = registered_flags - used_flags

    for flag_id in sorted(dead_flags):
        if flag_id in expected_dead_flags:
            logger.info(f"Expected dead flag: {flag_id} (whitelisted in audit config)")
        else:
            violations.append(
                AuditViolation(
                    code="DEAD_FLAG",
                    target_id=flag_id,
                    message=f"Flag '{flag_id}' is registered but never used in any action",
                    severity="warning",
                )
            )

    return violations
