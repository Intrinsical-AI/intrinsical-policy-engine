# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Library-first release gate for public bundle verification."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# =============================================================================
# CONFIG: Spec v1 Invariants
# =============================================================================

MANDATORY_FILES = [
    # Porcelain
    "README.md",
    "01_EXECUTIVE_SUMMARY.md",
    "02_SCOPE_AND_CLASSIFICATION.md",
    "03_GAP_REGISTER.md",
    "04_ACTION_BACKLOG.csv",
    "05_EVIDENCE_CHECKLIST.csv",
    "06_EVIDENCE_MAP.md",
    "07_LIMITATIONS_AND_DISCLAIMERS.md",
    "08_OMISSIONS_REPORT.md",
    "attachments_stub/README.md",
]

# G1: PII Patterns
PII_REGEX = [
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "Email Address"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "US SSN-like"),
    (r"\b(ES)?[0-9]{8}[A-Z]\b", "Spanish DNI-like"),
]

ALLOWLIST = [
    "support@example.com",
    "contact@example.com",
    "compliance@demo-company.example",
    "dpo@demo-company.example",
]

STOP_WORDS = [
    "CONFIDENTIAL",
    "INTERNAL USE ONLY",
    "DO NOT DISTRIBUTE",
]


@dataclass(frozen=True)
class GateResult:
    ok: bool
    errors: list[str]
    errors_by_gate: dict[str, list[str]]
    target_dir: Path
    export_root: Path | None


def _calculate_sha256(path: Path) -> str:
    """Calculate SHA256 of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def gate_g1_safety(target_dir: Path) -> list[str]:
    """G1: Content Safety (PII & Stop Words)."""
    errors = []
    for file in target_dir.rglob("*"):
        if not file.is_file() or file.name == "CHECKSUMS.sha256":
            continue

        # Skip binary-ish files if needed (Spec says PDFs act as derivatives, scan metadata only?)
        # For v1 apply to all text-like
        if file.suffix not in [".md", ".json", ".csv", ".yml", ".txt", ".html"]:
            continue

        try:
            content = file.read_text(encoding="utf-8", errors="ignore")

            # 1. PII
            for pattern, name in PII_REGEX:
                matches = re.finditer(pattern, content)
                for m in matches:
                    text = m.group(0)
                    if text not in ALLOWLIST:
                        errors.append(
                            f"Use of PII ({name}): '{text}' in {file.relative_to(target_dir)}"
                        )

            # 2. Stop Words
            for word in STOP_WORDS:
                if word in content:
                    errors.append(f"Found STOP WORD: '{word}' in {file.relative_to(target_dir)}")

        except (OSError, UnicodeDecodeError):
            errors.append(f"Could not read {file.relative_to(target_dir)}")

    return errors


def gate_g2_structure(target_dir: Path) -> list[str]:
    """G2: Structural Integrity (Mandatory Files)."""
    errors = []
    for rel_path in MANDATORY_FILES:
        f = target_dir / rel_path
        if not f.exists():
            errors.append(f"MISSING mandatory file: {rel_path}")

    # Specific check for Omissions Report content
    omissions = target_dir / "08_OMISSIONS_REPORT.md"
    if omissions.exists():
        content = omissions.read_text(encoding="utf-8", errors="ignore")
        if (
            "OmittedBy" not in content
            and "Omitted By" not in content
            and "No omissions" not in content
        ):
            errors.append("INVALID 08_OMISSIONS_REPORT.md: Must contain omission details.")

    return errors


def gate_g3_integrity(export_root: Path) -> list[str]:
    """G3: Cryptographic Integrity (CHECKSUMS.sha256)."""
    errors = []

    checksums_file = export_root / "CHECKSUMS.sha256"
    if not checksums_file.exists():
        return ["SKIPPED G3: CHECKSUMS.sha256 missing."]

    # Parse expected checksums
    expected: dict[str, str] = {}
    try:
        lines = checksums_file.read_text(encoding="utf-8").strip().splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                errors.append(f"Malformed line {i} in CHECKSUMS.sha256")
                continue
            sha, fname = parts
            expected[fname.strip()] = sha.strip()
    except (OSError, UnicodeDecodeError, ValueError) as exc:  # noqa: BLE001
        return [f"CRITICAL: Failed to parse CHECKSUMS.sha256: {exc}"]

    # Verify files on disk match expected
    all_files_on_disk = {
        str(f.relative_to(export_root)) for f in export_root.rglob("*") if f.is_file()
    }

    # Exclude checksum file itself, seal artifacts, and non-deterministic exports
    all_files_on_disk.discard("CHECKSUMS.sha256")
    all_files_on_disk.discard("_metadata/manifest_sealed.json")
    all_files_on_disk.discard("_metadata/seal_report.json")
    all_files_on_disk.discard("fingerprint.json")
    for f in list(all_files_on_disk):
        if f.startswith("exports/"):
            all_files_on_disk.discard(f)

    expected_set = set(expected.keys())

    extras = all_files_on_disk - expected_set
    for e in extras:
        errors.append(f"UNVERIFIED file (extra): {e}")

    missing = expected_set - all_files_on_disk
    for m in missing:
        errors.append(f"MISSING file (listed in checksums): {m}")

    # Validate hashes
    for fname, expected_sha in expected.items():
        fpath = export_root / fname
        if fpath.exists():
            actual_sha = _calculate_sha256(fpath)
            if actual_sha != expected_sha:
                errors.append(
                    f"INTEGRITY FAIL: {fname} (Expected {expected_sha[:8]}, got {actual_sha[:8]})"
                )

    return errors


def _resolve_export_root(target_dir: Path) -> Path | None:
    """Infer export root by walking parents for CHECKSUMS.sha256."""
    for parent in [target_dir] + list(target_dir.parents):
        if (parent / "CHECKSUMS.sha256").exists():
            return parent
    return None


def verify_public_bundle(
    target_dir: Path,
    *,
    export_root: Path | None = None,
    fail_fast: bool = False,
) -> GateResult:
    """Run G1/G2/G3 gate checks and return aggregated result."""
    errors_by_gate: dict[str, list[str]] = {}
    errors: list[str] = []

    if not target_dir.exists():
        errors_by_gate["G0"] = [f"Target directory does not exist: {target_dir}"]
        return GateResult(False, errors_by_gate["G0"], errors_by_gate, target_dir, export_root)

    resolved_export_root = export_root or _resolve_export_root(target_dir)

    # G1
    g1 = gate_g1_safety(target_dir)
    if g1:
        errors_by_gate["G1"] = g1
        errors.extend(g1)
        if fail_fast:
            return GateResult(False, errors, errors_by_gate, target_dir, resolved_export_root)

    # G2
    g2 = gate_g2_structure(target_dir)
    if g2:
        errors_by_gate["G2"] = g2
        errors.extend(g2)
        if fail_fast:
            return GateResult(False, errors, errors_by_gate, target_dir, resolved_export_root)

    # G3
    if resolved_export_root is not None:
        g3 = gate_g3_integrity(resolved_export_root)
        if g3:
            errors_by_gate["G3"] = g3
            errors.extend(g3)

    return GateResult(not errors, errors, errors_by_gate, target_dir, resolved_export_root)
