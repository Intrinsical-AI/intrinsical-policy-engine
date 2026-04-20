# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Common ops used by CLI and UI.

Centralizes: lint, answers loading, assess, export, and artifacts render.
"""

import json
from pathlib import Path

import yaml

from src.adapters.contracts.yaml.yaml_contract_adapter import YamlContractsAdapter
from src.adapters.export.base.exporters.base_exporter import BaseExporter
from src.adapters.export.base.models.registry import get_exporter
from src.adapters.frameworks.layout_loader import load_framework_layout
from src.adapters.logging import StructuredLogger
from src.adapters.store.fs.fs_store import FsPlanStore
from src.app.config.artifact_names import SUMMARY_FILE
from src.app.config.constants import METADATA_DIR
from src.app.config.context import get_plan_fingerprint
from src.app.config.paths import resolve_answers_path
from src.app.export import ExportOrchestrator, ExportRunResult
from src.app.export.orchestrator import ExportConfig
from src.app.rendering import render_artifacts as render_artifacts_func
from src.common.constants import DEFAULT_ENCODING
from src.domain.bundles.predicates import register_core_predicates
from src.domain.services.assess_service import assess_from_bundle
from src.domain.types import Plan

# Ensure predicates are registered for all ops
register_core_predicates()

__all__ = [
    "BaseExporter",
    "ExportRunResult",
    "get_exporter",
    "load_answers",
    "render_artifacts",
    "run_assess",
    "run_export",
    "run_lint",
    "write_assess",
]


def run_lint(
    adapter: YamlContractsAdapter, contracts_dir: Path, strict: bool
) -> tuple[str, list[str]]:
    """Run schema/business-lint and return status string plus errors."""
    errs = adapter.validate(str(contracts_dir), use_framework_schemas=strict, strict_schemas=strict)
    status = "OK" if not errs else "FAIL"
    return status, errs


def load_answers(answers_path: str | None) -> dict:
    """Load answers JSON/YAML file with validation and size checks.

    Raises:
        SystemExit: If answers_path is provided but file cannot be found or parsed.
            This is intentional fail-fast behavior to avoid silent "out of scope" plans.
    """
    if not answers_path:
        return {}
    p = resolve_answers_path(answers_path)
    if not p or not p.exists():
        raise SystemExit(f"[error] Answers file not found: {answers_path}")
    try:
        from src.app.config.constants import MAX_ANSWERS_SIZE_BYTES

        if p.stat().st_size > MAX_ANSWERS_SIZE_BYTES:
            size_bytes = p.stat().st_size
            raise SystemExit(
                f"[error] Answers file too large ({size_bytes} bytes); max {MAX_ANSWERS_SIZE_BYTES}"
            )

        content = p.read_text(encoding=DEFAULT_ENCODING)
        if p.suffix.lower() in [".yml", ".yaml"]:
            data = yaml.safe_load(content)
        else:
            data = json.loads(content)

        if isinstance(data, dict):
            return data
        raise SystemExit(f"[error] Answers file {p} does not contain a mapping/object")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError, yaml.YAMLError) as e:
        raise SystemExit(f"[error] Failed to load answers from {p}: {e}") from e


def run_assess(
    bundle,
    raw_answers: dict,
    outdir: Path,
    logger: StructuredLogger | None,
    save_plan: bool,
    include_full_trace: bool = False,
    templates_hash: str | None = None,
    templates_dir: Path | str | None = None,
    framework_pack_hashes: dict[str, str] | None = None,
) -> Plan:
    """Execute assess use case, persisting plan artifacts if requested.

    Per INV-05 (TEST-DRIVEN-DEVELOPMENT-v1.md §2.1) and
    docs/invariants/ENGINE-ARCHITECTURE-v1.md, templates_hash is required
    for snapshot reproducibility. If not provided, it will be computed
    automatically from templates_dir (if given) or inferred from
    bundle.path/render.

    Args:
        bundle: Contract bundle with rules and actions
        raw_answers: User answers or direct flags
        outdir: Output directory for plan artifacts
        logger: Optional logger for detailed tracing
        save_plan: Whether to persist a stable ID snapshot
        include_full_trace: Whether to include detailed answers in trace
        templates_hash: Pre-computed hash of templates (optional)
        templates_dir: Path to templates directory for auto-computing hash
    """
    # Auto-compute templates_hash if not provided (INV-05)
    if templates_hash is None:
        from src.common.hashing import sha256_directory

        if templates_dir:
            tdir = Path(templates_dir) if isinstance(templates_dir, str) else templates_dir
        else:
            # Infer from bundle path
            tdir = load_framework_layout(Path(bundle.path)).templates_dir

        if tdir.exists():
            templates_hash = sha256_directory(tdir, warn_if_missing=False)
        else:
            # CRITICAL: Cannot compute templates hash if dir missing
            # -> traceability compromised (INV-05)
            from src.domain.exceptions import AssessmentError

            raise AssessmentError(
                f"Cannot assess: Templates directory not found at {tdir}. "
                "Reproducibility requires templates hash. "
                "Provide valid templates_dir or pre-computed templates_hash."
            )

    # Compute framework pack hashes if not provided (SSOT runtime identity)
    if framework_pack_hashes is None:
        from src.common.hashing import compute_framework_pack_hashes
        from src.domain.services.integrity import compute_bundle_hash

        bundle_hash = compute_bundle_hash(bundle)
        framework_pack_hashes = compute_framework_pack_hashes(
            load_framework_layout(Path(bundle.path)), law_data_hash=bundle_hash
        )

    plan = assess_from_bundle(
        bundle,
        raw_answers,
        logger=logger,
        include_full_trace=include_full_trace,
        templates_hash=templates_hash,
        framework_pack_hashes=framework_pack_hashes,
    )
    write_assess(plan, outdir, logger, save_plan)
    return plan


def write_assess(
    plan: Plan, outdir: Path, logger: StructuredLogger | None, save_plan: bool
) -> None:
    """Persist assessment plan and optionally save deterministic plan copy.

    Writes the plan as summary.json to _metadata/ subdirectory per TREE-SPECS-v1.
    Optionally saves a deterministic plan snapshot with fingerprint ID.

    Args:
        plan: Compliance plan dictionary to persist.
        outdir: Output directory path.
        logger: Optional logger for tracing.
        save_plan: Whether to save deterministic plan snapshot with fingerprint.

    Note:
        Per O-04 (TREE-SPECS-v1.md), summary.json is written to _metadata/
        subdirectory, not the root output directory.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    # O-04: Write to _metadata/ subdirectory
    metadata_dir = outdir / METADATA_DIR
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / SUMMARY_FILE).write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding=DEFAULT_ENCODING, newline="\n"
    )
    if logger:
        logger.info(
            "assess.finish",
            {"actions": len(plan.get("actions", [])), "outcome": plan.get("outcome")},
        )
    if save_plan:
        plan_dict = dict(plan)
        plan_id = get_plan_fingerprint(plan_dict)
        FsPlanStore(base_dir=str(outdir)).save(plan_id, plan_dict)


def run_export(
    plan: Plan,
    contracts_dir: Path,
    outdir: Path,
    logger: StructuredLogger | None,
    save_plan: bool,
    templates: str | None,
    targets: list[str] | None,
    config: str | None,
    strict: bool,
    strict_templates: bool = False,
    export_mode: str = "full",
    profile: str | None = None,
    release: bool = False,
    include_raw_answers: bool = False,
    wizard_answers: dict | None = None,
) -> ExportRunResult:
    """Execute export command: render templates and optionally persist plan.

    Orchestrates the export pipeline: renders artifact templates, generates
    evidence bundles, and exports to configured targets (filesystem, Jira,
    Asana, Linear, CSV). Optionally persists plan snapshot.

    Args:
        plan: Compliance plan to export.
        contracts_dir: Path to contracts directory for resolving templates.
        outdir: Output directory path.
        logger: Optional logger for detailed tracing.
        save_plan: Whether to persist plan snapshot.
        templates: Override templates directory path (optional).
        targets: List of export targets (e.g., ['filesystem', 'jira']).
            Default: ['filesystem'].
        config: Path to YAML config file with per-target settings (optional).
        strict: Whether to enforce strict validation and fail on errors.
        export_mode: Packaging mode - 'executive' (L0+L1 only), 'full' (all
            except _debug), 'dev' (everything including _debug). Default: 'full'.
        profile: Optional filter to export only a specific bundle profile
            (e.g., 'public_snapshot_v1').
        release: Whether to run the release gate when public_snapshot_v1 is present.
        include_raw_answers: Persist raw answers to _metadata/wizard_answers.json (sensitive).
        wizard_answers: Raw answers payload for wizard_answers.json and template context.

    Returns:
        ExportRunResult summarizing artifact generation and per-target export status.
    """
    strict_templates = bool(strict_templates or strict)
    export_config = ExportConfig(
        plan=plan,
        contracts_dir=contracts_dir,
        outdir=outdir,
        save_plan=save_plan,
        templates=templates,
        targets=targets,
        config_path=config,
        strict=strict,
        strict_templates=strict_templates,
        export_mode=export_mode,
        profile=profile,
        release=release,
        include_raw_answers=include_raw_answers,
        wizard_answers=wizard_answers,
    )
    orchestrator = ExportOrchestrator(export_config, logger)
    return orchestrator.run()


def render_artifacts(
    templates_dir: str, summary_json: str, out_dir: str, strict: bool = True
) -> None:
    """Render templates into compliance artifacts.

    Renders Jinja2 templates using the plan data from summary.json, producing
    markdown, CSV, and other artifact files in the output directory.

    Args:
        templates_dir: Path to directory containing Jinja2 templates.
        summary_json: Path to summary.json file containing plan data.
        out_dir: Output directory for rendered artifacts.
        strict: Whether to fail on missing template variables or placeholders.

    Raises:
        FileNotFoundError: If rendering fails (missing templates, invalid data, etc.).

    Note:
        Delegates to src.app.rendering.artifact_renderer for actual rendering logic.
    """
    result = render_artifacts_func(
        templates_dir=templates_dir,
        plan_json=summary_json,
        out_dir=out_dir,
        strict=strict,
    )
    if not result.success:
        error_msgs = result.errors.copy()
        # Include warnings and missing fields in error message for context
        if result.warnings:
            error_msgs.append(f"Validation warnings: {len(result.warnings)}")
        if result.missing_fields:
            error_msgs.append(f"Missing fields detected: {', '.join(result.missing_fields[:10])}")
        if result.incomplete_files:
            error_msgs.append(f"Incomplete files: {', '.join(result.incomplete_files[:10])}")
        for error in error_msgs:
            raise FileNotFoundError(error)
