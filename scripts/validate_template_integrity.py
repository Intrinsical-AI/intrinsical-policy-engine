# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Small public template integrity check used by strict exports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.adapters.frameworks.layout_loader import (
    load_framework_layout_cached as load_framework_layout,
)


@dataclass(frozen=True)
class IntegrityIssue:
    severity: str
    category: str
    location: str
    message: str
    suggestion: str = ""


@dataclass
class IntegrityReport:
    errors: list[IntegrityIssue] = field(default_factory=list)
    warnings: list[IntegrityIssue] = field(default_factory=list)
    info: list[IntegrityIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


FLAG_REF = re.compile(r"['\"]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)['\"]")
ACTION_REF = re.compile(r"(?<!TOPIC-)STARTER-[A-Z0-9-]+")
ARTICLE_REF = re.compile(r"TOPIC-STARTER-[A-Z0-9-]+")


def _read_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _first_contract(root: Path, section: str, fallback: Path) -> Path | None:
    layout = load_framework_layout(root)
    files = layout.resolve_contract_files(section)
    return files[0] if files else fallback


def _template_root(root: Path) -> Path:
    layout = load_framework_layout(root)
    return layout.evidence_templates_dir


def _defined_ids(root: Path) -> tuple[set[str], set[str], set[str], set[str]]:
    flags_doc = _read_yaml(_first_contract(root, "flags", root / "law/core/flags.yml"))
    actions_doc = _read_yaml(_first_contract(root, "actions", root / "law/core/actions.yml"))
    articles_doc = _read_yaml(_first_contract(root, "articles", root / "law/content/articles.yml"))
    evidence_doc = _read_yaml(
        _first_contract(root, "evidence_map", root / "law/policy/evidence_map.yml")
    )

    flags = {str(item.get("id")) for item in flags_doc.get("registry", []) if item.get("id")}
    actions = {str(item.get("id")) for item in actions_doc.get("actions", []) if item.get("id")}
    articles = {str(item.get("id")) for item in articles_doc.get("taxonomy", []) if item.get("id")}
    evidence_paths: set[str] = set()
    for entries in evidence_doc.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path"):
                evidence_paths.add(str(entry["path"]))
            elif isinstance(entry, str):
                evidence_paths.add(entry)
    return flags, actions, articles, evidence_paths


def validate_integrity(root: str | Path) -> IntegrityReport:
    """Validate template references against public starter contracts."""
    pack_root = Path(root)
    report = IntegrityReport()
    flags, actions, articles, evidence_paths = _defined_ids(pack_root)
    templates_root = _template_root(pack_root)

    report.stats = {
        "flags": len(flags),
        "actions": len(actions),
        "articles": len(articles),
        "evidence_templates": len(evidence_paths),
    }

    for rel_path in sorted(evidence_paths):
        full_path = templates_root / rel_path
        if not full_path.exists():
            report.errors.append(
                IntegrityIssue(
                    severity="error",
                    category="missing_template",
                    location=rel_path,
                    message="Evidence template referenced by the pack is missing.",
                )
            )
            continue

        text = full_path.read_text(encoding="utf-8")
        for flag_id in sorted(set(FLAG_REF.findall(text))):
            if flag_id.startswith("starter.") and flag_id not in flags:
                report.errors.append(
                    IntegrityIssue(
                        severity="error",
                        category="unknown_flag",
                        location=rel_path,
                        message=f"Template references unknown flag '{flag_id}'.",
                    )
                )
        for action_id in sorted(set(ACTION_REF.findall(text))):
            if action_id not in actions and action_id not in articles:
                report.errors.append(
                    IntegrityIssue(
                        severity="error",
                        category="unknown_action",
                        location=rel_path,
                        message=f"Template references unknown action '{action_id}'.",
                    )
                )
        for article_id in sorted(set(ARTICLE_REF.findall(text))):
            if article_id not in actions and article_id not in articles:
                report.warnings.append(
                    IntegrityIssue(
                        severity="warn",
                        category="unclassified_reference",
                        location=rel_path,
                        message=f"Template contains '{article_id}' outside known contract ids.",
                    )
                )

    return report
