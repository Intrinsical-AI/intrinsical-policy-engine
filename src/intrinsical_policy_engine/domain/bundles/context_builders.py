# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure context builders for bundle templates.

Red Team Fixes:
- Lawyer: wizard_answers must be available for PREGUNTA -> RESPUESTA traceability
- Auditor: engine.commit and audit hashes must be populated
"""

from collections.abc import Callable
from typing import Any, cast

from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION
from intrinsical_policy_engine.common.jinja_env import resolve_relative_date
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.view_models import (
    backlog_to_dict,
    build_backlog_views,
    build_compliance_view,
    view_to_dict,
)

ContextBuilder = Callable[[EvalContext], dict[str, Any]]

DEFAULT_ENGINE_VERSION = CANONICAL_ENGINE_VERSION
DEFAULT_DISCLAIMER_VERSION = "1.3"


def _get_engine_version() -> str:
    """Return the version of the code building this context."""
    return DEFAULT_ENGINE_VERSION


def _resolve_system_name(ctx: EvalContext) -> str:
    """Resolve system name with fallback strategy.

    Ensure system name is always present to avoid
    [REQUIRED] placeholders in generated artifacts.

    Args:
        ctx: Evaluation context with system profile and plan data.

    Returns:
        System name string. Priority order:
        1. SubjectProfile.name from ctx.system_profile (canonical source since Red Team fix)
        2. Raw answers trace
        3. Wizard answers
        4. Base answers
        5. Fallback to "Sistema AI"
    """
    # 1. Try system profile's name field (now canonical per Red Team CEO fix)
    if ctx.system_profile:
        name = ctx.system_profile.name
        if name and name != "Sistema AI":
            return name

    # 2. Try raw answers trace
    if name := ctx.plan.get("trace", {}).get("answers_raw", {}).get("system", {}).get("name"):
        return str(name)

    # 3. Try wizard answers
    if name := ctx.extras.get("wizard_answers", {}).get("system", {}).get("name"):
        return str(name)

    # 4. Try base answers
    if name := ctx.extras.get("answers", {}).get("system", {}).get("name"):
        return str(name)

    # 5. Fallback - also check system_profile.name even if default
    if ctx.system_profile and ctx.system_profile.name:
        return ctx.system_profile.name

    return "Sistema AI"


def _normalize_outcome(raw_outcome: str | list[str] | tuple[str, ...] | None) -> str:
    """Normalize outcome to a string, handling legacy list formats.

    Templates should receive a clean string, never a list or other type.
    This fixes the symptom; the root cause should be addressed upstream.

    Args:
        raw_outcome: Outcome value (string, list/tuple of strings, or None).

    Returns:
        Normalized string outcome. Returns first element if list/tuple,
        "pending" if None, otherwise string representation.

    Example:
        >>> _normalize_outcome("review")
        'review'
        >>> _normalize_outcome(["review", "limited_risk"])
        'review'
        >>> _normalize_outcome(None)
        'pending'
    """
    if raw_outcome is None:
        return "pending"
    if isinstance(raw_outcome, str):
        return raw_outcome
    if isinstance(raw_outcome, (list, tuple)) and len(raw_outcome) > 0:
        return str(raw_outcome[0])
    return str(raw_outcome) if raw_outcome else "pending"


def default_builder(ctx: EvalContext) -> dict[str, Any]:
    """Default context: plan + system_profile + flags + audit/engine metadata."""
    trace = ctx.plan.get("trace", {}) or {}

    # Build audit metadata (Red Team Fix: Auditor needs hashes)
    audit = ctx.extras.get("audit", {}).copy() if ctx.extras.get("audit") else {}
    plan_hash = trace.get("plan_hash", "—")
    audit.setdefault("plan_hash", plan_hash)
    audit.setdefault("plan_sha256", audit["plan_hash"])
    audit.setdefault("answers_hash", trace.get("answers_raw", {}).get("answers_hash", "—"))
    audit.setdefault("rules_sha256", trace.get("bundle_hash", "—"))

    # Red Team Fix Round 2 (Auditor): Calculate evidence_map hash if available
    evidence_map = ctx.extras.get("evidence_map") or ctx.plan.get("evidence_map")
    if evidence_map and isinstance(evidence_map, dict):
        import hashlib
        import json

        evidence_hash = hashlib.sha256(
            json.dumps(evidence_map, sort_keys=True).encode()
        ).hexdigest()  # Full SHA256 for cryptographic integrity (Red Team Fix R3)
        audit.setdefault("evidence_map_sha256", evidence_hash)
    else:
        audit.setdefault("evidence_map_sha256", "—")

    # Build engine metadata (Auditor needs commit != None)
    engine_raw = ctx.extras.get("engine")
    engine = engine_raw.copy() if isinstance(engine_raw, dict) else {}
    runtime = ctx.extras.get("runtime")
    presentation = getattr(runtime, "presentation", None)
    engine.setdefault("version", _get_engine_version())

    # Unknown is honest and deterministic; never inspect the caller's Git cwd.
    engine.setdefault("commit", trace.get("engine_commit") or "unknown")
    engine.setdefault(
        "name",
        getattr(presentation, "engine_name", None) or "intrinsical-policy-engine",
    )

    # Inject plan.fingerprint for templates using {{ plan.fingerprint }}.
    # BundleExporter uses default_builder() which bypasses build_base_context(),
    # so we need to inject fingerprint here from trace.plan_hash
    plan_raw = dict(ctx.plan)
    plan_with_fingerprint = dict(plan_raw)
    plan_with_fingerprint["fingerprint"] = plan_hash

    # Base context expected by most templates
    base = {
        "plan": plan_with_fingerprint,
        "plan_raw": plan_raw,
        "system": ctx.system_profile,
        "system_profile": ctx.system_profile,
        "system_name": _resolve_system_name(ctx),
        "flags": ctx.flags,
        "audit": audit,
        "engine": engine,
        "disclaimer_version": DEFAULT_DISCLAIMER_VERSION,
    }

    # Merge extras (but don't overwrite our computed audit/engine)
    for key, value in ctx.extras.items():
        if key not in base:
            base[key] = value

    # Build pre-classified backlog views for CSV templates
    # Templates iterate backlog.engineering, backlog.legal, etc. without logic
    actions_meta = ctx.plan.get("actions_meta", []) or []
    due_hints = ctx.plan.get("due_hints", {}) or {}
    meta = ctx.extras.get("meta", {}) or {}
    generated_at = meta.get("generated_at")
    base_date = str(generated_at)[:10] if isinstance(generated_at, str) and generated_at else ""

    # Resolve T+30d to ISO 8601 for Jira/Linear compatibility
    backlog_views = build_backlog_views(
        actions_meta=actions_meta,
        due_hints=due_hints,
        base_date=base_date,
        resolve_date_fn=resolve_relative_date,
    )
    base["backlog"] = backlog_to_dict(backlog_views)

    return base


def audit_trail_builder(ctx: EvalContext) -> dict[str, Any]:
    """Context for audit trail with full traceability.

    Red Team Fixes:
    - Lawyer: Include wizard_answers for PREGUNTA -> RESPUESTA chain
    - Auditor: Include trace data and all hashes
    """
    base = default_builder(ctx)
    trace = ctx.plan.get("trace", {}) or {}

    # Extract wizard_answers from multiple possible sources
    # Priority: extras > plan.answers > trace.answers_sanitized
    answers_raw = trace.get("answers_raw", {}) or {}
    wizard_answers = (
        ctx.extras.get("wizard_answers")
        or ctx.extras.get("answers")
        or ctx.plan.get("answers", {})
        or answers_raw.get("answers_sanitized", {})  # Fallback to sanitized trace
        or {}
    )

    # Unwrap nested structure if present
    if isinstance(wizard_answers, dict) and "answers" in wizard_answers:
        wizard_answers = wizard_answers["answers"]

    # Extract selected_actions with full metadata for action traceability
    raw_selected_actions = ctx.plan.get("actions_meta", []) or ctx.plan.get("selected_actions", [])
    if not isinstance(raw_selected_actions, list):
        raw_selected_actions = []

    # Enrich actions with trigger/triggered_by from when clause.
    # Template uses {{ action.trigger | default(action.triggered_by | default('—')) }}
    selected_actions: list[dict[str, Any]] = []
    for action in raw_selected_actions:
        if isinstance(action, dict):
            enriched = dict(action)
            when_clause = enriched.get("when", "—")
            if "trigger" not in enriched:
                enriched["trigger"] = when_clause
            if "triggered_by" not in enriched:
                enriched["triggered_by"] = when_clause
            selected_actions.append(enriched)
        else:
            selected_actions.append(action)

    # Build pre-computed view model for templates.
    # Templates can use {{ view.outcome_display }} instead of complex Jinja logic.
    raw_quality_report = ctx.plan.get("quality_report")
    quality_report: dict[str, Any] | None
    if isinstance(raw_quality_report, dict):
        quality_report = cast(dict[str, Any], raw_quality_report)
    else:
        quality_report = None

    plan_dict = cast(dict[str, Any], ctx.plan)
    compliance_view = build_compliance_view(plan_dict, ctx.flags, quality_report)

    return {
        **base,
        "trace": trace,
        "plan_hash": trace.get("plan_hash", base["audit"].get("plan_hash", "—")),
        "wizard_answers": wizard_answers,
        "selected_actions": selected_actions,
        # Flatten common fields for template convenience
        # Normalize outcome to string here, not in Jinja templates
        "outcome": _normalize_outcome(ctx.plan.get("outcome")),
        "routing": ctx.plan.get("routing", {}),
        # Feedback Fix (Fase 3): Pre-computed view model for templates
        "view": view_to_dict(compliance_view),
    }


# Simple registry
BUILDERS: dict[str, ContextBuilder] = {
    "default": default_builder,
    "audit_trail": audit_trail_builder,
}


def get_builder(name: str) -> ContextBuilder:
    return BUILDERS.get(name, default_builder)


# =============================================================================
# BACKLOG CONTEXT BUILDERS
# Dynamic configuration loading - NO hardcoded keywords
# =============================================================================


def _get_backlog_config(ctx: EvalContext) -> Any | None:
    """Return preloaded backlog config injected by the app/adapter layer."""
    return ctx.extras.get("_backlog_config")


def _filter_to_tasks(actions: list[dict], ctx: EvalContext) -> list[dict]:
    """Filter actions to concrete tasks only (exclude advisory/reminder)."""
    result = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        # Exclude advisory and reminder entries
        kind = a.get("kind", "")
        if kind in ("advisory", "reminder"):
            continue
        # Keep tasks and anything without explicit kind
        result.append(a)
    return result


def _clean_owner(action: dict) -> None:
    """Clean owner field (CTO requirement: no 'any' or empty values)."""
    applies_to = action.get("applies_to")
    if not applies_to or applies_to == "any":
        owner_source = str(applies_to) if applies_to else ""
        owner_value = "System-Managed" if "any" in owner_source else "Unassigned"
        action["applies_to"] = owner_value


def _fill_due_dates(actions: list[dict], ctx: EvalContext, default_due_days: int) -> dict:
    """Fill missing due dates with defaults."""
    from datetime import UTC, datetime, timedelta

    # Get base date from context
    gen_at_str = ctx.extras.get("meta", {}).get("generated_at", "")
    try:
        base_date = datetime.fromisoformat(gen_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        base_date = datetime.now(UTC)  # Audit BUG-2: Use UTC-aware datetime

    default_due = (base_date + timedelta(days=default_due_days)).strftime("%Y-%m-%d")

    # Build due_hints dict
    due_hints = dict(ctx.extras.get("due_hints", {}) or {})
    for a in actions:
        aid = a.get("id")
        if aid and aid not in due_hints:
            due_hints[aid] = default_due
    return due_hints


def backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    """Context for main backlog.csv - all tasks filtered and cleaned.
    Loads configuration from backlog_config.yml dynamically.
    """
    base = default_builder(ctx)

    config = _get_backlog_config(ctx)

    # Get all actions
    actions_meta = list(
        ctx.extras.get("actions_meta", []) or ctx.plan.get("actions_meta", []) or []
    )

    # Filter to tasks
    filtered = _filter_to_tasks(actions_meta, ctx)

    # Clean owners and fill due dates
    for a in filtered:
        _clean_owner(a)

    default_due_days = config.default_due_days if config else 30
    due_hints = _fill_due_dates(filtered, ctx, default_due_days)

    return {
        **base,
        "actions_meta": filtered,
        "due_hints": due_hints,
        "_backlog_config": config,
    }


def engineering_backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    """Context for engineering_backlog.csv - filtered by engineering keywords.

    Keywords are loaded from backlog_config.yml at runtime.
    If YAML changes, output changes - no Python modification needed.
    """
    from intrinsical_policy_engine.domain.bundles.backlog_config import (
        filter_actions_by_keywords,
        get_keywords_for_split,
    )

    # Start with full backlog context
    base = backlog_builder(ctx)

    # Load config dynamically
    config = _get_backlog_config(ctx)

    if not config:
        # No config, return empty
        return {**base, "actions_meta": []}

    # Get engineering keywords from YAML config (NOT hardcoded)
    keywords = get_keywords_for_split(config, "engineering")

    # Filter actions
    filtered = filter_actions_by_keywords(base["actions_meta"], keywords)

    # Inject keywords into context for template debugging
    return {
        **base,
        "actions_meta": filtered,
        "engineering_keywords": list(keywords),  # For template access if needed
    }


def legal_backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    """Context for legal_backlog.csv - governance/legal focused tasks.

    If governance split has keywords in YAML, use them.
    Otherwise includes actions NOT matched by any other split (fallback).
    """
    from intrinsical_policy_engine.domain.bundles.backlog_config import (
        filter_actions_by_keywords,
        get_keywords_for_split,
    )

    base = backlog_builder(ctx)

    config = _get_backlog_config(ctx)

    if not config:
        return {**base, "actions_meta": []}

    # Check if governance split has explicit keywords
    governance_keywords = get_keywords_for_split(config, "governance")

    if governance_keywords:
        # Use explicit keywords
        filtered = filter_actions_by_keywords(base["actions_meta"], governance_keywords)
    else:
        # Fallback: actions NOT matched by engineering
        engineering_keywords = get_keywords_for_split(config, "engineering")
        all_actions = base["actions_meta"]

        # Get what engineering matched
        engineering_matched = set()
        for a in all_actions:
            text = (str(a.get("id", "")) + " " + str(a.get("title", ""))).upper()
            if any(k in text for k in engineering_keywords):
                engineering_matched.add(a.get("id"))

        # Governance = complement
        filtered = [a for a in all_actions if a.get("id") not in engineering_matched]

    return {
        **base,
        "actions_meta": filtered,
    }


def calendar_builder(ctx: EvalContext) -> dict[str, Any]:
    """Context for compliance.ics - calendar events from actions with due dates."""
    base = backlog_builder(ctx)

    # Calendar needs due_hints and actions_meta
    # Also include regulatory dates if available
    regulatory_dates = ctx.extras.get("regulatory_dates", []) or ctx.plan.get(
        "regulatory_dates", []
    )

    return {
        **base,
        "regulatory_dates": regulatory_dates,
    }


# Register the new builders
BUILDERS.update(
    {
        "backlog": backlog_builder,
        "engineering_backlog": engineering_backlog_builder,
        "legal_backlog": legal_backlog_builder,
        "calendar": calendar_builder,
    }
)
