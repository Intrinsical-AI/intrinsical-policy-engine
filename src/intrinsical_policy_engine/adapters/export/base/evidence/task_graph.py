# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Utilities for transforming plan actions into normalized task items."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from intrinsical_policy_engine.adapters.export.base.models.shapes import TaskItem
from intrinsical_policy_engine.domain.services.integrity import compute_plan_hash


def _articles_for_action(aid: str, plan: dict[str, Any]) -> list[str]:
    """Read the canonical union of authored forward and inverse links."""
    overlay = plan.get("articles_overlay", {}) or {}
    arts = [str(art) for art, ids in overlay.items() if aid in (ids or [])]
    return sorted(set(arts))


def compute_content_hash(
    title: str, description_md: str, legal_refs: list[str], articles: list[str] | None = None
) -> str:
    """Compute a deterministic content hash for a task.

    The hash is based solely on stable textual content (title, description, legal refs, articles)
    and is intentionally independent from plan-wide fingerprints or export config.

    Args:
        title: Task title string.
        description_md: Markdown description text.
        legal_refs: List of legal reference strings.
        articles: Optional list of article IDs.

    Returns:
        SHA-256 hex digest (64-character string) of the content hash.
    """

    payload = {
        "title": title,
        "description_md": description_md,
        "legal_refs": sorted(str(r) for r in (legal_refs or [])),
        "articles": sorted(str(a) for a in (articles or [])),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_uid(namespace: str, action_id: str) -> str:
    """Hash the exact namespace and action ID, preserving distinctions."""
    raw = json.dumps([namespace, action_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() + "@intrinsical-policy-engine"


def build_task_items(plan: dict[str, Any]) -> list[TaskItem]:
    """Build normalized task items from a plan dictionary.

    Transforms plan actions into TaskItem objects suitable for API export.
    Each task includes metadata, evidence references, and article mappings.

    Args:
        plan: Compliance plan dictionary with:
            - actions: List of action IDs
            - actions_meta: List of action metadata dicts
            - articles_overlay: Article to actions mapping
            - due_hints: Action ID to due date mapping
            - actions_evidence_map: Action ID to evidence paths mapping
            - articles_evidence_map: Article ID to evidence paths mapping
            - export_context: Required context with uid_namespace

    Returns:
        List of TaskItem objects, one per action in the plan.
    """
    plan_hash = compute_plan_hash(plan)
    items: list[TaskItem] = []

    due_hints = plan.get("due_hints", {}) or {}
    act_evidence = plan.get("actions_evidence_map", {}) or {}
    art_evidence = plan.get("articles_evidence_map", {}) or {}

    namespace = plan["export_context"]["uid_namespace"]
    metadata = {item["id"]: item for item in plan.get("actions_meta", [])}

    for aid in plan.get("actions", []) or []:
        meta = metadata.get(aid, {})
        arts = _articles_for_action(aid, plan)
        first_art = arts[0] if arts else "CTRL-Act"
        title_src = meta.get("title") or aid
        authored_description = meta.get("description")
        title = f"[{first_art}] {title_src} ({aid})"
        due = due_hints.get(aid)
        priority = meta.get("priority")
        applies_to = meta.get("applies_to")

        labels = ["CTRL-Act", *arts]
        if priority:
            labels.append(str(priority))

        legal_refs = meta.get("legal_refs") or []
        ae = act_evidence.get(aid, [])
        # Merge article evidence per article for visibility
        art_ev_lines = []
        for art in arts:
            evs = art_evidence.get(art, []) or []
            if evs:
                for e in evs:
                    art_ev_lines.append(f"- [{art}] {e}")

        # Articles list for description (explicitly include all article IDs)
        articles_lines = []
        if arts:
            articles_lines = ["## Articles", *[f"- {a}" for a in arts], ""]

        desc_lines = [
            f"# {title_src}",
            "",
            *([authored_description, ""] if authored_description else []),
            f"- action_id: `{aid}`",
            f"- due: `{due}`" if due else "- due: `n/a`",
            f"- applies_to: `{applies_to}`" if applies_to else None,
            f"- priority: `{priority}`" if priority else None,
            f"- legal_refs: {', '.join(legal_refs)}" if legal_refs else None,
            "",
            *articles_lines,
            "## Evidence needed (action)",
            *(f"- {x}" for x in ae),
            "",
            "## Evidence needed (articles)",
            *art_ev_lines,
            "",
            "## Trace",
            f"- plan_hash: `{plan_hash}`",
        ]
        description_md = "\n".join([x for x in desc_lines if x is not None])

        # Exclude only the generated final plan hash, preserving authored text
        # even when it happens to start with the same marker.
        description_for_hash = "\n".join([x for x in desc_lines[:-1] if x is not None])

        content_hash = compute_content_hash(title_src, description_for_hash, list(legal_refs), arts)

        items.append(
            TaskItem(
                uid=stable_uid(namespace, aid),
                action_id=aid,
                title=title,
                description_md=description_md,
                due=due,
                labels=labels,
                article_ids=arts,
                content_hash=content_hash,
            )
        )

    return items
