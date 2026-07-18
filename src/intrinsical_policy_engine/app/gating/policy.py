# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Single policy decision for consuming an export run.

Export stages collect facts; this module alone decides whether those facts
block consumption.  CLI and embedding facades must map the same outcome to
their own presentation types instead of reimplementing fatality rules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportGatePolicy:
    """Explicit policy inputs that affect export consumption."""

    strict: bool
    release: bool = False
    allow_incomplete_coverage: bool = False
    skip_gpg_signing: bool = False


@dataclass(frozen=True)
class ExportGateFacts:
    """Policy-neutral facts emitted by the export workflow."""

    pre_artifact_error: bool = False
    quality_gating_error: bool = False
    release_gate_error: bool = False
    bundle_coherence_error: bool = False
    target_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportGateOutcome:
    """Canonical export decision plus stable reason codes."""

    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        """Return whether callers must reject the export result."""
        return bool(self.blockers)

    @property
    def allowed(self) -> bool:
        """Return whether callers may consume the export result."""
        return not self.blocked


def evaluate_export_gate(
    facts: ExportGateFacts,
    policy: ExportGatePolicy,
) -> ExportGateOutcome:
    """Apply the canonical export policy exactly once."""
    blockers: list[str] = []
    warnings: list[str] = []

    if policy.release and policy.allow_incomplete_coverage:
        blockers.append("RELEASE_COVERAGE_BYPASS_FORBIDDEN")
    if policy.release and policy.skip_gpg_signing:
        blockers.append("RELEASE_UNSIGNED_EXPORT_FORBIDDEN")
    if policy.release and not policy.strict:
        blockers.append("RELEASE_REQUIRES_STRICT_POLICY")

    if facts.pre_artifact_error:
        blockers.append("EXPORT_PREPARATION_FAILED")
    if facts.quality_gating_error:
        blockers.append("QUALITY_GATE_FAILED")
    if facts.release_gate_error:
        blockers.append("RELEASE_GATE_FAILED")

    enforce_strict = policy.strict or policy.release
    if facts.bundle_coherence_error:
        target = blockers if enforce_strict else warnings
        target.append("BUNDLE_COHERENCE_FAILED")

    if facts.target_errors:
        target = blockers if enforce_strict else warnings
        target.extend(f"EXPORT_TARGET_FAILED:{name}" for name in sorted(facts.target_errors))

    return ExportGateOutcome(tuple(blockers), tuple(warnings))
