# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Typed framework-pack errors exposed by the public embedding API."""

from __future__ import annotations

from pathlib import Path


class PackError(ValueError):
    """Base class for public framework-pack resolution failures."""


class PackMetadataError(PackError):
    """Base class for invalid or incomplete pack metadata."""


class PackCompatibilityMetadataError(PackMetadataError):
    """Raised when engine compatibility metadata is absent or malformed."""

    def __init__(self, pack_root: Path, reason: str) -> None:
        self.pack_root = pack_root
        self.reason = reason
        super().__init__(f"Invalid engine compatibility metadata in {pack_root}: {reason}")


class PackLicenseMetadataError(PackMetadataError):
    """Raised when a declared pack license file is invalid or unavailable."""

    def __init__(self, pack_root: Path, reason: str) -> None:
        self.pack_root = pack_root
        self.reason = reason
        super().__init__(f"Invalid license metadata in {pack_root}: {reason}")


class PackCompatibilityError(PackError):
    """Raised when the installed engine is outside every declared pack range."""

    def __init__(
        self,
        *,
        pack_root: Path,
        pack_id: str,
        pack_version: str,
        engine_version: str,
        compatible_engine_versions: tuple[str, ...],
    ) -> None:
        self.pack_root = pack_root
        self.pack_id = pack_id
        self.pack_version = pack_version
        self.engine_version = engine_version
        self.compatible_engine_versions = compatible_engine_versions
        declared = " or ".join(compatible_engine_versions)
        super().__init__(
            f"Pack '{pack_id}' {pack_version} requires engine {declared}; "
            f"installed engine is {engine_version}"
        )
