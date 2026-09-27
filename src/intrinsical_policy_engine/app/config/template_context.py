# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Type definitions for template context.

This module provides TypedDict definitions for the context passed to Jinja2
templates, enabling better IDE support and documentation.

These types are used by:
- build_artifact_context() in context.py
- Template validators
- IDE type checking with stubs
"""

from __future__ import annotations

from typing import Any, TypedDict


class ScopeContext(TypedDict, total=False):
    """Scope-related context variables."""

    is_ai: bool
    not_ai: bool
    in_scope: bool
    excluded: bool
    research_only: bool


class MODELContext(TypedDict, total=False):
    """Model context."""

    is_model: bool
    is_systemic: bool
    is_provider: bool
    is_integrator: bool
    is_foss: bool
    compute_threshold: str
    has_complex_case: bool


class ImpactReviewContext(TypedDict, total=False):
    """Impact review context."""

    required: bool
    voluntary: bool
    optional_for_lea: bool
    reason: str


class DPIAContext(TypedDict, total=False):
    """Data Protection Impact Assessment context."""

    required: bool
    reason: str


class TransparencyContext(TypedDict, total=False):
    """Transparency requirements context."""

    required: bool
    deepfake: bool
    synthetic_content: bool
    affective_signal_detection: bool
    sensitive_categorisation: bool


class IncidentsContext(TypedDict, total=False):
    """Incident reporting context."""

    reporting_required: bool
    monitoring_required: bool


class PMMContext(TypedDict, total=False):
    """Post-Market Monitoring context."""

    required: bool
    plan_required: bool


class SensitiveInputsContext(TypedDict, total=False):
    """Sensitive-input processing context."""

    uses_sensitive_inputs: bool
    real_time_id: bool
    post_id: bool
    categorisation: bool
    public_space: bool
    is_blocked: bool


class DownstreamContext(TypedDict, total=False):
    """Downstream source/operator context."""

    is_downstream: bool
    provider_info_required: bool
    documentation_required: bool


class RoutingContext(TypedDict, total=False):
    """Routing assessment context."""

    route: str
    source: str
    reason: str
    enforce: bool
    safety_override: bool
    override_flags: list[str]
    alternative_route: str | None


class LegalTokenContext(TypedDict, total=False):
    """Legal reference token."""

    eli: list[str]
    date: str


class AuditContext(TypedDict, total=False):
    """Audit trail context."""

    rules_sha256: str
    evidence_map_sha256: str
    answers_sha256: str
    answers_path: str


class EngineContext(TypedDict, total=False):
    """Engine metadata context."""

    name: str
    version: str
    commit: str | None


class PlanContext(TypedDict, total=False):
    """Plan metadata context."""

    fingerprint: str
    assessment_date: str


class ProjectContext(TypedDict, total=False):
    """Project configuration context."""

    key: str
    name: str
    uid_namespace: str


class TraceContext(TypedDict, total=False):
    """Trace/debugging context."""

    answers_raw: dict[str, Any]
    flags_emitted: list[str]
    flags_final: list[str]
    rules_applied: dict[str, Any]


class OutcomeAxesContext(TypedDict, total=False):
    """Outcome classification axes."""

    risk_tier: str  # "blocked", "review", "model", "transparency", "minimal"
    roles: list[str]
    scope_result: str
    regulatory_path: str


class TemplateContext(TypedDict, total=False):
    """Complete context passed to Jinja2 templates.

    This is the full structure of the context dict passed to templates
    by build_artifact_context().
    """

    # Core plan data
    flags: list[str]
    actions: list[str]
    actions_meta: list[dict[str, Any]]
    due_hints: dict[str, str]
    articles_overlay: dict[str, list[str]]
    flag_article_index: dict[str, list[str]]
    articles_evidence_map: dict[str, list[str]]
    actions_evidence_map: dict[str, list[str]]
    section_refs_by_flag: dict[str, list[str]]
    outcome: list[str]
    plan_raw: dict[str, Any]
    plan_hash: str

    # Structured sub-contexts
    scope: ScopeContext
    model: MODELContext
    impact_review: ImpactReviewContext
    dpia: DPIAContext
    transparency: TransparencyContext
    incidents: IncidentsContext
    pmm: PMMContext
    sensitive_inputs: SensitiveInputsContext
    downstream: DownstreamContext
    routing: RoutingContext
    legal_token: LegalTokenContext
    outcome_axes: OutcomeAxesContext
    trace: TraceContext

    # Metadata
    meta: dict[str, Any]
    assessment: dict[str, Any]
    audit: AuditContext
    engine: EngineContext
    plan: PlanContext
    project: ProjectContext
    disclaimer_version: str
    demo_mode: bool
    regulatory_version: dict[str, Any]
    regulatory_warnings: list[str]
    gpg_key_id: str
    gpg_signature_available: bool

    # Risk tier shorthand
    risk_tier: str
    roles: list[str]
    legal_refs: list[str]

    # Computed metrics
    metrics: dict[str, Any]
    evidence_quality_summary: dict[str, Any]
    artifact_coverage_by_article: dict[str, Any]
    artifact_coverage_pct: dict[str, Any]
    coverage_operational_by_article: dict[str, Any]
    missing_reasons_by_article: dict[str, Any]
    evidence_by_article: dict[str, Any]
    deadline_metrics: dict[str, Any]
    techdoc_stats: dict[str, Any]

    # Convenience view models
    system: dict[str, Any]
    system_name: str
    system_profile: dict[str, Any]
    provider: dict[str, Any]
    declared_by: dict[str, Any]
    role_display: dict[str, Any]
    classification: dict[str, Any]
    backlog: dict[str, Any]
    selected_actions: list[dict[str, Any]]

    # User inputs
    answers: dict[str, Any]
    wizard_answers: dict[str, Any]

    # Export context
    export_context: dict[str, Any]
    project_key: str
    evidence_map: dict[str, Any]
    flag_to_articles: dict[str, list[str]]
    gaps: list[dict[str, Any]]
    omissions: list[dict[str, Any]]
    calendar_events: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    predicates: list[str]
    context: Any


# List of all known top-level context keys for validation
KNOWN_CONTEXT_KEYS: set[str] = {
    # From TemplateContext
    "flags",
    "actions",
    "actions_meta",
    "due_hints",
    "articles_overlay",
    "plan_raw",
    "plan_hash",
    "flag_article_index",
    "articles_evidence_map",
    "actions_evidence_map",
    "section_refs_by_flag",
    "outcome",
    "scope",
    "model",
    "impact_review",
    "dpia",
    "transparency",
    "incidents",
    "pmm",
    "sensitive_inputs",
    "downstream",
    "routing",
    "legal_token",
    "outcome_axes",
    "trace",
    "meta",
    "assessment",
    "audit",
    "engine",
    "plan",
    "project",
    "disclaimer_version",
    "demo_mode",
    "regulatory_version",
    "regulatory_warnings",
    "gpg_key_id",
    "gpg_signature_available",
    "risk_tier",
    "roles",
    "legal_refs",
    "metrics",
    "evidence_quality_summary",
    "artifact_coverage_by_article",
    "artifact_coverage_pct",
    "coverage_operational_by_article",
    "missing_reasons_by_article",
    "evidence_by_article",
    "deadline_metrics",
    "techdoc_stats",
    "system",
    "system_name",
    "system_profile",
    "provider",
    "declared_by",
    "role_display",
    "classification",
    "backlog",
    "selected_actions",
    "answers",
    "wizard_answers",
    "export_context",
    "project_key",
    "evidence_map",
    "flag_to_articles",
    "gaps",
    "omissions",
    "calendar_events",
    "sources",
    "predicates",
    "context",
    # Jinja helpers and built-ins
    "fill",
    "now",
    "date",
    "datetime",
    "range",
    "dict",
    "list",
    "true",
    "false",
    "none",
}
