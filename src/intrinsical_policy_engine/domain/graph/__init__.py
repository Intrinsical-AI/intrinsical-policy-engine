# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Compliance knowledge graph construction and analysis.

This module provides tools for building and querying compliance graphs
based on framework contract bundles.

Architecture:
    - ComplianceGraphBuilder: Class-based builder with dependency injection
    - GraphInput: Decoupled input model (from intrinsical_policy_engine.domain.graph.models)
    - FlagExtractor: Protocol for parsing 'when' conditions (from ports)

Usage:
    >>> from intrinsical_policy_engine.domain.graph.adapters import BundleToGraphMapper
    >>> from intrinsical_policy_engine.domain.graph.adapters import RuleEngineFlagExtractor
    >>> mapper = BundleToGraphMapper()
    >>> graph_input = mapper.map(bundle)
    >>> builder = ComplianceGraphBuilder(flag_extractor=RuleEngineFlagExtractor())
    >>> graph = builder.build(graph_input)
"""

from intrinsical_policy_engine.domain.graph.audit import audit_graph
from intrinsical_policy_engine.domain.graph.builder import ComplianceGraphBuilder
from intrinsical_policy_engine.domain.graph.export import export_graphml
from intrinsical_policy_engine.domain.graph.filters import (
    GraphFilter,
    apply_filters_or,
    combine_filters,
    filter_graph,
)
from intrinsical_policy_engine.domain.graph.models import (
    ActionNode,
    ArticleNode,
    EvidenceEntry,
    GraphInput,
)
from intrinsical_policy_engine.domain.graph.queries import GraphQueryEngine

__all__ = [
    "ActionNode",
    "ArticleNode",
    "ComplianceGraphBuilder",
    "EvidenceEntry",
    "GraphFilter",
    "GraphInput",
    "GraphQueryEngine",
    "apply_filters_or",
    "audit_graph",
    "combine_filters",
    "export_graphml",
    "filter_graph",
]
