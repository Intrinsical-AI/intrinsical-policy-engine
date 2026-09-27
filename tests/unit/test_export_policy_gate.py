# SPDX-License-Identifier: MPL-2.0
"""Canonical export-gate policy tests."""

from intrinsical_policy_engine.app.gating.policy import (
    ExportGateFacts,
    ExportGatePolicy,
    evaluate_export_gate,
)


def test_strict_policy_blocks_coherence_and_target_failures() -> None:
    outcome = evaluate_export_gate(
        ExportGateFacts(
            bundle_coherence_error=True,
            target_errors=("filesystem",),
        ),
        ExportGatePolicy(strict=True),
    )

    assert outcome.blocked
    assert outcome.blockers == (
        "BUNDLE_COHERENCE_FAILED",
        "EXPORT_TARGET_FAILED:filesystem",
    )
    assert outcome.warnings == ()


def test_tolerant_policy_warns_for_noncritical_export_failures() -> None:
    outcome = evaluate_export_gate(
        ExportGateFacts(
            bundle_coherence_error=True,
            target_errors=("jira",),
        ),
        ExportGatePolicy(strict=False),
    )

    assert outcome.allowed
    assert outcome.warnings == (
        "BUNDLE_COHERENCE_FAILED",
        "EXPORT_TARGET_FAILED:jira",
    )


def test_quality_and_release_stage_failures_are_always_blocking() -> None:
    outcome = evaluate_export_gate(
        ExportGateFacts(quality_gating_error=True, release_gate_error=True),
        ExportGatePolicy(strict=False),
    )

    assert outcome.blockers == ("QUALITY_GATE_FAILED", "RELEASE_GATE_FAILED")


def test_release_policy_rejects_security_bypasses_even_without_stage_errors() -> None:
    outcome = evaluate_export_gate(
        ExportGateFacts(),
        ExportGatePolicy(
            strict=True,
            release=True,
            allow_incomplete_coverage=True,
            skip_gpg_signing=True,
        ),
    )

    assert outcome.blockers == (
        "RELEASE_COVERAGE_BYPASS_FORBIDDEN",
        "RELEASE_UNSIGNED_EXPORT_FORBIDDEN",
    )


def test_release_policy_cannot_be_downgraded_to_tolerant() -> None:
    preflight = evaluate_export_gate(
        ExportGateFacts(),
        ExportGatePolicy(strict=False, release=True),
    )
    failed_run = evaluate_export_gate(
        ExportGateFacts(
            bundle_coherence_error=True,
            target_errors=("filesystem",),
        ),
        ExportGatePolicy(strict=False, release=True),
    )

    assert preflight.blockers == ("RELEASE_REQUIRES_STRICT_POLICY",)
    assert failed_run.blockers == (
        "RELEASE_REQUIRES_STRICT_POLICY",
        "BUNDLE_COHERENCE_FAILED",
        "EXPORT_TARGET_FAILED:filesystem",
    )
    assert failed_run.warnings == ()
