# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Ops command group for runtime-backed artifact rendering."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the supported ``ops render`` command group."""
    ops_parser = subparsers.add_parser(
        "ops",
        help="Operational template rendering",
        description="Render framework templates using a generated plan.",
    )
    ops_sub = ops_parser.add_subparsers(
        dest="ops_cmd",
        title="operations",
        description="Available operations",
    )

    _register_render(ops_sub)

    ops_parser.set_defaults(handler=_handle_ops_help, _parser=ops_parser)


def _handle_ops_help(args: Namespace) -> int:
    """Show help when ``ops`` is called without a subcommand."""
    args._parser.print_help()
    return 0


def _register_render(subparsers: argparse._SubParsersAction) -> None:
    """Register the runtime-backed ``ops render`` command."""
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

    for path, name in [(templates_dir, "templates"), (plan_path, "plan")]:
        if not path.exists():
            sys.stderr.write(f"Error: {name} not found: {path}\n")
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
    for error in result.errors[:5]:
        print(f"  - {error}")
    if result.warnings:
        print(f"\n⚠️  Warnings: {len(result.warnings)}")
        for warning in result.warnings[:5]:
            print(f"  - {warning}")
    if result.missing_fields:
        print(f"\nMissing fields: {', '.join(result.missing_fields[:10])}")
    if result.incomplete_files:
        print(f"Incomplete files: {', '.join(result.incomplete_files[:10])}")


def _handle_render(args: Namespace) -> int:
    """Render artifacts using the canonical runtime implementation."""
    from intrinsical_policy_engine.app.rendering.artifact_renderer import render_artifacts

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
