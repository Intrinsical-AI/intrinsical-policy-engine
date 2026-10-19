# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure domain logic for sealing compliance exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from intrinsical_policy_engine.domain.snapshot_contract import REQUIRED_METADATA


@dataclass(frozen=True)
class SealFileSnapshot:
    """Immutable snapshot of one exported file."""

    path: str
    sha256: str


@dataclass(frozen=True)
class SealInput:
    """Pure input required to evaluate seal integrity."""

    export_root: str
    export_exists: bool
    existing_checksums: dict[str, str]
    files: tuple[SealFileSnapshot, ...]
    quality_issues: tuple[dict[str, Any], ...] = ()
    evidence_errors: tuple[str, ...] = ()


@dataclass
class FileDiff:
    """Represents a change detected during seal diffing."""

    path: str
    status: Literal["modified", "added", "removed"]
    expected_hash: str | None = None
    actual_hash: str | None = None


@dataclass
class SealReport:
    """Report of the sealing process."""

    status: Literal["success", "failed", "warnings"]
    timestamp: str
    files_validated: int
    files_modified: list[FileDiff] = field(default_factory=list)
    files_added: list[str] = field(default_factory=list)
    files_removed: list[str] = field(default_factory=list)
    quality_issues: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "status": self.status,
            "timestamp": self.timestamp,
            "files_validated": self.files_validated,
            "files_modified": [
                {
                    "path": d.path,
                    "status": d.status,
                    "expected_hash": d.expected_hash,
                    "actual_hash": d.actual_hash,
                }
                for d in self.files_modified
            ],
            "files_added": self.files_added,
            "files_removed": self.files_removed,
            "quality_issues": self.quality_issues,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class SealResult:
    """Result of the sealing operation."""

    success: bool
    seal_report: SealReport
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    signature_path: str | None = None
    signature_fingerprint: str | None = None


def seal_export(snapshot: SealInput, *, strict: bool = True) -> SealResult:
    """Evaluate a seal snapshot and return the pure sealing result."""
    errors: list[str] = []
    warnings: list[str] = []
    timestamp = datetime.now(UTC).isoformat()

    if not snapshot.export_exists:
        error = f"Export directory not found: {snapshot.export_root}"
        return SealResult(
            success=False,
            seal_report=SealReport(
                status="failed",
                timestamp=timestamp,
                files_validated=0,
                errors=[error],
            ),
            errors=[error],
        )

    current_checksums = {file.path: file.sha256 for file in snapshot.files}

    if not snapshot.existing_checksums:
        errors.append("No valid CHECKSUMS.sha256 baseline - cannot verify integrity")

    missing_critical = REQUIRED_METADATA - current_checksums.keys()
    if missing_critical:
        errors.append(f"Required metadata missing: {sorted(missing_critical)}")

    errors.extend(snapshot.evidence_errors)

    quality_issues = [dict(issue) for issue in snapshot.quality_issues]
    for issue in quality_issues:
        path = issue.get("path", "<unknown>")
        reason = issue.get("reason", "unknown")
        if strict:
            errors.append(f"Quality issue: {path} - {reason}")
        else:
            warnings.append(f"Quality warning: {path} - {reason}")

    modified_files: list[FileDiff] = []
    for rel_path, current_hash in current_checksums.items():
        if rel_path in snapshot.existing_checksums:
            expected_hash = snapshot.existing_checksums[rel_path]
            if current_hash != expected_hash:
                modified_files.append(
                    FileDiff(
                        path=rel_path,
                        status="modified",
                        expected_hash=expected_hash,
                        actual_hash=current_hash,
                    )
                )
                errors.append(f"File modified: {rel_path}")

    removed_files: list[str] = []
    for rel_path in snapshot.existing_checksums:
        if rel_path not in current_checksums:
            removed_files.append(rel_path)
            errors.append(f"File removed: {rel_path}")

    added_files = [
        rel_path
        for rel_path in current_checksums
        if rel_path not in snapshot.existing_checksums and snapshot.existing_checksums
    ]
    errors.extend(f"File added: {rel_path}" for rel_path in added_files)

    success = len(errors) == 0
    status: Literal["success", "failed", "warnings"] = (
        "failed" if not success else ("warnings" if warnings else "success")
    )

    seal_report = SealReport(
        status=status,
        timestamp=timestamp,
        files_validated=len(snapshot.files),
        files_modified=modified_files,
        files_added=added_files,
        files_removed=removed_files,
        quality_issues=quality_issues,
        errors=errors,
        warnings=warnings,
    )

    return SealResult(
        success=success,
        seal_report=seal_report,
        errors=errors,
        warnings=warnings,
    )
