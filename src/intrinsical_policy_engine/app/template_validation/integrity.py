# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Referential integrity checks for framework-pack evidence templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from intrinsical_policy_engine.adapters.frameworks.layout_loader import (
    FrameworkPackSymlinkError,
)
from intrinsical_policy_engine.adapters.frameworks.layout_loader import (
    load_framework_layout_cached as load_framework_layout,
)
from intrinsical_policy_engine.common.text.front_matter import parse_front_matter


@dataclass(frozen=True)
class IntegrityIssue:
    """A contract reference issue found in an evidence template."""

    severity: str
    category: str
    location: str
    message: str
    suggestion: str = ""


@dataclass
class IntegrityReport:
    """Collected template integrity issues and source-contract statistics."""

    errors: list[IntegrityIssue] = field(default_factory=list)
    warnings: list[IntegrityIssue] = field(default_factory=list)
    info: list[IntegrityIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        """Return whether the validation found blocking issues."""
        return bool(self.errors)


TEXT_TEMPLATE_SUFFIXES = frozenset(
    {".csv", ".htm", ".html", ".j2", ".json", ".md", ".rst", ".txt", ".xml", ".yaml", ".yml"}
)
REFERENCE_FIELDS = {
    "action_ids": "action",
    "article_ids": "article",
    "flag_ids": "flag",
    "related_actions": "action",
    "related_articles": "article",
    "related_flags": "flag",
}
STRICT_REFERENCE_FIELDS = frozenset({"action_ids", "article_ids", "flag_ids"})


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


def _issue(
    report: IntegrityReport,
    *,
    category: str,
    location: str,
    message: str,
    suggestion: str = "",
) -> None:
    report.errors.append(
        IntegrityIssue(
            severity="error",
            category=category,
            location=location,
            message=message,
            suggestion=suggestion,
        )
    )


def _resolve_evidence_files(
    templates_root: Path,
    rel_path: str,
    report: IntegrityReport,
) -> tuple[Path, ...]:
    """Resolve a declared file or trailing-slash directory to concrete files."""
    declared_path = Path(rel_path)
    if declared_path.is_absolute() or ".." in declared_path.parts or ":" in rel_path:
        _issue(
            report,
            category="unsafe_template_path",
            location=rel_path,
            message="Evidence template path must stay inside the pack template root.",
        )
        return ()

    root = templates_root.resolve()
    full_path = (root / rel_path).resolve()
    try:
        full_path.relative_to(root)
    except ValueError:
        _issue(
            report,
            category="unsafe_template_path",
            location=rel_path,
            message="Evidence template path resolves outside the pack template root.",
        )
        return ()

    if not full_path.exists():
        _issue(
            report,
            category="missing_template",
            location=rel_path,
            message="Evidence template referenced by the pack is missing.",
        )
        return ()

    expects_directory = rel_path.endswith("/")
    if expects_directory:
        if not full_path.is_dir():
            _issue(
                report,
                category="template_path_kind",
                location=rel_path,
                message="Evidence path ends with '/' but does not resolve to a directory.",
                suggestion="Remove the trailing slash for a file entry.",
            )
            return ()

        files: list[Path] = []
        for path in sorted(full_path.rglob("*")):
            if path.name.startswith(".") or not path.is_file():
                continue
            resolved_file = path.resolve()
            try:
                resolved_file.relative_to(full_path)
                resolved_file.relative_to(root)
            except ValueError:
                _issue(
                    report,
                    category="unsafe_template_path",
                    location=path.relative_to(root).as_posix(),
                    message="Evidence template entry resolves outside its declared directory.",
                )
                continue
            files.append(resolved_file)
        if not files:
            _issue(
                report,
                category="empty_template_directory",
                location=rel_path,
                message="Evidence template directory contains no files to export or validate.",
            )
        return tuple(files)

    if full_path.is_dir():
        _issue(
            report,
            category="template_path_kind",
            location=rel_path,
            message="Evidence path resolves to a directory but is declared as a file.",
            suggestion="Add a trailing '/' to declare a directory template.",
        )
        return ()
    if not full_path.is_file():
        _issue(
            report,
            category="template_path_kind",
            location=rel_path,
            message="Evidence path is neither a regular file nor a directory template.",
        )
        return ()
    return (full_path,)


def _structured_metadata(template_path: Path, text: str) -> dict[str, Any]:
    """Load static front matter or a structured text template document."""
    front_matter = parse_front_matter(text)
    if front_matter is not None:
        return front_matter

    if template_path.suffix.lower() not in {".json", ".yaml", ".yml"}:
        return {}
    if "{{" in text or "{%" in text:
        return {}
    try:
        loaded = yaml.safe_load(text) or {}
    except (yaml.YAMLError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _collect_structured_references(
    value: Any,
    *,
    defined_ids: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Collect exact typed references from nested structured metadata.

    ``*_ids`` fields are machine contracts, so every string is validated.
    Historical ``related_*`` fields also contain display labels, article
    numbers and legal citations.  For those fields, only values in a namespace
    already declared by the pack are identifiers; prose remains metadata.
    """
    references: dict[str, set[str]] = {
        "action": set(),
        "article": set(),
        "flag": set(),
    }

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                reference_type = REFERENCE_FIELDS.get(str(key))
                if reference_type is not None:
                    candidates = child if isinstance(child, list) else [child]
                    for candidate in candidates:
                        if not isinstance(candidate, str):
                            continue
                        if key in STRICT_REFERENCE_FIELDS or _looks_like_pack_id(
                            candidate,
                            defined_ids[reference_type],
                        ):
                            references[reference_type].add(candidate)
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return references


def _looks_like_pack_id(candidate: str, defined_ids: set[str]) -> bool:
    """Recognize an ID using namespaces derived from this pack, not vocabulary."""
    if candidate in defined_ids:
        return True

    def namespace(value: str) -> str:
        for separator in ("-", "_", ".", ":"):
            if separator in value:
                return value.split(separator, 1)[0]
        return ""

    candidate_namespace = namespace(candidate)
    if not candidate_namespace:
        return False
    declared_namespaces = {namespace(identifier) for identifier in defined_ids}
    declared_namespaces.discard("")
    return candidate_namespace in declared_namespaces


def validate_integrity(root: str | Path) -> IntegrityReport:
    """Validate template references against the contracts in a framework pack."""
    pack_root = Path(root)
    report = IntegrityReport()
    try:
        flags, actions, articles, evidence_paths = _defined_ids(pack_root)
        templates_root = _template_root(pack_root)
    except FrameworkPackSymlinkError as exc:
        resolved_root = pack_root.resolve()
        try:
            location = exc.path.relative_to(resolved_root).as_posix()
        except ValueError:
            location = exc.path.as_posix()
        _issue(
            report,
            category="unsafe_pack_path",
            location=location,
            message="Framework packs must not contain symbolic links.",
        )
        return report

    report.stats = {
        "flags": len(flags),
        "actions": len(actions),
        "articles": len(articles),
        "evidence_templates": len(evidence_paths),
    }

    for rel_path in sorted(evidence_paths):
        for template_path in _resolve_evidence_files(templates_root, rel_path, report):
            location = template_path.relative_to(templates_root.resolve()).as_posix()
            if template_path.suffix.lower() not in TEXT_TEMPLATE_SUFFIXES:
                report.info.append(
                    IntegrityIssue(
                        severity="info",
                        category="non_text_template",
                        location=location,
                        message="Non-text evidence template skipped during reference scanning.",
                    )
                )
                continue
            try:
                text = template_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                _issue(
                    report,
                    category="unreadable_template",
                    location=location,
                    message=f"Evidence template could not be read as UTF-8 text: {exc}",
                )
                continue

            ids_by_type = {
                "action": actions,
                "article": articles,
                "flag": flags,
            }
            references = _collect_structured_references(
                _structured_metadata(template_path, text),
                defined_ids=ids_by_type,
            )
            for reference_type, declared_ids in (
                ("action", actions),
                ("article", articles),
                ("flag", flags),
            ):
                for reference_id in sorted(references[reference_type] - declared_ids):
                    _issue(
                        report,
                        category=f"unknown_{reference_type}",
                        location=location,
                        message=(f"Template references unknown {reference_type} '{reference_id}'."),
                    )

    return report
