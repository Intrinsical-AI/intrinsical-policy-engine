# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Verify a closed export, optionally sign its baseline and package exact bytes."""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.adapters.security.gpg_signer import GpgSigner, SigningError
from intrinsical_policy_engine.common.document_io import parse_document
from intrinsical_policy_engine.common.io_safety import acquire_lock
from intrinsical_policy_engine.common.output_safety import (
    output_lock_path,
    publish_file_no_replace,
    require_new_output,
)
from intrinsical_policy_engine.common.package_integrity import (
    load_checksums,
    open_regular,
    read_regular,
    scan_payload,
    verify_payload,
)
from intrinsical_policy_engine.domain.ports import QualityPort
from intrinsical_policy_engine.domain.services.evidence_requirements import (
    missing_evidence_requirements,
)
from intrinsical_policy_engine.domain.services.seal_service import (
    SealFileSnapshot,
    SealInput,
    SealReport,
    SealResult,
    seal_export,
)
from intrinsical_policy_engine.domain.snapshot_contract import (
    CHECKSUMS_NAME,
    MAX_SNAPSHOT_METADATA_DEPTH,
    MAX_SNAPSHOT_METADATA_NODES,
    REQUIRED_METADATA,
    SIGNATURE_NAME,
    PackageIntegrityError,
    canonical_path,
    validate_snapshot_metadata,
)


def _metadata_document(path: Path, relative: str) -> Any:
    try:
        return parse_document(
            read_regular(path).decode("utf-8"),
            ".json",
            max_depth=MAX_SNAPSHOT_METADATA_DEPTH,
            max_nodes=MAX_SNAPSHOT_METADATA_NODES,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise PackageIntegrityError(f"Invalid metadata: {relative}: {exc}") from exc


def _validate_evidence_manifest(value: Any) -> None:
    if not isinstance(value, dict):
        raise PackageIntegrityError("Invalid evidence manifest structure")
    for field in ("included", "missing"):
        items = value.get(field)
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise PackageIntegrityError(f"Invalid evidence manifest {field}")
    if len(value["included"]) != len(set(value["included"])):
        raise PackageIntegrityError("Duplicate evidence manifest paths")
    for name in value["included"]:
        canonical_path(name)


def _validate_metadata(relative: str, value: Any) -> None:
    if relative in REQUIRED_METADATA and not isinstance(value, dict):
        raise PackageIntegrityError(f"Required metadata is not an object: {relative}")
    if relative == "_metadata/actions.json" and (
        not isinstance(value, list) or not all(isinstance(item, dict) for item in value)
    ):
        raise PackageIntegrityError("Action metadata must be a list of objects")
    if relative == "_metadata/evidence_manifest.json":
        _validate_evidence_manifest(value)
    if relative == "_metadata/evidence_quality.json" and (
        not isinstance(value, dict)
        or not isinstance(value.get("quality_by_file"), dict)
        or not isinstance(value.get("problems"), list)
    ):
        raise PackageIntegrityError("Invalid evidence quality metadata structure")


def _evidence_consistency(metadata: dict[str, Any], names: set[str]) -> list[str]:
    summary = metadata.get("_metadata/summary.json", {})
    errors: list[str] = []
    actions = metadata.get("_metadata/actions.json")
    if actions is not None and "actions_meta" in summary and actions != summary["actions_meta"]:
        errors.append("Action metadata differs from assessment summary")
    manifest = metadata.get("_metadata/evidence_manifest.json")
    if manifest is not None:
        if set(manifest["included"]) != names:
            errors.append("Evidence manifest included paths differ from actual evidence files")
        errors.extend(
            f"Evidence manifest reports missing evidence: {item}" for item in manifest["missing"]
        )
    elif any(key in summary for key in ("actions_evidence_map", "articles_evidence_map")):
        errors.append("Evidence manifest missing for declared evidence requirements")
    quality = metadata.get("_metadata/evidence_quality.json")
    if quality is not None and set(quality["quality_by_file"]) != names:
        errors.append("Evidence quality paths differ from actual evidence files")
    try:
        errors.extend(missing_evidence_requirements(summary, names))
    except ValueError as exc:
        errors.append(f"Invalid evidence requirements: {exc}")
    return list(dict.fromkeys(errors))


def _snapshot(export_dir: Path, expected: dict[str, str], quality_engine: QualityPort) -> SealInput:
    actual = scan_payload(export_dir)
    issues = []
    metadata: dict[str, Any] = {}
    evidence_names: set[str] = set()
    for relative in actual:
        path = export_dir / relative
        if relative.startswith("_metadata/") and path.suffix.lower() == ".json":
            value = _metadata_document(path, relative)
            _validate_metadata(relative, value)
            metadata[relative] = value
        if relative.startswith("evidence/"):
            evidence_names.add(relative.removeprefix("evidence/"))
            try:
                valid, reason = quality_engine.diagnose_file(path)
            except ValueError as exc:
                raise PackageIntegrityError(f"Invalid evidence: {relative}: {exc}") from exc
            if not valid:
                issues.append({"path": relative, "reason": reason})
    validate_snapshot_metadata(
        metadata.get("_metadata/summary.json"), metadata.get("_metadata/trace.json")
    )
    return SealInput(
        export_root=str(export_dir),
        export_exists=True,
        existing_checksums=expected,
        files=tuple(SealFileSnapshot(rel, digest) for rel, digest in actual.items()),
        quality_issues=tuple(issues),
        evidence_errors=tuple(_evidence_consistency(metadata, evidence_names)),
    )


def _failed(message: str, result: SealResult | None = None) -> SealResult:
    if result is None:
        errors = [message]
        return SealResult(
            success=False,
            seal_report=SealReport("failed", datetime.now(UTC).isoformat(), 0, errors=errors),
            errors=errors,
        )
    result.success = False
    result.errors.append(message)
    if result.seal_report.errors is not result.errors:
        result.seal_report.errors.append(message)
    result.seal_report.status = "failed"
    return result


def _check_source_controls(root: Path, baseline: bytes, old_signature: bytes | None) -> None:
    if read_regular(root / CHECKSUMS_NAME) != baseline:
        raise PackageIntegrityError("CHECKSUMS changed during sealing")
    signature = root / SIGNATURE_NAME
    if old_signature is None:
        if signature.exists() or signature.is_symlink():
            raise PackageIntegrityError("A signature appeared during sealing")
    elif read_regular(signature) != old_signature:
        raise PackageIntegrityError("Signature changed during sealing")


def _prepare_zip(
    root: Path,
    prepared: Path,
    expected: dict[str, str],
    baseline: bytes,
    signature: bytes | None,
) -> None:
    """Copy and hash the same stream, rejecting bytes that differ from baseline."""
    with zipfile.ZipFile(prepared, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel, expected_hash in expected.items():
            digest = hashlib.sha256()
            with open_regular(root / rel) as source, archive.open(rel, "w") as output:
                for chunk in iter(lambda: source.read(65536), b""):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected_hash:
                raise PackageIntegrityError(f"File changed during packaging: {rel}")
        archive.writestr(CHECKSUMS_NAME, baseline)
        if signature is not None:
            archive.writestr(SIGNATURE_NAME, signature)
    with prepared.open("rb") as stream:
        os.fsync(stream.fileno())


def seal_and_package(
    export_dir: Path,
    output_zip: Path | None = None,
    sign: bool = True,
    strict: bool = True,
) -> SealResult:
    """Validate all drift regardless of quality strictness; never rebase an export.

    A signature and an external ZIP are published individually. A publication
    failure can leave a complete valid signature without a ZIP, and is reported
    as failure. The cooperative lock does not prevent unrelated writers.
    """
    result: SealResult | None = None
    prepared_zip: Path | None = None
    try:
        if export_dir.is_symlink() or not export_dir.is_dir():
            raise PackageIntegrityError(f"Export directory not found or unsafe: {export_dir}")
        root = export_dir.resolve()
        zip_target = None
        if output_zip is not None:
            if output_zip.is_symlink():
                raise PackageIntegrityError("ZIP output is a symbolic link")
            zip_target = output_zip.resolve()
            if zip_target.is_relative_to(root):
                raise PackageIntegrityError("ZIP output must be outside the export directory")
            require_new_output(zip_target)
            zip_target.parent.mkdir(parents=True, exist_ok=True)

        with acquire_lock(output_lock_path(root)):
            baseline, expected = load_checksums(root)
            result = seal_export(_snapshot(root, expected, QualityEngine()), strict=strict)
            if not result.success:
                return result
            signature_path = root / SIGNATURE_NAME
            old_signature = (
                read_regular(signature_path)
                if signature_path.exists() or signature_path.is_symlink()
                else None
            )
            with tempfile.TemporaryDirectory(prefix=".ipe-seal-", dir=root.parent) as directory:
                staging = Path(directory)
                staged_baseline = staging / CHECKSUMS_NAME
                staged_baseline.write_bytes(baseline)
                staged_signature = staging / SIGNATURE_NAME
                signature_bytes = old_signature
                signer = GpgSigner()
                if old_signature is not None:
                    staged_signature.write_bytes(old_signature)
                    result.signature_fingerprint = signer.verify_file(
                        staged_baseline, staged_signature
                    )
                    result.signature_path = str(signature_path)
                elif sign:
                    signed = signer.sign_file(staged_baseline)
                    if signed != staged_signature:
                        raise SigningError("GPG did not create the expected signature")
                    signature_bytes = read_regular(staged_signature)
                    result.signature_fingerprint = signer.verify_file(
                        staged_baseline, staged_signature
                    )

                if zip_target is not None:
                    descriptor, name = tempfile.mkstemp(prefix=".ipe-zip-", dir=zip_target.parent)
                    os.close(descriptor)
                    prepared_zip = Path(name)
                    _prepare_zip(root, prepared_zip, expected, baseline, signature_bytes)

                verify_payload(root, expected)
                _check_source_controls(root, baseline, old_signature)
                if old_signature is None and signature_bytes is not None:
                    with staged_signature.open("rb") as stream:
                        os.fsync(stream.fileno())
                    publish_file_no_replace(staged_signature, signature_path)
                    result.signature_path = str(signature_path)
                if zip_target is not None and prepared_zip is not None:
                    publish_file_no_replace(prepared_zip, zip_target)
                    prepared_zip = None
            return result
    except (OSError, PackageIntegrityError, SigningError, zipfile.BadZipFile) as exc:
        return _failed(str(exc), result)
    finally:
        if prepared_zip is not None:
            prepared_zip.unlink(missing_ok=True)
