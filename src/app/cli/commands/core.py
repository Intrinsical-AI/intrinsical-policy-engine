# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Core CLI commands: lint, assess, export, seal, ui, wizard, inspect.

These are the primary command handlers registered at the top level.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from src.adapters.frameworks.layout_loader import load_framework_layout

if TYPE_CHECKING:
    from argparse import Namespace


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register core commands."""
    _register_lint(subparsers)
    _register_assess(subparsers)
    _register_export(subparsers)
    _register_seal(subparsers)
    _register_ui(subparsers)
    _register_wizard(subparsers)
    _register_inspect(subparsers)


# =============================================================================
# LINT
# =============================================================================


def _register_lint(subparsers: argparse._SubParsersAction) -> None:
    """Register 'lint' command."""
    parser = subparsers.add_parser(
        "lint",
        help="Validate contract YAML files for consistency",
        description="Run schema and business validation on contracts.",
    )
    parser.add_argument(
        "--contracts",
        required=True,
        help="Path to framework pack directory (manifest.yml + declared pack layout)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (exit 1)",
    )
    parser.set_defaults(handler=_handle_lint)


def _handle_lint(args: Namespace) -> int:
    """Handle 'lint' command.

    Validates contract YAML files for consistency using schema and business
    validation. Emits structured JSON output.

        Args:
            args: Parsed command-line arguments containing:
            - contracts: Path to framework pack directory
            - strict: Whether to treat warnings as errors

    Returns:
        Exit code: 0 on success, 1 if validation fails (or warnings in strict mode).
    """
    # Lazy import to avoid slow startup
    import json
    import sys

    from src.adapters.contracts.yaml.yaml_contract_adapter import YamlContractsAdapter
    from src.app.config.paths import resolve_contracts_path
    from src.app.use_cases import ops

    adapter = YamlContractsAdapter()
    contracts_dir = resolve_contracts_path(args.contracts)

    status, problems = ops.run_lint(adapter, contracts_dir, args.strict)
    sys.stdout.write(json.dumps({"status": status, "problems": problems}, indent=2) + "\n")
    if args.strict and problems:
        return 1
    return 0


# =============================================================================
# ASSESS
# =============================================================================


def _register_assess(subparsers: argparse._SubParsersAction) -> None:
    """Register 'assess' command."""
    parser = subparsers.add_parser(
        "assess",
        help="Run compliance assessment and generate plan",
        description="Evaluate answers against contracts and produce a compliance plan.",
    )
    _add_common_args(parser)
    parser.set_defaults(handler=_handle_assess)


def _handle_assess(args: Namespace) -> int:
    """Handle 'assess' command.

    Runs compliance assessment and generates a compliance plan from user
    answers and contract bundle. Optionally performs CI compliance checks
    and saves the plan for later use.

    Args:
        args: Parsed command-line arguments containing:
            - contracts: Path to framework pack directory
            - answers: Path to answers JSON/YAML file
            - out: Output directory path
            - strict: Whether to enforce strict validation
            - check_ci: Whether to run CI compliance checks
            - save_plan: Whether to persist plan snapshot
            - debug: Whether to include full trace in output

    Returns:
        Exit code: 0 on success, 1 if assessment fails or CI checks fail.
    """
    from src.app.cli.commands._helpers import (
        check_ci_compliance,
        load_bundle_and_answers,
        run_assessment,
    )
    from src.app.config.paths import resolve_contracts_path
    from src.app.use_cases import ops

    # Load bundle and answers
    bundle, raw_answers, outdir, logger = load_bundle_and_answers(args)
    contracts_dir = resolve_contracts_path(args.contracts)

    # Run assessment
    plan = run_assessment(bundle, raw_answers, contracts_dir, logger, args)

    # CI Check
    strict = getattr(args, "strict", False)
    if getattr(args, "check_ci", False):
        ci_result = check_ci_compliance(plan, strict)
        if ci_result != 0:
            return ci_result

    # Save plan
    save_plan = getattr(args, "save_plan", False)
    ops.write_assess(plan, outdir, logger, save_plan)
    return 0


# =============================================================================
# EXPORT
# =============================================================================


def _register_export(subparsers: argparse._SubParsersAction) -> None:
    """Register 'export' command."""
    parser = subparsers.add_parser(
        "export",
        help="Export compliance artifacts to filesystem/integrations",
        description="Materialize plan into CSV, Markdown, ICS, and integration targets.",
    )
    _add_common_args(parser)
    parser.add_argument(
        "--target",
        nargs="+",
        dest="targets",
        help="Export targets (e.g., filesystem asana). Default: filesystem",
    )
    parser.add_argument(
        "--templates",
        help="Override templates directory",
    )
    parser.add_argument(
        "--config",
        help="YAML config file with per-target settings",
    )
    parser.add_argument(
        "--mode",
        dest="export_mode",
        choices=["executive", "full", "dev"],
        default="full",
        help="Export packaging mode",
    )
    parser.add_argument(
        "--profile",
        dest="profile",
        help="Filter export to specific bundle profile (e.g. public_audit_bundle)",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="Run release gate for public snapshot bundles (only if public_snapshot_v1 is present)",
    )
    parser.add_argument(
        "--include-raw-answers",
        dest="include_raw_answers",
        action="store_true",
        help="Persist raw answers (review) to _metadata/wizard_answers.json",
    )
    parser.add_argument(
        "--base-date",
        dest="base_date",
        help="Base date for computing relative deadlines (YYYY-MM-DD or 'today')",
    )
    parser.set_defaults(handler=_handle_export)


def _handle_export(args: Namespace) -> int:
    """Handle 'export' command.

    Exports compliance artifacts to filesystem and/or integration targets
    (Jira, Asana, Linear, CSV). First runs assessment to generate plan,
    then materializes artifacts according to configured targets and mode.

    Args:
        args: Parsed command-line arguments containing:
            - contracts: Path to framework pack directory
            - answers: Path to answers JSON/YAML file
            - out: Output directory path
            - targets: List of export targets (default: ['filesystem'])
            - templates: Override templates directory path
            - config: YAML config file with per-target settings
            - mode: Export packaging mode ('executive', 'full', 'dev')
            - profile: Filter to specific bundle profile
            - base_date: Base date for computing relative deadlines
            - strict: Whether to enforce strict validation

    Returns:
        Exit code: 0 on success, 1 if export fails or quality gating fails.
    """
    import sys

    from src.app.cli.commands._helpers import load_bundle_and_answers, run_assessment
    from src.app.config.paths import resolve_contracts_path
    from src.app.use_cases import ops

    # Load bundle and answers
    bundle, raw_answers, outdir, logger = load_bundle_and_answers(args)
    contracts_dir = resolve_contracts_path(args.contracts)

    # Run assessment first
    plan = run_assessment(bundle, raw_answers, contracts_dir, logger, args)

    # Run export
    strict = getattr(args, "strict", False)
    strict_templates = bool(getattr(args, "strict_templates", False) or strict)
    result = ops.run_export(
        plan,
        contracts_dir,
        outdir,
        logger,
        getattr(args, "save_plan", False),
        templates=getattr(args, "templates", None),
        targets=getattr(args, "targets", None),
        config=getattr(args, "config", None),
        strict=strict,
        strict_templates=strict_templates,
        export_mode=getattr(args, "export_mode", "full"),
        profile=getattr(args, "profile", None),
        release=getattr(args, "release", False),
        include_raw_answers=getattr(args, "include_raw_answers", False),
        wizard_answers=raw_answers,
    )

    # Bundle coherence errors are fatal only in strict mode
    # In non-strict mode, even CRITICAL SAFETY errors are warnings (allows dev iteration)
    fatal_error = bool(
        getattr(result, "pre_artifact_error", False)
        or getattr(result, "quality_gating_error", False)
        or getattr(result, "release_gate_error", False)
        or (strict and getattr(result, "bundle_coherence_error", False))
        or (strict and getattr(result, "target_errors", {}))
    )

    if fatal_error:
        if logger is None and getattr(result, "config_error", False):
            msg = "[export] Failed to load export config"
            cem = getattr(result, "config_error_msg", None)
            if cem:
                truncated = cem if len(cem) <= 200 else cem[:197] + "..."
                msg += f" ({truncated})"
            sys.stderr.write(msg + "\n")

        if logger is None and getattr(result, "templates_validation_error", False):
            msg = "[export] Template validation failed (--strict-templates)"
            tvm = getattr(result, "templates_validation_msg", None)
            if tvm:
                truncated = tvm if len(tvm) <= 200 else tvm[:197] + "..."
                msg += f": {truncated}"
            sys.stderr.write(msg + "\n")

        if logger is None and getattr(result, "quality_gating_error", False):
            qgm = getattr(result, "quality_gating_msg", None)
            msg = "[export] Quality gating failed (--strict mode)"
            if qgm:
                msg += f": {qgm}"
            sys.stderr.write(msg + "\n")

        if logger is None and getattr(result, "bundle_coherence_error", False):
            bcm = getattr(result, "bundle_coherence_msg", None)
            msg = "[export] Bundle coherence error"
            if bcm:
                msg += f": {bcm}"
            sys.stderr.write(msg + "\n")

        if logger is None and getattr(result, "release_gate_error", False):
            rgm = getattr(result, "release_gate_msg", None)
            msg = "[export] Release gate failed"
            if rgm:
                msg += f": {rgm}"
            sys.stderr.write(msg + "\n")

        sys.stderr.write("[export] Completed with errors. Use --log-jsonl for diagnostics.\n")
        return 1

    if result.any_error:
        # This should only happen in non-strict mode with non-critical errors
        # (critical errors and strict mode errors are caught by fatal_error above)
        sys.stderr.write("[export] Completed with target errors in non-strict mode.\n")

    if logger is None and result.success:
        sys.stderr.write(f"Wrote export files to {outdir}\n")

    return 0


# =============================================================================
# SEAL
# =============================================================================


def _register_seal(subparsers: argparse._SubParsersAction) -> None:
    """Register 'seal' command."""
    parser = subparsers.add_parser(
        "seal",
        help="Seal and package an export with cryptographic verification",
        description="Validate evidence, compute checksums, and optionally sign.",
    )
    parser.add_argument(
        "--export-dir",
        dest="export_dir",
        required=True,
        help="Directory to seal",
    )
    parser.add_argument(
        "--output",
        dest="seal_output",
        help="Output ZIP file path (optional)",
    )
    parser.add_argument(
        "--no-sign",
        dest="no_sign",
        action="store_true",
        help="Skip GPG signing",
    )
    parser.add_argument(
        "--evidence-dir",
        dest="evidence_dir",
        help="External evidence directory to validate",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on validation warnings",
    )
    parser.set_defaults(handler=_handle_seal)


def _handle_seal(args: Namespace) -> int:
    """Handle 'seal' command.

    Seals and packages an export directory with cryptographic verification.
    Validates evidence, computes checksums (SHA-256), and optionally signs
    the package with GPG for non-repudiation.

    Args:
        args: Parsed command-line arguments containing:
            - export_dir: Directory to seal (required)
            - output: Optional output ZIP file path
            - no_sign: Skip GPG signing if set
            - evidence_dir: External evidence directory to validate
            - strict: Fail on validation warnings

    Returns:
        Exit code: 0 on success, 1 if sealing fails or validation errors occur.
    """
    import sys
    from pathlib import Path

    from src.app.use_cases.seal import seal_and_package

    export_dir = Path(args.export_dir).resolve()
    if not export_dir.exists():
        sys.stderr.write(f"Error: Export directory not found: {export_dir}\n")
        return 1

    output_zip = Path(args.seal_output).resolve() if args.seal_output else None
    evidence_dir = Path(args.evidence_dir).resolve() if args.evidence_dir else None
    sign = not args.no_sign

    print(f"Sealing export: {export_dir}")

    result = seal_and_package(
        export_dir=export_dir,
        output_zip=output_zip,
        sign=sign,
        strict=args.strict,
        evidence_dir=evidence_dir,
    )

    if result.success:
        print(f"✓ Seal successful: {result.seal_report.files_validated} files validated")
        if output_zip:
            print(f"✓ Bundle created: {output_zip}")
        if result.warnings:
            print(f"⚠ Warnings ({len(result.warnings)}):")
            for w in result.warnings[:5]:
                print(f"  - {w}")
        return 0
    else:
        print(f"✗ Seal failed with {len(result.errors)} errors:")
        for e in result.errors[:5]:
            print(f"  - {e}")
        return 1


# =============================================================================
# UI
# =============================================================================


def _register_ui(subparsers: argparse._SubParsersAction) -> None:
    """Register 'ui' command."""
    parser = subparsers.add_parser(
        "ui",
        help="Launch interactive questionnaire UI",
        description="Start Flask-based questionnaire server.",
    )
    parser.add_argument(
        "--contracts",
        required=True,
        help="Path to framework pack directory (manifest.yml + declared pack layout)",
    )
    parser.add_argument(
        "--answers",
        help="Path to answers.json (optional)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Server host",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server port",
    )
    parser.set_defaults(handler=_handle_ui)


def _handle_ui(args: Namespace) -> int:
    """Handle 'ui' command.

    Launches interactive Flask-based questionnaire UI server for answering
    compliance questions through a web interface.

    Args:
        args: Parsed command-line arguments containing:
            - contracts: Path to framework pack directory
            - answers: Optional path to pre-filled answers JSON
            - host: Server host (default: 127.0.0.1)
            - port: Server port (default: 8000)

    Returns:
        Exit code: 0 on success, 1 if UI dependencies are missing.
    """
    import sys
    from pathlib import Path

    from src.app.config.paths import resolve_contracts_path

    contracts_dir = resolve_contracts_path(args.contracts)

    # Lazy import UI to avoid Flask dependency when not needed
    try:
        from src.adapters.ui.server import run_ui_server
    except ImportError:
        sys.stderr.write("UI requires optional dependency. Install with: uv sync --dev\n")
        return 1

    ans_path = Path(args.answers) if args.answers else None
    run_ui_server(
        contracts_dir,
        str(ans_path) if ans_path else None,
        host=args.host,
        port=args.port,
    )
    return 0


# =============================================================================
# WIZARD
# =============================================================================


def _register_wizard(subparsers: argparse._SubParsersAction) -> None:
    """Register 'wizard' command."""
    parser = subparsers.add_parser(
        "wizard",
        help="Interactive CLI wizard for template fill() placeholders",
        description="Interview-style interface to capture document inputs.",
    )
    _add_common_args(parser)
    parser.add_argument(
        "--templates",
        help="Override templates directory",
    )
    parser.set_defaults(handler=_handle_wizard)


def _handle_wizard(args: Namespace) -> int:
    """Handle 'wizard' command.

    Runs interactive CLI wizard to capture template fill() placeholders through
    an interview-style interface. First runs assessment to generate plan,
    then prompts user for missing template variables.

    Args:
        args: Parsed command-line arguments containing:
            - contracts: Path to framework pack directory
            - answers: Path to answers JSON/YAML file
            - out: Output directory path
            - templates: Override templates directory path

    Returns:
        Exit code: 0 on success, 1 if wizard fails or contracts are missing.
    """
    import json
    import sys
    from pathlib import Path
    from typing import Any, cast

    from src.app.cli.commands._helpers import load_bundle_and_answers, run_assessment
    from src.app.cli.wizard import ComplianceWizard
    from src.app.config.constants import DEFAULT_ENCODING, WIZARD_ANSWERS_JSON
    from src.app.config.paths import resolve_contracts_path

    # Load bundle and answers
    bundle, raw_answers, outdir, logger = load_bundle_and_answers(args)
    contracts_dir = resolve_contracts_path(args.contracts)

    if contracts_dir is None:
        sys.stderr.write("Error: Contracts directory required for wizard\n")
        return 1

    # Run assessment to get plan
    plan = run_assessment(bundle, raw_answers, contracts_dir, logger, args)

    # Setup wizard
    templates_dir_wizard = load_framework_layout(contracts_dir).templates_dir
    if getattr(args, "templates", None):
        templates_dir_wizard = Path(args.templates)

    wizard = ComplianceWizard(templates_dir_wizard, cast("dict[str, Any]", plan))
    answers = wizard.run_interview()

    # Save answers
    outdir.mkdir(parents=True, exist_ok=True)
    answers_path = outdir / WIZARD_ANSWERS_JSON
    answers_path.write_text(
        json.dumps(answers, indent=2, ensure_ascii=False), encoding=DEFAULT_ENCODING
    )
    sys.stderr.write(f"Wizard completed. Answers saved to {answers_path}\n")
    return 0


# =============================================================================
# INSPECT
# =============================================================================


def _register_inspect(subparsers: argparse._SubParsersAction) -> None:
    """Register 'inspect' command."""
    parser = subparsers.add_parser(
        "inspect",
        help="Inspect rules and artifacts",
        description="Debug tool to visualize rule decision trees.",
    )
    parser.add_argument(
        "--contracts",
        required=True,
        help="Path to framework pack directory (manifest.yml + declared pack layout)",
    )
    parser.add_argument(
        "inspect_args",
        nargs="*",
        help="Subcommand and arguments (e.g., 'rule RULE_ID')",
    )
    parser.set_defaults(handler=_handle_inspect)


def _handle_inspect(args: Namespace) -> int:
    """Handle 'inspect' command.

    Inspects rules, actions, or artifacts from the framework pack.
    Provides detailed information about specific entities for debugging
    and understanding the compliance framework structure.

    Args:
        args: Parsed command-line arguments containing:
            - contracts: Path to framework pack directory
            - rule: Optional rule ID to inspect
            - action: Optional action ID to inspect
            - Other inspection-specific arguments

    Returns:
        Exit code: 0 on success, 1 if inspection fails or entity not found.
    """
    import sys

    from src.app.cli.commands.inspect import inspect_rule
    from src.app.config.paths import resolve_contracts_path

    if not args.inspect_args:
        sys.stderr.write("Usage: ipe inspect rule <RULE_ID>\n")
        return 1

    contracts_dir = resolve_contracts_path(args.contracts)
    subcmd = args.inspect_args[0]

    if subcmd == "rule":
        if len(args.inspect_args) < 2:
            sys.stderr.write("Error: Missing rule ID. Usage: inspect rule <RULE_ID>\n")
            return 1
        rule_id = args.inspect_args[1]
        inspect_rule(contracts_dir, rule_id)
        return 0
    else:
        sys.stderr.write(f"Unknown inspect subcommand: {subcmd}\n")
        return 1


# =============================================================================
# COMMON ARGS
# =============================================================================


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments shared by assess/export/wizard."""
    parser.add_argument(
        "--contracts",
        help="Path to framework pack directory (required)",
    )
    parser.add_argument(
        "--answers",
        help="JSON file with answers or flags",
    )
    parser.add_argument(
        "--out",
        default="out",
        help="Output directory",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on errors (warnings may also become errors)",
    )
    parser.add_argument(
        "--strict-templates",
        dest="strict_templates",
        action="store_true",
        help="Validate templates structure/integrity without requiring filled content",
    )
    parser.add_argument(
        "--log-jsonl",
        dest="log_jsonl",
        help="JSONL log file path",
    )
    parser.add_argument(
        "--save-plan",
        action="store_true",
        help="Persist plan with deterministic ID",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write debug trace.jsonl and extra diagnostics",
    )
    parser.add_argument(
        "--check-ci",
        dest="check_ci",
        action="store_true",
        help="Run compliance integrity check (CI Mode)",
    )
