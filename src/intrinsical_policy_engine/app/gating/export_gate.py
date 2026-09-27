# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Shared application policy for bundle coherence gating.

This module centralizes the decision of whether bundle evidence validation
findings should block export, while keeping the evidence validator pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from intrinsical_policy_engine.adapters.quality.bundle_evidence_validator import (
    BundleEvidenceValidator,
    CoverageReport,
    ValidationReport,
)
from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.ports import ContractBundle
from intrinsical_policy_engine.domain.types import Plan


@dataclass(frozen=True)
class BundleCoherenceResult:
    """Result of evaluating bundle evidence coherence for export."""

    integrity_report: ValidationReport | None = None
    coverage_report: CoverageReport | None = None
    blocked_reason: Literal["config", "integrity", "coverage"] | None = None
    config_warning: str | None = None
    coverage_bypass_used: bool = False

    @property
    def blocked(self) -> bool:
        """Return True when export should be blocked."""
        return self.blocked_reason is not None


def _allow_incomplete_coverage(export_mode: str, explicit_allow: bool | None = None) -> bool:
    """Return whether coverage gaps may be bypassed for the current run."""
    if explicit_allow is not None:
        return explicit_allow

    return export_mode == "dev"


def evaluate_bundle_coherence(
    bundle_profiles: dict[str, BundleProfile] | None,
    contract_bundle: ContractBundle | None,
    plan: Plan,
    *,
    strict: bool,
    export_mode: str = "full",
    allow_incomplete_coverage: bool | None = None,
) -> BundleCoherenceResult:
    """Evaluate bundle evidence integrity and coverage under application policy."""
    if not bundle_profiles:
        return BundleCoherenceResult()
    if contract_bundle is None:
        msg = (
            "bundle_profiles provided but contract_bundle missing. "
            "INV-B2 validation skipped. This may indicate incomplete configuration."
        )
        if strict:
            return BundleCoherenceResult(
                blocked_reason="config",
                config_warning=msg,
            )
        return BundleCoherenceResult(config_warning=msg)

    profiles_dict = {
        pid: profile
        for pid, profile in bundle_profiles.items()
        if isinstance(profile, BundleProfile)
    }
    if not profiles_dict:
        return BundleCoherenceResult()

    validator = BundleEvidenceValidator()

    integrity_report = validator.validate_integrity(profiles_dict, contract_bundle)
    if integrity_report.has_errors() and strict:
        return BundleCoherenceResult(
            integrity_report=integrity_report,
            blocked_reason="integrity",
        )

    coverage_report = validator.validate_coverage(profiles_dict, contract_bundle, plan)
    coverage_bypass_used = False
    if coverage_report.has_critical_gaps():
        allow_incomplete = _allow_incomplete_coverage(
            export_mode, explicit_allow=allow_incomplete_coverage
        )
        if not allow_incomplete:
            return BundleCoherenceResult(
                integrity_report=integrity_report,
                coverage_report=coverage_report,
                blocked_reason="coverage",
            )
        coverage_bypass_used = True

    return BundleCoherenceResult(
        integrity_report=integrity_report,
        coverage_report=coverage_report,
        coverage_bypass_used=coverage_bypass_used,
    )
