# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Export compliance graph to GraphML format."""

import logging
from datetime import UTC, datetime
from pathlib import Path

import networkx as nx

logger = logging.getLogger(__name__)


def export_graphml(G: nx.DiGraph, output_path: str) -> None:
    """Export graph to GraphML format for Gephi/Cytoscape.

    Args:
        G: Compliance graph
        output_path: Path to output .graphml file

    Example:
        >>> export_graphml(G, "out/compliance_graph.graphml")
    """
    path_obj = Path(output_path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)

    # Add graph-level metadata
    G.graph["title"] = "Intrinsical Policy Engine Knowledge Graph"
    G.graph["created"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    G.graph["node_count"] = G.number_of_nodes()
    G.graph["edge_count"] = G.number_of_edges()

    # Write to GraphML
    nx.write_graphml(G, str(path_obj))

    logger.info(f"Exported graph to {path_obj}")
