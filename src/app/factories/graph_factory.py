# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Factory for building compliance knowledge graphs.

This module provides the application-level factory for constructing compliance
graphs from ContractBundles. It orchestrates the mapping from YAML structure
to the domain's GraphInput model.

Usage:
    >>> from src.app.factories import build_compliance_graph
    >>> from src.adapters.contracts.yaml import YamlContractsAdapter
    >>> bundle = YamlContractsAdapter().load("frameworks/starter")
    >>> graph = build_compliance_graph(bundle)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import networkx as nx

from src.domain.graph import ComplianceGraphBuilder, GraphInput
from src.domain.graph.models import ActionNode, ArticleNode, EvidenceEntry

if TYPE_CHECKING:
    from src.domain.graph.filters import GraphFilter
    from src.domain.ports import ContractBundle

logger = logging.getLogger(__name__)


# ============================================================================
# Risk Configuration
# ============================================================================


@dataclass(frozen=True)
class RiskConfig:
    """Configuration for risk level detection.

    Can be loaded from YAML or constructed programmatically.
    Default values are used if no configuration is provided.
    """

    review_prefixes: tuple[str, ...] = ("classification.", "blocked.")
    blocked_flags: frozenset[str] = frozenset(
        {
            "blocked.unsupported_use",
            "blocked.unsupported_context",
            "blocked.missing_required_controls",
        }
    )

    @classmethod
    def from_dict(cls, data: dict) -> RiskConfig:
        """Create RiskConfig from dictionary (e.g., from YAML)."""
        return cls(
            review_prefixes=tuple(data.get("review_prefixes", cls.review_prefixes)),
            blocked_flags=frozenset(data.get("blocked_flags", cls.blocked_flags)),
        )

    @classmethod
    def from_bundle(cls, bundle: ContractBundle) -> RiskConfig:
        """Load RiskConfig from bundle's risk_config if available.

        Uses in-memory bundle.risk_config (loaded by adapter) for reproducibility.
        The adapter must load risk_config.yml into the bundle to ensure
        RiskConfig is covered by bundle_hash (ENGINE ARCHITECTURE v1, INV-05).

        If risk_config is required, ensure the adapter loads it before calling this.
        """
        # Use pre-loaded risk_config in bundle (hashed by adapter)
        if hasattr(bundle, "risk_config") and bundle.risk_config:
            return cls.from_dict(bundle.risk_config)

        # No fallback - return defaults (risk_config is optional)
        # The adapter loads risk_config.yml into bundle.risk_config at load time
        return cls()


# ============================================================================
# Risk Resolver
# ============================================================================


class FlagBasedRiskResolver:
    """Determines risk level based on flag patterns.

    Uses configurable prefixes and blocked flag sets.
    """

    def __init__(self, config: RiskConfig | None = None):
        """Store risk configuration (or defaults) for later evaluations."""
        self._config = config or RiskConfig()

    def resolve(self, article_id: str, flags: set[str]) -> str:
        """Determine risk level based on flags.

        Args:
            article_id: Article identifier
            flags: Set of flag IDs from the registry

        Returns:
            "blocked", "high", or "standard"
        """
        # Check for blocked flags.
        if any(f in self._config.blocked_flags for f in flags):
            return "blocked"

        # Check for review indicators
        if article_id.upper().startswith("SECTION-III"):
            return "high"

        # Check if any review flags reference this article pattern
        for flag in flags:
            for prefix in self._config.review_prefixes:
                if flag.startswith(prefix):
                    return "high"

        return "standard"


# ============================================================================
# Bundle to Graph Mapper
# ============================================================================


class BundleToGraphMapper:
    """Maps ContractBundle to GraphInput.

    Converts the YAML-loaded ContractBundle structure into the clean
    GraphInput model, centralizing:
    - Risk level detection
    - Alias map construction
    - Evidence normalization
    """

    def __init__(self, risk_resolver: FlagBasedRiskResolver | None = None):
        """Inject the risk resolver used while normalizing bundle data."""
        self._risk_resolver = risk_resolver or FlagBasedRiskResolver()

    def map(self, bundle: ContractBundle) -> GraphInput:
        """Convert ContractBundle to GraphInput.

        Args:
            bundle: Loaded contract bundle from YAML adapter

        Returns:
            GraphInput with normalized, decoupled data
        """
        # Extract flag IDs for risk resolution
        flag_ids = self._extract_flag_ids(bundle)

        # Build articles
        articles = self._map_articles(bundle, flag_ids)

        # Build alias map
        alias_to_canonical = self._build_alias_map(bundle)

        # Build actions
        actions = self._map_actions(bundle)

        # Build evidence map
        evidence_map = self._map_evidence(bundle)

        return GraphInput(
            articles=articles,
            actions=actions,
            evidence_map=evidence_map,
            alias_to_canonical=alias_to_canonical,
        )

    def _extract_flag_ids(self, bundle: ContractBundle) -> set[str]:
        """Extract all flag IDs from the registry."""
        if not bundle.flags:
            return set()
        return {entry.id for entry in bundle.flags.registry if entry.id}

    def _map_articles(self, bundle: ContractBundle, flag_ids: set[str]) -> list[ArticleNode]:
        """Map articles from bundle taxonomy."""
        if not bundle.articles:
            return []
        articles = []

        for article in bundle.articles.taxonomy:
            if not article.id:
                continue

            risk_level = self._risk_resolver.resolve(article.id, flag_ids)

            articles.append(
                ArticleNode(
                    id=article.id,
                    title=article.title,
                    risk_level=risk_level,
                    scope=getattr(article, "scope", ""),
                )
            )

        return articles

    def _build_alias_map(self, bundle: ContractBundle) -> dict[str, str]:
        """Build alias -> canonical mapping from dedups."""
        alias_map: dict[str, str] = {}
        if not bundle.dedups:
            return alias_map

        for mapping in bundle.dedups.mappings:
            if mapping.alias and mapping.canonical:
                alias_map[mapping.alias] = mapping.canonical

        return alias_map

    def _map_actions(self, bundle: ContractBundle) -> list[ActionNode]:
        """Map actions from bundle."""
        if not bundle.actions:
            return []
        actions = []

        for action in bundle.actions.actions:
            if not action.id:
                continue

            raw_applies_to = action.applies_to
            if isinstance(raw_applies_to, list):
                applies_to: str | tuple[str, ...] = tuple(raw_applies_to)
            elif raw_applies_to:
                applies_to = raw_applies_to
            else:
                applies_to = "any"

            actions.append(
                ActionNode(
                    id=action.id,
                    title=action.title,
                    articles=tuple(action.articles or []),
                    when_condition=action.when,
                    priority=action.priority,
                    applies_to=applies_to,
                )
            )

        return actions

    def _map_evidence(self, bundle: ContractBundle) -> dict[str, list[EvidenceEntry]]:
        """Map evidence from bundle evidence_map."""
        evidence_map: dict[str, list[EvidenceEntry]] = {}

        for article_id, evidence_list in (bundle.evidence_map or {}).items():
            if not evidence_list:
                continue

            entries = []
            for evidence_entry in evidence_list:
                if isinstance(evidence_entry, dict):
                    path = evidence_entry.get("path", "")
                    required = evidence_entry.get("required", True)
                else:
                    path = str(evidence_entry)
                    required = True

                if path:
                    entries.append(EvidenceEntry(path=path, required=required))

            if entries:
                evidence_map[article_id] = entries

        return evidence_map


# ============================================================================
# Graph Factory
# ============================================================================


class GraphFactory:
    """Factory for creating compliance graphs with configurable components.

    Allows injection of custom risk configuration and flag extractors.
    """

    def __init__(
        self,
        risk_config: RiskConfig | None = None,
    ):
        """Instantiate mapper/resolver stacks using the provided risk config."""
        self._risk_config = risk_config or RiskConfig()
        self._risk_resolver = FlagBasedRiskResolver(self._risk_config)
        self._mapper = BundleToGraphMapper(self._risk_resolver)

    def build(self, bundle: ContractBundle) -> nx.DiGraph:
        """Build compliance graph from ContractBundle.

        Args:
            bundle: Contract bundle loaded from YAML

        Returns:
            NetworkX DiGraph with compliance structure
        """
        from src.domain.graph.adapters import RuleEngineFlagExtractor

        # Convert bundle to decoupled GraphInput
        graph_input = self._mapper.map(bundle)

        # Build using injected dependencies
        builder = ComplianceGraphBuilder(flag_extractor=RuleEngineFlagExtractor())
        return builder.build(graph_input)


# ============================================================================
# Convenience Function
# ============================================================================


def build_compliance_graph(
    bundle: ContractBundle,
    risk_config: RiskConfig | None = None,
    graph_filter: GraphFilter | None = None,
) -> nx.DiGraph:
    """Build compliance graph from ContractBundle.

    This is the main entry point for graph construction.
    If risk_config is not provided, attempts to load from bundle's
    risk_config.yml, falling back to defaults.

    Args:
        bundle: Contract bundle loaded from YAML
        risk_config: Optional custom risk configuration
        graph_filter: Optional filter configuration for building focused subgraphs

    Returns:
        NetworkX DiGraph with compliance structure (filtered if graph_filter provided)

    Example:
        >>> from src.adapters.contracts.yaml import YamlContractsAdapter
        >>> from src.domain.graph.filters import GraphFilter
        >>> bundle = YamlContractsAdapter().load("frameworks/starter")
        >>>
        >>> # Full graph
        >>> graph = build_compliance_graph(bundle)
        >>>
        >>> # Filtered: only review articles and actions
        >>> filter_config = GraphFilter(
        ...     include_risk_levels={"high", "blocked"},
        ...     include_node_types={"Article", "Action"},
        ...     transitive=True
        ... )
        >>> filtered_graph = build_compliance_graph(bundle, graph_filter=filter_config)
    """
    # Auto-load risk config from bundle if not provided
    if risk_config is None:
        risk_config = RiskConfig.from_bundle(bundle)

    factory = GraphFactory(risk_config=risk_config)
    graph = factory.build(bundle)

    # Apply filter if provided
    if graph_filter is not None:
        from src.domain.graph.filters import filter_graph

        graph = filter_graph(graph, graph_filter)

    return graph
