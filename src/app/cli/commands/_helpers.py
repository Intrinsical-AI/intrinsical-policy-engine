# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Shared helper functions for CLI command handlers."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from src.adapters.frameworks.layout_loader import load_framework_layout
from src.adapters.logging import StructuredLogger
from src.adapters.logging.fs.fs_log import FsLogger
from src.app.config.constants import DEFAULT_ENCODING, METADATA_DIR, TRACE_JSONL
from src.app.config.paths import (
    get_out_dir,
    normalize_log_jsonl_path,
    resolve_answers_path,
    resolve_contracts_path,
)
from src.app.use_cases import ops
from src.app.use_cases.bundle_orchestrator import BundleOrchestrator
from src.common.hashing import compute_framework_pack_hashes, sha256_directory
from src.domain.services.assess_service import assess_from_bundle
from src.domain.services.integrity import compute_bundle_hash

if TYPE_CHECKING:
    from argparse import Namespace
    from typing import Any

    from src.domain.ports import ContractBundle
    from src.domain.types import Plan


def load_bundle_and_answers(
    args: Namespace, logger: StructuredLogger | None = None
) -> tuple[ContractBundle, dict[str, Any], Path, StructuredLogger | None]:
    """Load contract bundle and answers from CLI arguments.

    Validates and loads the framework pack (contracts) and user answers
    from the provided CLI arguments. Sets up logging if configured.

    Args:
        args: Parsed command-line arguments containing:
            - contracts: Path to framework pack directory (required)
            - answers: Path to answers JSON/YAML file (optional)
            - out: Output directory path
            - log_jsonl: Path to JSONL log file (optional)
            - strict: Whether to enforce strict validation
        logger: Optional existing logger instance.

    Returns:
        Tuple containing:
            - bundle: Loaded and validated ContractBundle
            - raw_answers: Dictionary of user answers (empty if not provided)
            - outdir: Resolved output directory Path
            - logger: Logger instance (created or provided)

    Raises:
        SystemExit: If contracts path is missing or invalid, or if bundle
            validation fails.
    """
    # Validate --contracts
    contracts_arg = getattr(args, "contracts", None)
    if not contracts_arg:
        sys.stderr.write(f"Error: --contracts is required for '{args.command}' command\n")
        sys.exit(1)

    contracts_dir = resolve_contracts_path(contracts_arg)
    if contracts_dir is None:
        sys.stderr.write("Error: Contracts directory could not be resolved\n")
        sys.exit(1)

    outdir = get_out_dir(getattr(args, "out", "out"))
    log_path = normalize_log_jsonl_path(getattr(args, "log_jsonl", None), outdir)
    logger = FsLogger(str(log_path)) if log_path else logger

    # Load bundle
    strict = getattr(args, "strict", False)
    orchestrator = BundleOrchestrator(strict=strict)

    try:
        bundle_result = orchestrator.load_and_validate_complete_bundle(contracts_dir)
        bundle = bundle_result.contract_bundle

        if bundle_result.validation_report.has_errors():
            _log_event(
                logger,
                "assess.validation_issues",
                {
                    "issues_count": len(bundle_result.validation_report.problems),
                    "summary": bundle_result.validation_report.summary(),
                },
            )
    except Exception as e:
        _log_event(logger, "assess.validation_failed", {"error": str(e)})
        raise

    # Load answers
    answers_arg = getattr(args, "answers", None)
    raw_answers = ops.load_answers(answers_arg) if answers_arg else {}

    _log_event(
        logger,
        "assess.start",
        {
            "contracts": str(contracts_dir),
            "answers": answers_arg or "<none>",
        },
    )

    return bundle, raw_answers, outdir, logger


def run_assessment(
    bundle: ContractBundle,
    raw_answers: dict[str, Any],
    contracts_dir: Path,
    logger: StructuredLogger | None,
    args: Namespace,
) -> Plan:
    """Run compliance assessment and return plan.

    Executes the assessment pipeline: computes templates hash, resolves
    framework pack hashes, runs assess_from_bundle, and enriches the plan
    with audit metadata and debug traces if requested.

    Args:
        bundle: Contract bundle with rules and actions.
        raw_answers: User answers dictionary.
        contracts_dir: Path to contracts directory for resolving templates.
        logger: Optional logger for detailed tracing.
        args: Parsed command-line arguments containing:
            - templates: Override templates directory path
            - base_date: Base date for calendar offsets
            - debug: Whether to include full trace

    Returns:
        Plan dictionary with flags, actions, due_hints, routing, etc.
        Includes audit metadata (answers_path, hashes) if answers file provided.
    """
    # Compute templates hash
    _, templates_hash = _resolve_templates_and_hash(contracts_dir, getattr(args, "templates", None))
    base_date = _parse_base_date(getattr(args, "base_date", None))

    plan = assess_from_bundle(
        bundle,
        raw_answers,
        logger=logger,
        include_full_trace=getattr(args, "debug", False),
        templates_hash=templates_hash,
        framework_pack_hashes=_compute_framework_pack_hashes(contracts_dir, bundle),
        base_date=base_date,
    )

    # Debug trace
    if getattr(args, "debug", False):
        from src.app.config.paths import get_out_dir

        outdir = get_out_dir(getattr(args, "out", "out"))
        metadata_dir = outdir / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)
        (metadata_dir / TRACE_JSONL).write_text(
            json.dumps(plan.get("trace", {}), ensure_ascii=False) + "\n",
            encoding=DEFAULT_ENCODING,
        )

    # Inject audit metadata
    answers_arg = getattr(args, "answers", None)
    if answers_arg:
        import hashlib

        ans_path = resolve_answers_path(answers_arg)
        if ans_path and ans_path.exists():
            ans_bytes = ans_path.read_bytes()
            ans_sha256 = hashlib.sha256(ans_bytes).hexdigest()
            plan["audit"] = {
                # Use basename to avoid leaking repo structure into client artifacts.
                "answers_path": ans_path.name,
                "answers_sha256": ans_sha256,
                "plan_sha256": plan.get("trace", {}).get("plan_hash", "—"),
                "rules_sha256": plan.get("trace", {}).get("bundle_hash", "—"),
                "evidence_map_sha256": "—",
            }
        # Allow answers.json to carry non-flag metadata used by templates (demo-safe).
        for key in ("system", "provider", "declared_by", "approvals"):
            if key in raw_answers and isinstance(raw_answers.get(key), dict):
                plan[key] = raw_answers[key]

    return plan


def check_ci_compliance(plan: Plan, strict: bool) -> int:
    """Run CI compliance checks and return exit code.

    Executes quality gates for CI/CD pipelines. Checks for blocked
    outcomes, missing evidence for review actions, evidence quality
    thresholds, and action coverage.

    Args:
        plan: Compliance plan to validate.
        strict: Whether to treat warnings as errors (exit 1).

    Returns:
        Exit code: 0 if all checks pass, 1 if critical gaps found in strict mode.
    """
    from src.app.cli.commands.ci_runner import CIRunner

    runner = CIRunner()
    findings = runner.check(plan)
    if findings:
        sys.stderr.write("\n[CI] Compliance Gaps Detected:\n")
        for f in findings:
            code = f.get("code", "UNK")
            msg = f.get("msg", "")
            severity = f.get("severity", "warning").upper()
            sys.stderr.write(f" - [{severity}] {code}: {msg}\n")

        if strict and any(f.get("severity") == "error" for f in findings):
            sys.stderr.write("\n[CI] Critical compliance gaps. Aborting.\n")
            return 1
    return 0


def _log_event(logger: StructuredLogger | None, event: str, data: dict[str, Any]) -> None:
    """Log event if logger is available."""
    if logger:
        logger.info(event, data)


def _parse_base_date(value: str | None) -> date | None:
    """Parse --base-date argument to date object."""
    if not value:
        return None
    if value.lower() == "today":
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        sys.stderr.write(f"WARNING: Invalid --base-date '{value}', using default.\n")
        return None


def _resolve_templates_and_hash(
    contracts_dir: Path, templates_override: str | None
) -> tuple[Path, str | None]:
    """Resolve templates directory and compute hash."""
    try:
        layout = load_framework_layout(contracts_dir)
        templates_dir = layout.templates_dir
    except (FileNotFoundError, ValueError):
        templates_dir = contracts_dir / "render"
    if templates_override:
        templates_dir = Path(templates_override)

    is_official = (contracts_dir / "FRAMEWORK_VERSION.yml").exists()
    templates_hash = None

    if templates_dir.exists() and templates_dir.is_dir():
        templates_hash = sha256_directory(templates_dir)
    elif is_official:
        sys.stderr.write(f"CRITICAL: Official framework but templates missing at {templates_dir}\n")
        sys.exit(1)
    else:
        sys.stderr.write(f"WARNING: Templates not found at {templates_dir}. Skipping hash.\n")

    return templates_dir, templates_hash


def _compute_framework_pack_hashes(contracts_dir: Path, bundle: ContractBundle) -> dict[str, str]:
    """Compute framework pack hashes for traceability."""
    bundle_hash = compute_bundle_hash(bundle)
    layout = load_framework_layout(contracts_dir)
    return compute_framework_pack_hashes(layout, law_data_hash=bundle_hash)
