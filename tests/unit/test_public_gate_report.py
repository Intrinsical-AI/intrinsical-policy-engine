# SPDX-License-Identifier: MPL-2.0
"""Public logical-AND gate composition contracts."""

import pytest

from intrinsical_policy_engine.api import (
    Diagnostic,
    DiagnosticSeverity,
    GateCheck,
    GateDecision,
    GateStatus,
    evaluate_gate,
)


def test_product_gate_can_tighten_but_not_override_engine_failure() -> None:
    engine_failure = GateCheck.from_decision(
        "engine.export",
        GateDecision(
            GateStatus.BLOCKED,
            (
                Diagnostic(
                    code="ENGINE_BLOCK",
                    message="Engine rejected export",
                    severity=DiagnosticSeverity.ERROR,
                ),
            ),
        ),
    )
    product_pass = GateCheck("product.release", GateStatus.PASSED, source="product")

    report = evaluate_gate(engine_failure, product_pass)

    assert report.status is GateStatus.BLOCKED
    assert not report.allowed
    assert report.decision.status is GateStatus.BLOCKED
    assert [diagnostic.code for diagnostic in report.diagnostics] == ["ENGINE_BLOCK"]


def test_warning_is_preserved_when_every_check_allows() -> None:
    report = evaluate_gate(
        GateCheck("engine.integrity", GateStatus.PASSED),
        GateCheck("pack.coverage", GateStatus.WARNED, source="pack"),
    )

    assert report.allowed
    assert report.status is GateStatus.WARNED


@pytest.mark.parametrize(
    "checks",
    [
        (GateCheck("", GateStatus.PASSED),),
        (
            GateCheck("duplicate", GateStatus.PASSED),
            GateCheck("duplicate", GateStatus.PASSED, source="product"),
        ),
    ],
)
def test_gate_check_identity_must_be_auditable(checks: tuple[GateCheck, ...]) -> None:
    with pytest.raises(ValueError):
        evaluate_gate(*checks)
