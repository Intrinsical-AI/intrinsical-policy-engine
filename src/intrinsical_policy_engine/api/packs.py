# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Framework-pack resolution boundary for the public facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout
from intrinsical_policy_engine.api.errors import (
    PackCompatibilityError,
    PackCompatibilityMetadataError,
    PackLicenseMetadataError,
)
from intrinsical_policy_engine.api.models import PackDescriptor, PackReference
from intrinsical_policy_engine.common.constants import (
    CANONICAL_ENGINE_VERSION,
)


@runtime_checkable
class PackProvider(Protocol):
    """Resolve an external pack reference without exposing loaded contracts.

    Custom providers may map registry IDs or application-specific aliases to a
    local, immutable pack root. Loading and validation remain engine-owned.
    """

    def resolve(self, reference: PackReference) -> PackDescriptor:
        """Resolve ``reference`` to a local framework-pack descriptor."""
        ...


class FilesystemPackProvider:
    """Resolve canonical framework packs already present on disk."""

    def resolve(self, reference: PackReference) -> PackDescriptor:
        """Validate a pack layout and read its declared public identity."""
        root = Path(reference).expanduser().resolve()
        layout = load_framework_layout(root)
        version_data = _read_mapping(layout.framework_version_path)
        manifest_data = _read_mapping(layout.manifest_path)

        framework = version_data.get("framework")
        framework_data = framework if isinstance(framework, dict) else {}

        pack_id = _first_string(
            framework_data.get("id"),
            manifest_data.get("framework_id"),
        )
        version = _first_string(
            framework_data.get("version"),
            manifest_data.get("version"),
        )
        if pack_id is None or version is None:
            raise ValueError(f"Framework identity is incomplete in {layout.framework_version_path}")

        raw_format_version = manifest_data.get("pack_format_version")
        format_version = raw_format_version if isinstance(raw_format_version, int) else None
        compatible_engine_versions = _compatibility_ranges(manifest_data, layout.framework_dir)
        descriptor = PackDescriptor(
            id=pack_id,
            version=version,
            root=layout.framework_dir,
            name=_optional_string(framework_data.get("name")),
            status=_optional_string(framework_data.get("status")),
            format_version=format_version,
            compatible_engine_versions=compatible_engine_versions,
            engine_version=installed_engine_version(),
            manifest_timestamp=_optional_string(manifest_data.get("timestamp")),
            license=_optional_string(manifest_data.get("license")),
            license_file=_license_file(manifest_data, layout.framework_dir),
            attribution=_optional_string(manifest_data.get("attribution")),
        )
        validate_pack_compatibility(descriptor)
        validate_pack_license_metadata(descriptor)
        return descriptor


def installed_engine_version() -> str:
    """Return the version of the loaded engine code.

    Compatibility must not depend on whichever duplicate ``.dist-info`` or
    ``.egg-info`` directory happens to win ambient metadata discovery. Build
    contracts separately assert that distribution metadata matches this
    canonical runtime value.
    """
    return CANONICAL_ENGINE_VERSION


def validate_pack_compatibility(
    descriptor: PackDescriptor,
    *,
    engine_version: str | None = None,
) -> None:
    """Raise a typed error unless an engine version matches one declared range.

    Entries in ``compatible_engine_versions`` are alternatives. Comma-separated
    clauses inside one entry form a normal PEP 440 intersection.
    """
    ranges = descriptor.compatible_engine_versions
    if not ranges:
        raise PackCompatibilityMetadataError(
            descriptor.root,
            "manifest.yml must declare a non-empty compatible_engine_versions list",
        )

    # The engine version is runtime-owned metadata. Never trust a value supplied
    # by a custom pack provider when enforcing compatibility.
    selected_version = engine_version or installed_engine_version()
    try:
        parsed_version = Version(selected_version)
    except InvalidVersion as exc:
        raise PackCompatibilityMetadataError(
            descriptor.root,
            f"installed engine version is not PEP 440 compliant: {selected_version!r}",
        ) from exc

    specifiers: list[SpecifierSet] = []
    for declared_range in ranges:
        if not isinstance(declared_range, str) or not declared_range.strip():
            raise PackCompatibilityMetadataError(
                descriptor.root,
                "compatible_engine_versions entries must be non-empty PEP 440 strings",
            )
        normalized_range = declared_range.strip()
        try:
            specifiers.append(SpecifierSet(normalized_range))
        except InvalidSpecifier as exc:
            raise PackCompatibilityMetadataError(
                descriptor.root,
                f"invalid PEP 440 specifier {normalized_range!r}",
            ) from exc

    if any(specifier.contains(parsed_version, prereleases=True) for specifier in specifiers):
        return

    raise PackCompatibilityError(
        pack_root=descriptor.root,
        pack_id=descriptor.id,
        pack_version=descriptor.version,
        engine_version=selected_version,
        compatible_engine_versions=ranges,
    )


def validate_pack_license_metadata(descriptor: PackDescriptor) -> None:
    """Validate a declared license path without allowing escape from the pack."""
    declared = descriptor.license_file
    if declared is None:
        return
    if not declared.strip():
        raise PackLicenseMetadataError(descriptor.root, "license_file must not be empty")

    relative_path = Path(declared)
    if relative_path.is_absolute():
        raise PackLicenseMetadataError(descriptor.root, "license_file must be pack-relative")

    pack_root = descriptor.root.resolve()
    resolved_path = (pack_root / relative_path).resolve()
    if not resolved_path.is_relative_to(pack_root):
        raise PackLicenseMetadataError(descriptor.root, "license_file escapes the pack root")
    if not resolved_path.is_file():
        raise PackLicenseMetadataError(
            descriptor.root,
            f"license_file does not exist or is not a file: {declared}",
        )


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read pack metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Pack metadata must be a mapping: {path}")
    return value


def _compatibility_ranges(manifest: dict[str, Any], pack_root: Path) -> tuple[str, ...]:
    raw_ranges = manifest.get("compatible_engine_versions")
    if not isinstance(raw_ranges, list) or not raw_ranges:
        raise PackCompatibilityMetadataError(
            pack_root,
            "manifest.yml must declare a non-empty compatible_engine_versions list",
        )
    if any(not isinstance(item, str) or not item.strip() for item in raw_ranges):
        raise PackCompatibilityMetadataError(
            pack_root,
            "compatible_engine_versions entries must be non-empty PEP 440 strings",
        )
    return tuple(item.strip() for item in raw_ranges)


def _license_file(manifest: dict[str, Any], pack_root: Path) -> str | None:
    raw_path = manifest.get("license_file")
    if raw_path is None:
        return None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PackLicenseMetadataError(pack_root, "license_file must be a non-empty string")
    return raw_path.strip()


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_string(*values: Any) -> str | None:
    for value in values:
        result = _optional_string(value)
        if result is not None:
            return result
    return None
