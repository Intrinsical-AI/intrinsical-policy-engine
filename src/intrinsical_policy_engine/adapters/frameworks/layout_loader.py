# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Framework pack filesystem loading and resolution.

This adapter owns manifest parsing, root discovery, glob resolution, and
filesystem validation for framework packs. Domain code should consume the
resulting ``FrameworkLayout`` value object instead of reading pack files
directly.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from os import PathLike
from pathlib import Path
from typing import Any

import yaml

from intrinsical_policy_engine.domain.framework_layout import FrameworkLayout


class FrameworkPackSymlinkError(ValueError):
    """A framework pack contains a symbolic link at ``path``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"framework packs must not contain symbolic links: {path}")


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"failed to parse manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"manifest {path} is not a YAML mapping")
    return data


def _require_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"manifest.yml missing required mapping: {key}")
    return value


def _require_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = _as_tuple(data.get(key))
    if not value:
        raise ValueError(f"manifest.yml missing required entries: {key}")
    return value


def _resolve_under_root(root: Path, candidate: Path, *, label: str) -> Path:
    """Resolve a manifest path and reject traversal or symlink escapes."""
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes framework pack root: {candidate}") from exc
    return resolved_candidate


def _reject_pack_symlinks(framework_dir: Path) -> None:
    """Reject links before any pack file can be hashed, loaded or rendered."""
    for candidate in framework_dir.rglob("*"):
        if candidate.is_symlink():
            raise FrameworkPackSymlinkError(candidate)


def _require_dir(framework_dir: Path, configured_rel: Any, *, label: str) -> Path:
    if not isinstance(configured_rel, str) or not configured_rel:
        raise ValueError(f"manifest.yml missing required directory path: {label}")
    configured = _resolve_under_root(
        framework_dir,
        framework_dir / configured_rel,
        label=label,
    )
    if not configured.exists() or not configured.is_dir():
        raise FileNotFoundError(f"{label} not found: {configured}")
    return configured


def _require_file(framework_dir: Path, configured_rel: str | None, *, label: str) -> Path:
    if not configured_rel:
        raise ValueError(f"manifest.yml missing required file path: {label}")
    configured = _resolve_under_root(
        framework_dir,
        framework_dir / configured_rel,
        label=label,
    )
    if not configured.exists() or not configured.is_file():
        raise FileNotFoundError(f"{label} not found: {configured}")
    return configured


def resolve_manifest_entries(
    framework_dir: Path, entries: Iterable[str] | str
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Resolve manifest entries to concrete paths with stable ordering."""
    resolved: list[Path] = []
    missing: list[str] = []
    entries_list = [entries] if isinstance(entries, str) else list(entries or [])

    for entry in entries_list:
        rel = str(entry)
        entry_path = framework_dir / rel
        if any(ch in rel for ch in ("*", "?", "[")):
            safe_parent = _resolve_under_root(
                framework_dir,
                entry_path.parent,
                label=f"manifest entry {rel!r}",
            )
            matches = [
                _resolve_under_root(
                    framework_dir,
                    match,
                    label=f"manifest entry {rel!r}",
                )
                for match in sorted(safe_parent.glob(entry_path.name))
            ]
            if not matches:
                missing.append(rel)
            resolved.extend(matches)
            continue
        safe_entry_path = _resolve_under_root(
            framework_dir,
            entry_path,
            label=f"manifest entry {rel!r}",
        )
        if safe_entry_path.is_dir():
            resolved.extend(
                _resolve_under_root(
                    framework_dir,
                    match,
                    label=f"manifest entry {rel!r}",
                )
                for match in sorted(safe_entry_path.glob("*.yml"))
            )
            continue
        if safe_entry_path.exists():
            resolved.append(safe_entry_path)
            continue
        missing.append(rel)

    seen: set[str] = set()
    unique: list[Path] = []
    for path in sorted(resolved, key=lambda item: item.as_posix()):
        key = path.as_posix()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique), tuple(sorted(set(missing)))


def load_framework_layout(framework_dir: Path | str | PathLike[str]) -> FrameworkLayout:
    """Build a strict framework layout from a framework-pack root."""
    resolved_framework_dir = Path(framework_dir).resolve()
    if not resolved_framework_dir.is_dir():
        raise FileNotFoundError(f"framework pack root not found: {resolved_framework_dir}")
    # Packs are external input. Reject links before reading the manifest so a
    # later hash, schema load or Jinja render cannot dereference host content.
    _reject_pack_symlinks(resolved_framework_dir)
    manifest_path = _require_file(
        resolved_framework_dir,
        "manifest.yml",
        label="manifest.yml",
    )
    framework_version_path = _require_file(
        resolved_framework_dir,
        "FRAMEWORK_VERSION.yml",
        label="FRAMEWORK_VERSION.yml",
    )

    manifest_meta = _load_yaml_mapping(manifest_path)
    if not manifest_meta:
        raise ValueError(f"manifest.yml is empty or invalid: {manifest_path}")

    contracts = _require_mapping(manifest_meta, "contracts")
    contract_entries = {str(section): _as_tuple(entries) for section, entries in contracts.items()}
    if any(not entries for entries in contract_entries.values()):
        raise ValueError("manifest.yml contains empty contract entries")

    bundle_profile_entries = _require_tuple(manifest_meta, "bundle_profiles")

    runtime_section = manifest_meta.get("runtime")
    if runtime_section is None:
        runtime_entries: dict[str, tuple[str, ...]] = {}
    else:
        if not isinstance(runtime_section, dict) or not runtime_section:
            raise ValueError("manifest.yml missing required mapping: runtime")
        runtime_entries = {
            str(section): _as_tuple(entries) for section, entries in runtime_section.items()
        }
        if any(not entries for entries in runtime_entries.values()):
            raise ValueError("manifest.yml contains empty runtime entries")

    templates_dir = _require_dir(
        resolved_framework_dir, manifest_meta.get("templates_dir"), label="templates_dir"
    )
    render_artifacts_dir = _require_dir(
        templates_dir, "artifacts", label="render artifacts directory"
    )
    evidence_templates_dir = _require_dir(
        resolved_framework_dir,
        manifest_meta.get("evidence_templates_dir"),
        label="evidence_templates_dir",
    )
    schemas_dir = _require_dir(
        resolved_framework_dir, manifest_meta.get("schemas_dir"), label="schemas_dir"
    )

    runtime_files = {
        str(key): str(value)
        for key, value in _require_mapping(manifest_meta, "runtime_files").items()
        if isinstance(key, str) and isinstance(value, str)
    }
    context_defaults_path = _require_file(
        resolved_framework_dir,
        runtime_files.get("context_defaults"),
        label="runtime_files.context_defaults",
    )
    backlog_config_path = _require_file(
        resolved_framework_dir,
        runtime_files.get("backlog_config"),
        label="runtime_files.backlog_config",
    )

    contract_files: dict[str, tuple[Path, ...]] = {}
    for section, entries in contract_entries.items():
        resolved, missing = resolve_manifest_entries(resolved_framework_dir, entries)
        if missing:
            raise FileNotFoundError(
                f"manifest.yml contract section '{section}' contains missing entries: "
                + ", ".join(missing)
            )
        if not resolved:
            raise FileNotFoundError(
                f"manifest.yml contract section '{section}' resolved to no files"
            )
        contract_files[section] = resolved

    bundle_profile_files, missing_bundle_entries = resolve_manifest_entries(
        resolved_framework_dir, bundle_profile_entries
    )
    if missing_bundle_entries:
        raise FileNotFoundError(
            "manifest.yml bundle_profiles contains missing entries: "
            + ", ".join(missing_bundle_entries)
        )
    if not bundle_profile_files:
        raise FileNotFoundError("manifest.yml bundle_profiles resolved to no files")

    runtime_section_files: dict[str, tuple[Path, ...]] = {}
    for section, entries in runtime_entries.items():
        resolved, missing = resolve_manifest_entries(resolved_framework_dir, entries)
        if missing:
            raise FileNotFoundError(
                f"manifest.yml runtime section '{section}' contains missing entries: "
                + ", ".join(missing)
            )
        if not resolved:
            raise FileNotFoundError(
                f"manifest.yml runtime section '{section}' resolved to no files"
            )
        runtime_section_files[section] = resolved

    return FrameworkLayout(
        framework_dir=resolved_framework_dir,
        manifest_path=manifest_path,
        framework_version_path=framework_version_path,
        templates_dir=templates_dir,
        render_artifacts_dir=render_artifacts_dir,
        evidence_templates_dir=evidence_templates_dir,
        schemas_dir=schemas_dir,
        context_defaults_path=context_defaults_path,
        backlog_config_path=backlog_config_path,
        contract_entries=contract_entries,
        contract_files=contract_files,
        bundle_profile_entries=bundle_profile_entries,
        bundle_profile_files=bundle_profile_files,
        runtime_entries=runtime_entries,
        runtime_section_files=runtime_section_files,
        runtime_files=runtime_files,
    )


@functools.lru_cache(maxsize=16)
def load_framework_layout_cached(framework_dir: Path) -> FrameworkLayout:
    """Cached variant of ``load_framework_layout`` for stable packs.

    Use only when the pack does not mutate in runtime. Tests that rewrite
    ``manifest.yml`` inside the same ``tmp_path`` must call
    ``load_framework_layout`` directly or invoke
    ``load_framework_layout_cached.cache_clear()`` between writes.
    """
    return load_framework_layout(framework_dir)


def maybe_load_framework_layout(path: Path | str | PathLike[str]) -> FrameworkLayout | None:
    """Discover and load the framework root from any descendant path, if present."""
    candidate = Path(path).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        if (current / "manifest.yml").exists():
            return load_framework_layout(current)
    return None


def load_framework_layout_from_path(path: Path | str | PathLike[str]) -> FrameworkLayout:
    """Discover and load the framework root from any descendant path."""
    maybe = maybe_load_framework_layout(path)
    if maybe is not None:
        return maybe
    candidate = Path(path).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    return load_framework_layout(candidate)


@functools.lru_cache(maxsize=16)
def load_framework_layout_from_path_cached(path: Path) -> FrameworkLayout:
    """Cached variant of ``load_framework_layout_from_path`` for stable packs.

    Use only when the pack does not mutate in runtime. Tests that rewrite
    ``manifest.yml`` must call ``load_framework_layout_from_path`` directly
    or invoke ``load_framework_layout_from_path_cached.cache_clear()``
    between writes.
    """
    return load_framework_layout_from_path(path)
