# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Validate command group: contracts, evidence, templates, all.

Centralizes all integrity validation operations that were previously
scattered across multiple standalone scripts.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register 'validate' command group."""
    validate_parser = subparsers.add_parser(
        "validate",
        help="Run integrity validations",
        description="Validate contracts, evidence paths, and template integrity.",
    )
    validate_sub = validate_parser.add_subparsers(
        dest="validate_cmd",
        title="validation commands",
        description="Available validation targets",
    )

    _register_contracts(validate_sub)
    _register_evidence(validate_sub)
    _register_templates(validate_sub)
    _register_all(validate_sub)

    # Default handler when no subcommand given
    validate_parser.set_defaults(handler=_handle_validate_help, _parser=validate_parser)


def _handle_validate_help(args: Namespace) -> int:
    """Show help when 'validate' called without subcommand."""
    args._parser.print_help()
    return 0


# =============================================================================
# VALIDATE CONTRACTS
# =============================================================================


def _register_contracts(subparsers: argparse._SubParsersAction) -> None:
    """Register 'validate contracts' command."""
    parser = subparsers.add_parser(
        "contracts",
        help="Validate YAML contract files for consistency",
        description=(
            "Check actions.yml, rules.yml, articles.yml, flags.yml, dedups.yml "
            "for structural integrity, missing references, and cycles."
        ),
    )
    parser.add_argument(
        "--contracts",
        required=True,
        help="Path to framework pack directory",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (exit 1)",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human)",
    )
    parser.set_defaults(handler=_handle_contracts)


def _handle_contracts(args: Namespace) -> int:
    """Handle 'validate contracts' command."""
    import json
    import sys
    from pathlib import Path

    # Lazy import to keep CLI startup fast
    from src.adapters.contracts.yaml.yaml_contract_adapter import YamlContractsAdapter
    from src.app.use_cases import ops

    adapter = YamlContractsAdapter()
    contracts_dir = Path(args.contracts).resolve()

    if not contracts_dir.exists():
        sys.stderr.write(f"Error: Framework pack directory not found: {contracts_dir}\n")
        return 1

    if args.format == "json":
        probs_text, probs_struct = adapter.validate_detailed(
            str(contracts_dir),
            use_framework_schemas=args.strict,
            strict_schemas=args.strict,
        )
        has_probs = bool(probs_text) or bool(probs_struct)
        status = "OK" if not has_probs else "FAIL"
        payload = {
            "status": status,
            "problems": [p.__dict__ for p in probs_struct],
            "problems_text": probs_text,
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        if args.strict and (probs_text or probs_struct):
            return 1
        return 0

    # Human-readable output
    status, problems = ops.run_lint(adapter, contracts_dir, args.strict)

    if status == "OK":
        print("✅ Contracts validation passed")
        return 0
    else:
        print(f"❌ Contracts validation failed with {len(problems)} problems:\n")
        for p in problems[:20]:
            print(f"  • {p}")
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more")
        return 1 if args.strict else 0


# =============================================================================
# VALIDATE EVIDENCE
# =============================================================================


def _register_evidence(subparsers: argparse._SubParsersAction) -> None:
    """Register 'validate evidence' command."""
    parser = subparsers.add_parser(
        "evidence",
        help="Validate evidence_map.yml paths exist",
        description=(
            "Check that all paths declared in evidence_map.yml exist under evidence/templates/."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat optional missing files as errors",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human)",
    )
    parser.set_defaults(handler=_handle_evidence)


def _handle_evidence(args: Namespace) -> int:
    """Handle 'validate evidence' command."""
    import json
    import sys
    from dataclasses import asdict
    from pathlib import Path

    # Lazy import
    from src.adapters.export.base.evidence.evidence_utils import (
        collect_evidence_entries,
        load_evidence_map_raw,
        validate_evidence_paths,
    )
    from src.adapters.frameworks.layout_loader import load_framework_layout

    root = Path(args.root).resolve()
    layout = load_framework_layout(root / "frameworks" / "starter")
    evidence_map_files = layout.resolve_contract_files("evidence_map")
    evidence_map_path = (
        evidence_map_files[0] if evidence_map_files else layout.framework_dir / "evidence_map.yml"
    )
    evidence_base = layout.evidence_templates_dir

    if not evidence_map_path.exists():
        sys.stderr.write(f"Error: evidence_map.yml not found at {evidence_map_path}\n")
        return 1

    try:
        emap = load_evidence_map_raw(evidence_map_path)
        entries = collect_evidence_entries(emap)
        report = validate_evidence_paths(evidence_base, entries)
    except (FileNotFoundError, ValueError, OSError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    if args.format == "json":
        data = {
            "ok": report.ok(),
            "missing_required": [asdict(r) for r in report.missing_required],
            "missing_optional": [asdict(r) for r in report.missing_optional],
            "found_count": len(report.found),
            "total": report.total,
        }
        print(json.dumps(data, indent=2))
    else:
        # Human output
        if report.missing_required:
            print(f"❌ Missing REQUIRED evidence files ({len(report.missing_required)}):\n")
            for r in sorted(report.missing_required, key=lambda x: (x.article, x.path))[:10]:
                print(f"  [{r.article}] {r.path}")
            if len(report.missing_required) > 10:
                print(f"  ... and {len(report.missing_required) - 10} more")
        elif report.missing_optional:
            print(f"⚠ Missing optional files ({len(report.missing_optional)})")
        else:
            print(f"✅ All {len(report.found)} evidence paths validated")

    # Exit code
    if report.missing_required:
        return 1
    if args.strict and report.missing_optional:
        return 1
    return 0


# =============================================================================
# VALIDATE TEMPLATES
# =============================================================================


def _register_templates(subparsers: argparse._SubParsersAction) -> None:
    """Register 'validate templates' command."""
    parser = subparsers.add_parser(
        "templates",
        help="Validate template integrity vs contracts",
        description=(
            "Check that templates reference valid flags, actions, and docs. "
            "Detect dead code (defined but unused flags/actions)."
        ),
    )
    parser.add_argument(
        "--contracts",
        required=True,
        help="Path to framework pack directory",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (exit 1)",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human)",
    )
    parser.set_defaults(handler=_handle_templates)


def _handle_templates(args: Namespace) -> int:
    """Handle 'validate templates' command."""
    import json
    import sys
    from dataclasses import asdict
    from pathlib import Path

    # Import the validation logic from scripts (will be migrated later)
    # For now we call the script's main function
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

    try:
        from scripts.validate_template_integrity import validate_integrity
    except ImportError as e:
        sys.stderr.write(f"Error importing validator: {e}\n")
        return 1

    contracts_dir = Path(args.contracts).resolve()
    if not contracts_dir.exists():
        sys.stderr.write(f"Error: Framework pack directory not found: {contracts_dir}\n")
        return 1

    report = validate_integrity(contracts_dir)

    if args.format == "json":
        data = {
            "has_errors": bool(
                report.has_errors() if callable(report.has_errors) else report.has_errors
            ),
            "errors": [asdict(err) for err in report.errors],
            "warnings": [asdict(warn) for warn in report.warnings],
            "stats": report.stats,
        }
        print(json.dumps(data, indent=2))
    else:
        # Human output
        if report.errors:
            print(f"❌ Template integrity errors ({len(report.errors)}):\n")
            for err in report.errors[:10]:
                print(f"  [{err.category}] {err.location}: {err.message}")
            if len(report.errors) > 10:
                print(f"  ... and {len(report.errors) - 10} more")
        if report.warnings:
            print(f"\n⚠ Warnings ({len(report.warnings)}):")
            for warn in report.warnings[:5]:
                print(f"  [{warn.category}] {warn.location}: {warn.message}")
        if not report.errors and not report.warnings:
            print("✅ Template integrity validated")

    has_errors = bool(report.has_errors() if callable(report.has_errors) else report.has_errors)
    if has_errors:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


# =============================================================================
# VALIDATE ALL
# =============================================================================


def _register_all(subparsers: argparse._SubParsersAction) -> None:
    """Register 'validate all' command."""
    parser = subparsers.add_parser(
        "all",
        help="Run all validations (contracts, evidence, templates)",
        description="Execute all validation checks in sequence.",
    )
    parser.add_argument(
        "--contracts",
        required=True,
        help="Path to framework pack directory",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    parser.set_defaults(handler=_handle_all)


def _handle_all(args: Namespace) -> int:
    """Handle 'validate all' command."""
    from argparse import Namespace

    print("=" * 60)
    print("Running all validations...")
    print("=" * 60)

    exit_code = 0

    # 1. Contracts
    print("\n[1/3] Validating contracts...")
    contracts_args = Namespace(
        contracts=args.contracts,
        strict=args.strict,
        format="human",
    )
    result = _handle_contracts(contracts_args)
    if result != 0:
        exit_code = 1

    # 2. Evidence
    print("\n[2/3] Validating evidence paths...")
    evidence_args = Namespace(
        root=args.root,
        strict=args.strict,
        format="human",
    )
    result = _handle_evidence(evidence_args)
    if result != 0:
        exit_code = 1

    # 3. Templates
    print("\n[3/3] Validating template integrity...")
    templates_args = Namespace(
        contracts=args.contracts,
        strict=args.strict,
        format="human",
    )
    result = _handle_templates(templates_args)
    if result != 0:
        exit_code = 1

    # Summary
    print("\n" + "=" * 60)
    if exit_code == 0:
        print("✅ All validations passed")
    else:
        print("❌ Some validations failed")
    print("=" * 60)

    return exit_code
