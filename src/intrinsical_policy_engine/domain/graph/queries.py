# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Graph traversal queries for compliance analysis."""

import logging

import networkx as nx

logger = logging.getLogger(__name__)


class GraphQueryEngine:
    """Engine for querying the compliance knowledge graph.

    Provides type-safe traversal methods with defensive validation.
    """

    # Expected node types for validation
    NODE_TYPE_ARTICLE = "Article"
    NODE_TYPE_ACTION = "Action"
    NODE_TYPE_FLAG = "Flag"
    NODE_TYPE_EVIDENCE = "Evidence"

    def __init__(self, graph: nx.DiGraph):
        """Store the graph handle used for all traversal helpers."""
        self.graph = graph

    def _get_node_type(self, node_id: str) -> str | None:
        """Get node_type attribute, or None if missing."""
        if node_id not in self.graph:
            return None
        return str(val) if (val := self.graph.nodes[node_id].get("node_type")) else None

    def _validate_node_type(self, node_id: str, expected_type: str) -> bool:
        """Validate that a node has the expected type.

        E16: Defensive check for node_type mismatches.
        """
        actual_type = self._get_node_type(node_id)
        if actual_type is None:
            logger.warning(f"Node '{node_id}' has no node_type attribute")
            return False
        if actual_type != expected_type:
            logger.warning(f"Node '{node_id}' has type '{actual_type}', expected '{expected_type}'")
            return False
        return True

    def get_evidence_for_article(self, article_id: str) -> list[str]:
        """Get all evidence paths required for a specific article.

        Traversal: Article <-(implements)- Action <-(proves)- Evidence
        """
        if article_id not in self.graph:
            return []

        # E16: Validate node type
        if not self._validate_node_type(article_id, self.NODE_TYPE_ARTICLE):
            return []

        evidence_nodes = set()

        # Find actions implementing this article
        actions = [
            n
            for n in self.graph.predecessors(article_id)
            if self.graph.edges[n, article_id].get("edge_type") == "implements"
        ]

        for action_id in actions:
            # Find evidence proving this action
            evidence = [
                n
                for n in self.graph.predecessors(action_id)
                if self.graph.edges[n, action_id].get("edge_type") == "proves"
            ]
            evidence_nodes.update(evidence)

        return sorted([self.graph.nodes[n].get("path", n) for n in evidence_nodes])

    def get_actions_triggered_by_flag(self, flag_id: str) -> list[str]:
        """Get all actions triggered by a specific flag.

        Traversal: Flag -(triggers)-> Action
        """
        if flag_id not in self.graph:
            return []

        # E16: Validate node type
        if not self._validate_node_type(flag_id, self.NODE_TYPE_FLAG):
            return []

        actions = [
            n
            for n in self.graph.successors(flag_id)
            if self.graph.edges[flag_id, n].get("edge_type") == "triggers"
        ]

        return sorted(actions)

    def get_impact_analysis(self, node_id: str) -> dict[str, list[str]]:
        """Analyze dependencies for a node (upstream and downstream).

        Returns:
            Dict with 'upstream' (dependencies) and 'downstream' (dependents)
        """
        if node_id not in self.graph:
            return {"upstream": [], "downstream": []}

        # Note: No type validation here - impact analysis works for any node type

        upstream = list(nx.ancestors(self.graph, node_id))
        downstream = list(nx.descendants(self.graph, node_id))

        return {"upstream": sorted(upstream), "downstream": sorted(downstream)}
