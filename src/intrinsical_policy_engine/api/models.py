# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Stable value objects for embedding Intrinsical Policy Engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypeAlias

PackReference: TypeAlias = str | Path
ExportMode: TypeAlias = Literal["executive", "full", "dev"]


class DiagnosticSeverity(str, Enum):
    """Portable diagnostic levels returned by the public facade."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Diagnostic:
    """A stable, presentation-neutral engine diagnostic."""

    code: str
    message: str
    severity: DiagnosticSeverity
    source: str | None = None


class GateStatus(str, Enum):
    """Outcome of the policy gate associated with an operation."""

    PASSED = "passed"
    WARNED = "warned"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class GateDecision:
    """Whether an operation may be consumed by its caller."""

    status: GateStatus
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def allowed(self) -> bool:
        """Return whether policy permits the result despite possible warnings."""
        return self.status is not GateStatus.BLOCKED


@dataclass(frozen=True)
class GateCheck:
    """One named, attributable gate decision in a composed report."""

    id: str
    status: GateStatus
    diagnostics: tuple[Diagnostic, ...] = ()
    source: str = "engine"

    @classmethod
    def from_decision(
        cls,
        id: str,
        decision: GateDecision,
        *,
        source: str = "engine",
    ) -> GateCheck:
        """Create a named check from an operation decision."""
        return cls(id=id, status=decision.status, diagnostics=decision.diagnostics, source=source)


@dataclass(frozen=True)
class GateReport:
    """Deterministic logical-AND composition of engine, pack and product gates."""

    checks: tuple[GateCheck, ...] = ()

    @property
    def status(self) -> GateStatus:
        """Aggregate checks without allowing a later check to downgrade failure."""
        if any(check.status is GateStatus.BLOCKED for check in self.checks):
            return GateStatus.BLOCKED
        if any(check.status is GateStatus.WARNED for check in self.checks):
            return GateStatus.WARNED
        return GateStatus.PASSED

    @property
    def allowed(self) -> bool:
        """Return whether every composed check allows consumption."""
        return self.status is not GateStatus.BLOCKED

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Flatten diagnostics in check order for stable presentation."""
        return tuple(diagnostic for check in self.checks for diagnostic in check.diagnostics)

    @property
    def decision(self) -> GateDecision:
        """Return the aggregate in the legacy single-decision shape."""
        return GateDecision(self.status, self.diagnostics)


def evaluate_gate(*checks: GateCheck) -> GateReport:
    """Build a report while enforcing unique, non-empty check identities."""
    identities = [check.id.strip() for check in checks]
    if any(not identity for identity in identities):
        raise ValueError("Gate check ids must be non-empty")
    if len(set(identities)) != len(identities):
        raise ValueError("Gate check ids must be unique within a report")
    return GateReport(tuple(checks))


@dataclass(frozen=True)
class PackDescriptor:
    """Identity, compatibility and distribution metadata for a resolved pack."""

    id: str
    version: str
    root: Path
    name: str | None = None
    status: str | None = None
    format_version: int | None = None
    compatible_engine_versions: tuple[str, ...] = ()
    engine_version: str | None = None
    manifest_timestamp: str | None = None
    license: str | None = None
    license_file: str | None = None
    attribution: str | None = None


@dataclass(frozen=True)
class ExecutionPolicy:
    """Cross-operation strictness and trace policy.

    ``strict_templates`` defaults to the value of ``strict`` when omitted,
    matching the existing export command semantics.
    """

    strict: bool = True
    include_full_trace: bool = False
    strict_templates: bool | None = None
    export_mode: ExportMode = "full"
    tolerate_questions_errors: bool = False
    allow_incomplete_coverage: bool | None = None
    demo_mode: bool = False
    skip_gpg_signing: bool = False

    @property
    def templates_are_strict(self) -> bool:
        """Return the effective template validation policy."""
        if self.strict_templates is None:
            return self.strict
        return self.strict_templates or self.strict


@dataclass(frozen=True)
class AssessmentRequest:
    """Inputs required for a side-effect-free assessment."""

    pack: PackReference
    answers: Mapping[str, Any] = field(default_factory=dict)
    base_date: date | None = None
    policy: ExecutionPolicy | None = None


@dataclass(frozen=True)
class PackValidationRequest:
    """Inputs for resolving, compatibility-checking and validating a pack."""

    pack: PackReference
    policy: ExecutionPolicy | None = None


@dataclass(frozen=True)
class PackValidationResult:
    """Public pack validation result suitable for CLI or shell lint output."""

    pack: PackDescriptor | None
    gate: GateDecision

    @property
    def success(self) -> bool:
        """Return whether the pack resolved and passed the requested policy."""
        return self.pack is not None and self.gate.allowed

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Return compatibility and contract diagnostics in evaluation order."""
        return self.gate.diagnostics

    @property
    def gate_report(self) -> GateReport:
        """Return the validation decision as a composable gate report."""
        return evaluate_gate(GateCheck.from_decision("pack.validation", self.gate))


@dataclass(frozen=True)
class AssessmentResult:
    """Public assessment result without leaking domain runtime models."""

    pack: PackDescriptor | None
    plan: Mapping[str, Any] | None
    gate: GateDecision

    @property
    def success(self) -> bool:
        """Return whether an assessment plan was produced and accepted."""
        return self.plan is not None and self.gate.allowed

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Return all diagnostics associated with the assessment."""
        return self.gate.diagnostics

    @property
    def gate_report(self) -> GateReport:
        """Return the assessment decision as a composable gate report."""
        return evaluate_gate(GateCheck.from_decision("assessment", self.gate))


@dataclass(frozen=True)
class ProductIdentity:
    """Optional product-shell provenance attached to an export."""

    name: str
    version: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("Product identity name and version must be non-empty")


@dataclass(frozen=True)
class ExportRequest:
    """Inputs for assessment followed by artifact export."""

    pack: PackReference
    answers: Mapping[str, Any]
    output_dir: Path
    targets: tuple[str, ...] = ("filesystem",)
    templates_dir: Path | None = None
    target_config: Path | None = None
    save_plan: bool = False
    profile: str | None = None
    release: bool = False
    include_raw_answers: bool = False
    base_date: date | None = None
    policy: ExecutionPolicy | None = None
    product: ProductIdentity | None = None


@dataclass(frozen=True)
class ExportResult:
    """Aggregated public result for assessment and artifact export."""

    output_dir: Path
    assessment: AssessmentResult
    gate: GateDecision
    target_errors: Mapping[str, str] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Return whether export policy permits consuming the artifacts."""
        return self.assessment.success and self.gate.allowed

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Return assessment and export diagnostics in execution order."""
        return self.gate.diagnostics

    @property
    def gate_report(self) -> GateReport:
        """Return the canonical export decision as a composable gate report."""
        return evaluate_gate(GateCheck.from_decision("export", self.gate))


@dataclass(frozen=True)
class SealRequest:
    """Inputs for integrity-checking and optionally packaging an export."""

    export_dir: Path
    output_zip: Path | None = None
    evidence_dir: Path | None = None
    sign: bool = False
    strict: bool = True


@dataclass(frozen=True)
class SealResult:
    """Public seal result independent of the domain seal implementation."""

    export_dir: Path
    output_zip: Path | None
    files_validated: int
    gate: GateDecision

    @property
    def success(self) -> bool:
        """Return whether the export passed seal policy."""
        return self.gate.allowed

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Return diagnostics produced while sealing."""
        return self.gate.diagnostics

    @property
    def gate_report(self) -> GateReport:
        """Return the seal decision as a composable gate report."""
        return evaluate_gate(GateCheck.from_decision("seal", self.gate))
