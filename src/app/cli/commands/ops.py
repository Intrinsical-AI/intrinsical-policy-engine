# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Ops command group: pdf, package, drift, render.

Operational commands for pipeline execution and artifact processing.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register 'ops' command group."""
    ops_parser = subparsers.add_parser(
        "ops",
        help="Operational commands (PDF, packaging, drift)",
        description="Pipeline and artifact processing operations.",
    )
    ops_sub = ops_parser.add_subparsers(
        dest="ops_cmd",
        title="operations",
        description="Available operations",
    )

    _register_pdf(ops_sub)
    _register_package(ops_sub)
    _register_drift(ops_sub)
    _register_render(ops_sub)

    ops_parser.set_defaults(handler=_handle_ops_help, _parser=ops_parser)


def _handle_ops_help(args: Namespace) -> int:
    """Show help when 'ops' called without subcommand."""
    args._parser.print_help()
    return 0


# =============================================================================
# OPS PDF
# =============================================================================


def _register_pdf(subparsers: argparse._SubParsersAction) -> None:
    """Register 'ops pdf' command."""
    parser = subparsers.add_parser(
        "pdf",
        help="Convert Markdown artifacts to PDF",
        description="Use Pandoc to convert .md files to .pdf with proper escaping.",
    )
    parser.add_argument(
        "--in",
        dest="input_dir",
        required=True,
        help="Input directory containing Markdown files",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory for PDFs",
    )
    parser.add_argument(
        "--engine",
        default="xelatex",
        choices=["xelatex", "pdflatex", "lualatex"],
        help="LaTeX engine to use (default: xelatex)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on conversion errors instead of skipping",
    )
    parser.set_defaults(handler=_handle_pdf)


def _handle_pdf(args: Namespace) -> int:
    """Handle 'ops pdf' command."""
    import sys
    from pathlib import Path

    # Import from scripts (will be migrated to src later)
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.convert_to_pdfs import check_dependencies, process_directory
    except ImportError as e:
        sys.stderr.write(f"Error importing PDF converter: {e}\n")
        return 1

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.out).resolve()

    if not input_dir.exists():
        sys.stderr.write(f"Error: Input directory not found: {input_dir}\n")
        return 1

    # Check dependencies
    try:
        check_dependencies(args.engine)
    except RuntimeError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    print("📄 Converting Markdown to PDF...")
    print(f"   Input:  {input_dir}")
    print(f"   Output: {output_dir}")

    success, failed = process_directory(input_dir, output_dir, args.engine, args.strict)

    print(f"\n✅ Converted: {success}")
    if failed:
        print(f"❌ Failed: {failed}")
        return 1 if args.strict else 0

    return 0


# =============================================================================
# OPS PACKAGE
# =============================================================================


def _register_package(subparsers: argparse._SubParsersAction) -> None:
    """Register 'ops package' command."""
    parser = subparsers.add_parser(
        "package",
        help="Package demo output for client delivery",
        description="Organize raw export into client/internal structure.",
    )
    parser.add_argument(
        "--raw",
        required=True,
        help="Path to raw export output",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Path to output directory",
    )
    parser.set_defaults(handler=_handle_package)


def _handle_package(args: Namespace) -> int:
    """Handle 'ops package' command."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.package_demo import package_demo
    except ImportError as e:
        sys.stderr.write(f"Error importing packager: {e}\n")
        return 1

    raw_dir = Path(args.raw).resolve()
    out_dir = Path(args.out).resolve()

    if not raw_dir.exists():
        sys.stderr.write(f"Error: Raw directory not found: {raw_dir}\n")
        return 1

    print(f"📦 Packaging demo from {raw_dir}")

    try:
        package_demo(raw_dir, out_dir)
        return 0
    except (OSError, RuntimeError, ValueError, TypeError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


# =============================================================================
# OPS DRIFT
# =============================================================================


def _register_drift(subparsers: argparse._SubParsersAction) -> None:
    """Register 'ops drift' command."""
    parser = subparsers.add_parser(
        "drift",
        help="Compare snapshots for regulatory drift",
        description="Detect changes between two compliance snapshots.",
    )
    parser.add_argument(
        "old",
        help="Path to old snapshot directory",
    )
    parser.add_argument(
        "new",
        help="Path to new snapshot directory",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format",
    )
    parser.set_defaults(handler=_handle_drift)


def _handle_drift(args: Namespace) -> int:
    """Handle 'ops drift' command."""
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.drift_compare import (
            compare_snapshots,
            format_human_report,
            load_snapshot,
        )
    except ImportError as e:
        sys.stderr.write(f"Error importing drift comparator: {e}\n")
        return 1

    old_path = Path(args.old).resolve()
    new_path = Path(args.new).resolve()

    for p, name in [(old_path, "old"), (new_path, "new")]:
        if not p.exists():
            sys.stderr.write(f"Error: {name} snapshot not found: {p}\n")
            return 1

    try:
        old_snap = load_snapshot(old_path)
        new_snap = load_snapshot(new_path)
        report = compare_snapshots(old_snap, new_snap)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        sys.stderr.write(f"Error loading snapshots: {e}\n")
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(format_human_report(report))

    # Exit code based on drift severity
    if report.get("has_drift", False):
        severity = report.get("severity", "low")
        if severity in ("high", "critical"):
            return 1
    return 0


# =============================================================================
# OPS RENDER
# =============================================================================


def _register_render(subparsers: argparse._SubParsersAction) -> None:
    """Register 'ops render' command."""
    parser = subparsers.add_parser(
        "render",
        help="Render Jinja templates to artifacts",
        description="Transform template files using plan context.",
    )
    parser.add_argument(
        "--templates",
        required=True,
        help="Templates directory",
    )
    parser.add_argument(
        "--plan",
        required=True,
        help="Path to plan JSON file (usually out/_metadata/summary.json)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on missing template variables",
    )
    parser.add_argument(
        "--include-internals",
        action="store_true",
        help="Include _partials, _deprecated, TODO.md",
    )
    parser.set_defaults(handler=_handle_render)


def _validate_render_inputs(templates_dir: Path, plan_path: Path) -> int:
    """Validate render command inputs."""
    import sys

    for p, name in [(templates_dir, "templates"), (plan_path, "plan")]:
        if not p.exists():
            sys.stderr.write(f"Error: {name} not found: {p}\n")
            return 1
    return 0


def _print_render_warnings(result, strict: bool) -> None:
    """Print warnings about incomplete artifacts."""
    print("⚠️  Warning: Incomplete artifacts detected")
    if result.warnings:
        print(f"   Validation warnings: {len(result.warnings)}")
        for warning in result.warnings[:5]:
            print(f"     - {warning}")
    if result.missing_fields:
        print(f"   Missing fields: {len(result.missing_fields)}")
        for field in result.missing_fields[:5]:
            print(f"     - {field}")
    if result.incomplete_files:
        print(f"   Incomplete files: {len(result.incomplete_files)}")
        for file in result.incomplete_files[:5]:
            print(f"     - {file}")
    if not strict:
        print("   Note: Run with --strict to fail on missing fields and validation issues")


def _print_render_errors(result) -> None:
    """Print render errors and diagnostics."""
    print(f"❌ Render failed with {len(result.errors)} errors:")
    for e in result.errors[:5]:
        print(f"  - {e}")
    if result.warnings:
        print(f"\n⚠️  Warnings: {len(result.warnings)}")
        for warning in result.warnings[:5]:
            print(f"  - {warning}")
    if result.missing_fields:
        print(f"\nMissing fields: {', '.join(result.missing_fields[:10])}")
    if result.incomplete_files:
        print(f"Incomplete files: {', '.join(result.incomplete_files[:10])}")


def _handle_render(args: Namespace) -> int:
    """Handle 'ops render' command."""
    # Use the module in src directly
    from src.app.rendering.artifact_renderer import render_artifacts

    templates_dir = Path(args.templates).resolve()
    plan_path = Path(args.plan).resolve()
    out_dir = Path(args.out).resolve()

    if _validate_render_inputs(templates_dir, plan_path) != 0:
        return 1

    print("📝 Rendering artifacts...")
    print(f"   Templates: {templates_dir}")
    print(f"   Plan:      {plan_path}")
    print(f"   Output:    {out_dir}")

    result = render_artifacts(
        templates_dir=str(templates_dir),
        plan_json=str(plan_path),
        out_dir=str(out_dir),
        strict=args.strict,
        include_internals=args.include_internals,
    )

    if result.success:
        total_written = result.files_rendered + result.files_copied
        print(f"✅ Rendered {total_written} files")

        has_warnings = result.warnings or result.missing_fields or result.incomplete_files
        if has_warnings:
            _print_render_warnings(result, args.strict)
        return 0

    _print_render_errors(result)
    return 1
