# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Domain service for calculating compliance metrics and statistics."""

from __future__ import annotations

from typing import Any

from src.domain.contract_models import FrameworkRuntime
from src.domain.services.rule_engine import eval_ast, parse_when
from src.domain.types import Plan

HITL_SOURCE_CLASSIFICATION = "classification"
HITL_SOURCE_TRANSPARENCY = "transparency_only"
HITL_SOURCE_DEFAULT = "heuristic_default"


def compute_hitl_signal(flags: list[str]) -> tuple[float, str]:
    """Heuristic-based HITL signal computation, no runtime config required."""
    flag_set = set(flags)

    classification_flags = {f for f in flag_set if f.startswith("classification.")}
    if classification_flags and "classification.not_review" not in flag_set:
        return 0.85, HITL_SOURCE_CLASSIFICATION

    if "model.systemic_risk" in flag_set:
        return 0.65, "model_systemic"

    transparency_flags = {f for f in flag_set if f.startswith("transparency.")}
    public_output_flags = {f for f in flag_set if f.startswith("gen.public_output")}
    if transparency_flags or public_output_flags:
        return 0.25, HITL_SOURCE_TRANSPARENCY

    return 0.10, HITL_SOURCE_DEFAULT


def compute_hitl_signal_context(
    flags: list[str],
    articles: set[str] | None,
    runtime: FrameworkRuntime | None = None,
) -> tuple[float, str]:
    """Compute HITL signal using runtime-supplied metrics hints, with heuristic fallback."""
    metrics_cfg = runtime.policies.metrics if runtime is not None else None

    if metrics_cfg is None:
        return compute_hitl_signal(flags)

    flag_set = set(flags)

    for signal in metrics_cfg.hitl_signals:
        when_flags = signal.when_flags
        if not when_flags:
            continue
        ast = parse_when(when_flags)
        if eval_ast(ast, flag_set):
            return signal.score, signal.source

    article_ids = {str(article).upper() for article in articles or set()}
    for signal in metrics_cfg.article_signals:
        if signal.articles_any and any(
            any(token in article for article in article_ids) for token in signal.articles_any
        ):
            return signal.score, signal.source

    return metrics_cfg.default_score, metrics_cfg.default_source


def compute_metrics(plan: Plan, runtime: FrameworkRuntime | None = None) -> dict[str, Any]:
    """Compute coverage and legal-reference metrics for a plan."""

    if not isinstance(plan, dict):
        raise ValueError(f"Plan must be a dictionary, got {type(plan)}")

    metrics: dict[str, Any] = {}
    actions = plan.get("actions", []) or []
    overlay = plan.get("articles_overlay", {}) or {}
    flags = plan.get("flags", []) or []
    actions_meta = plan.get("actions_meta", []) or []
    total_actions = len(actions)

    if total_actions > 0:
        for article_id, mapped_actions in overlay.items():
            metrics[f"coverage_{article_id}"] = len(mapped_actions) / total_actions

    hitl_actions = [action for action in actions if "HITL" in action]
    metrics["hitl_required"] = len(hitl_actions) / total_actions if total_actions > 0 else 0.0

    articles_context = set(overlay.keys())
    hitl_score, source = compute_hitl_signal_context(flags, articles_context, runtime)
    metrics["hitl_index"] = hitl_score
    metrics["hitl_signal_source"] = source

    sources = set()
    for meta in actions_meta:
        for ref in meta.get("legal_refs", []):
            sources.add(ref)
    metrics["sources"] = sorted(sources)
    return metrics


def calculate_techdoc_stats(plan: dict[str, Any]) -> dict[str, Any]:
    """Calculate progress statistics for technical documentation."""

    total = len(plan.get("actions", []))
    completed = 0
    if "backlog" in plan and isinstance(plan["backlog"], dict):
        all_items = []
        for cat_items in plan["backlog"].values():
            if isinstance(cat_items, list):
                all_items.extend(cat_items)
        total = len(all_items)
        completed = sum(
            1 for item in all_items if item.get("status") in ("Done", "Completed", "✅")
        )

    progress = int(min((completed / total) * 100, 100)) if total > 0 else 0
    return {"progress_pct": progress, "completed": completed, "total": total}
