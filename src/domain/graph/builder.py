# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Build compliance knowledge graph from contract bundle.

Architecture:
    The builder is decoupled from YAML structure via GraphInput model and
    from rule engine parsing via FlagExtractor protocol.

Usage:
    >>> from src.domain.graph.adapters import BundleToGraphMapper, RuleEngineFlagExtractor
    >>> mapper = BundleToGraphMapper()
    >>> graph_input = mapper.map(bundle)
    >>> builder = ComplianceGraphBuilder(flag_extractor=RuleEngineFlagExtractor())
    >>> graph = builder.build(graph_input)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import networkx as nx

from src.domain.exceptions import RuleParseError
from src.domain.graph.models import GraphInput

if TYPE_CHECKING:
    from src.domain.graph.ports import FlagExtractor

logger = logging.getLogger(__name__)


class ComplianceGraphBuilder:
    """Build compliance knowledge graphs with injected dependencies.

    This class implements the builder pattern with dependency injection,
    enabling testability and decoupling from concrete implementations.

    Example:
        >>> from src.domain.graph.adapters import RuleEngineFlagExtractor
        >>> builder = ComplianceGraphBuilder(RuleEngineFlagExtractor())
        >>> graph = builder.build(graph_input)

    Nodes:
        - Article (id=TOPIC-XX)
        - Action (id=ACTION-ID)
        - Flag (id=flag.name)
        - Evidence (id=FILE:path)

    Edges:
        - Action -> Article (implements)
        - Flag -> Action (triggers)
        - Evidence -> Action (proves)
    """

    def __init__(self, flag_extractor: FlagExtractor) -> None:
        """Initialize builder with dependencies.

        Args:
            flag_extractor: Implementation of FlagExtractor protocol for
                           parsing 'when' conditions.
        """
        self._flag_extractor = flag_extractor

    def build(self, graph_input: GraphInput) -> nx.DiGraph:
        """Build compliance graph from GraphInput.

        Constructs a directed graph representing the compliance knowledge structure:
        - Nodes: Articles, Actions, Flags, Evidence
        - Edges: Action->Article (implements), Flag->Action (triggers), Evidence->Action (proves)

        Args:
            graph_input: Normalized input data (decoupled from YAML structure)
                containing articles, actions, flags, and evidence mappings.

        Returns:
            NetworkX DiGraph with Article, Action, Flag, and Evidence nodes,
            connected by semantic relationships. Each node has 'node_type' and 'id' attributes.

        Note:
            The graph is optimized for O(A) evidence linking using a pre-built
            article-to-actions index (E03 fix).
        """
        G = nx.DiGraph()

        # Pre-build article_id -> action_ids index for O(A) evidence linking (E03 fix)
        article_to_actions: dict[str, list[str]] = {}

        # 1. Add Article nodes
        self._add_article_nodes(G, graph_input)

        # 2. Add Action nodes and build article index
        self._add_action_nodes(G, graph_input, article_to_actions)

        # 3. Add Flag nodes
        self._add_flag_nodes(G, graph_input)

        # 4. Add Evidence nodes (using optimized index)
        self._add_evidence_nodes(G, graph_input, article_to_actions)

        logger.info(
            f"Built compliance graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges"
        )
        return G

    def _add_article_nodes(self, G: nx.DiGraph, graph_input: GraphInput) -> None:
        """Add Article nodes from graph input.

        Args:
            G: Graph to add nodes to.
            graph_input: Input data containing articles.
        """
        for article in graph_input.articles:
            G.add_node(
                article.id,
                node_type="Article",
                id=article.id,
                source="articles.yml",
                title=article.title,
                scope=article.scope,
                risk_level=article.risk_level,
            )

        logger.debug(f"Added {len(graph_input.articles)} Article nodes")

    def _add_action_nodes(
        self,
        G: nx.DiGraph,
        graph_input: GraphInput,
        article_to_actions: dict[str, list[str]],
    ) -> None:
        """Add Action nodes and 'implements' edges to Articles."""
        skipped_aliases = 0

        for action in graph_input.actions:
            # Skip aliases (E04: use centralized alias map)
            if graph_input.is_alias(action.id):
                skipped_aliases += 1
                continue

            G.add_node(
                action.id,
                node_type="Action",
                id=action.id,
                source="actions.yml",
                title=action.title,
                priority=action.priority,
                applies_to=action.applies_to,
            )

            # Link to Articles and build index for evidence linking
            for art_id in action.articles:
                if G.has_node(art_id):
                    G.add_edge(action.id, art_id, edge_type="implements")
                    # Build index for E03 optimization
                    if art_id not in article_to_actions:
                        article_to_actions[art_id] = []
                    article_to_actions[art_id].append(action.id)
                else:
                    logger.warning(f"Action {action.id} references non-existent Article {art_id}")

        logger.debug(
            f"Added {len(graph_input.actions) - skipped_aliases} Action nodes "
            f"(skipped {skipped_aliases} aliases)"
        )

    def _add_flag_nodes(self, G: nx.DiGraph, graph_input: GraphInput) -> None:
        """Add Flag nodes and 'triggers' edges from Action.when conditions."""
        flags_seen: set[str] = set()

        for action in graph_input.actions:
            # Skip aliases
            if graph_input.is_alias(action.id):
                continue

            if not action.when_condition:
                continue

            try:
                # E01 fix: Use injected extractor instead of direct import
                has_flags, _prefixes = self._flag_extractor.extract(action.when_condition)

                for flag in has_flags:
                    if flag not in flags_seen:
                        G.add_node(
                            flag,
                            node_type="Flag",
                            id=flag,
                            source="derived",
                        )
                        flags_seen.add(flag)

                    G.add_edge(flag, action.id, edge_type="triggers")

            except RuleParseError as e:
                # E13 fix: Catch only RuleParseError, not generic exceptions
                logger.warning(f"Failed to parse 'when' for {action.id}: {e}")

        logger.debug(f"Added {len(flags_seen)} Flag nodes")

    def _add_evidence_nodes(
        self,
        G: nx.DiGraph,
        graph_input: GraphInput,
        article_to_actions: dict[str, list[str]],
    ) -> None:
        """Add Evidence nodes and 'proves' edges to Actions.

        E03 fix: Uses pre-built article_to_actions index instead of
        O(E*N) iteration over all graph nodes.
        """
        evidence_count = 0

        for article_id, evidence_list in graph_input.evidence_map.items():
            for evidence_entry in evidence_list:
                evidence_id = f"FILE:{evidence_entry.path}"

                if not G.has_node(evidence_id):
                    G.add_node(
                        evidence_id,
                        node_type="Evidence",
                        id=evidence_id,
                        source="evidence_map.yml",
                        path=evidence_entry.path,
                        required=evidence_entry.required,
                    )
                    evidence_count += 1

                # E03 fix: Use index instead of iterating all nodes
                for action_id in article_to_actions.get(article_id, []):
                    G.add_edge(evidence_id, action_id, edge_type="proves")

        logger.debug(f"Added {evidence_count} Evidence nodes")
