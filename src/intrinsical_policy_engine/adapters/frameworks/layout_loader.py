# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Resolve a version three manifest with containment and capture its source bytes."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from intrinsical_policy_engine.common.document_io import parse_document
from intrinsical_policy_engine.common.package_integrity import read_regular
from intrinsical_policy_engine.domain.contract_models import PackManifest
from intrinsical_policy_engine.domain.framework_layout import FrameworkLayout
from intrinsical_policy_engine.domain.services.contract_validation import ensure_engine_compatible
from intrinsical_policy_engine.domain.snapshot_contract import canonical_path


def contained_path(root: Path, relative: str, *, allow_root: bool = False) -> Path:
    if not (allow_root and relative == "."):
        canonical_path(relative)
    result = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"pack paths cannot contain symbolic links: {relative}")
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"pack path escapes its root: {relative}")
    return result


def resolve_manifest_entries(root: Path, entries: Iterable[str]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for entry in entries:
        candidate = contained_path(root, entry)
        paths = sorted(root.glob(entry)) if any(c in entry for c in "*?[") else [candidate]
        if not paths:
            raise FileNotFoundError(f"manifest entry matches no files: {entry}")
        for path in paths:
            path = contained_path(root, path.relative_to(root).as_posix())
            if not path.is_file():
                raise FileNotFoundError(f"manifest entry is not a file: {entry}")
            if path in seen:
                raise ValueError(f"manifest entries duplicate a source file: {entry}")
            seen.add(path)
            result.append(path)
    return tuple(result)


def load_framework_layout(framework_dir: Path) -> FrameworkLayout:
    root = framework_dir.resolve()
    manifest_bytes = read_regular(root / "manifest.yml")
    manifest = PackManifest.model_validate(parse_document(manifest_bytes.decode("utf-8"), ".yml"))
    ensure_engine_compatible(manifest)
    contract_entries = {key: tuple(value) for key, value in manifest.contracts.model_dump().items()}
    runtime_entries = {key: tuple(value) for key, value in manifest.runtime.model_dump().items()}
    for key in ("flags", "actions", "rules", "articles"):
        if not contract_entries[key]:
            raise ValueError(f"required contract section is empty: {key}")
    if any(not entries for entries in runtime_entries.values()):
        raise ValueError("runtime section entries cannot be empty")
    contract_files = {
        key: resolve_manifest_entries(root, entries) for key, entries in contract_entries.items()
    }
    runtime_section_files = {
        key: resolve_manifest_entries(root, entries) for key, entries in runtime_entries.items()
    }
    bundle_profile_files = resolve_manifest_entries(root, manifest.bundle_profiles)
    templates_dir = contained_path(root, manifest.templates_dir)
    evidence_templates_dir = contained_path(root, manifest.evidence_templates_dir)
    for directory in (templates_dir, templates_dir / "artifacts", evidence_templates_dir):
        if not directory.is_dir():
            raise NotADirectoryError(
                f"pack directory does not exist: {directory.relative_to(root)}"
            )
    context_path = contained_path(root, manifest.runtime_files.context_defaults)
    backlog_path = contained_path(root, manifest.runtime_files.backlog_config)
    source_paths = set(bundle_profile_files) | {context_path, backlog_path}
    for section_files in (*contract_files.values(), *runtime_section_files.values()):
        source_paths.update(section_files)
    for directory in (templates_dir, evidence_templates_dir):
        for path in directory.rglob("*"):
            safe_path = contained_path(root, path.relative_to(root).as_posix())
            if not safe_path.is_dir():
                source_paths.add(safe_path)
    source_bytes = {"manifest.yml": manifest_bytes}
    for path in sorted(source_paths):
        source_bytes[path.relative_to(root).as_posix()] = read_regular(path)
    return FrameworkLayout(
        framework_dir=root,
        manifest=manifest,
        templates_dir=templates_dir,
        context_defaults_path=context_path,
        backlog_config_path=backlog_path,
        contract_files=contract_files,
        bundle_profile_files=bundle_profile_files,
        runtime_section_files=runtime_section_files,
        source_bytes=source_bytes,
    )
