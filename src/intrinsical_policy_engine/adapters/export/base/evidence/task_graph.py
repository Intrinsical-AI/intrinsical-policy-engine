# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Utilities for transforming plan actions into normalized task items."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from intrinsical_policy_engine.adapters.export.base.models.shapes import TaskItem
from intrinsical_policy_engine.app.config.context import get_plan_fingerprint


def _first_article_for_action(aid: str, plan: dict[str, Any]) -> str | None:
    """Return a representative article id for ordering/labeling."""
    # Prefer articles from actions_meta
    for a in plan.get("actions_meta", []):
        if a.get("id") == aid:
            arts = a.get("articles") or []
            if arts:
                return str(arts[0])
    # Fallback: look into articles_overlay
    overlay = plan.get("articles_overlay", {}) or {}
    for art, ids in overlay.items():
        if aid in (ids or []):
            return str(art)
    return None


def _articles_for_action(aid: str, plan: dict[str, Any]) -> list[str]:
    """Return all article ids linked to the specified action."""
    # Prefer actions_meta
    for a in plan.get("actions_meta", []):
        if a.get("id") == aid:
            arts = [str(x) for x in (a.get("articles") or [])]
            if arts:
                return sorted(set(arts))
    # Fallback: derive from overlay
    overlay = plan.get("articles_overlay", {}) or {}
    arts = [str(art) for art, ids in overlay.items() if aid in (ids or [])]
    return sorted(set(arts))


def _meta_for_action(aid: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Return the metadata entry for the given action id."""
    val = plan.get("actions_meta", [])
    if isinstance(val, list):
        for a in val:
            if isinstance(a, dict) and a.get("id") == aid:
                return a
    return None


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
        Empty string if serialization fails.
    """

    payload = {
        "title": title,
        "description_md": description_md,
        "legal_refs": sorted(str(r) for r in (legal_refs or [])),
        "articles": sorted(str(a) for a in (articles or [])),
    }
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_uid(prefix: str, context_key: str | None, aid: str) -> str:
    """Return a stable UID for a task given a logical context and action id.

    Context is a human/tenant-level namespace (e.g. project_key). When absent,
    a deterministic default namespace is used so that UIDs remain stable across
    regenerations of the same plan.
    """
    import re

    ctx = (context_key or "default").strip()
    if not ctx:
        ctx = "default"

    # Normalise: lowercase, replace non-alphanumeric chars with dashes, strip dashes
    ctx_norm = re.sub(r"[^a-z0-9]+", "-", ctx.lower()).strip("-")
    if not ctx_norm:
        ctx_norm = "default"

    return f"{prefix}-{ctx_norm}-{aid}"


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
            - export_context: Optional context with project_key/namespace

    Returns:
        List of TaskItem objects, one per action in the plan.
    """
    plan_fp = get_plan_fingerprint(plan)
    items: list[TaskItem] = []

    due_hints = plan.get("due_hints", {}) or {}
    act_evidence = plan.get("actions_evidence_map", {}) or {}
    art_evidence = plan.get("articles_evidence_map", {}) or {}

    export_ctx = plan.get("export_context") or {}
    context_key: str | None = None
    if isinstance(export_ctx, dict):
        context_key = (
            export_ctx.get("project_key")
            or export_ctx.get("uid_namespace")
            or export_ctx.get("namespace")
        )

    for aid in plan.get("actions", []) or []:
        meta = _meta_for_action(aid, plan) or {}
        arts = _articles_for_action(aid, plan)
        first_art = _first_article_for_action(aid, plan) or "CTRL-Act"
        title_src = meta.get("title") or aid
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
            f"- plan_fingerprint: `{plan_fp}`",
        ]
        description_md = "\n".join([x for x in desc_lines if x is not None])

        # For content_hash, ignore the plan fingerprint line so hashes remain
        # invariant across semantically identical plans with different action order.
        desc_for_hash_lines = [
            ln
            for ln in desc_lines
            if not (isinstance(ln, str) and ln.startswith("- plan_fingerprint:"))
        ]
        description_for_hash = "\n".join([x for x in desc_for_hash_lines if x is not None])

        content_hash = compute_content_hash(title_src, description_for_hash, list(legal_refs), arts)

        items.append(
            TaskItem(
                uid=_stable_uid("ctrl-act", context_key, aid),
                action_id=aid,
                title=title,
                description_md=description_md,
                due=due,
                labels=labels,
                article_ids=arts,
                priority=str(priority) if priority is not None else None,
                applies_to=str(applies_to) if applies_to is not None else None,
                attachments=[],
                content_hash=content_hash,
            )
        )

    return items
