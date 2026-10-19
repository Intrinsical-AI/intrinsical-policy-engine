# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Application evidence snapshots and checks shared by library and CLI callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.domain.services.evidence_requirements import (
    ready_evidence_problems,
    required_evidence_by_action,
)


@dataclass(frozen=True)
class EvidenceSnapshot:
    files: dict[str, bytes] = field(default_factory=dict)
    quality: dict[str, str] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    supplied: bool = False


def collect_evidence(plan: dict[str, Any], root: Path | None) -> EvidenceSnapshot:
    if root is None:
        return EvidenceSnapshot()
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Evidence directory is not a directory: {root}")
    required = {item for values in required_evidence_by_action(plan).values() for item in values}
    expected = {
        item for values in plan.get("actions_evidence_map", {}).values() for item in values
    } | required
    files: dict[str, bytes] = {}
    quality: dict[str, str] = {}
    problems: list[str] = []
    engine = QualityEngine()
    for relative in sorted(expected):
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError(f"Unsafe evidence path: {relative}")
        candidate = root / rel
        candidates = sorted(candidate.rglob("*")) if candidate.is_dir() else [candidate]
        available = False
        for path in candidates:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError(f"Unsafe evidence file: {path}")
            if path.is_dir():
                continue
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            files[name] = path.read_bytes()
            quality[name] = engine.classify_bytes(files[name], path.suffix)
            available = True
        if not available and relative in required:
            problems.append(f"Missing evidence: {relative}")
    return EvidenceSnapshot(files, quality, tuple(problems), supplied=True)


def check_ci(plan: dict[str, Any], evidence: EvidenceSnapshot) -> list[str]:
    problems = list(evidence.problems)
    if not evidence.supplied:
        problems.append("Explicit --evidence-dir is required for --check-ci")
    profile = plan.get("system_profile", {})
    # Classification is authored by the compiler; names are never inferred by prefix.
    if profile.get("blocked") or plan.get("outcome_axes", {}).get("blocked"):
        problems.append("CI-001: blocked classification")
    problems.extend(ready_evidence_problems(plan, evidence.quality, evidence.files))
    for name, status in evidence.quality.items():
        if status != "ready":
            problems.append(f"CI-003: evidence {name} is {status}")
    return problems
