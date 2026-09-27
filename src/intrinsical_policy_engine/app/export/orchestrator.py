# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Export orchestrator for coordinating export workflow.

This module provides the main ExportOrchestrator class that coordinates
the export workflow without mixing I/O operations with orchestration logic.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

# Bundle hash coherence check imports
from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.export.base.exporters.base_exporter import EvidenceManifest
from intrinsical_policy_engine.adapters.export.base.models.registry import (
    canonical_target,
    get_exporter,
    target_name_variants,
)
from intrinsical_policy_engine.adapters.export.base.models.shapes import ExportResult
from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout
from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.adapters.quality.bundle_evidence_validator import CoverageReport
from intrinsical_policy_engine.adapters.store.fs.fs_store import FsPlanStore
from intrinsical_policy_engine.app.config.constants import (
    DEFAULT_ENCODING,
    METADATA_DIR,
    WIZARD_ANSWERS_JSON,
)
from intrinsical_policy_engine.app.config.context import (
    build_artifact_context,
    get_plan_fingerprint,
)
from intrinsical_policy_engine.app.export.artifacts import ArtifactsState, ArtifactWriter
from intrinsical_policy_engine.app.gating.export_gate import evaluate_bundle_coherence
from intrinsical_policy_engine.app.gating.policy import (
    ExportGateFacts,
    ExportGatePolicy,
    evaluate_export_gate,
)
from intrinsical_policy_engine.common.hashing import sha256_directory
from intrinsical_policy_engine.common.io_safety import validate_export_output_boundary
from intrinsical_policy_engine.common.validation.placeholders import (
    validate_required_context_fields,
)
from intrinsical_policy_engine.domain.bundles.predicates import register_core_predicates
from intrinsical_policy_engine.domain.exceptions import (
    ExportConfigError,
    ExportConsistencyError,
    YAMLLoadError,
)
from intrinsical_policy_engine.domain.framework_layout import FrameworkLayout
from intrinsical_policy_engine.domain.services.integrity import (
    compute_bundle_hash,
    compute_plan_hash,
)
from intrinsical_policy_engine.domain.types import Plan


@dataclass
class ExportRunResult:
    """Aggregated result for a full export run (artifacts + all targets)."""

    outdir: Path
    pre_artifact_error: bool
    summary_error: bool
    evidence_manifest_error: bool
    evidence_quality_error: bool
    trace_error: bool
    manifest_error: bool
    templates_validation_error: bool = False
    templates_validation_msg: str | None = None

    config_error: bool = False
    config_error_msg: str | None = None

    # Bundle coherence error per docs/invariants/ENGINE-ARCHITECTURE-v1.md
    bundle_coherence_error: bool = False
    bundle_coherence_msg: str | None = None

    # Gating error per docs/invariants/ENGINE-ARCHITECTURE-v1.md
    quality_gating_error: bool = False
    quality_gating_msg: str | None = None

    # Release gate (public bundle) errors
    release_gate_error: bool = False
    release_gate_msg: str | None = None

    # Map target name -> error message for failing targets
    target_errors: dict[str, str] = field(default_factory=dict)
    target_results: list[ExportResult] = field(default_factory=list)

    @property
    def any_error(self) -> bool:
        """Return True when pre-artifacts, bundle coherence, quality gating, or any
        target failed."""
        return (
            self.pre_artifact_error
            or self.bundle_coherence_error
            or self.quality_gating_error
            or self.release_gate_error
            or bool(self.target_errors)
        )

    @property
    def success(self) -> bool:
        """Return True when every stage finished without error."""
        return not self.any_error


@dataclass
class ExportConfig:
    """Configuration for export workflow."""

    plan: Plan
    contracts_dir: Path
    outdir: Path
    save_plan: bool
    templates: str | None
    targets: list[str] | None
    config_path: str | None
    strict: bool
    strict_templates: bool = False
    export_mode: str = "full"
    profile: str | None = None
    release: bool = False
    include_raw_answers: bool = False
    wizard_answers: dict | None = None
    tolerate_questions_errors: bool = False
    allow_incomplete_coverage: bool | None = None
    skip_gpg_signing: bool = False


class ExportOrchestrator:
    """Orchestrates the export workflow.

    Separates workflow coordination from I/O operations by delegating
    to ArtifactWriter for file operations.
    """

    def __init__(self, config: ExportConfig, logger: StructuredLogger | None = None):
        """Store config, logger, and hydrate an ArtifactWriter.

        Args:
            config: Export configuration with plan, paths, targets, etc.
            logger: Optional logger implementing StructuredLogger protocol for structured logging.
        """
        self.config = config
        self._logger = logger
        self._artifact_writer = ArtifactWriter(logger)

        # Initialize domain logic
        register_core_predicates()

        # Resolve layout once; None for partially migrated or non-canonical packs.
        try:
            self._layout: FrameworkLayout | None = load_framework_layout(config.contracts_dir)
        except (FileNotFoundError, ValueError) as exc:
            self._layout = None
            self._log_event(
                "export.layout_resolution.failed",
                {
                    "reason": str(exc),
                    "contracts_dir": str(config.contracts_dir),
                },
            )

    def run(self) -> ExportRunResult:
        """Execute the full export workflow.

        Orchestrates the complete export process:
        1. Prepare pre-export artifacts (summary, config, evidence manifest)
        2. Run all configured export targets (filesystem, API exporters, etc.)
        3. Save plan if requested
        4. Build and return aggregated result

        Returns:
            ExportRunResult with status of all operations, including:
            - Pre-artifact errors
            - Bundle coherence errors
            - Quality gating errors
            - Target-specific errors
            - Target results

        Note:
            In strict mode, any error causes immediate failure.
        """
        preflight = evaluate_export_gate(
            ExportGateFacts(),
            ExportGatePolicy(
                strict=self.config.strict,
                release=self.config.release,
                allow_incomplete_coverage=bool(self.config.allow_incomplete_coverage),
                skip_gpg_signing=self.config.skip_gpg_signing,
            ),
        )
        if preflight.blocked:
            raise ExportConfigError("Invalid release policy: " + ", ".join(preflight.blockers))

        # Reused output directories are untrusted input. Validate overlap and
        # the complete tree before ArtifactWriter creates, replaces, or removes
        # anything. ManifestStrategy repeats the tree check later as defense in
        # depth after all exporters have run.
        validate_export_output_boundary(self.config.contracts_dir, self.config.outdir)

        state = self._prepare_artifacts()
        target_results, target_errors, quality_gating_msg, bundle_coherence_msg = self._run_targets(
            state
        )
        release_gate_msg = self._run_release_gate()
        self._save_plan_if_requested()
        result = self._build_result(
            state,
            target_results,
            target_errors,
            quality_gating_msg,
            bundle_coherence_msg,
            release_gate_msg,
        )

        if self.config.strict and result.any_error:
            self._log_event("export.strict.exit", {"any_error": True})

        return result

    def _prepare_artifacts(self) -> ArtifactsState:
        """Prepare all pre-export artifacts."""
        if self.config.templates:
            templates_dir = self.config.templates
        elif self._layout is not None:
            templates_dir = str(self._layout.templates_dir)
        else:
            templates_dir = str(self.config.contracts_dir / "templates")
        tgt_list = self.config.targets or ["filesystem"]

        summary_error = self._artifact_writer.write_summary(self.config.plan, self.config.outdir)
        cfg_all, config_error, config_error_msg = self._load_export_config_safe()
        if cfg_all is None:
            cfg_all = {}

        templates_validation_error = False
        templates_validation_msg: str | None = None
        if self.config.strict_templates:
            templates_validation_msg = self._validate_templates_integrity(templates_dir)
            templates_validation_error = bool(templates_validation_msg)
            if templates_validation_error:
                self._log_event(
                    "export.templates_validation.failed",
                    {"reason": templates_validation_msg},
                )

        # Early validation of templates and framework files (QW7)
        templates_warning = self._warn_if_missing_templates(templates_dir, tgt_list)
        if templates_warning and self.config.strict and not config_error:
            # Treat missing templates/rules as configuration error in strict mode
            config_error = True
            config_error_msg = templates_warning

        # Load wizard answers if present
        wizard_path = self.config.outdir / WIZARD_ANSWERS_JSON
        persisted_wizard_path = self.config.outdir / METADATA_DIR / WIZARD_ANSWERS_JSON
        wizard_answers = self.config.wizard_answers
        if wizard_answers is None and wizard_path.exists():
            try:
                wizard_answers = json.loads(wizard_path.read_text(encoding=DEFAULT_ENCODING))
                self._log_event("export.wizard_answers_loaded", {"path": str(wizard_path)})
            except (OSError, json.JSONDecodeError) as e:
                self._log_event("export.wizard_answers_failed", {"error": str(e)})

        if isinstance(wizard_answers, dict) and isinstance(cfg_all, dict):
            if self.config.include_raw_answers:
                cfg_all["wizard_answers"] = wizard_answers
                cfg_all["wizard_answers_sanitized"] = False
            else:
                # The trace already carries a one-way answers hash. Do not emit
                # answer keys or reversible categorical values without opt-in.
                wizard_answers = None
                cfg_all.pop("wizard_answers", None)
                cfg_all["wizard_answers_sanitized"] = True
            cfg_all["include_raw_answers"] = bool(self.config.include_raw_answers)

        if not self.config.include_raw_answers:
            # A caller may reuse an output directory that was previously
            # exported with raw-answer opt-in. Remove both the legacy root
            # input and the canonical persisted metadata before rebuilding the
            # manifest so sensitive stale data cannot survive or be sealed.
            for raw_answers_path in (wizard_path, persisted_wizard_path):
                with contextlib.suppress(OSError):
                    raw_answers_path.unlink(missing_ok=True)

        (
            pre_manifest,
            quality_report,
            evidence_manifest_error,
            evidence_quality_error,
        ) = self._artifact_writer.build_evidence_manifest_and_quality(
            self.config.plan,
            templates_dir,
            self.config.outdir,
        )

        trace_error = self._artifact_writer.write_trace(self.config.plan, self.config.outdir)
        manifest_error = self._artifact_writer.write_manifest_md(
            self.config.plan, self.config.outdir, templates_dir
        )

        return ArtifactsState(
            templates_dir=templates_dir,
            tgt_list=tgt_list,
            cfg_all=cfg_all,
            pre_manifest=pre_manifest,
            quality_report=quality_report,
            summary_error=summary_error,
            evidence_manifest_error=evidence_manifest_error,
            evidence_quality_error=evidence_quality_error,
            trace_error=trace_error,
            manifest_error=manifest_error,
            config_error=config_error,
            config_error_msg=config_error_msg,
            templates_validation_error=templates_validation_error,
            templates_validation_msg=templates_validation_msg,
        )

    def _handle_strict_gate_message(
        self,
        message: str,
        *,
        blocked_event: str,
        warning_event: str | None = None,
    ) -> bool:
        """Centralize the strict/non-strict wrapper used by repeated gate checks."""
        if self.config.strict:
            self._log_event(blocked_event, {"reason": message})
            return True

        if warning_event:
            self._log_event(warning_event, {"reason": message})
        return False

    def _run_targets(  # noqa: C901
        self, state: ArtifactsState
    ) -> tuple[list[ExportResult], dict[str, str], str | None, str | None]:
        """Run exporters for all targets.

        Returns:
            Tuple of (results, errors, quality_gating_msg, bundle_coherence_msg)
            quality_gating_msg is set if strict mode blocked due to critical placeholders.
            bundle_coherence_msg is set if strict mode blocked due to bundle hash mismatch.
        """
        project_key = self._extract_project_key(state.cfg_all)
        plan_for_targets = self._ensure_export_context_namespace(project_key)
        export_metrics: dict[str, Any] = {}

        if state.pre_artifact_error:
            return self._skip_targets(state.tgt_list), {}, None, None

        # Plan hash consistency check per docs/invariants/ENGINE-ARCHITECTURE-v1.md
        plan_hash_msg = self._check_plan_hash_consistency(plan_for_targets)
        if plan_hash_msg and self._handle_strict_gate_message(
            plan_hash_msg,
            blocked_event="export.plan_hash_consistency.blocked",
            warning_event="export.plan_hash_consistency.warning",
        ):
            # Treat as bundle coherence style error (fourth return value)
            return self._skip_targets(state.tgt_list), {}, None, plan_hash_msg

        # Guard: validate trace integrity in strict mode per INV-05, ENGINE ARCHITECTURE v1
        trace_integrity_msg = self._validate_trace_integrity(plan_for_targets)
        if trace_integrity_msg and self._handle_strict_gate_message(
            trace_integrity_msg,
            blocked_event="export.trace_integrity.blocked",
        ):
            return self._skip_targets(state.tgt_list), {}, None, trace_integrity_msg

        # Check templates coherence per INV-05 (TEST-DRIVEN-DEVELOPMENT-v1.md §2.1)
        # In strict mode, block export if templates changed since assessment
        templates_coherence_msg = self._check_templates_coherence(
            plan_for_targets, state.templates_dir
        )
        if templates_coherence_msg and self._handle_strict_gate_message(
            templates_coherence_msg,
            blocked_event="export.templates_coherence.blocked",
        ):
            return self._skip_targets(state.tgt_list), {}, None, templates_coherence_msg

        # Check routing consistency (WARN ONLY)
        self._check_routing_consistency(plan_for_targets)

        # Bundle coherence check per docs/invariants/ENGINE-ARCHITECTURE-v1.md:
        # Verify plan's bundle_hash matches current contracts to prevent drift
        coherence_msg = self._check_bundle_coherence(plan_for_targets)
        if coherence_msg and self._handle_strict_gate_message(
            coherence_msg,
            blocked_event="export.bundle_coherence.blocked",
            warning_event="export.bundle_coherence.warning",
        ):
            return self._skip_targets(state.tgt_list), {}, None, coherence_msg

        # Quality gating per docs/invariants/ENGINE-ARCHITECTURE-v1.md:
        # In strict mode, block export if there are placeholder evidences for critical actions
        if self.config.strict:
            gating_msg = self._check_quality_gating(plan_for_targets, state.quality_report)
            if gating_msg:
                self._log_event("export.quality_gating.blocked", {"reason": gating_msg})
                return self._skip_targets(state.tgt_list), {}, gating_msg, None

        # Red Team Fix (CEO/Lawyer): Block export if required fields have unfilled placeholders
        # Per feedback: "[REQUIRED: AI System Name]" in outputs is negligence
        if self.config.strict:
            placeholder_msg = self._check_required_fields(plan_for_targets)
            if placeholder_msg:
                self._log_event(
                    "export.placeholder_validation.blocked",
                    {"reason": placeholder_msg},
                )
                return self._skip_targets(state.tgt_list), {}, placeholder_msg, None

        # Load bundle profiles (Domain 3) from contracts dir
        # This allows declarative bundles to be injected into exporters
        contract_bundle = None  # PR1: Always available for regulatory_meta access
        try:
            adapter = YamlContractsAdapter(
                strict=self.config.strict,
                tolerate_questions_errors=self.config.tolerate_questions_errors,
            )
            bundle_profiles = adapter.load_bundle_profiles(str(self.config.contracts_dir))

            # PR1: Always load contract bundle (for regulatory_meta in exporter)
            contract_bundle = adapter.load(str(self.config.contracts_dir))

            # Shared bundle coherence policy for INV-B2 and INV-B1.
            coherence = evaluate_bundle_coherence(
                bundle_profiles,
                contract_bundle,
                plan_for_targets,
                strict=self.config.strict,
                export_mode=self.config.export_mode,
                allow_incomplete_coverage=self.config.allow_incomplete_coverage,
            )

            if coherence.blocked_reason == "config" and coherence.config_warning:
                if self.config.strict:
                    self._log_event(
                        "export.bundle_profiles.error",
                        {"error": coherence.config_warning},
                    )
                    return (
                        self._skip_targets(state.tgt_list),
                        {},
                        None,
                        f"Bundle evidence validation failed (INV-B2): {coherence.config_warning}",
                    )
                self._log_event(
                    "export.bundle_profiles.warning",
                    {"warning": coherence.config_warning},
                )

            if coherence.integrity_report and coherence.integrity_report.has_errors():
                error_summary = coherence.integrity_report.summary()
                self._log_event(
                    "export.evidence_validation.issues",
                    {
                        "error_count": len(coherence.integrity_report.problems),
                        "summary": error_summary,
                    },
                )
                if self.config.strict and coherence.blocked_reason == "integrity":
                    msg = f"Bundle evidence validation failed (INV-B2): {error_summary}"
                    return self._skip_targets(state.tgt_list), {}, None, msg

            if coherence.coverage_report:
                # Capture coverage metrics for export context without mutating the plan.
                export_metrics.update(self._build_coverage_metrics(coherence.coverage_report))

                if coherence.coverage_report.has_critical_gaps():
                    coverage_summary = coherence.coverage_report.summary()
                    self._log_event(
                        "export.coverage_validation.issues",
                        {
                            "missing_actions": len(coherence.coverage_report.missing_actions),
                            "missing_evidences": len(coherence.coverage_report.missing_evidences),
                            "active_profiles": len(coherence.coverage_report.active_profiles),
                            "summary": coverage_summary,
                        },
                    )

                    if coherence.coverage_bypass_used:
                        self._log_event(
                            "export.coverage_validation.warning",
                            {"summary": coverage_summary, "allow_incomplete": True},
                        )
                    elif coherence.blocked_reason == "coverage":
                        msg = (
                            "CRITICAL SAFETY: Bundle coverage validation failed "
                            f"(INV-B1): {coverage_summary}"
                        )
                        return self._skip_targets(state.tgt_list), {}, None, msg

            if state.cfg_all is None:
                state.cfg_all = {}
            if isinstance(state.cfg_all, dict):
                state.cfg_all["bundle_profiles"] = bundle_profiles
                # PR1: Store bundle for regulatory_meta access in exporter
                state.cfg_all["_bundle"] = contract_bundle

                # Filter applied profiles if filtered by config (CLI --profile)
                if self.config.profile:
                    requested_profile = self.config.profile
                    filtered_profiles = {
                        k: v
                        for k, v in bundle_profiles.items()
                        if k == requested_profile
                        or getattr(v, "kind", "") in {"technical", "reporting"}
                    }
                    if requested_profile not in bundle_profiles:
                        msg = (
                            f"Profile '{requested_profile}' not found in bundle profiles: "
                            f"{list(bundle_profiles.keys())}"
                        )
                        if self.config.strict:
                            return self._skip_targets(state.tgt_list), {}, None, msg
                        else:
                            self._log_event("export.profile_filter.warning", {"msg": msg})
                    else:
                        bundle_profiles = filtered_profiles
                        state.cfg_all["bundle_profiles"] = bundle_profiles

                # Load core output profiles separately for FilesystemExporter.
                # This enables working with declarative profiles.
                core_profiles = {
                    k: v
                    for k, v in bundle_profiles.items()
                    if getattr(v, "kind", "") == "technical"
                }
                state.cfg_all["core_bundle_profiles"] = core_profiles
        except YAMLLoadError as e:
            # In strict mode, failing to load profiles is critical if we rely on them
            self._log_event(
                "export.bundle_profiles.error",
                {
                    "error": str(e),
                },
            )
            if self.config.strict:
                msg = f"Failed to load bundle profiles: {e}"
                return self._skip_targets(state.tgt_list), {}, None, msg

        results, errors = self._export_targets(
            plan=plan_for_targets,
            templates_dir=state.templates_dir,
            tgt_list=state.tgt_list,
            cfg_all=state.cfg_all,
            pre_manifest=state.pre_manifest,
            quality_report=state.quality_report,
            export_metrics=export_metrics,
        )
        return results, errors, None, None

    def _check_plan_hash_consistency(self, plan: Plan) -> str | None:
        """Verify that trace.plan_hash matches recomputed deterministic plan hash.

        Uses domain helper compute_plan_hash which strips volatile fields
        (assessment_timestamp, export_context, audit, wizard_answers, etc.) so that
        the hash reflects only the semantic content of the plan.

        Returns:
            Error message if mismatch is detected, None otherwise.
        """
        if not isinstance(plan, dict):
            return None

        trace = plan.get("trace", {}) or {}
        expected = trace.get("plan_hash")
        if not expected:
            self._log_event(
                "export.plan_hash_consistency.skip",
                {"reason": "trace.plan_hash missing"},
            )
            return None

        try:
            recomputed = compute_plan_hash(dict(plan))
        except Exception as exc:
            self._log_event(
                "export.plan_hash_consistency.error",
                {"error": str(exc)},
            )
            if self.config.strict:
                raise ExportConsistencyError(
                    "Plan hash recomputation failed in strict mode"
                ) from exc
            return None

        if recomputed != expected:
            self._log_event(
                "export.plan_hash_consistency.mismatch",
                {
                    "trace_plan_hash": expected,
                    "recomputed_plan_hash": recomputed,
                },
            )
            return (
                "Plan hash mismatch: trace.plan_hash="
                f"{expected[:16]}... but recomputed plan_hash={recomputed[:16]}... "
                "Plan may have been mutated after assessment; re-run 'assess' with current "
                "contracts or inspect CLI injections."
            )

        return None

    def _should_run_release_gate(self) -> Path | None:
        """Return target bundle path if release gate should run, else None."""
        target_dir = self.config.outdir / "deliverables" / "public_snapshot_v1"
        if self.config.profile == "public_snapshot_v1":
            return target_dir
        if self.config.release and target_dir.exists():
            return target_dir
        return None

    def _run_release_gate(self) -> str | None:
        """Run public bundle release gate if applicable."""
        target_dir = self._should_run_release_gate()
        if target_dir is None:
            return None

        from intrinsical_policy_engine.app.gating.public_bundle import verify_public_bundle

        result = verify_public_bundle(target_dir, export_root=self.config.outdir, fail_fast=False)
        if result.ok:
            self._log_event("export.release_gate.passed", {"target": str(target_dir)})
            return None

        msg = f"Release gate failed: {len(result.errors)} issue(s)"
        if result.errors:
            msg += f" (first: {result.errors[0]})"
        self._log_event(
            "export.release_gate.failed",
            {"target": str(target_dir), "errors": result.errors[:10]},
        )
        return msg

    def _check_quality_gating(  # noqa: C901
        self, plan: Plan, quality_report: dict | None
    ) -> str | None:
        """Check if export should be blocked due to quality issues.

        Per docs/invariants/ENGINE-ARCHITECTURE-v1.md: evidences in state 'placeholder' for actions
        marked as priority: critical should block export in strict mode.

        Per CRITICAL review: Also block if required evidences are completely missing
        (not just placeholder) for critical actions. Uses missing_reasons_by_article
        from compute_evidence_quality to detect absent required files.

        Returns:
            Error message if blocked, None if OK to proceed.
        """
        if not quality_report:
            return None

        quality_by_file = quality_report.get("quality_by_file", {})
        missing_reasons_by_article = quality_report.get("missing_reasons_by_article", {})

        # Get critical actions from plan
        actions_meta = plan.get("actions_meta", [])
        if not isinstance(actions_meta, list):
            return None

        # Build set of evidence paths for critical actions and their articles
        critical_evidence_paths: set[str] = set()
        critical_articles: set[str] = set()
        for action in actions_meta:
            if not isinstance(action, dict):
                continue
            priority = action.get("priority", "medium")
            if priority == "critical":
                # Collect evidence paths
                evidence_list = action.get("evidence", [])
                if isinstance(evidence_list, list):
                    for ev in evidence_list:
                        if isinstance(ev, str):
                            critical_evidence_paths.add(ev)
                        elif isinstance(ev, dict) and ev.get("path"):
                            critical_evidence_paths.add(str(ev.get("path")))
                # Collect articles for missing evidence check
                articles = action.get("articles", [])
                if isinstance(articles, list):
                    for art in articles:
                        if isinstance(art, str):
                            critical_articles.add(art)

        # Placeholder evidences for critical actions
        placeholder_critical: list[str] = []
        if quality_by_file and critical_evidence_paths:
            for path, status in quality_by_file.items():
                if status == "placeholder":
                    # Check if this path matches any critical evidence
                    for crit_path in critical_evidence_paths:
                        if path == crit_path or path.endswith(crit_path) or crit_path in path:
                            placeholder_critical.append(path)
                            break

        # Missing required evidences for articles linked to critical actions
        # Block if missing_reasons_by_article has entries for critical articles
        missing_critical: list[str] = []
        if missing_reasons_by_article and critical_articles:
            for art in critical_articles:
                missing_list = missing_reasons_by_article.get(art, [])
                for m in missing_list:
                    if isinstance(m, dict):
                        missing_critical.append(f"{art}:{m.get('path', 'unknown')}")
                    else:
                        missing_critical.append(f"{art}:{m}")

        # Build combined error message
        errors: list[str] = []
        if placeholder_critical:
            sample = placeholder_critical[:3]
            errors.append(
                f"{len(placeholder_critical)} placeholder evidence(s) for critical actions "
                f"(e.g. {sample})"
            )
        if missing_critical:
            sample = missing_critical[:3]
            errors.append(
                f"{len(missing_critical)} missing required evidence(s) for critical articles "
                f"(e.g. {sample})"
            )

        if errors:
            return f"Quality gating failed: {'; '.join(errors)}"

        return None

    def _check_required_fields(self, plan: Plan) -> str | None:
        """Check if required context fields contain unfilled placeholders.

        Per Red Team feedback (CEO/Lawyer): outputs with "[REQUIRED: AI System Name]"
        indicate negligence and should block production exports in strict mode.

        Returns:
            Error message if unfilled placeholders found, None if OK.
        """
        # Build context to get the fully-resolved values
        ctx = build_artifact_context(
            dict(plan) if isinstance(plan, dict) else {},
            framework_path=self.config.contracts_dir,
        )

        # Validate required fields
        is_valid, errors = validate_required_context_fields(ctx, strict=False)

        if not is_valid:
            sample = errors[:3]
            return (
                f"Required context fields have unfilled placeholders: {sample}. "
                "Provide values via --answers file with 'system.name', 'provider.name', etc. "
                "or use non-strict mode for draft exports."
            )

        return None

    def _validate_trace_integrity(self, plan: Plan) -> str | None:
        """Validate that trace contains required hashes for reproducibility.

        Per INV-05 (TEST-DRIVEN-DEVELOPMENT-v1.md §2.1) and
        docs/invariants/ENGINE-ARCHITECTURE-v1.md, a valid snapshot requires:
        - trace.bundle_hash: identifies the contracts used for assessment
        - trace.templates_hash: identifies the templates used for assessment
        - trace.framework_pack_hash: SSOT for pack composition and delivery

        In strict mode, missing hashes indicate an incomplete plan that cannot
        guarantee snapshot reproducibility.

        Returns:
            Error message if critical hashes are missing, None otherwise.
        """
        trace = plan.get("trace", {}) or {}
        missing = []

        if not trace.get("bundle_hash"):
            missing.append("bundle_hash")
        if not trace.get("templates_hash"):
            missing.append("templates_hash")
        if not trace.get("framework_pack_hash"):
            missing.append("framework_pack_hash")

        if missing:
            self._log_event(
                "export.trace_integrity.warning",
                {"missing_hashes": missing},
            )
            # P4: Provide actionable guidance for the user
            action_hint = (
                "Run 'ipe assess --contracts <path>' to regenerate "
                "the plan with complete trace. "
                "If using the API, ensure run_assess() is called "
                "(it auto-computes templates_hash)."
            )
            return (
                f"Trace missing required hashes for reproducibility: {', '.join(missing)}. "
                f"{action_hint} (INV-05)"
            )

        return None

    def _check_templates_coherence(self, plan: Plan, templates_dir: str) -> str | None:
        """Check if templates hash matches between assess and export.

        Per INV-05 and docs/invariants/ENGINE-ARCHITECTURE-v1.md, templates_hash must be
        captured at assess time to ensure reproducibility. This check detects
        drift when templates are modified between assessment and export.

        Returns:
            Error message string if mismatch detected, None otherwise.
            In strict mode, caller should block export on mismatch.
        """
        trace = plan.get("trace", {}) or {}
        plan_templates_hash = trace.get("templates_hash")

        if not plan_templates_hash:
            self._log_event(
                "export.templates_coherence.skipped",
                {"reason": "trace.templates_hash missing"},
            )
            return None

        try:
            # Use public helper for consistent hashing (P3.1)
            current_hash = sha256_directory(Path(templates_dir), warn_if_missing=False)

            if plan_templates_hash != current_hash:
                msg = (
                    f"Templates hash mismatch: plan was assessed with templates "
                    f"{plan_templates_hash[:16]}... but current templates are "
                    f"{current_hash[:16]}... - artifacts may not match assessment"
                )
                self._log_event(
                    "export.templates_coherence.mismatch",
                    {
                        "reason": msg,
                        "plan_hash": plan_templates_hash,
                        "current_hash": current_hash,
                    },
                )
                return msg
        except (OSError, ValueError) as exc:
            self._log_event(
                "export.templates_coherence.error",
                {"error": str(exc)},
            )

        return None

    def _check_routing_consistency(self, plan: Plan) -> None:
        """Check consistency between routing route and actions in plan.

        Verify that mutually exclusive route-specific actions are not mixed.
        This detects if filtering failed or if inputs are incoherent.
        """
        routing = plan.get("routing", {}) or {}
        route = routing.get("route")
        actions = set(plan.get("actions", []) or [])

        inconsistent = False
        details = {}

        if route == "standard-review":
            if "ROUTE-ENHANCED" in actions:
                inconsistent = True
                details = {"route": route, "unexpected_action": "ROUTE-ENHANCED"}
        elif route == "enhanced-review" and "ROUTE-STANDARD" in actions:
            inconsistent = True
            details = {"route": route, "unexpected_action": "ROUTE-STANDARD"}

        # We rely on the route check above as the primary consistency signal.

        if inconsistent:
            self._log_event(
                "export.routing_consistency.warning",
                details,
            )

    def _check_bundle_coherence(self, plan: Plan) -> str | None:
        """Check if plan's bundle_hash matches current contracts.

        Per docs/invariants/ENGINE-ARCHITECTURE-v1.md: Export should verify
        that the plan was generated with the same contracts that are currently
        on disk. This prevents exporting a plan that was assessed with different
        rules/actions than the current framework.

        Returns:
            Error message if mismatch detected, None if OK.
        """
        # Extract bundle_hash from plan trace
        trace = plan.get("trace", {}) or {}
        plan_bundle_hash = trace.get("bundle_hash")

        if not plan_bundle_hash:
            msg = "Plan has no bundle_hash in trace"
            if self.config.strict:
                return f"Bundle coherence failed: {msg}"

            # Old plans without bundle_hash - can't verify, allow with warning
            self._log_event(
                "export.bundle_coherence.skip",
                {"reason": msg},
            )
            return None

        # Load current contracts and compute hash
        try:
            adapter = YamlContractsAdapter(
                strict=self.config.strict,
                tolerate_questions_errors=self.config.tolerate_questions_errors,
            )
            current_bundle = adapter.load(str(self.config.contracts_dir))
            current_hash = compute_bundle_hash(current_bundle)
        except Exception as exc:
            if self.config.strict:
                raise RuntimeError(
                    f"Failed to load contracts for coherence check in strict mode: {exc}"
                ) from exc

            # Can't load contracts - skip coherence check with warning
            self._log_event(
                "export.bundle_coherence.skip",
                {"reason": f"Could not load current contracts: {exc}"},
            )
            return None

        if plan_bundle_hash != current_hash:
            return (
                "Bundle hash mismatch: plan was assessed with "
                f"bundle_hash={plan_bundle_hash}... "
                f"but current contracts have hash={current_hash}... "
                "The framework may have been updated since the assessment was run. "
                "Re-run assessment with current contracts or use --no-strict to force export."
            )

        # Also validate framework_pack_hash to cover templates/bundles/schemas drift
        plan_pack_hash = trace.get("framework_pack_hash")
        if plan_pack_hash and self._layout is not None:
            from intrinsical_policy_engine.common.hashing import compute_framework_pack_hashes

            current_pack = compute_framework_pack_hashes(self._layout, law_data_hash=current_hash)
            current_pack_hash = current_pack.get("framework_pack_hash")
            if plan_pack_hash != current_pack_hash:
                return (
                    "Framework pack hash mismatch: plan was assessed with "
                    f"framework_pack_hash={plan_pack_hash}... "
                    f"but current pack hash={current_pack_hash}... "
                    "The framework pack may have changed (templates, bundle, schemas, manifest). "
                    "Re-run assessment or use --no-strict to force export."
                )

        return None

    def _skip_targets(self, tgt_list: list[str]) -> list[ExportResult]:
        """Create skipped results for all targets."""
        return [ExportResult(target=tgt, outputs=[], stats={"skipped": True}) for tgt in tgt_list]

    def _export_targets(
        self,
        plan: Plan,
        templates_dir: str,
        tgt_list: list[str],
        cfg_all: dict | object,
        pre_manifest: EvidenceManifest | None,
        quality_report: dict | None,
        export_metrics: dict[str, Any] | None,
    ) -> tuple[list[ExportResult], dict[str, str]]:
        """Execute exporters for all targets."""
        target_results: list[ExportResult] = []
        target_errors: dict[str, str] = {}

        for tgt in tgt_list:
            had_error = False
            self._log_event("export.start", {"templates": templates_dir, "target": tgt})

            try:
                cfg = self._resolve_target_config(tgt, cfg_all)
                exporter = get_exporter(canonical_target(tgt))

                exporter.setup(
                    self._logger,
                    {
                        **(cfg or {}),
                        "strict": bool(self.config.strict),
                        "evidence_manifest": pre_manifest,
                        "quality_report": quality_report,
                        "export_metrics": export_metrics or {},
                        "export_mode": self.config.export_mode,
                        "bundle_profiles": (
                            cfg_all.get("bundle_profiles") if isinstance(cfg_all, dict) else None
                        ),
                        "wizard_answers": (
                            cfg_all.get("wizard_answers") if isinstance(cfg_all, dict) else None
                        ),
                        "include_raw_answers": bool(self.config.include_raw_answers),
                        "skip_gpg_signing": bool(self.config.skip_gpg_signing),
                        "release": bool(self.config.release),
                        # PR1: Pass bundle for regulatory_meta access (removes silent failure)
                        "bundle": (cfg_all.get("_bundle") if isinstance(cfg_all, dict) else None),
                        "framework_path": self.config.contracts_dir,
                        "skip_nodes": ["summary_json"],
                    },
                )
                exporter.export(plan, templates_dir, str(self.config.outdir))

                self._log_event(
                    "export.finish",
                    {
                        "actions": len(plan.get("actions", [])),
                        "outcome": plan.get("outcome"),
                        "target": tgt,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                # Catch all exceptions to prevent a buggy exporter from crashing the entire CLI
                # Record the error and continue with other targets; strict mode handling
                # happens at the end of run() where it checks result.any_error
                msg = f"{type(exc).__name__}: {exc}"
                self._log_event("export.error", {"target": tgt, "error": msg})
                had_error = True
                target_errors[tgt] = msg

            target_results.append(ExportResult(target=tgt, outputs=[], stats={"error": had_error}))

        return target_results, target_errors

    def _resolve_target_config(self, tgt: str, cfg_all: dict | object) -> dict | None:
        """Resolve configuration for a specific target."""
        if not isinstance(cfg_all, dict):
            return None

        keys_to_try = target_name_variants(tgt)
        cfg_targets = cfg_all.get("targets", {})
        if not isinstance(cfg_targets, dict):
            cfg_targets = {}

        # Try targets.<key> first
        for key in keys_to_try:
            if key in cfg_targets:
                return cfg_targets.get(key)

        # Try top-level <key>
        for key in keys_to_try:
            if key in cfg_all:
                return cfg_all.get(key)

        return None

    def _extract_project_key(self, cfg_all: dict | object) -> str | None:
        """Extract project_key from configuration."""
        if not isinstance(cfg_all, dict):
            return None

        project_key = cfg_all.get("project_key")
        if not project_key:
            targets = cfg_all.get("targets")
            if isinstance(targets, dict):
                fs_cfg = targets.get("filesystem")
                if isinstance(fs_cfg, dict):
                    project_key = fs_cfg.get("project_key")

        return project_key

    def _build_coverage_metrics(self, coverage_report: CoverageReport) -> dict[str, Any]:
        """Build INV-B1 coverage metrics for downstream context and metrics.json."""
        required_actions = set(coverage_report.required_actions or set())
        covered_actions = set(coverage_report.covered_actions or set())
        missing_actions = coverage_report.missing_actions

        required_evidences = set(coverage_report.required_evidences or set())
        covered_evidences = set(coverage_report.covered_evidences or set())
        missing_evidences = coverage_report.missing_evidences

        def _ratio(num: int, den: int) -> float:
            return round(num / den, 4) if den > 0 else 0.0

        coverage_invb1 = {
            "required_actions": sorted(required_actions),
            "covered_actions": sorted(covered_actions),
            "missing_actions": sorted(missing_actions),
            "required_evidences": sorted(required_evidences),
            "covered_evidences": sorted(covered_evidences),
            "missing_evidences": sorted(missing_evidences),
            "active_profiles": list(coverage_report.active_profiles),
            "actions_coverage_pct": _ratio(len(covered_actions), len(required_actions)),
            "evidences_coverage_pct": _ratio(len(covered_evidences), len(required_evidences)),
        }

        return {
            "coverage_invb1": coverage_invb1,
            # Convenience numeric metrics (picked up by coverage_metrics in context)
            "coverage_invb1_actions_required": len(required_actions),
            "coverage_invb1_actions_covered": len(covered_actions),
            "coverage_invb1_actions_missing": len(missing_actions),
            "coverage_invb1_actions_pct": coverage_invb1["actions_coverage_pct"],
            "coverage_invb1_evidences_required": len(required_evidences),
            "coverage_invb1_evidences_covered": len(covered_evidences),
            "coverage_invb1_evidences_missing": len(missing_evidences),
            "coverage_invb1_evidences_pct": coverage_invb1["evidences_coverage_pct"],
        }

    def _ensure_export_context_namespace(self, project_key: str | None = None) -> Plan:
        """Ensure plan has export context with namespace."""
        plan = self.config.plan
        if not isinstance(plan, dict):
            return plan

        ctx = plan.get("export_context") or {}
        new_ctx = dict(ctx) if isinstance(ctx, dict) else {}

        if project_key:
            new_ctx["project_key"] = project_key

        if new_ctx.get("uid_namespace"):
            new_plan_raw = dict(plan)
            new_plan_raw["export_context"] = new_ctx
            return cast(Plan, new_plan_raw)

        trace = plan.get("trace")
        trace_mapping = trace if isinstance(trace, dict) else {}
        portable_identity: object = (
            trace_mapping.get("framework_pack_hash")
            or trace_mapping.get("bundle_hash")
            or trace_mapping.get("pack_hashes")
            or "default"
        )
        try:
            canonical_identity = json.dumps(
                portable_identity,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(canonical_identity.encode(DEFAULT_ENCODING)).hexdigest()
        except (TypeError, ValueError, UnicodeError):
            digest = hashlib.sha256(b'"default"').hexdigest()

        new_ctx.setdefault("uid_namespace", f"contracts-{digest}")
        # The namespace is derived only from portable pack provenance. Host and
        # checkout paths are intentionally absent so relocating a pack cannot
        # change downstream UIDs.

        new_plan_raw = dict(plan)
        new_plan_raw["export_context"] = new_ctx
        return cast(Plan, new_plan_raw)

    def _save_plan_if_requested(self) -> None:
        """Persist plan if save_plan is enabled."""
        if self.config.save_plan:
            plan_dict = dict(self.config.plan)
            plan_id = get_plan_fingerprint(plan_dict)
            if not plan_id:
                plan_id = f"err-{uuid.uuid4().hex}"
            FsPlanStore(base_dir=str(self.config.outdir)).save(plan_id, plan_dict)

    def _load_export_config_safe(self) -> tuple[dict, bool, str | None]:
        """Load export configuration with error handling."""
        config_error = False
        config_error_msg: str | None = None

        try:
            cfg_all = self._load_export_config()
        except ExportConfigError as exc:
            cfg_all = {}
            config_error = True
            config_error_msg = str(exc)
            self._log_event(
                "export.config_error",
                {"path": self.config.config_path or "<none>", "error": str(exc)},
            )

        return cfg_all, config_error, config_error_msg

    def _load_export_config(self) -> dict:
        """Load export configuration from file."""
        if not self.config.config_path:
            return {}

        p = Path(self.config.config_path)
        if not p.exists():
            raise ExportConfigError(f"Export config file not found: {p}")

        try:
            data = yaml.safe_load(p.read_text(encoding=DEFAULT_ENCODING)) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            raise ExportConfigError(f"Failed to load export config {p}: {exc}") from exc

        if not isinstance(data, dict):
            raise ExportConfigError(f"Export config {p} must contain a mapping at top level")

        return data

    def _warn_if_missing_templates(self, templates_dir: str, tgt_list: list[str]) -> str | None:
        """Check that templates directory and required framework files exist.

        Logs warnings in all cases; returns an error message when a hard problem is
        detected (missing templates dir or core files). Caller decides whether this
        is fatal (e.g. in strict mode) or just a soft warning.
        """
        import sys

        templates_path = Path(templates_dir)

        # Missing templates directory: nothing to export for any target
        if not templates_path.exists():
            msg = (
                f"templates dir not found: {templates_dir}; "
                f"export may be partial or targets may be skipped for {tgt_list}"
            )
            data = {"templates": templates_dir, "targets": tgt_list}
            if self._logger:
                self._logger.warning("export.templates_missing", data)
            else:
                sys.stderr.write(f"[export] {msg}\n")
            return msg

        # Check for required framework files (rules.yml and evidence_map.yml)
        missing: list[str] = []
        if self._layout is not None:
            rules_files = self._layout.resolve_contract_files("rules")
            evidence_map_files = self._layout.resolve_contract_files("evidence_map")
            rules_path = rules_files[0] if rules_files else self._layout.framework_dir / "rules.yml"
            ev_map_path = (
                evidence_map_files[0]
                if evidence_map_files
                else self._layout.framework_dir / "evidence_map.yml"
            )
        else:
            # Fallback for packs without resolved layout: anchor on contracts_dir,
            # not templates_path.parent
            rules_path = self.config.contracts_dir / "config" / "rules.yml"
            ev_map_path = self.config.contracts_dir / "config" / "evidence_map.yml"

        if not rules_path.exists():
            missing.append(str(rules_path))
        if not ev_map_path.exists():
            missing.append(str(ev_map_path))

        if missing:
            msg = (
                "Missing required framework files for export: "
                + ", ".join(missing)
                + f". Targets {tgt_list} may be incomplete."
            )
            data = {"templates": templates_dir, "targets": tgt_list, "missing": missing}
            if self._logger:
                self._logger.warning("export.framework_files_missing", data)
            else:
                sys.stderr.write(f"[export] {msg}\n")
            return msg

        return None

    def _validate_templates_integrity(self, templates_dir: str) -> str | None:  # noqa: C901
        """Validate template structure/integrity without requiring filled content."""
        from intrinsical_policy_engine.app.template_validation import validate_templates

        errors: list[str] = []
        templates_path = Path(templates_dir)
        if self._layout is not None:
            evidence_templates_dir = self._layout.evidence_templates_dir
        else:
            evidence_templates_dir = self.config.contracts_dir / "evidence_templates"

        # Validate templates directory presence
        if not templates_path.exists():
            errors.append(f"Templates directory not found: {templates_path}")
        if not evidence_templates_dir.exists():
            errors.append(f"Evidence templates directory not found: {evidence_templates_dir}")

        # Jinja/template syntax + frontmatter integrity (markdown)
        if templates_path.exists():
            # Structure-only validation: do not fail on undefined template variables.
            result = validate_templates(templates_path, strict=False)
            if result.errors:
                sample = result.errors[:3]
                errors.append(
                    f"Templates validation errors in {templates_path} "
                    f"({len(result.errors)}): {sample}"
                )
        if evidence_templates_dir.exists():
            result = validate_templates(evidence_templates_dir, strict=False)
            if result.errors:
                sample = result.errors[:3]
                errors.append(
                    f"Evidence templates validation errors in {evidence_templates_dir} "
                    f"({len(result.errors)}): {sample}"
                )

        # Evidence map paths integrity
        if self._layout is not None:
            try:
                from intrinsical_policy_engine.adapters.export.base.evidence.evidence_utils import (
                    collect_evidence_entries,
                    load_evidence_map_raw,
                    validate_evidence_paths,
                )

                evidence_map_files = self._layout.resolve_contract_files("evidence_map")
                evidence_map_path = (
                    evidence_map_files[0]
                    if evidence_map_files
                    else self._layout.framework_dir / "evidence_map.yml"
                )
                if not evidence_map_path.exists():
                    errors.append(f"evidence_map.yml not found at {evidence_map_path}")
                else:
                    emap = load_evidence_map_raw(evidence_map_path)
                    entries = collect_evidence_entries(emap)
                    evidence_report = validate_evidence_paths(evidence_templates_dir, entries)
                    if evidence_report.missing_required:
                        errors.append(
                            "Missing required evidence templates: "
                            f"{len(evidence_report.missing_required)}"
                        )
                    if evidence_report.missing_optional:
                        errors.append(
                            "Missing optional evidence templates: "
                            f"{len(evidence_report.missing_optional)}"
                        )
            except (OSError, ValueError, TypeError) as exc:
                errors.append(f"Evidence path validation failed: {exc}")

        # Template ↔ contract referential integrity
        try:
            from intrinsical_policy_engine.app.template_validation import validate_integrity

            integrity_report = validate_integrity(self.config.contracts_dir)
            if integrity_report.errors:
                integrity_sample = "; ".join(
                    f"{issue.location}: {issue.message}"
                    + (f" {issue.suggestion}" if issue.suggestion else "")
                    for issue in integrity_report.errors[:3]
                )
                errors.append(
                    f"Template integrity errors "
                    f"({len(integrity_report.errors)}): {integrity_sample}"
                )
            # Warnings are informational only; don't block --strict-templates
        except (ImportError, OSError, ValueError) as exc:
            errors.append(f"Template integrity validation failed: {exc}")

        if not errors:
            return None
        return "; ".join(errors)

    def _build_result(
        self,
        state: ArtifactsState,
        target_results: list[ExportResult],
        target_errors: dict[str, str],
        quality_gating_msg: str | None = None,
        bundle_coherence_msg: str | None = None,
        release_gate_msg: str | None = None,
    ) -> ExportRunResult:
        """Build the final export result."""
        return ExportRunResult(
            outdir=self.config.outdir,
            pre_artifact_error=state.pre_artifact_error,
            summary_error=state.summary_error,
            evidence_manifest_error=state.evidence_manifest_error,
            evidence_quality_error=state.evidence_quality_error,
            trace_error=state.trace_error,
            manifest_error=state.manifest_error,
            templates_validation_error=state.templates_validation_error,
            templates_validation_msg=state.templates_validation_msg,
            config_error=state.config_error,
            config_error_msg=state.config_error_msg,
            bundle_coherence_error=bundle_coherence_msg is not None,
            bundle_coherence_msg=bundle_coherence_msg,
            quality_gating_error=quality_gating_msg is not None,
            quality_gating_msg=quality_gating_msg,
            release_gate_error=release_gate_msg is not None,
            release_gate_msg=release_gate_msg,
            target_errors=target_errors,
            target_results=target_results,
        )

    def _log_event(self, event: str, data: dict) -> None:
        """Log orchestration events when a logger is available."""
        if self._logger:
            self._logger.info(event, data)
