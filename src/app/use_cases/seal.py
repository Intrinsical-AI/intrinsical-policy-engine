# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Seal use case: filesystem scanning + pure domain sealing."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, cast

from src.adapters.quality.engine import QualityEngine
from src.adapters.security.gpg_signer import GpgSigner
from src.app.config.constants import METADATA_DIR
from src.domain.ports import QualityPort
from src.domain.services.seal_service import SealFileSnapshot, SealInput, SealResult, seal_export


def _compute_file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_existing_checksums(export_dir: Path) -> dict[str, str]:
    """Load existing CHECKSUMS.sha256 from export directory."""
    checksums_path = export_dir / "CHECKSUMS.sha256"
    if not checksums_path.exists():
        return {}

    result = {}
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            file_hash, file_path = parts
            result[file_path.lstrip("* ")] = file_hash
    return result


def _load_fingerprint(export_dir: Path) -> dict[str, Any]:
    """Load fingerprint.json if present."""
    fp_path = export_dir / "fingerprint.json"
    if fp_path.exists():
        return cast(dict[str, Any], json.loads(fp_path.read_text(encoding="utf-8")))
    return {}


def _collect_export_files(export_dir: Path, include_metadata: bool = True) -> list[Path]:
    """Collect all files in export directory."""
    files = []
    excluded_files = {
        "CHECKSUMS.sha256",
        "manifest_sealed.json",
        "seal_report.json",
    }
    for p in export_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(export_dir).as_posix()
            basename = p.name
            if basename in excluded_files:
                continue
            if rel.startswith("exports/") or rel.startswith(METADATA_DIR + "/logs/"):
                continue
            if include_metadata or not rel.startswith(METADATA_DIR + "/"):
                files.append(p)
    return sorted(files)


def _collect_metadata_files(export_dir: Path) -> list[Path]:
    """Collect only _metadata/ files for critical integrity checks."""
    metadata_dir = export_dir / METADATA_DIR
    if not metadata_dir.exists():
        return []
    return sorted(p for p in metadata_dir.rglob("*") if p.is_file())


def collect_seal_input(
    export_dir: Path,
    *,
    evidence_dir: Path | None = None,
    quality_engine: QualityPort,
) -> SealInput:
    """Scan filesystem state and build the pure domain seal input."""
    if not export_dir.exists() or not export_dir.is_dir():
        return SealInput(
            export_root=str(export_dir),
            export_exists=False,
            existing_checksums={},
            fingerprint={},
            files=(),
            metadata_files=frozenset(),
            quality_issues=(),
        )

    existing_checksums = _load_existing_checksums(export_dir)
    fingerprint = _load_fingerprint(export_dir)
    export_files = _collect_export_files(export_dir, include_metadata=True)
    metadata_files = frozenset(p.name for p in _collect_metadata_files(export_dir))

    quality_issues: list[dict[str, Any]] = []
    file_snapshots: list[SealFileSnapshot] = []

    for fp in export_files:
        rel_path = fp.relative_to(export_dir).as_posix()
        file_snapshots.append(SealFileSnapshot(path=rel_path, sha256=_compute_file_hash(fp)))
        if fp.suffix in (".md", ".json", ".yml", ".yaml", ".csv"):
            is_valid, reason = quality_engine.diagnose_file(fp)
            if not is_valid:
                quality_issues.append({"path": rel_path, "reason": reason})

    if evidence_dir and evidence_dir.exists():
        for fp in evidence_dir.rglob("*"):
            if fp.is_file():
                is_valid, reason = quality_engine.diagnose_file(fp)
                if not is_valid:
                    quality_issues.append(
                        {
                            "path": f"evidence:{fp.relative_to(evidence_dir).as_posix()}",
                            "reason": reason,
                        }
                    )

    return SealInput(
        export_root=str(export_dir),
        export_exists=True,
        existing_checksums=existing_checksums,
        fingerprint=fingerprint,
        files=tuple(file_snapshots),
        metadata_files=metadata_files,
        quality_issues=tuple(quality_issues),
    )


def seal_and_package(
    export_dir: Path,
    output_zip: Path | None = None,
    sign: bool = True,
    strict: bool = True,
    evidence_dir: Path | None = None,
) -> SealResult:
    """Seal an export and optionally package it into a ZIP."""
    quality_engine = QualityEngine()
    result = seal_export(
        collect_seal_input(
            export_dir,
            evidence_dir=evidence_dir,
            quality_engine=quality_engine,
        ),
        strict=strict,
    )

    metadata_dir = export_dir / METADATA_DIR
    metadata_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = metadata_dir / "manifest_sealed.json"
    report_path = metadata_dir / "seal_report.json"
    _write_json(manifest_path, result.manifest_sealed)
    _write_json(report_path, result.seal_report.to_dict())

    checksums_path = metadata_dir / "CHECKSUMS.sha256"
    checksums_data = result.manifest_sealed.get("checksums", {})
    if checksums_data:
        lines = [f"{file_hash}  {path}" for path, file_hash in sorted(checksums_data.items())]
        checksums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if sign and result.success:
        signer = GpgSigner()
        if signer.is_available() and signer.has_secret_key():
            signature_path = signer.sign_file(manifest_path)
            if signature_path is None:
                result.warnings.append("GPG signing failed - manifest not signed")
        else:
            result.warnings.append("GPG not available or no secret key - manifest not signed")

    if output_zip and result.success:
        _create_bundle_zip(export_dir, output_zip)

    return result


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON data to file with consistent formatting."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _create_bundle_zip(export_dir: Path, output_zip: Path) -> Path:
    """Create a ZIP file containing the entire export."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(export_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(export_dir).as_posix()
                zf.write(file_path, arcname=arcname)
    return output_zip
