# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""
Dev command group: build-framework, generate-template, concat, dead-code.

Developer/maintainer tools. Hidden from main help by default.
Use IPE_DEV_MODE=1 or --dev-commands to show in help.

These commands are NOT for end users - they're for framework maintainers.
"""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace

# Hide from help unless explicitly requested
_SHOW_DEV_ENV = os.environ.get("IPE_DEV_MODE") or os.environ.get("LEXOPS_DEV_MODE", "0")
_SHOW_DEV = _SHOW_DEV_ENV.lower() in ("1", "true", "yes")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register 'dev' command group (hidden by default)."""
    # Use SUPPRESS to hide from help unless dev mode is on
    help_text = "Developer/maintainer tools" if _SHOW_DEV else argparse.SUPPRESS

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
    _register_generate_template(dev_sub)
    _register_concat(dev_sub)
    _register_dead_code(dev_sub)
    _register_graph(dev_sub)
    _register_profile(dev_sub)

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

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        # Direct inline implementation to avoid script import issues
        import yaml
    except ImportError as e:
        sys.stderr.write(f"Error importing yaml: {e}\n")
        return 1

    try:
        base_dir = Path(args.framework).resolve()
        manifest_path = base_dir / "manifest.yml"
        from src.adapters.frameworks.layout_loader import load_framework_layout

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
# DEV GENERATE-TEMPLATE
# =============================================================================


def _register_generate_template(subparsers: argparse._SubParsersAction) -> None:
    """Register 'dev generate-template' command."""
    parser = subparsers.add_parser(
        "generate-template",
        help="Generate evidence template scaffolding",
        description="Create MD/CSV/JSON templates following patterns.",
    )
    parser.add_argument(
        "--id",
        required=True,
        help="Template ID (e.g., prv.rms.a9.risk-policy.v1.md)",
    )
    parser.add_argument(
        "--article",
        type=int,
        required=True,
        help="Article number",
    )
    parser.add_argument(
        "--actor",
        required=True,
        choices=["prv", "dep", "model", "importer", "distributor"],
        help="Actor type",
    )
    parser.add_argument(
        "--category",
        required=True,
        choices=["policy", "plan", "sop", "register"],
        help="Template category",
    )
    parser.add_argument(
        "--title",
        required=True,
        help="Human-readable title",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview output without writing",
    )
    parser.set_defaults(handler=_handle_generate_template)


def _handle_generate_template(args: Namespace) -> int:
    """Handle 'dev generate-template' command."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.generate_template import (
            generate_csv_register,
            generate_md_policy,
        )
    except ImportError as e:
        sys.stderr.write(f"Error importing generator: {e}\n")
        return 1

    output_dir = Path(args.output).resolve()

    # Determine file extension from ID
    template_id = args.id
    if template_id.endswith(".md"):
        content = generate_md_policy(template_id, args.title, args.article, args.actor)
    elif template_id.endswith(".csv"):
        content = generate_csv_register(template_id, args.category)
    else:
        sys.stderr.write(f"Unsupported template type: {template_id}\n")
        return 1

    if args.dry_run:
        print(f"--- {template_id} ---")
        print(content)
        print("--- END ---")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / template_id
    output_file.write_text(content, encoding="utf-8")
    print(f"✅ Generated {output_file}")
    return 0


# =============================================================================
# DEV CONCAT
# =============================================================================


def _register_concat(subparsers: argparse._SubParsersAction) -> None:
    """Register 'dev concat' command."""
    parser = subparsers.add_parser(
        "concat",
        help="Generate code snapshots for LLM context",
        description="Concatenate source files into review-friendly text files.",
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        default=["all"],
        choices=["frameworks", "templates", "src", "tests", "all"],
        help="Groups to generate (ignored if --preset is used)",
    )
    parser.add_argument(
        "--preset",
        choices=["fast", "code", "full"],
        help="Shortcut for common group combos (fast=frameworks, code=src+tests, full=all)",
    )
    parser.add_argument(
        "--base-dir",
        default=".",
        help="Project base directory",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        help="Skip files larger than this",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory where snapshot txt files will be written (default=base dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files per group without writing outputs",
    )
    parser.add_argument(
        "--list-groups",
        action="store_true",
        help="Show available groups/presets and exit",
    )
    parser.set_defaults(handler=_handle_concat)


def _handle_concat(args: Namespace) -> int:
    """Handle 'dev concat' command."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.concat import main as concat_main
    except ImportError as e:
        sys.stderr.write(f"Error importing concat: {e}\n")
        return 1

    # Build argv for the script
    argv = []
    if args.groups != ["all"]:
        argv.extend(["--groups"] + args.groups)
    if args.preset:
        argv.extend(["--preset", args.preset])
    if args.base_dir != ".":
        argv.extend(["--base-dir", args.base_dir])
    if args.max_file_bytes:
        argv.extend(["--max-file-bytes", str(args.max_file_bytes)])
    if args.output_dir:
        argv.extend(["--output-dir", args.output_dir])
    if args.dry_run:
        argv.append("--dry-run")
    if args.list_groups:
        argv.append("--list-groups")

    concat_main(argv)
    return 0


# =============================================================================
# DEV DEAD-CODE
# =============================================================================


def _register_dead_code(subparsers: argparse._SubParsersAction) -> None:
    """Register 'dev dead-code' command."""
    parser = subparsers.add_parser(
        "dead-code",
        help="Analyze codebase for unused symbols",
        description="Static analysis to find potentially dead code.",
    )
    parser.set_defaults(handler=_handle_dead_code)


def _handle_dead_code(args: Namespace) -> int:
    """Handle 'dev dead-code' command."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.dead_code_analysis import main as dead_code_main
    except ImportError as e:
        sys.stderr.write(f"Error importing analyzer: {e}\n")
        return 1

    dead_code_main()
    return 0


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

    from src.app.factories.graph_factory import build_compliance_graph
    from src.app.use_cases.bundle_orchestrator import BundleOrchestrator
    from src.domain.graph.export import export_graphml

    # Load bundle
    contracts_path = Path(args.contracts)
    if not contracts_path.exists():
        sys.stderr.write(f"Error: Contracts directory not found: {contracts_path}\n")
        return 1

    try:
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
        from src.domain.graph.filters import GraphFilter

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


# =============================================================================
# DEV PROFILE
# =============================================================================


def _register_profile(subparsers: argparse._SubParsersAction) -> None:
    """Register 'dev profile' command."""
    parser = subparsers.add_parser(
        "profile",
        help="Profile command performance with cProfile",
        description="Run performance profiling on CLI commands to identify bottlenecks.",
    )
    parser.add_argument(
        "--command",
        required=True,
        help="Full command to profile (e.g., 'export --contracts frameworks/starter --answers demos/example/answers.json --out out')",  # noqa: E501
    )
    parser.add_argument(
        "--prefix",
        default="profile",
        help="Output file prefix (default: profile)",
    )
    parser.add_argument(
        "--sort",
        default="cumulative",
        choices=["cumulative", "time", "calls", "name"],
        help="Sort key for report (default: cumulative)",
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=50,
        help="Number of lines to show in text report (default: 50)",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate HTML report (requires snakeviz for interactive view)",
    )
    parser.add_argument(
        "--callgraph",
        action="store_true",
        help="Generate call graph visualization (requires pycallgraph and graphviz)",
    )
    parser.set_defaults(handler=_handle_profile)


def _handle_profile(args: Namespace) -> int:
    """Handle 'dev profile' command."""
    import subprocess
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.cprofile_tool import profile_command
    except ImportError as e:
        sys.stderr.write(f"Error importing profile script: {e}\n")
        return 1

    # Parse command string into list
    command_parts = args.command.split()
    if not command_parts:
        sys.stderr.write("Error: Empty command\n")
        return 1

    # Prepend the public CLI if not present.
    if command_parts[0] not in ("ipe", "uv"):
        command_parts = ["ipe"] + command_parts

    # If it's a CLI invocation, prepend 'uv run'
    if command_parts[0] == "ipe":
        command_parts = ["uv", "run"] + command_parts

    try:
        profile_command(
            command=command_parts,
            output_prefix=args.prefix,
            sort_by=args.sort,
            lines=args.lines,
            html=args.html,
            callgraph=args.callgraph,
        )
        return 0
    except (subprocess.CalledProcessError, OSError, ValueError, ImportError) as e:
        sys.stderr.write(f"Error profiling command: {e}\n")
        import traceback

        traceback.print_exc()
        return 1
