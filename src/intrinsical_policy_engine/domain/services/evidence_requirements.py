# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure evidence attribution and completeness over canonical package paths."""

from bisect import bisect_left
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from intrinsical_policy_engine.domain.snapshot_contract import canonical_path


def evidence_path_matches(name: str, requirement: str) -> bool:
    return name == requirement or name.startswith(requirement + "/")


@dataclass
class _EvidenceIndex:
    """Exact lookup plus a lazily sorted directory index shared by requirements."""

    files: frozenset[str]
    ordered: list[str] | None = None

    def matches(self, requirement: str) -> bool:
        if requirement in self.files:
            return True
        if self.ordered is None:
            self.ordered = sorted(self.files)
        prefix = requirement + "/"
        position = bisect_left(self.ordered, prefix)
        return position < len(self.ordered) and self.ordered[position].startswith(prefix)


def _strings(value: Any, where: str, *, paths: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where} must be a list of strings")
    if paths:
        for item in value:
            canonical_path(item)
    return value


def _mapping(value: Any, where: str, *, paths: bool = False) -> dict[str, list[str]]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{where} must be an object with string keys")
    return {key: _strings(items, f"{where}.{key}", paths=paths) for key, items in value.items()}


def required_evidence_by_action(plan: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    metadata = plan.get("actions_meta", [])
    if not isinstance(metadata, list) or not all(isinstance(item, dict) for item in metadata):
        raise ValueError("actions_meta must be a list of objects")
    action_map = _mapping(plan.get("actions_evidence_map", {}), "actions_evidence_map", paths=True)
    article_map = _mapping(
        plan.get("articles_evidence_map", {}), "articles_evidence_map", paths=True
    )
    overlay = _mapping(plan.get("articles_overlay", {}), "articles_overlay")
    articles_by_action: dict[str, set[str]] = {}
    for article, actions in overlay.items():
        for action in actions:
            articles_by_action.setdefault(action, set()).add(article)
    result: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for meta in metadata:
        aid = meta.get("id")
        required = meta.get("requires_evidence", True)
        if not isinstance(aid, str) or not aid or not isinstance(required, bool):
            raise ValueError(
                "action metadata requires a nonempty string id and boolean requires_evidence"
            )
        if aid in seen:
            raise ValueError(f"Duplicate action metadata: {aid}")
        seen.add(aid)
        declared = _strings(meta.get("evidence", []), f"actions_meta.{aid}.evidence", paths=True)
        articles = set(_strings(meta.get("articles", []), f"actions_meta.{aid}.articles"))
        articles.update(articles_by_action.get(aid, ()))
        inherited = [path for article in sorted(articles) for path in article_map.get(article, [])]
        if required:
            result[aid] = tuple(dict.fromkeys([*declared, *action_map.get(aid, []), *inherited]))
    if set(action_map) - seen:
        raise ValueError("actions_evidence_map references actions without metadata")
    return result


def missing_evidence_requirements(plan: Mapping[str, Any], files: Collection[str]) -> list[str]:
    requirements = required_evidence_by_action(plan)
    index = _EvidenceIndex(frozenset(files))
    problems: list[str] = []
    for aid, paths in requirements.items():
        if not paths:
            problems.append(f"Missing evidence requirements for action {aid}")
        for path in paths:
            if not index.matches(path):
                problems.append(f"Missing evidence: {path} (action {aid})")
    return problems


def ready_evidence_problems(
    plan: Mapping[str, Any], quality: Mapping[str, str], files: Collection[str]
) -> list[str]:
    requirements = required_evidence_by_action(plan)
    available = frozenset(files)
    index = _EvidenceIndex(
        frozenset(
            name for name, status in quality.items() if status == "ready" and name in available
        )
    )
    problems: list[str] = []
    for aid, paths in requirements.items():
        if not paths:
            problems.append(f"CI-002: action {aid} has no declared evidence requirements")
        for path in paths:
            if not index.matches(path):
                problems.append(f"CI-002: action {aid} has no ready evidence for {path}")
    return problems
