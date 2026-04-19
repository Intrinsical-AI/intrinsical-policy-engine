# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""View models for Jinja templates - pre-computed, type-safe data.

Feedback Fix (Fase 3): All business logic for templates stays in Python.
Templates receive only strings, ints, bools - never lists, dicts, or Optional.

This module addresses the feedback:
> "Trata a Jinja como si fuera estúpido. No le des lógica."

Usage in context_builders.py:
    from src.domain.bundles.view_models import build_compliance_view

    def audit_trail_builder(ctx: EvalContext) -> dict[str, Any]:
        base = default_builder(ctx)
        base["view"] = build_compliance_view(ctx)
        return base

Usage in templates:
    {{ view.outcome_display }}
    {{ view.role_display }}
    {% if view.is_review %}...{% endif %}
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

# Outcome display mappings (ES locale)
OUTCOME_DISPLAY_MAP: dict[str, str] = {
    "blocked": "Blocked",
    "out_of_scope": "Fuera de Alcance",
    "out_of_scope_territorial": "Fuera de Alcance Territorial",
    "excluded": "Excluido",
    "legacy_grandfathered": "Sistema Heredado",
    "review_provider": "Review - Source",
    "review_deployer": "Review - Operator",
    "review": "Review",
    "limited_risk": "Riesgo Limitado",
    "minimal_risk": "Riesgo Mínimo",
    "model_systemic": "MODEL Sistémico",
    "model_base": "MODEL Base",
    "pending": "Pendiente",
}

# Role display mappings
ROLE_DISPLAY_MAP: dict[str, str] = {
    "provider": "Source",
    "deployer": "Operator",
    "importer": "Importador",
    "distributor": "Distribuidor",
    "authorized_representative": "Representante Autorizado",
}

# Quality status mappings
QUALITY_STATUS_MAP: dict[str, str] = {
    "ready": "Listo",
    "draft": "Borrador",
    "experimental": "Experimental",
    "placeholder": "Pendiente",
}


@dataclass(frozen=True)
class ComplianceStatusView:
    """Pre-computed view for compliance status displays.

    All fields are primitive types (str, bool, int) - no Optional, no lists.
    Templates can use these directly without any conditional logic.
    """

    # Core outcome
    outcome: str  # Raw outcome code: "review", "blocked", etc.
    outcome_display: str  # Human-readable: "Review - Source"

    # Role information
    role: str  # Primary role: "provider", "deployer", etc.
    role_display: str  # Human-readable: "Proveedor"
    has_multiple_roles: bool  # True if more than one role flag is set

    # Risk classification
    is_blocked: bool
    is_review: bool
    is_model: bool
    is_model_systemic: bool
    requires_impact_review: bool

    # Quality status
    quality_status: str  # "ready", "draft", "experimental"
    quality_display: str  # "Listo", "Borrador", etc.

    # Audit hashes (for display)
    plan_hash: str
    plan_hash_short: str  # First 12 chars for UI

    # Counts (for display)
    action_count: int
    evidence_count: int
    article_count: int


def _normalize_outcome(raw_outcome: Any) -> str:
    """Normalize outcome to a string, handling legacy list formats."""
    if raw_outcome is None:
        return "pending"
    if isinstance(raw_outcome, str):
        return raw_outcome
    if isinstance(raw_outcome, (list, tuple)) and len(raw_outcome) > 0:
        return str(raw_outcome[0])
    return str(raw_outcome) if raw_outcome else "pending"


def _flags_to_set(flags: Any) -> set[str]:
    """Convert various flag formats to a set of flag names.

    Handles:
    - set[str] or frozenset[str]: Already a set, return as-is
    - list[str]: Convert to set
    - dict[str, bool]: Extract keys where value is truthy
    """
    if isinstance(flags, (set, frozenset)):
        return set(flags)
    if isinstance(flags, list):
        return set(flags)
    if isinstance(flags, dict):
        # dict[str, bool] format - extract truthy keys
        return {k for k, v in flags.items() if v}
    return set()


def _detect_primary_role(flags: set[str] | frozenset[str] | list[str] | dict[str, bool]) -> str:
    """Detect primary role from flags, with priority order."""
    flags_set = _flags_to_set(flags)

    # Priority order: provider > deployer > importer > distributor > authorized_representative
    role_priority = [
        ("role.source", "provider"),
        ("role.operator", "deployer"),
        ("role.importer", "importer"),
        ("role.distributor", "distributor"),
        ("role.authorized_representative", "authorized_representative"),
    ]

    for flag, role in role_priority:
        if flag in flags_set:
            return role

    return "unknown"


def _count_roles(flags: set[str] | frozenset[str] | list[str] | dict[str, bool]) -> int:
    """Count how many role flags are set."""
    flags_set = _flags_to_set(flags)
    role_flags = {
        "role.source",
        "role.operator",
        "role.importer",
        "role.distributor",
        "role.authorized_representative",
    }
    return len(flags_set & role_flags)


def _compute_quality_status(ready_count: int, draft_count: int, placeholder_count: int) -> str:
    """Compute quality status based on evidence counts."""
    if placeholder_count > 0:
        return "experimental"
    if draft_count > 0:
        return "draft"
    if ready_count > 0:
        return "ready"
    return "draft"  # Default


def build_compliance_view(
    plan: dict[str, Any],
    flags: set[str] | frozenset[str] | list[str] | dict[str, bool],
    quality_report: dict[str, Any] | None = None,
) -> ComplianceStatusView:
    """Build a ComplianceStatusView from plan and flags.

    Args:
        plan: The compliance plan dict
        flags: Set of active flags (can be set, list, or dict[str, bool])
        quality_report: Optional quality report with evidence counts

    Returns:
        ComplianceStatusView with all pre-computed display values
    """
    flags_set = _flags_to_set(flags)

    # Normalize outcome
    raw_outcome = plan.get("outcome")
    outcome = _normalize_outcome(raw_outcome)

    # Build outcome display - first try exact match, then try with role suffix
    outcome_display = OUTCOME_DISPLAY_MAP.get(outcome)
    if outcome_display is None:
        # Try review with role
        if "review" in outcome:
            role = _detect_primary_role(flags_set)
            if role in ("provider", "deployer"):
                outcome_display = OUTCOME_DISPLAY_MAP.get(f"review_{role}", "Review")
            else:
                outcome_display = "Review"
        else:
            outcome_display = outcome.replace("_", " ").title()

    # Role detection
    primary_role = _detect_primary_role(flags_set)
    role_count = _count_roles(flags_set)
    role_display = ROLE_DISPLAY_MAP.get(primary_role, primary_role.title())

    # Risk classification from flags
    is_blocked = "blocked" in outcome or "outcome.blocked" in flags_set
    is_review = (
        "review" in outcome
        or any(f.startswith("classification.") for f in flags_set)
        or "outcome.review" in flags_set
    )
    is_model = any(f.startswith("model.") for f in flags_set)
    is_model_systemic = "model.systemic_risk" in flags_set
    requires_impact_review = "impact_review.required" in flags_set

    # Quality status from report
    qr = quality_report or {}
    evidences_by_status = qr.get("evidences_by_status", {})
    ready_count = evidences_by_status.get("ready", 0)
    draft_count = evidences_by_status.get("draft", 0)
    placeholder_count = evidences_by_status.get("placeholder", 0)

    quality_status = _compute_quality_status(ready_count, draft_count, placeholder_count)
    quality_display = QUALITY_STATUS_MAP.get(quality_status, quality_status.title())

    # Plan hash
    trace = plan.get("trace", {}) or {}
    plan_hash = trace.get("plan_hash") or plan.get("fingerprint") or "—"
    plan_hash_short = plan_hash[:12] if len(plan_hash) > 12 else plan_hash

    # Counts
    actions = plan.get("actions", [])
    action_count = len(actions) if isinstance(actions, list) else 0

    articles_overlay = plan.get("articles_overlay", {})
    article_count = len(articles_overlay) if isinstance(articles_overlay, dict) else 0

    evidence_count = ready_count + draft_count + placeholder_count

    return ComplianceStatusView(
        outcome=outcome,
        outcome_display=outcome_display,
        role=primary_role,
        role_display=role_display,
        has_multiple_roles=role_count > 1,
        is_blocked=is_blocked,
        is_review=is_review,
        is_model=is_model,
        is_model_systemic=is_model_systemic,
        requires_impact_review=requires_impact_review,
        quality_status=quality_status,
        quality_display=quality_display,
        plan_hash=plan_hash,
        plan_hash_short=plan_hash_short,
        action_count=action_count,
        evidence_count=evidence_count,
        article_count=article_count,
    )


def view_to_dict(view: ComplianceStatusView) -> dict[str, Any]:
    """Convert ComplianceStatusView to dict for Jinja context injection."""
    return {
        "outcome": view.outcome,
        "outcome_display": view.outcome_display,
        "role": view.role,
        "role_display": view.role_display,
        "has_multiple_roles": view.has_multiple_roles,
        "is_blocked": view.is_blocked,
        "is_review": view.is_review,
        "is_model": view.is_model,
        "is_model_systemic": view.is_model_systemic,
        "requires_impact_review": view.requires_impact_review,
        "quality_status": view.quality_status,
        "quality_display": view.quality_display,
        "plan_hash": view.plan_hash,
        "plan_hash_short": view.plan_hash_short,
        "action_count": view.action_count,
        "evidence_count": view.evidence_count,
        "article_count": view.article_count,
    }


# =============================================================================
# Action View Models - Backlog classification logic
# =============================================================================

# Category classification rules (SSoT).
# These replace the hardcoded prefixes in templates
ENGINEERING_PREFIXES: frozenset[str] = frozenset(
    {
        "CTRL-9-",
        "CTRL-10-",
        "CTRL-11-",
        "CTRL-12-",
        "CTRL-13-",
        "CTRL-14-",
        "CTRL-15-",
        "HR-A10",
        "HR-A12",
        "HR-A14",
        "STDS-",
    }
)

LEGAL_PREFIXES: frozenset[str] = frozenset(
    {
        "CONF-",
        "REG-",
        "NB-",
        "CTRL-47",
        "CTRL-48",
        "CTRL-49",
        "CTRL-6-CLASS",
    }
)

COMPLIANCE_PREFIXES: frozenset[str] = frozenset(
    {
        "DP-26",
        "CTRL-4-LIT",
        "CTRL-53-",
        "CTRL-26-",
        "INNOV-SB",
    }
)

# Keywords that indicate engineering work (for fuzzy matching)
ENGINEERING_KEYWORDS: frozenset[str] = frozenset(
    {
        "RMS",
        "LOG",
        "LOGS",
        "DATA",
        "DOC",
        "DEPLOY",
        "HUMAN",
        "HITL",
        "ROBSEC",
        "QMS",
        "MONITOR",
        "SEC",
        "CYBER",
    }
)

# Owner mappings by category
CATEGORY_OWNER_MAP: dict[str, str] = {
    "engineering": "Engineering",
    "legal": "Legal",
    "compliance": "Compliance",
    "governance": "Governance",
}


@dataclass(frozen=True)
class ActionViewModel:
    """Pre-computed action view for templates.

    All business logic stays in Python. Templates just iterate and print.
    """

    id: str
    title: str
    priority: str  # lowercase: critical, high, medium, low
    category: str  # engineering, legal, compliance, governance
    owner: str  # Team name
    articles: str  # Pre-joined with pipes: "Topic-9|Topic-10" (Red Team Fix: semicolon breaks Jira)
    description: str  # Pre-escaped for CSV
    due_date: str  # ISO format or T+30d resolved
    applies_to: str  # provider, deployer, any
    status: str  # Red Team Fix (CTO PD#1): overdue, upcoming, future, na
    effort_t_shirt: str  # Red Team Fix (CTO PD#2): S, M, L, XL (estimated effort)


def classify_action_category(action: dict[str, Any]) -> str:
    """Classify action into category.

    Priority:
    1. Explicit 'category' field from actions.yml (DSL)
    2. Prefix matching (fallback for legacy actions without category)
    """
    # 1. Use explicit category from DSL if present
    if category := action.get("category"):
        return str(category).lower()

    # 2. Fallback: prefix matching (temporary until all actions have category)
    action_id = action.get("id", "")

    for prefix in LEGAL_PREFIXES:
        if action_id.startswith(prefix):
            return "legal"

    for prefix in ENGINEERING_PREFIXES:
        if action_id.startswith(prefix):
            return "engineering"

    for prefix in COMPLIANCE_PREFIXES:
        if action_id.startswith(prefix):
            return "compliance"

    # 3. Keyword matching for engineering
    text = f"{action_id} {action.get('title', '')}".upper()
    if any(kw in text for kw in ENGINEERING_KEYWORDS):
        return "engineering"

    # Default to compliance (safest bucket)
    return "compliance"


def _escape_csv_value(value: str) -> str:
    """Properly escape a string for CSV embedding.

    - Replaces double quotes with escaped double quotes
    - Replaces newlines with spaces
    """
    return value.replace('"', '""').replace("\n", " ").replace("\r", "")


def _compute_due_status(due_date: str, reference_date: str) -> str:
    """Compute status of a due date relative to reference date.

    Red Team Fix (CTO PD#1): Add status column for overdue/upcoming/future.

    Returns:
        - 'overdue': due_date < reference_date
        - 'upcoming': due_date within 30 days of reference_date
        - 'future': due_date > 30 days from reference_date
        - 'na': cannot parse dates
    """
    from datetime import UTC, datetime

    if not due_date or due_date in ("TBD", "—", "", "T+30d"):
        return "na"

    try:
        # Handle ISO date format (YYYY-MM-DD)
        due_dt = datetime.fromisoformat(due_date[:10])
        ref_dt = (
            datetime.fromisoformat(reference_date[:10])
            if reference_date
            else datetime.now(UTC)  # Audit BUG-3: Use UTC-aware datetime
        )

        delta = (due_dt - ref_dt).days

        if delta < 0:
            return "overdue"
        elif delta <= 30:
            return "upcoming"
        else:
            return "future"
    except (ValueError, TypeError):
        return "na"


def build_action_view(
    action: dict[str, Any],
    due_hints: dict[str, str],
    base_date: str,
    resolve_date_fn: Any | None = None,
) -> ActionViewModel:
    """Build a single ActionViewModel from raw action dict.

    Args:
        action: Raw action dict from actions_meta
        due_hints: Map of action_id -> due_date
        base_date: ISO date string for relative date resolution
        resolve_date_fn: Optional date resolver (for T+30d syntax)
    """
    action_id = action.get("id", "")
    title = action.get("title") or action_id
    priority = (action.get("priority") or "medium").lower()
    category = classify_action_category(action)
    owner = CATEGORY_OWNER_MAP.get(category, "Compliance")

    # Articles: join list into pipe-separated string (Red Team Fix: semicolon breaks Jira import)
    articles_list = action.get("articles") or []
    articles = "|".join(str(a) for a in articles_list)

    # Description: escape for CSV
    raw_desc = action.get("description") or ""
    description = _escape_csv_value(raw_desc)

    # Due date resolution
    due_hint = due_hints.get(action_id)
    due_date = due_hint if due_hint and due_hint not in ("TBD", "—", "") else "T+30d"

    if resolve_date_fn and due_date.startswith("T+"):
        with contextlib.suppress(ValueError, TypeError):
            due_date = resolve_date_fn(due_date, base_date)

    raw_applies_to = action.get("applies_to") or "any"
    if isinstance(raw_applies_to, list):
        applies_to = "|".join(str(item).lower() for item in raw_applies_to if str(item).strip())
    else:
        applies_to = str(raw_applies_to).lower()

    # Compute due status
    status = _compute_due_status(due_date, base_date)

    # Get effort estimate from action or default
    effort_t_shirt = action.get("effort_t_shirt", "M")  # Default to Medium

    return ActionViewModel(
        id=action_id,
        title=title,
        priority=priority,
        category=category,
        owner=owner,
        articles=articles,
        description=description,
        due_date=due_date,
        applies_to=applies_to,
        status=status,
        effort_t_shirt=effort_t_shirt,
    )


@dataclass(frozen=True)
class BacklogViews:
    """Pre-classified action lists for templates.

    Templates can iterate these directly without any filtering logic.
    """

    all: tuple[ActionViewModel, ...]
    engineering: tuple[ActionViewModel, ...]
    legal: tuple[ActionViewModel, ...]
    compliance: tuple[ActionViewModel, ...]
    governance: tuple[ActionViewModel, ...]

    # For JSON export
    by_priority: dict[str, tuple[str, ...]]  # priority -> tuple of action IDs


def build_backlog_views(
    actions_meta: list[dict[str, Any]],
    due_hints: dict[str, str] | None = None,
    base_date: str = "",
    resolve_date_fn: Any | None = None,
    system_roles: list[str] | None = None,
) -> BacklogViews:
    """Build pre-classified action views for templates.

    Args:
        actions_meta: List of raw action dicts from plan
        due_hints: Map of action_id -> due_date
        base_date: ISO date string (YYYY-MM-DD) for date resolution
        resolve_date_fn: Optional function to resolve T+Nd syntax
        system_roles: List of system roles for filtering (optional)

    Returns:
        BacklogViews with pre-classified action lists
    """
    due_hints = due_hints or {}
    all_views: list[ActionViewModel] = []
    engineering: list[ActionViewModel] = []
    legal: list[ActionViewModel] = []
    compliance: list[ActionViewModel] = []
    governance: list[ActionViewModel] = []

    by_priority: dict[str, list[str]] = {
        "critical": [],
        "high": [],
        "medium": [],
        "low": [],
    }

    for action in actions_meta:
        view = build_action_view(action, due_hints, base_date, resolve_date_fn)
        all_views.append(view)

        # Classify into buckets
        if view.category == "engineering":
            engineering.append(view)
        elif view.category == "legal":
            legal.append(view)
        elif view.category == "governance":
            governance.append(view)
        else:
            compliance.append(view)

        # Track by priority
        if view.priority in by_priority:
            by_priority[view.priority].append(view.id)

    return BacklogViews(
        all=tuple(all_views),
        engineering=tuple(engineering),
        legal=tuple(legal),
        compliance=tuple(compliance),
        governance=tuple(governance),
        by_priority={k: tuple(v) for k, v in by_priority.items()},
    )


def backlog_to_dict(views: BacklogViews) -> dict[str, Any]:
    """Convert BacklogViews to dict for Jinja context injection."""

    def actions_to_list(actions: tuple[ActionViewModel, ...]) -> list[dict[str, str]]:
        return [
            {
                "id": a.id,
                "title": a.title,
                "priority": a.priority,
                "category": a.category,
                "owner": a.owner,
                "articles": a.articles,
                "description": a.description,
                "due_date": a.due_date,
                "applies_to": a.applies_to,
                "status": a.status,  # overdue/upcoming/future/na
                "effort_t_shirt": a.effort_t_shirt,  # S/M/L/XL
            }
            for a in actions
        ]

    return {
        "all": actions_to_list(views.all),
        "engineering": actions_to_list(views.engineering),
        "legal": actions_to_list(views.legal),
        "compliance": actions_to_list(views.compliance),
        "governance": actions_to_list(views.governance),
        "by_priority": dict(views.by_priority),
        "counts": {
            "total": len(views.all),
            "engineering": len(views.engineering),
            "legal": len(views.legal),
            "compliance": len(views.compliance),
            "governance": len(views.governance),
        },
    }
