# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Evidence path utilities for loading and validating evidence maps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.adapters.frameworks.layout_loader import (
    load_framework_layout,
    load_framework_layout_from_path,
)
from src.app.config.constants import EVIDENCE_MANIFEST, EVIDENCE_MAP_YML

# Type aliases
Article = str
EvidencePath = str


@dataclass
class EvidenceEntryResult:
    """Result of validating a single evidence entry."""

    article: Article
    path: EvidencePath
    required: bool
    exists: bool
    is_file: bool
    is_dir: bool


@dataclass
class EvidenceValidationReport:
    """Result of validating all evidence paths."""

    results: list[EvidenceEntryResult]

    @property
    def missing_required(self) -> list[EvidenceEntryResult]:
        """Required entries that are missing at the resolved evidence path."""
        return [r for r in self.results if r.required and not r.exists]

    @property
    def missing_optional(self) -> list[EvidenceEntryResult]:
        """Optional entries that are missing."""
        return [r for r in self.results if not r.required and not r.exists]

    @property
    def found(self) -> list[EvidenceEntryResult]:
        """Entries that resolved to files or directories on disk."""
        return [r for r in self.results if r.exists]

    @property
    def total(self) -> int:
        """Total number of evidence entries that were evaluated."""
        return len(self.results)

    def ok(self) -> bool:
        """Return True when no required evidence entries are missing."""
        return len(self.missing_required) == 0


def load_evidence_map_raw(evidence_map_path: Path) -> dict[str, Any]:
    """Load evidence_map.yml with error handling.

    Args:
        evidence_map_path: Path to evidence_map.yml

    Returns:
        Raw dict from YAML

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If YAML is invalid or not a mapping
        OSError: If file can't be read
    """
    try:
        with evidence_map_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"evidence_map.yml not found: {evidence_map_path}") from e
    except yaml.YAMLError as e:
        raise ValueError(f"YAML error in evidence_map.yml: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("evidence_map.yml root must be a mapping (dict)")

    return data


def load_evidence_map(templates_dir: str) -> dict[str, list[dict[str, Any]]] | None:
    """Read and normalize evidence_map.yml if the file exists.

    Args:
        templates_dir: Path to the framework render root.

    Returns:
        Normalized evidence map dictionary (article_id -> list of template configs),
        or None if file doesn't exist or is invalid.

    """
    templates_path = Path(templates_dir)
    layout = load_framework_layout_from_path(templates_path)
    evidence_files = layout.resolve_contract_files("evidence_map")
    if not evidence_files:
        return None
    yml = evidence_files[0]
    try:
        data = yaml.safe_load(yml.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return normalize_evidence_map(data)
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None


def load_evidence_map_for_bundle(bundle_path: str) -> dict[str, list[dict[str, Any]]]:
    """Load evidence_map.yml from the framework pack.

    Args:
        bundle_path: Path to bundle root directory.

    Returns:
        Normalized evidence map dictionary (article_id -> list of template configs).
        Returns empty dict if file doesn't exist or is invalid.
    """
    try:
        layout = load_framework_layout(Path(bundle_path))
    except (FileNotFoundError, ValueError):
        return {}
    evidence_files = layout.resolve_contract_files("evidence_map")
    if not evidence_files:
        return {}
    try:
        data = yaml.safe_load(evidence_files[0].read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return normalize_evidence_map(data) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return {}


def normalize_evidence_map(
    data: dict[str, Any],
) -> dict[str, list[dict[str, Any]]] | None:
    """Normalize evidence_map content to article -> list[{path, required}].

    Converts various evidence_map formats (strings, dicts) into a consistent
    structure with normalized paths and required flags.

    Args:
        data: Raw evidence_map dictionary (may contain strings or dicts).

    Returns:
        Normalized dictionary mapping article IDs to lists of template configs
        with 'path' and 'required' keys. Returns None if data is invalid or empty.
    """
    if not isinstance(data, dict):
        return None
    out: dict[str, list[dict[str, Any]]] = {}
    for k, v in data.items():
        if not isinstance(v, list):
            continue
        by_path: dict[str, dict[str, Any]] = {}
        for item in v:
            if isinstance(item, str):
                p = str(item)
                req = True
            elif isinstance(item, dict) and item.get("path"):
                p = str(item.get("path"))
                rraw = item.get("required")
                req = bool(rraw) if rraw is not None else True
            else:
                continue
            cur = by_path.get(p)
            if cur is None:
                by_path[p] = {"path": p, "required": req}
            else:
                cur["required"] = bool(cur.get("required", True) or req)
        if by_path:
            out[str(k)] = list(by_path.values())
    return out if out else None


def evidence_summary(out_dir: str | Path) -> dict[str, Any]:
    """Summarize evidence manifest counts from a previous export run.

    Args:
        out_dir: Export output directory containing evidence manifest files.

    Returns:
        Dictionary with evidence summary statistics (file counts, article counts, etc.).
    """
    base = Path(out_dir)
    p = base / EVIDENCE_MANIFEST
    try:
        if not p.exists():
            p = base / "_metadata" / EVIDENCE_MANIFEST
        if not p.exists():
            return {"included_count": 0, "missing_count": 0, "articles_covered": 0}
        man = json.loads(p.read_text(encoding="utf-8"))
        inc = man.get("included") or []
        mis = man.get("missing") or []
        by_art = man.get("by_article") or {}
        return {
            "included_count": len(inc),
            "missing_count": len(mis),
            "articles_covered": len(by_art) if isinstance(by_art, dict) else 0,
        }
    except (OSError, ValueError):
        return {"included_count": 0, "missing_count": 0, "articles_covered": 0}


# ---- Validation functions ----


def collect_evidence_entries(
    emap: dict[str, Any],
) -> list[tuple[Article, EvidencePath, bool]]:
    """Flatten evidence_map structure into (article, path, required) tuples.

    Supports:
    - TOPIC-XX:
        - string
        - { path: "...", required: false }
    """
    entries: list[tuple[Article, EvidencePath, bool]] = []

    for article, values in emap.items():
        if not isinstance(values, list):
            continue

        for entry in values:
            if isinstance(entry, dict):
                path = entry.get("path")
                required = bool(entry.get("required", True))
            else:
                path = entry
                required = True

            if not path:
                continue

            entries.append((article, str(path), required))

    return entries


def validate_evidence_paths(
    evidence_base: Path,
    entries: list[tuple[Article, EvidencePath, bool]],
) -> EvidenceValidationReport:
    """Check existence of each evidence path under evidence_base.

    Args:
        evidence_base: Base directory for evidence files
        entries: List of (article, path, required) tuples

    Returns:
        EvidenceValidationReport with validation results
    """
    results: list[EvidenceEntryResult] = []

    for article, rel_path, required in entries:
        full_path = evidence_base / rel_path
        exists = full_path.exists()
        is_file = full_path.is_file()
        is_dir = full_path.is_dir()

        results.append(
            EvidenceEntryResult(
                article=article,
                path=rel_path,
                required=required,
                exists=exists,
                is_file=is_file,
                is_dir=is_dir,
            )
        )

    return EvidenceValidationReport(results=results)


def validate_framework_evidence(
    framework_path: Path,
) -> EvidenceValidationReport:
    """Validate all evidence paths for a framework.

    Args:
        framework_path: Path to framework directory (e.g., frameworks/starter)

    Returns:
        EvidenceValidationReport with validation results

    Raises:
        FileNotFoundError: If evidence_map.yml doesn't exist
        ValueError: If evidence_map.yml is invalid
    """
    layout = load_framework_layout(framework_path)
    evidence_files = layout.resolve_contract_files("evidence_map")
    evidence_map_path = (
        evidence_files[0] if evidence_files else layout.framework_dir / EVIDENCE_MAP_YML
    )
    evidence_base = layout.evidence_templates_dir

    emap = load_evidence_map_raw(evidence_map_path)
    entries = collect_evidence_entries(emap)
    return validate_evidence_paths(evidence_base, entries)
