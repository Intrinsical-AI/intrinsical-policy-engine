# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Deterministic hashing utilities for reproducibility (INV-05).

This module provides public helpers for computing SHA-256 hashes of files
and directories in a deterministic manner for snapshot reproducibility.

Per docs/invariants/FRAMEWORKS ARCHITECTURE v1.md: templates_hash and bundle_hash
are used to ensure that snapshots can be verified against the exact inputs
that produced them.

SENTINEL VALUES:
- EMPTY_HASH: SHA-256 of empty string. Used when computing hash of empty directory.
- "ABSENT": Literal string used by *_or_absent functions to indicate missing resource.

WHEN TO USE EACH:
- sha256_directory(): For computing hashes where missing = error or empty content.
  Returns EMPTY_HASH if directory doesn't exist (warns by default).
- sha256_directory_or_absent(): For computing component hashes in framework pack.
  Returns "ABSENT" string to clearly distinguish "missing" from "empty".

The "ABSENT" sentinel is intentional in framework_pack_hash computation to make
the absence of optional components part of the hash identity. This ensures that
adding/removing optional directories changes the pack hash.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml

from src.domain.framework_layout import FrameworkLayout

logger = logging.getLogger(__name__)

# Hash of empty content (SHA-256 of empty string)
EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of a single file.

    Reads the file in chunks for memory efficiency and computes a deterministic
    SHA-256 hash. Returns EMPTY_HASH if the file cannot be read.

    Args:
        path: Path to the file to hash.

    Returns:
        Hex digest (64-character hex string) of the file's SHA-256 hash, or
        EMPTY_HASH if file cannot be read (logged as warning).

    Note:
        Per docs/invariants/ENGINE-ARCHITECTURE-v1.md (INV-05), file hashes
        are used for reproducibility and snapshot verification.
    """
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        logger.warning("hashing.file.error", extra={"path": str(path), "error": str(e)})
        return EMPTY_HASH


def sha256_directory(
    path: Path, *, warn_if_missing: bool = True, raise_if_missing: bool = False
) -> str:
    """Compute SHA-256 hash of a directory's contents (sorted file hashes).

    Hashes relative paths (not just filenames) to ensure files in different
    subdirectories with the same name produce different hashes.

    The hash is deterministic: same directory contents always produce the
    same hash regardless of filesystem traversal order.

    Args:
        path: Path to the directory to hash.
        warn_if_missing: If True, log a warning when directory doesn't exist.
        raise_if_missing: If True, raise FileNotFoundError when directory doesn't exist.

    Returns:
        Hex digest of the combined SHA-256 hash of all files,
        or hash of empty string if directory doesn't exist/is empty.

    Note:
        Per P3.2 (TECH_DEBT), this function logs a warning if the directory
        doesn't exist, to help detect configuration errors early.
    """
    h = hashlib.sha256()

    if not path.exists():
        if raise_if_missing:
            raise FileNotFoundError(f"Directory not found: {path}")
        if warn_if_missing:
            logger.warning(
                "hashing.directory.not_found",
                extra={"path": str(path), "result": "empty_hash"},
            )
        return h.hexdigest()

    if not path.is_dir():
        if raise_if_missing:
            raise NotADirectoryError(f"Not a directory: {path}")
        if warn_if_missing:
            logger.warning(
                "hashing.directory.not_a_directory",
                extra={"path": str(path), "result": "empty_hash"},
            )
        return h.hexdigest()

    # Sort files for determinism, hash relative path to avoid collisions
    file_count = 0
    for fp in sorted(path.rglob("*")):
        if fp.is_file():
            rel_path = str(fp.relative_to(path))
            h.update(rel_path.encode("utf-8"))
            h.update(sha256_file(fp).encode("utf-8"))
            file_count += 1

    if file_count == 0 and warn_if_missing:
        logger.warning(
            "hashing.directory.empty",
            extra={"path": str(path), "result": "empty_hash"},
        )

    return h.hexdigest()


# Convenience alias for backwards compatibility
compute_templates_hash = sha256_directory


def sha256_file_or_absent(path: Path) -> str:
    """Return SHA-256 for a file or the sentinel 'ABSENT' if missing.

    Args:
        path: Path to the file to hash.

    Returns:
        SHA-256 hex digest if file exists, or the string 'ABSENT' if missing.
    """
    if not path.exists() or not path.is_file():
        return "ABSENT"
    return sha256_file(path)


def sha256_directory_or_absent(path: Path) -> str:
    """Return SHA-256 for a directory or the sentinel 'ABSENT' if missing.

    Use this function when:
    - Computing framework pack hashes where components may be optional
    - You need to distinguish "missing" from "empty" in the hash identity

    DO NOT use this when:
    - You expect the directory to exist (use sha256_directory with raise_if_missing)
    - "Missing" should be treated as "empty content"

    Args:
        path: Path to the directory to hash.

    Returns:
        SHA-256 hex digest if directory exists, or the literal string 'ABSENT' if missing.
        Does not log warnings (unlike sha256_directory).

    Note:
        The 'ABSENT' sentinel is part of the hash identity. This means:
        - Directory A missing + Directory B present → hash X
        - Directory A present + Directory B present → hash Y (different from X)
    """
    if not path.exists() or not path.is_dir():
        return "ABSENT"
    return sha256_directory(path, warn_if_missing=False)


def compute_framework_pack_hashes(
    layout: FrameworkLayout, *, law_data_hash: str | None = None
) -> dict[str, Any]:
    """Compute framework pack hashes per docs/invariants/FRAMEWORKS ARCHITECTURE v1.md.

    Computes deterministic hashes for all components of a framework pack,
    including render templates, evidence templates, bundle profiles, schemas, and
    metadata files. The composed framework_pack_hash ensures reproducibility.

    Args:
        layout: Resolved ``FrameworkLayout`` value object for the pack.
        law_data_hash: Optional pre-computed hash of law data. If None, uses 'ABSENT'.

    Returns:
        Dictionary containing:
            - framework_id: Framework identifier from FRAMEWORK_VERSION.yml
            - framework_version: Framework version string
            - law_data_hash: Hash of law data (or 'ABSENT')
            - render_templates_hash: Hash of the render directory
            - evidence_templates_hash: Hash of the evidence template root
            - bundle_profiles_hash: Hash of bundle profiles (from manifest.yml)
            - schemas_hash: Hash of the schema directory
            - framework_version_file_hash: Hash of FRAMEWORK_VERSION.yml
            - manifest_file_hash: Hash of manifest.yml
            - framework_pack_hash: Composed hash of all components

    Note:
        Per docs/invariants/FRAMEWORKS ARCHITECTURE v1.md, these hashes enable
        snapshot reproducibility and drift detection.
    """
    resolved_framework_dir = layout.framework_dir
    framework_version_path = layout.framework_version_path
    manifest_path = layout.manifest_path

    framework_id = "unknown"
    framework_version = "unknown"
    if framework_version_path.exists():
        try:
            data = yaml.safe_load(framework_version_path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                fw = data.get("framework")
                if isinstance(fw, dict):
                    framework_id = str(fw.get("id") or framework_id)
                    framework_version = str(fw.get("version") or framework_version)
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
            pass

    bundle_profile_files = layout.bundle_profile_files
    if bundle_profile_files:
        h = hashlib.sha256()
        for fp in sorted(bundle_profile_files, key=lambda p: p.as_posix()):
            rel_path = fp.relative_to(resolved_framework_dir).as_posix()
            h.update(rel_path.encode("utf-8"))
            if fp.exists() and fp.is_file():
                h.update(sha256_file(fp).encode("utf-8"))
            else:
                h.update(b"ABSENT")
        bundle_profiles_hash = h.hexdigest()
    else:
        bundle_profiles_hash = "ABSENT"

    law_hash = law_data_hash or "ABSENT"
    render_templates_hash = sha256_directory_or_absent(layout.templates_dir)
    evidence_templates_hash = sha256_directory_or_absent(layout.evidence_templates_dir)
    schemas_hash = sha256_directory_or_absent(layout.schemas_dir)
    manifest_file_hash = sha256_file_or_absent(manifest_path)
    framework_version_file_hash = sha256_file_or_absent(framework_version_path)

    digest_parts = [
        f"framework_id:{framework_id}",
        f"framework_version:{framework_version}",
        f"law_data_hash:{law_hash}",
        f"render_templates_hash:{render_templates_hash}",
        f"evidence_templates_hash:{evidence_templates_hash}",
        f"bundle_profiles_hash:{bundle_profiles_hash}",
        f"schemas_hash:{schemas_hash}",
        f"framework_version_file_hash:{framework_version_file_hash}",
        f"manifest_file_hash:{manifest_file_hash}",
    ]
    framework_pack_hash = hashlib.sha256("\n".join(digest_parts).encode("utf-8")).hexdigest()

    return {
        "framework_id": framework_id,
        "framework_version": framework_version,
        "law_data_hash": law_hash,
        "render_templates_hash": render_templates_hash,
        "evidence_templates_hash": evidence_templates_hash,
        "bundle_profiles_hash": bundle_profiles_hash,
        "schemas_hash": schemas_hash,
        "framework_version_file_hash": framework_version_file_hash,
        "manifest_file_hash": manifest_file_hash,
        "framework_pack_hash": framework_pack_hash,
    }
