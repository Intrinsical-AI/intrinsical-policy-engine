# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Typed embedding facade over the current application services."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import cast

from intrinsical_policy_engine.api.errors import (
    PackCompatibilityError,
    PackCompatibilityMetadataError,
    PackLicenseMetadataError,
)
from intrinsical_policy_engine.api.models import (
    AssessmentRequest,
    AssessmentResult,
    Diagnostic,
    DiagnosticSeverity,
    ExecutionPolicy,
    ExportRequest,
    ExportResult,
    GateDecision,
    GateStatus,
    PackDescriptor,
    PackReference,
    PackValidationRequest,
    PackValidationResult,
    SealRequest,
    SealResult,
)
from intrinsical_policy_engine.api.packs import (
    FilesystemPackProvider,
    PackProvider,
    installed_engine_version,
    validate_pack_compatibility,
    validate_pack_license_metadata,
)
from intrinsical_policy_engine.app.export.orchestrator import ExportRunResult
from intrinsical_policy_engine.app.gating.policy import (
    ExportGateFacts,
    ExportGateOutcome,
    ExportGatePolicy,
    evaluate_export_gate,
)
from intrinsical_policy_engine.app.use_cases import ops
from intrinsical_policy_engine.app.use_cases.bundle_orchestrator import BundleOrchestrator
from intrinsical_policy_engine.app.use_cases.seal import seal_and_package
from intrinsical_policy_engine.common.constants import CANONICAL_ARTIFACT_SCHEMA_VERSION
from intrinsical_policy_engine.common.hashing import (
    compute_framework_pack_hashes,
    sha256_directory,
)
from intrinsical_policy_engine.common.io_safety import (
    OutputPackPathOverlapError,
    UnsafeTreePathError,
    validate_export_output_boundary,
)
from intrinsical_policy_engine.domain.framework_layout import FrameworkLayout
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle
from intrinsical_policy_engine.domain.services.integrity import compute_bundle_hash
from intrinsical_policy_engine.domain.types import Plan


@dataclass(frozen=True)
class EngineConfig:
    """Dependencies and defaults for an embedded engine instance."""

    pack_provider: PackProvider = field(default_factory=FilesystemPackProvider)
    default_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)


class Engine:
    """Minimal stable facade for pack assessment, export, and sealing."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._config = config or EngineConfig()

    def describe_pack(self, reference: PackReference) -> PackDescriptor:
        """Resolve and compatibility-check a pack without loading its contracts."""
        descriptor = replace(
            self._config.pack_provider.resolve(reference),
            engine_version=installed_engine_version(),
        )
        # Custom providers must obey the same compatibility boundary as the
        # filesystem provider; resolution cannot bypass engine constraints.
        validate_pack_compatibility(descriptor)
        validate_pack_license_metadata(descriptor)
        return descriptor

    def validate_pack(self, request: PackValidationRequest) -> PackValidationResult:
        """Resolve and validate a pack without exposing loaded domain models."""
        policy = request.policy or self._config.default_policy
        try:
            descriptor = self.describe_pack(request.pack)
        except Exception as exc:  # noqa: BLE001 - public fault boundary
            return PackValidationResult(
                pack=None,
                gate=GateDecision(GateStatus.BLOCKED, (_pack_resolution_diagnostic(exc),)),
            )

        try:
            loaded = BundleOrchestrator(
                strict=policy.strict,
                tolerate_questions_errors=policy.tolerate_questions_errors,
            ).load_and_validate_complete_bundle(descriptor.root)
        except Exception as exc:  # noqa: BLE001 - public fault boundary
            diagnostic = _diagnostic(
                "PACK_VALIDATION_FAILED",
                exc,
                source=str(descriptor.root),
            )
            return PackValidationResult(
                pack=descriptor,
                gate=GateDecision(GateStatus.BLOCKED, (diagnostic,)),
            )

        severity = DiagnosticSeverity.ERROR if policy.strict else DiagnosticSeverity.WARNING
        diagnostics = tuple(
            Diagnostic(
                code="PACK_VALIDATION_PROBLEM",
                message=problem,
                severity=severity,
                source=str(descriptor.root),
            )
            for problem in loaded.validation_report.problems
        )
        status = (
            GateStatus.BLOCKED
            if diagnostics and policy.strict
            else GateStatus.WARNED
            if diagnostics
            else GateStatus.PASSED
        )
        return PackValidationResult(
            pack=descriptor,
            gate=GateDecision(status, diagnostics),
        )

    def assess(self, request: AssessmentRequest) -> AssessmentResult:
        """Load a pack and produce a typed assessment result."""
        policy = request.policy or self._config.default_policy
        try:
            descriptor = self.describe_pack(request.pack)
        except Exception as exc:  # noqa: BLE001 - public fault boundary
            diagnostic = _pack_resolution_diagnostic(exc)
            return AssessmentResult(
                pack=None,
                plan=None,
                gate=GateDecision(GateStatus.BLOCKED, (diagnostic,)),
            )

        try:
            loaded = BundleOrchestrator(
                strict=policy.strict,
                tolerate_questions_errors=policy.tolerate_questions_errors,
            ).load_and_validate_complete_bundle(descriptor.root)
        except Exception as exc:  # noqa: BLE001 - public fault boundary
            return _failed_assessment("PACK_VALIDATION_FAILED", exc, pack=descriptor)

        diagnostics = tuple(
            Diagnostic(
                code="PACK_INTEGRITY_WARNING",
                message=problem,
                severity=DiagnosticSeverity.WARNING,
                source=str(descriptor.root),
            )
            for problem in loaded.validation_report.problems
        )

        try:
            layout = _load_layout(descriptor)
            bundle_hash = compute_bundle_hash(loaded.contract_bundle)
            pack_hashes = cast(
                dict[str, str],
                compute_framework_pack_hashes(layout, law_data_hash=bundle_hash),
            )
            plan = assess_from_bundle(
                loaded.contract_bundle,
                dict(request.answers),
                include_full_trace=policy.include_full_trace,
                templates_hash=sha256_directory(
                    layout.templates_dir,
                    warn_if_missing=False,
                    raise_if_missing=True,
                ),
                framework_pack_hashes=pack_hashes,
                base_date=request.base_date,
            )
            if policy.demo_mode:
                plan["demo_mode"] = True
        except Exception as exc:  # noqa: BLE001 - public fault boundary
            failure = _diagnostic("ASSESSMENT_FAILED", exc, source=str(descriptor.root))
            return AssessmentResult(
                pack=descriptor,
                plan=None,
                gate=GateDecision(GateStatus.BLOCKED, (*diagnostics, failure)),
            )

        status = GateStatus.WARNED if diagnostics else GateStatus.PASSED
        return AssessmentResult(
            pack=descriptor,
            plan=plan,
            gate=GateDecision(status, diagnostics),
        )

    def export(self, request: ExportRequest) -> ExportResult:
        """Assess inputs and materialize artifacts using existing exporters."""
        policy = request.policy or self._config.default_policy
        requested_output_dir = request.output_dir.expanduser()
        output_dir = requested_output_dir.resolve()
        assessment = self.assess(
            AssessmentRequest(
                pack=request.pack,
                answers=request.answers,
                base_date=request.base_date,
                policy=policy,
            )
        )
        if not assessment.success or assessment.pack is None or assessment.plan is None:
            return ExportResult(
                output_dir=output_dir,
                assessment=assessment,
                gate=GateDecision(GateStatus.BLOCKED, assessment.diagnostics),
            )

        try:
            validate_export_output_boundary(assessment.pack.root, requested_output_dir)
        except OutputPackPathOverlapError as exc:
            diagnostic = Diagnostic(
                code="OUTPUT_PACK_PATH_OVERLAP",
                message=str(exc),
                severity=DiagnosticSeverity.ERROR,
                source=str(output_dir),
            )
            return ExportResult(
                output_dir=output_dir,
                assessment=assessment,
                gate=GateDecision(
                    GateStatus.BLOCKED,
                    (*assessment.diagnostics, diagnostic),
                ),
            )
        except UnsafeTreePathError as exc:
            diagnostic = Diagnostic(
                code="UNSAFE_OUTPUT_TREE",
                message=str(exc),
                severity=DiagnosticSeverity.ERROR,
                source=str(output_dir),
            )
            return ExportResult(
                output_dir=output_dir,
                assessment=assessment,
                gate=GateDecision(
                    GateStatus.BLOCKED,
                    (*assessment.diagnostics, diagnostic),
                ),
            )

        gate_policy = ExportGatePolicy(
            strict=policy.strict,
            release=request.release,
            allow_incomplete_coverage=bool(policy.allow_incomplete_coverage),
            skip_gpg_signing=policy.skip_gpg_signing,
        )
        preflight = evaluate_export_gate(ExportGateFacts(), gate_policy)
        if preflight.blocked:
            diagnostics = assessment.diagnostics + _gate_outcome_diagnostics(
                preflight,
                source=str(output_dir),
            )
            return ExportResult(
                output_dir=output_dir,
                assessment=assessment,
                gate=GateDecision(GateStatus.BLOCKED, diagnostics),
            )

        try:
            export_plan = dict(assessment.plan)
            export_plan["artifact_schema_version"] = CANONICAL_ARTIFACT_SCHEMA_VERSION
            pack = assessment.pack
            if pack is not None:
                export_plan["pack"] = {
                    "id": pack.id,
                    "version": pack.version,
                    "manifest_timestamp": pack.manifest_timestamp,
                    "license": pack.license,
                    "attribution": pack.attribution,
                }
            if request.product is not None:
                product_name = request.product.name.strip()
                product_version = request.product.version.strip()
                export_plan["product"] = {
                    "name": product_name,
                    "version": product_version,
                }
                export_plan["product_name"] = product_name
                export_plan["product_version"] = product_version

            raw_result = ops.run_export(
                cast(Plan, export_plan),
                assessment.pack.root,
                output_dir,
                logger=None,
                save_plan=request.save_plan,
                templates=str(request.templates_dir) if request.templates_dir else None,
                targets=list(request.targets) if request.targets else None,
                config=str(request.target_config) if request.target_config else None,
                strict=policy.strict,
                strict_templates=policy.templates_are_strict,
                export_mode=policy.export_mode,
                profile=request.profile,
                release=request.release,
                include_raw_answers=request.include_raw_answers,
                wizard_answers=dict(request.answers),
                tolerate_questions_errors=policy.tolerate_questions_errors,
                allow_incomplete_coverage=policy.allow_incomplete_coverage,
                skip_gpg_signing=policy.skip_gpg_signing,
            )
        except Exception as exc:  # noqa: BLE001 - public fault boundary
            diagnostic = _diagnostic("EXPORT_FAILED", exc, source=str(output_dir))
            return ExportResult(
                output_dir=output_dir,
                assessment=assessment,
                gate=GateDecision(
                    GateStatus.BLOCKED,
                    (*assessment.diagnostics, diagnostic),
                ),
            )

        gate_outcome = evaluate_export_gate(
            ExportGateFacts(
                pre_artifact_error=bool(raw_result.pre_artifact_error),
                quality_gating_error=bool(raw_result.quality_gating_error),
                release_gate_error=bool(raw_result.release_gate_error),
                bundle_coherence_error=bool(raw_result.bundle_coherence_error),
                target_errors=tuple(raw_result.target_errors),
            ),
            gate_policy,
        )
        export_diagnostics = _export_diagnostics(raw_result, outcome=gate_outcome)
        diagnostics = assessment.diagnostics + export_diagnostics
        status = (
            GateStatus.BLOCKED
            if gate_outcome.blocked
            else GateStatus.WARNED
            if diagnostics
            else GateStatus.PASSED
        )
        return ExportResult(
            output_dir=output_dir,
            assessment=assessment,
            gate=GateDecision(status, diagnostics),
            target_errors=dict(raw_result.target_errors),
        )

    def seal(self, request: SealRequest) -> SealResult:
        """Validate and optionally package an existing export directory."""
        export_dir = request.export_dir.expanduser().resolve()
        output_zip = request.output_zip.expanduser().resolve() if request.output_zip else None
        if not export_dir.is_dir():
            diagnostic = Diagnostic(
                code="EXPORT_DIRECTORY_NOT_FOUND",
                message=f"Export directory not found: {export_dir}",
                severity=DiagnosticSeverity.ERROR,
                source=str(export_dir),
            )
            return SealResult(
                export_dir=export_dir,
                output_zip=output_zip,
                files_validated=0,
                gate=GateDecision(GateStatus.BLOCKED, (diagnostic,)),
            )

        try:
            raw_result = seal_and_package(
                export_dir=export_dir,
                output_zip=output_zip,
                sign=request.sign,
                strict=request.strict,
                evidence_dir=request.evidence_dir,
            )
        except Exception as exc:  # noqa: BLE001 - public fault boundary
            diagnostic = _diagnostic("SEAL_FAILED", exc, source=str(export_dir))
            return SealResult(
                export_dir=export_dir,
                output_zip=output_zip,
                files_validated=0,
                gate=GateDecision(GateStatus.BLOCKED, (diagnostic,)),
            )

        diagnostics = tuple(
            Diagnostic(
                code="SEAL_ERROR",
                message=message,
                severity=DiagnosticSeverity.ERROR,
                source=str(export_dir),
            )
            for message in raw_result.errors
        ) + tuple(
            Diagnostic(
                code="SEAL_WARNING",
                message=message,
                severity=DiagnosticSeverity.WARNING,
                source=str(export_dir),
            )
            for message in raw_result.warnings
        )
        status = (
            GateStatus.BLOCKED
            if not raw_result.success
            else GateStatus.WARNED
            if diagnostics
            else GateStatus.PASSED
        )
        return SealResult(
            export_dir=export_dir,
            output_zip=output_zip,
            files_validated=raw_result.seal_report.files_validated,
            gate=GateDecision(status, diagnostics),
        )


def _load_layout(descriptor: PackDescriptor) -> FrameworkLayout:
    # Kept behind the facade so FrameworkLayout never becomes part of the API.
    from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout

    return load_framework_layout(descriptor.root)


def _failed_assessment(
    code: str,
    exc: Exception,
    *,
    pack: PackDescriptor | None = None,
) -> AssessmentResult:
    diagnostic = _diagnostic(code, exc, source=str(pack.root) if pack else None)
    return AssessmentResult(
        pack=pack,
        plan=None,
        gate=GateDecision(GateStatus.BLOCKED, (diagnostic,)),
    )


def _diagnostic(code: str, exc: Exception, *, source: str | None = None) -> Diagnostic:
    message = str(exc).strip() or type(exc).__name__
    return Diagnostic(
        code=code,
        message=message,
        severity=DiagnosticSeverity.ERROR,
        source=source,
    )


def _pack_resolution_diagnostic(exc: Exception) -> Diagnostic:
    if isinstance(exc, PackCompatibilityError):
        code = "PACK_ENGINE_INCOMPATIBLE"
    elif isinstance(exc, PackCompatibilityMetadataError):
        code = "PACK_COMPATIBILITY_METADATA_INVALID"
    elif isinstance(exc, PackLicenseMetadataError):
        code = "PACK_LICENSE_METADATA_INVALID"
    else:
        code = "PACK_RESOLUTION_FAILED"
    source = (
        str(exc.pack_root)
        if isinstance(
            exc,
            (PackCompatibilityError, PackCompatibilityMetadataError, PackLicenseMetadataError),
        )
        else None
    )
    return _diagnostic(code, exc, source=source)


def _gate_outcome_diagnostics(
    outcome: ExportGateOutcome,
    *,
    source: str,
) -> tuple[Diagnostic, ...]:
    diagnostics = [
        Diagnostic(
            code=code,
            message=code.replace("_", " ").title(),
            severity=DiagnosticSeverity.ERROR,
            source=source,
        )
        for code in outcome.blockers
    ]
    diagnostics.extend(
        Diagnostic(
            code=code,
            message=code.replace("_", " ").title(),
            severity=DiagnosticSeverity.WARNING,
            source=source,
        )
        for code in outcome.warnings
    )
    return tuple(diagnostics)


def _export_diagnostics(
    raw_result: ExportRunResult,
    *,
    outcome: ExportGateOutcome,
) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    blocking_codes = {code.split(":", 1)[0] for code in outcome.blockers}

    def add(code: str, message: str | None) -> None:
        diagnostics.append(
            Diagnostic(
                code=code,
                message=message or code.replace("_", " ").title(),
                severity=(
                    DiagnosticSeverity.ERROR
                    if code in blocking_codes
                    else DiagnosticSeverity.WARNING
                ),
                source=str(raw_result.outdir),
            )
        )

    if raw_result.pre_artifact_error:
        add("EXPORT_PREPARATION_FAILED", raw_result.config_error_msg)
    if raw_result.templates_validation_error:
        add("TEMPLATE_VALIDATION_FAILED", raw_result.templates_validation_msg)
    if raw_result.bundle_coherence_error:
        add("BUNDLE_COHERENCE_FAILED", raw_result.bundle_coherence_msg)
    if raw_result.quality_gating_error:
        add("QUALITY_GATE_FAILED", raw_result.quality_gating_msg)
    if raw_result.release_gate_error:
        add("RELEASE_GATE_FAILED", raw_result.release_gate_msg)
    for target, message in sorted(raw_result.target_errors.items()):
        add("EXPORT_TARGET_FAILED", f"{target}: {message}")
    return tuple(diagnostics)
