# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""
Dev command group: build-framework and graph.

Developer/maintainer tools. Hidden from main help by default.
Use IPE_DEV_MODE=1 or --dev-commands to show in help.

These commands are NOT for end users - they're for framework maintainers.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace


def register(subparsers: argparse._SubParsersAction, *, show_dev: bool = False) -> None:
    """Register 'dev' command group (hidden by default)."""
    # Use SUPPRESS to hide from help unless dev mode is on
    help_text = "Developer/maintainer tools" if show_dev else argparse.SUPPRESS

    dev_parser = subparsers.add_parser(
        "dev",
        help=help_text,
        description="Tools for framework maintainers (not for end users).",
    )
    dev_sub = dev_parser.add_subparsers(
        dest="dev_cmd",
        title="developer commands",
        description="Available developer tools",
    )

    _register_build_framework(dev_sub)
    _register_graph(dev_sub)

    dev_parser.set_defaults(handler=_handle_dev_help, _parser=dev_parser)


def _handle_dev_help(args: Namespace) -> int:
    """Show help when 'dev' called without subcommand."""
    args._parser.print_help()
    return 0


# =============================================================================
# DEV BUILD-FRAMEWORK
# =============================================================================


def _register_build_framework(subparsers: argparse._SubParsersAction) -> None:
    """Register 'dev build-framework' command."""
    parser = subparsers.add_parser(
        "build-framework",
        help="Build actions.yml from module manifest",
        description="Combine modular action files into unified actions.yml.",
    )
    parser.add_argument(
        "--framework",
        required=True,
        help="Framework directory containing manifest.yml",
    )
    parser.set_defaults(handler=_handle_build_framework)


def _handle_build_framework(args: Namespace) -> int:
    """Handle 'dev build-framework' command."""
    import sys
    from pathlib import Path

    try:
        # Runtime dependency used by the canonical framework builder.
        import yaml
    except ImportError as e:
        sys.stderr.write(f"Error importing yaml: {e}\n")
        return 1

    try:
        base_dir = Path(args.framework).resolve()
        manifest_path = base_dir / "manifest.yml"
        from intrinsical_policy_engine.adapters.frameworks.layout_loader import (
            load_framework_layout,
        )
        from intrinsical_policy_engine.api.packs import FilesystemPackProvider

        FilesystemPackProvider().resolve(base_dir)
        layout = load_framework_layout(base_dir)
        action_files = layout.resolve_contract_files("actions")
        output_path = (
            action_files[0] if action_files else (base_dir / "law" / "core" / "actions.yml")
        )

        if not manifest_path.exists():
            sys.stderr.write(f"Error: Manifest not found at {manifest_path}\n")
            return 1

        with open(manifest_path, encoding="utf-8") as f:
            manifest = yaml.safe_load(f)

        modules = manifest.get("modules_included", [])
        print(f"Building actions.yml from {len(modules)} modules...")

        combined_actions = []
        seen_ids = set()

        for mod_rel_path in modules:
            mod_path = base_dir / mod_rel_path
            if not mod_path.exists():
                print(f"  ⚠ Module {mod_rel_path} not found, skipping")
                continue

            print(f"  • Processing {mod_rel_path}")
            with open(mod_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            for action in data.get("actions", []):
                aid = action.get("id")
                if aid in seen_ids:
                    sys.stderr.write(f"Error: Duplicate action ID '{aid}'\n")
                    return 1
                seen_ids.add(aid)
                combined_actions.append(action)

        final_doc = {
            "version": manifest.get("version", "1.0.0"),
            "schema": "actions/v1",
            "actions": combined_actions,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(final_doc, f, sort_keys=False, allow_unicode=True, width=1000)

        print(f"✅ Built {output_path} with {len(combined_actions)} actions")
        return 0

    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


# =============================================================================
# DEV GRAPH
# =============================================================================


def _register_graph(subparsers: argparse._SubParsersAction) -> None:
    """Register 'dev graph' command."""
    parser = subparsers.add_parser(
        "graph",
        help="Generate compliance graph visualization",
        description="Build and export compliance knowledge graph to GraphML format.",
    )
    parser.add_argument(
        "--contracts",
        required=True,
        help="Path to framework pack directory (must include manifest.yml)",
    )
    parser.add_argument(
        "--output",
        default="out/compliance_graph.graphml",
        help="Output GraphML file path (default: out/compliance_graph.graphml)",
    )

    # Filtering options
    filter_group = parser.add_argument_group(
        "filtering", "Filter graph nodes and edges (all filters combine with AND logic)"
    )
    filter_group.add_argument(
        "--include-node-types",
        nargs="+",
        choices=["Article", "Action", "Flag", "Evidence"],
        help="Include only these node types (default: all)",
    )
    filter_group.add_argument(
        "--include-articles",
        nargs="+",
        help=(
            "Include articles matching these IDs or patterns "
            "(supports wildcards, e.g., TOPIC-8*, SECTION-*)"
        ),
    )
    filter_group.add_argument(
        "--include-priorities",
        nargs="+",
        choices=["critical", "high", "medium", "low"],
        help="Include actions with these priorities",
    )
    filter_group.add_argument(
        "--include-applies-to",
        nargs="+",
        help="Include actions that apply to these roles (e.g., provider, deployer)",
    )
    filter_group.add_argument(
        "--include-risk-levels",
        nargs="+",
        choices=["high", "standard", "blocked"],
        help="Include articles with these risk levels",
    )
    filter_group.add_argument(
        "--include-flags",
        nargs="+",
        help=(
            "Include actions triggered by these flags "
            "(supports wildcards, e.g., role.source, classification.*)"
        ),
    )
    filter_group.add_argument(
        "--exclude-node-types",
        nargs="+",
        choices=["Article", "Action", "Flag", "Evidence"],
        help="Exclude these node types",
    )
    filter_group.add_argument(
        "--exclude-articles",
        nargs="+",
        help="Exclude articles matching these IDs or patterns",
    )
    filter_group.add_argument(
        "--no-evidence",
        action="store_true",
        help="Exclude Evidence nodes",
    )
    filter_group.add_argument(
        "--no-transitive",
        action="store_true",
        help="Disable transitive filtering (don't include related nodes)",
    )
    filter_group.add_argument(
        "--exclude-completed-actions",
        action="store_true",
        help="Exclude actions that have evidence connected (progress dashboard view)",
    )
    filter_group.add_argument(
        "--include-only-missing-evidence",
        action="store_true",
        help="Include only actions without evidence (missing evidence view)",
    )

    parser.set_defaults(handler=_handle_graph)


def _handle_graph(args: Namespace) -> int:
    """Handle 'dev graph' command.

    Builds a compliance knowledge graph from contract bundle and exports it
    to GraphML format for visualization in tools like Gephi or Cytoscape.

    The graph includes:
    - Article nodes (TOPIC-XX)
    - Action nodes (ACTION-ID)
    - Flag nodes (flag.name)
    - Evidence nodes (FILE:path)
    - Edges: Action->Article (implements), Flag->Action (triggers), Evidence->Action (proves)
    """
    import sys
    from pathlib import Path

    from intrinsical_policy_engine.api.packs import FilesystemPackProvider
    from intrinsical_policy_engine.app.factories.graph_factory import build_compliance_graph
    from intrinsical_policy_engine.app.use_cases.bundle_orchestrator import BundleOrchestrator
    from intrinsical_policy_engine.domain.graph.export import export_graphml

    # Load bundle
    contracts_path = Path(args.contracts)
    if not contracts_path.exists():
        sys.stderr.write(f"Error: Contracts directory not found: {contracts_path}\n")
        return 1

    try:
        FilesystemPackProvider().resolve(contracts_path)
        orchestrator = BundleOrchestrator(strict=False)
        bundle_result = orchestrator.load_and_validate_complete_bundle(contracts_path)
        bundle = bundle_result.contract_bundle

        if bundle_result.validation_report.has_errors():
            sys.stderr.write(
                f"Warning: Bundle validation issues detected:\n"
                f"{bundle_result.validation_report.summary()}\n"
            )

    except (ValueError, FileNotFoundError, OSError) as e:
        sys.stderr.write(f"Error loading bundle: {e}\n")
        return 1

    # Build filter configuration from CLI arguments
    graph_filter = None
    if any(
        [
            args.include_node_types,
            args.include_articles,
            args.include_priorities,
            args.include_applies_to,
            args.include_risk_levels,
            args.include_flags,
            args.exclude_node_types,
            args.exclude_articles,
            args.no_evidence,
            args.exclude_completed_actions,
            args.include_only_missing_evidence,
        ]
    ):
        from intrinsical_policy_engine.domain.graph.filters import GraphFilter

        graph_filter = GraphFilter(
            include_node_types=set(args.include_node_types) if args.include_node_types else None,
            exclude_node_types=set(args.exclude_node_types) if args.exclude_node_types else None,
            include_articles=set(args.include_articles) if args.include_articles else None,
            exclude_articles=set(args.exclude_articles) if args.exclude_articles else None,
            include_priorities=set(args.include_priorities) if args.include_priorities else None,
            include_applies_to=set(args.include_applies_to) if args.include_applies_to else None,
            include_risk_levels=set(args.include_risk_levels) if args.include_risk_levels else None,
            include_flags=set(args.include_flags) if args.include_flags else None,
            include_evidence=not args.no_evidence,
            exclude_completed_actions=args.exclude_completed_actions,
            include_only_missing_evidence=args.include_only_missing_evidence,
            transitive=not args.no_transitive,
        )

    # Build graph
    try:
        graph = build_compliance_graph(bundle, graph_filter=graph_filter)
        sys.stdout.write(
            f"Built compliance graph: {graph.number_of_nodes()} nodes, "
            f"{graph.number_of_edges()} edges\n"
        )
    except (ValueError, RuntimeError, AttributeError) as e:
        sys.stderr.write(f"Error building graph: {e}\n")
        return 1

    # Export to GraphML
    try:
        output_path = Path(args.output)
        export_graphml(graph, str(output_path))
        sys.stdout.write(f"Exported graph to: {output_path}\n")
        sys.stdout.write(
            "Open with Gephi (https://gephi.org) or Cytoscape (https://cytoscape.org)\n"
        )
    except (OSError, ValueError, RuntimeError) as e:
        sys.stderr.write(f"Error exporting graph: {e}\n")
        return 1

    return 0
