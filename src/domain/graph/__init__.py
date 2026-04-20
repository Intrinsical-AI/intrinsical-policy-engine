# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Compliance knowledge graph construction and analysis.

This module provides tools for building and querying compliance graphs
based on framework contract bundles.

Architecture:
    - ComplianceGraphBuilder: Class-based builder with dependency injection
    - GraphInput: Decoupled input model (from src.domain.graph.models)
    - FlagExtractor: Protocol for parsing 'when' conditions (from ports)

Usage:
    >>> from src.domain.graph.adapters import BundleToGraphMapper, RuleEngineFlagExtractor
    >>> mapper = BundleToGraphMapper()
    >>> graph_input = mapper.map(bundle)
    >>> builder = ComplianceGraphBuilder(flag_extractor=RuleEngineFlagExtractor())
    >>> graph = builder.build(graph_input)
"""

from src.domain.graph.audit import audit_graph
from src.domain.graph.builder import ComplianceGraphBuilder
from src.domain.graph.export import export_graphml
from src.domain.graph.filters import GraphFilter, apply_filters_or, combine_filters, filter_graph
from src.domain.graph.models import ActionNode, ArticleNode, EvidenceEntry, GraphInput
from src.domain.graph.queries import GraphQueryEngine

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
