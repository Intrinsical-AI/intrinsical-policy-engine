# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure domain logic for sealing compliance exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intrinsical_policy_engine.domain.snapshot_contract import REQUIRED_METADATA


@dataclass(frozen=True)
class SealFileSnapshot:
    """Immutable snapshot of one exported file."""

    path: str
    sha256: str


@dataclass(frozen=True)
class SealInput:
    """Pure input required to evaluate seal integrity."""

    existing_checksums: dict[str, str]
    files: tuple[SealFileSnapshot, ...]
    quality_issues: tuple[dict[str, Any], ...] = ()
    evidence_errors: tuple[str, ...] = ()


@dataclass
class SealResult:
    """Result of the sealing operation; ``success`` holds exactly when ``errors`` is empty."""

    success: bool
    files_validated: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    signature_path: str | None = None
    signature_fingerprint: str | None = None


def seal_export(snapshot: SealInput, *, strict: bool = True) -> SealResult:
    """Evaluate a seal snapshot; quality issues are errors only when ``strict``."""
    errors: list[str] = []
    warnings: list[str] = []
    current = {file.path: file.sha256 for file in snapshot.files}
    expected = snapshot.existing_checksums

    if not expected:
        errors.append("No valid CHECKSUMS.sha256 baseline - cannot verify integrity")

    missing_critical = REQUIRED_METADATA - current.keys()
    if missing_critical:
        errors.append(f"Required metadata missing: {sorted(missing_critical)}")

    errors.extend(snapshot.evidence_errors)

    for issue in snapshot.quality_issues:
        detail = f"{issue.get('path', '<unknown>')} - {issue.get('reason', 'unknown')}"
        if strict:
            errors.append(f"Quality issue: {detail}")
        else:
            warnings.append(f"Quality warning: {detail}")

    errors.extend(
        f"File modified: {path}"
        for path, digest in current.items()
        if path in expected and digest != expected[path]
    )
    errors.extend(f"File removed: {path}" for path in expected if path not in current)
    if expected:
        errors.extend(f"File added: {path}" for path in current if path not in expected)

    return SealResult(not errors, len(snapshot.files), errors, warnings)
