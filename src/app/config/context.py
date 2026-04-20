# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Context building utilities for framework-pack compliance plans.

Defaults are loaded from the framework pack layout to allow:
- i18n: Localize placeholder text without touching Python code
- Framework customization: Different defaults per regulatory framework
- Maintainability: Single source of truth for default values

All presentation strings MUST come from context_defaults.yml.
This module handles only structural defaults (empty dicts/lists, computed dates).
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from src.adapters.frameworks.layout_loader import load_framework_layout
from src.app.config.constants import DEFAULT_ENCODING
from src.common.constants import CANONICAL_ENGINE_NAME, CANONICAL_ENGINE_VERSION
from src.domain.services.metrics import calculate_techdoc_stats

logger = logging.getLogger(__name__)


def _get_git_commit() -> str:
    """Get current git commit hash, or 'dev' if not in git repo."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return "dev"


CONTEXT_DEFAULTS_FILE = "context_defaults.yml"

# Fallback defaults if YAML loading fails (minimal set for graceful degradation)
_FALLBACK_DEFAULTS: dict[str, Any] = {
    "provider": {"name": "[Provider name]"},
    "system": {"name": "[System name]", "version": "v0"},
    "engine": {"name": CANONICAL_ENGINE_NAME, "version": CANONICAL_ENGINE_VERSION},
    "dpia": {"id": "DPIA-ID"},
    "scope": {
        "versions": "All current system versions",
        "components": "Core system components and sub-systems",
    },
    "doc": {
        "author_name": "Risk Owner / AI Compliance Lead",
        "approver_name": "Management Representative",
    },
}

# Default engineering keywords if backlog_config.yml not found
_DEFAULT_ENGINEERING_KEYWORDS = [
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
    "ROBUST",
    "TEST",
    "BIAS",
    "SEC",
    "CYBER",
    "MONITOR",
]


@functools.lru_cache(maxsize=4)
def load_backlog_keywords(framework_path: Path | None = None) -> dict[str, list[str]]:
    """Load backlog split keywords from backlog_config.yml.

    Single source of truth for backlog filtering.

    Args:
        framework_path: Optional path to framework directory.

    Returns:
        Dictionary mapping split ID to list of keywords (uppercase for matching).
        Returns empty dict if file not found or invalid.

    Note:
        Results are cached (maxsize=4) to avoid repeated file reads.
    """
    if framework_path is None:
        return {"engineering": _DEFAULT_ENGINEERING_KEYWORDS}
    try:
        layout = load_framework_layout(framework_path)
        path = layout.backlog_config_path
    except (FileNotFoundError, ValueError):
        return {"engineering": _DEFAULT_ENGINEERING_KEYWORDS}
    try:
        with open(path, encoding=DEFAULT_ENCODING) as f:
            data = yaml.safe_load(f) or {}
            splits = data.get("splits", [])
            result: dict[str, list[str]] = {}
            for split in splits:
                sid = split.get("id", "")
                keywords = split.get("keywords", [])
                result[sid] = [k.upper() for k in keywords if isinstance(k, str)]
            logger.debug("Loaded backlog keywords from %s: %s", path, list(result.keys()))
            return result
    except FileNotFoundError:
        logger.warning("Backlog config not found at %s, using defaults", path)
        return {"engineering": _DEFAULT_ENGINEERING_KEYWORDS}
    except yaml.YAMLError as e:
        logger.warning("Failed to parse backlog config: %s", e)
        return {"engineering": _DEFAULT_ENGINEERING_KEYWORDS}


@functools.lru_cache(maxsize=4)
def load_context_defaults(framework_path: Path | None = None) -> dict[str, Any]:
    """Load context defaults from context_defaults.yml.

    Args:
        framework_path: Optional path to framework directory.

    Returns:
        Dictionary with default context values. Falls back to minimal defaults
        if file not found or invalid.

    Note:
        All presentation strings MUST come from context_defaults.yml.
        This module handles only structural defaults (empty dicts/lists, computed dates).
    """
    if framework_path is None:
        return _FALLBACK_DEFAULTS.copy()
    try:
        layout = load_framework_layout(framework_path)
        path = layout.context_defaults_path
    except (FileNotFoundError, ValueError):
        return _FALLBACK_DEFAULTS.copy()
    try:
        with open(path, encoding=DEFAULT_ENCODING) as f:
            data = yaml.safe_load(f) or {}
            logger.debug("Loaded context defaults from %s", path)
            return data
    except FileNotFoundError:
        logger.warning("Context defaults not found at %s, using fallback", path)
        return _FALLBACK_DEFAULTS.copy()
    except yaml.YAMLError as e:
        logger.warning("Failed to parse context defaults: %s", e)
        return _FALLBACK_DEFAULTS.copy()


def now_iso_z() -> str:
    """Get current UTC timestamp in ISO 8601 format with Z suffix.

    Returns:
        ISO 8601 formatted string (e.g., '2024-01-15T10:30:00Z').
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_plan_fingerprint(plan: dict[str, Any]) -> str:
    """Compute deterministic fingerprint for a plan.

    Args:
        plan: Plan dictionary.

    Returns:
        SHA-256 hex digest of the plan's deterministic content.
        Same inputs always produce the same fingerprint.
        Returns empty string on non-serializable input.
    """
    try:
        return hashlib.sha256(json.dumps(plan, sort_keys=True).encode(DEFAULT_ENCODING)).hexdigest()
    except (TypeError, ValueError):
        return ""


def _extract_assessment_timestamp(plan: dict[str, Any]) -> str | None:
    """Resolve assessment timestamp from plan or trace when available."""

    if not isinstance(plan, dict):
        return None
    if plan.get("assessment_timestamp"):
        return plan.get("assessment_timestamp")
    trace = plan.get("trace")
    if isinstance(trace, dict) and trace.get("assessment_timestamp"):
        return trace.get("assessment_timestamp")
    return None


def _merge_mapping(base: dict[str, Any] | Any, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge updates into a mapping, replacing non-mapping values.

    Args:
        base: Base dictionary to merge into, or any other value (will be replaced).
        updates: Dictionary of updates to apply.

    Returns:
        Merged dictionary. If base is a dict, returns merged result.
        Otherwise, returns a copy of updates.
    """
    if isinstance(base, dict):
        return {**base, **updates}
    return updates.copy()


def _as_mapping(value: dict[str, Any] | Any) -> dict[str, Any]:
    """Return a dict view of a value, falling back to empty mapping.

    Args:
        value: Value to convert to dictionary.

    Returns:
        Dictionary representation of value, or empty dict if not a dict.
    """
    if isinstance(value, dict):
        return value
    return {}


def _normalize_metrics(ctx: dict[str, Any], extra_metrics: dict[str, Any] | None) -> dict[str, Any]:
    metrics = ctx.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    if isinstance(extra_metrics, dict) and extra_metrics:
        metrics = {**metrics, **extra_metrics}
    ctx["metrics"] = metrics
    return metrics


def _flatten_metrics_into_context(ctx: dict[str, Any], metrics: dict[str, Any]) -> None:
    for key, value in metrics.items():
        if key not in ctx:
            ctx[key] = value


def _collect_coverage_metrics(ctx: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    coverage_metrics: dict[str, Any] = {}
    for mapping in (ctx, metrics):
        for key, value in mapping.items():
            if isinstance(key, str) and key.startswith("coverage_"):
                coverage_metrics[key] = value
    return coverage_metrics


def _inject_meta_and_fingerprint(ctx: dict[str, Any], plan: dict[str, Any]) -> str:
    assessment_ts = _extract_assessment_timestamp(plan)
    generated_at = assessment_ts or now_iso_z()
    exported_at = now_iso_z()
    plan_fp = get_plan_fingerprint(plan)

    ctx["meta"] = {
        "generated_at": generated_at,
        "template_version": "1.0.0",
        "exported_at": exported_at,
    }
    ctx["assessment"] = _merge_mapping(ctx.get("assessment"), {"timestamp": generated_at})
    ctx["plan"] = _merge_mapping(ctx.get("plan"), {"fingerprint": plan_fp})
    return generated_at


def _ensure_mapping(ctx: dict[str, Any], key: str) -> None:
    if not isinstance(ctx.get(key), dict):
        ctx[key] = {}


def _ensure_metrics_defaults(ctx: dict[str, Any]) -> None:
    if not isinstance(ctx.get("metrics"), dict):
        ctx["metrics"] = {}
    ctx.setdefault("artifact_coverage_by_article", {})
    ctx.setdefault("artifact_coverage_pct", {})
    # Mirror artifact coverage maps under metrics for convenience
    ctx["metrics"].setdefault("artifact_coverage_by_article", {})
    ctx["metrics"].setdefault("artifact_coverage_pct", {})


def _ensure_system_defaults(ctx: dict[str, Any]) -> None:
    _ensure_mapping(ctx, "system")
    ctx["system"].setdefault("version", "v0")
    _ensure_mapping(ctx, "roles")


def _ensure_plan_defaults(ctx: dict[str, Any], plan: dict[str, Any]) -> None:
    ctx.setdefault("plan_raw", deepcopy(plan) if isinstance(plan, dict) else {})
    trace = _as_mapping(ctx.get("trace"))
    plan_meta = _as_mapping(ctx.get("plan"))
    plan_fp = plan_meta.get("fingerprint", "")
    plan_hash = trace.get("plan_hash") or plan_fp or "—"
    ctx.setdefault("plan_hash", plan_hash)
    system_meta = _as_mapping(ctx.get("system"))
    ctx.setdefault("system_name", system_meta.get("name", ""))
    if ctx.get("system_profile") is None:
        ctx["system_profile"] = system_meta


def _ensure_party_defaults(ctx: dict[str, Any]) -> None:
    _ensure_mapping(ctx, "provider")
    _ensure_mapping(ctx, "declared_by")


def _ensure_misc_defaults(ctx: dict[str, Any]) -> None:
    ctx.setdefault("context", None)
    ctx.setdefault("predicates", [])
    ctx.setdefault("gaps", [])
    ctx.setdefault("omissions", [])
    ctx.setdefault("evidence_map", {})
    ctx.setdefault("regulatory_version", {})
    ctx.setdefault("regulatory_warnings", [])
    ctx.setdefault("gpg_key_id", "")
    ctx.setdefault("gpg_signature_available", False)
    ctx.setdefault("flag_to_articles", ctx.get("flag_article_index", {}))
    ctx.setdefault("coverage_operational_by_article", {})
    ctx.setdefault("missing_reasons_by_article", {})
    ctx.setdefault("calendar_events", [])
    ctx.setdefault("sources", [])


def _inject_demo_mode(ctx: dict[str, Any], plan: dict[str, Any]) -> None:
    import os

    demo_env = os.environ.get("IPE_DEMO_MODE") or os.environ.get("LEXOPS_DEMO_MODE", "")
    demo_mode = demo_env.lower() in ("1", "true", "yes")
    plan_demo_mode = plan.get("demo_mode")
    if isinstance(plan_demo_mode, bool):
        demo_mode = plan_demo_mode
    ctx["demo_mode"] = demo_mode


def build_base_context(
    plan: dict[str, Any], *, extra_metrics: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a baseline context shared by exporters and artifact renderers.

    Responsibilities:
    - Ensure metrics mapping exists and expose coverage_* keys under coverage_metrics
    - Add meta (generated_at, template_version)
    - Add assessment.timestamp
    - Add plan.fingerprint (deterministic over input plan)
    - Provide safe defaults for artifact coverage maps
    """
    if not isinstance(plan, dict):
        plan = {}
    # Use a deep copy to avoid mutating the original plan via nested dicts
    ctx: dict[str, Any] = deepcopy(plan)

    # Metrics normalization
    metrics = _normalize_metrics(ctx, extra_metrics)

    # Inject TechDoc Stats (P0 Fix: Logic Leak)
    ctx["techdoc_stats"] = calculate_techdoc_stats(plan)

    # Flatten metrics keys for template convenience
    _flatten_metrics_into_context(ctx, metrics)

    # Collect coverage_* keys from both top-level and metrics
    ctx["coverage_metrics"] = _collect_coverage_metrics(ctx, metrics)

    # Meta and fingerprint (deterministic on the provided plan, not enriched ctx)
    _inject_meta_and_fingerprint(ctx, plan)

    # Safe defaults expected by templates
    _ensure_metrics_defaults(ctx)

    # Common defaults used by exporters/renderers
    ctx.setdefault("evidence_quality_summary", {"ready": 0, "draft": 0, "placeholder": 0})
    ctx.setdefault("national_context", None)

    # Minimal shared defaults for renderers
    _ensure_system_defaults(ctx)
    _ensure_plan_defaults(ctx, plan)
    _ensure_party_defaults(ctx)
    _ensure_misc_defaults(ctx)

    # Red Team Fix (CEO): Inject demo_mode flag from environment or plan
    # This allows demos to show clean "Versión Final" instead of "BORRADOR PRELIMINAR"
    _inject_demo_mode(ctx, plan)

    return ctx


def _ensure_minimal_artifact_objects(ctx: dict[str, Any]) -> None:
    """Ensure nested mappings exist so templates can safely dereference attributes."""
    for key in (
        "system",
        "roles",
        "provider",
        "review",
        "doc",
        "incident",
        "org",
        "scope",
        "contacts",
        "decision",
        "code",
        "dpia",
        "notice",
        "model",
        "risk",
    ):
        if not isinstance(ctx.get(key), dict):
            ctx[key] = {}


def _ensure_dict_block(ctx: dict[str, Any], key: str) -> dict[str, Any]:
    """Ensure `ctx[key]` is a dict and return it."""
    block = ctx.get(key)
    if not isinstance(block, dict):
        block = {}
        ctx[key] = block
    return block


def _context_base_date(ctx: dict[str, Any]) -> str:
    """Resolve the base date used by context defaults."""
    base_ts = ctx.get("assessment", {}).get("timestamp") or ctx.get("meta", {}).get("generated_at")
    if base_ts:
        try:
            return datetime.fromisoformat(base_ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            pass
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _apply_defaults(
    target: dict[str, Any], source: dict[str, Any], defaults: dict[str, Any]
) -> None:
    """Apply `setdefault()` values from a YAML block using a key->fallback map."""
    for key, fallback in defaults.items():
        target.setdefault(key, source.get(key, fallback))


def _apply_provider_and_system_defaults(
    ctx: dict[str, Any], defaults: dict[str, Any], base_date: str
) -> None:
    """Apply provider, system and document identity defaults."""
    prov_d = defaults.get("provider", {})
    provider = _ensure_dict_block(ctx, "provider")
    _apply_defaults(
        provider,
        prov_d,
        {
            "name": "",
            "address": "",
            "representative": "",
            "contact": "",
        },
    )

    sys_d = defaults.get("system", {})
    system = _ensure_dict_block(ctx, "system")
    _apply_defaults(
        system,
        sys_d,
        {
            "name": "",
            "version": "v0",
            "type": "",
            "intended_purpose": "",
            "risk_category": "",
            "classification_category": "",
            "standards": "",
            "stakeholders": "",
            "interfaces": "",
            "dependencies": "",
            "performance": "",
            "limitations": "",
        },
    )
    # Use plan fingerprint for deterministic ID (reproducibility)
    plan_fp = ctx.get("plan", {}).get("fingerprint", "")
    system_id = f"SYS-{plan_fp[:8].upper()}" if plan_fp else ""
    system.setdefault("id", system_id)
    system.setdefault("description", system.get("intended_purpose") or sys_d.get("description", ""))

    doc_d = defaults.get("doc", {})
    ctx.setdefault("date", base_date)
    ctx.setdefault("author_name", doc_d.get("author_name", ""))
    ctx.setdefault("approver_name", doc_d.get("approver_name", ""))


def _apply_governance_defaults(
    ctx: dict[str, Any], defaults: dict[str, Any], base_date: str
) -> None:
    """Apply governance and policy defaults."""
    conf_d = defaults.get("routing", {})
    routing = _ensure_dict_block(ctx, "routing")
    _apply_defaults(
        routing,
        conf_d,
        {
            "route": "",
            "notified_body": "",
            "other_acts": "",
            "place": "",
            "signatory": "",
        },
    )
    routing.setdefault("date", base_date)
    routing.setdefault("safety_override", False)
    routing.setdefault("override_flags", [])
    routing.setdefault("alternative_route", None)

    dpia_d = defaults.get("dpia", {})
    dpia = _ensure_dict_block(ctx, "dpia")
    _apply_defaults(dpia, dpia_d, {"id": ""})
    dpia.setdefault("last_review_date", base_date)

    scope_d = defaults.get("scope", {})
    scope = _ensure_dict_block(ctx, "scope")
    _apply_defaults(scope, scope_d, {"versions": "", "components": ""})

    notice_d = defaults.get("notice", {})
    notice = _ensure_dict_block(ctx, "notice")
    _apply_defaults(notice, notice_d, {"version": ""})

    risk_d = defaults.get("risk", {})
    risk = _ensure_dict_block(ctx, "risk")
    _apply_defaults(risk, risk_d, {"section_category": ""})

    deploy_d = defaults.get("deployment", {})
    deployment = _ensure_dict_block(ctx, "deployment")
    _apply_defaults(
        deployment,
        deploy_d,
        {"context": "", "environments": "", "installation": ""},
    )

    roles_d = defaults.get("roles", {})
    roles = _ensure_dict_block(ctx, "roles")
    _apply_defaults(
        roles,
        roles_d,
        {
            "owner": "",
            "risk_manager": "",
            "dpo": "",
            "oversight": "",
            "active": "deployer",
        },
    )


def _apply_compliance_workflow_defaults(
    ctx: dict[str, Any], defaults: dict[str, Any], base_date: str
) -> None:
    """Apply approval, RMP and supporting process defaults."""
    legal_d = defaults.get("legal", {})
    legal = _ensure_dict_block(ctx, "legal")
    _apply_defaults(legal, legal_d, {"scope": "", "articles": [], "sections": []})

    rmp_d = defaults.get("rmp", {})
    rmp = _ensure_dict_block(ctx, "rmp")
    methodology = _ensure_dict_block(rmp, "methodology")
    _apply_defaults(
        methodology,
        rmp_d.get("methodology", {}),
        {"hazard": "", "scoring": ""},
    )
    rmp.setdefault("criteria", rmp_d.get("criteria", ""))
    controls = _ensure_dict_block(rmp, "controls")
    _apply_defaults(
        controls,
        rmp_d.get("controls", {}),
        {"preventive": "", "detective": ""},
    )
    rmp.setdefault("verification", rmp_d.get("verification", ""))

    approvals_d = defaults.get("approvals", {})
    approvals = _ensure_dict_block(ctx, "approvals")
    _apply_defaults(
        approvals,
        approvals_d,
        {"by": "", "justification": "", "status": "pending"},
    )
    approvals.setdefault("date", base_date)

    declared_by_d = defaults.get("declared_by", {})
    declared_by = _ensure_dict_block(ctx, "declared_by")
    _apply_defaults(
        declared_by,
        declared_by_d,
        {"name": "", "organization": "", "email": ""},
    )
    declared_date_default = declared_by_d.get("date", base_date)
    if declared_date_default is None:
        declared_date_default = base_date
    declared_by.setdefault("date", declared_date_default)

    pmm_d = defaults.get("pmm", {})
    pmm = _ensure_dict_block(ctx, "pmm")
    _apply_defaults(pmm, pmm_d, {"kpis": "", "frequency": "", "feedback": ""})

    data_d = defaults.get("data", {})
    data = _ensure_dict_block(ctx, "data")
    _apply_defaults(data, data_d, {"inputs": "", "pii": ""})

    use_d = defaults.get("use", {})
    use = _ensure_dict_block(ctx, "use")
    _apply_defaults(use, use_d, {"tasks": ""})

    oversight_d = defaults.get("oversight", {})
    oversight = _ensure_dict_block(ctx, "oversight")
    _apply_defaults(oversight, oversight_d, {"checkpoints": ""})

    warnings_d = defaults.get("warnings", {})
    warnings_block = _ensure_dict_block(ctx, "warnings")
    _apply_defaults(warnings_block, warnings_d, {"contraindications": ""})

    safety_d = defaults.get("safety", {})
    safety = _ensure_dict_block(ctx, "safety")
    _apply_defaults(safety, safety_d, {"mitigations": ""})

    logging_d = defaults.get("logging", {})
    logging_block = _ensure_dict_block(ctx, "logging")
    _apply_defaults(logging_block, logging_d, {"events": "", "retention": ""})

    maintenance_d = defaults.get("maintenance", {})
    maintenance = _ensure_dict_block(ctx, "maintenance")
    _apply_defaults(maintenance, maintenance_d, {"update_policy": "", "change_control": ""})


def _apply_operational_defaults(
    ctx: dict[str, Any], defaults: dict[str, Any], base_date: str
) -> None:
    """Apply operational, incident and publication defaults."""
    incidents_d = defaults.get("incidents", {})
    incidents = _ensure_dict_block(ctx, "incidents")
    _apply_defaults(
        incidents,
        incidents_d,
        {"report_channel": "", "sla": ""},
    )

    labeling_d = defaults.get("labeling", {})
    labeling = _ensure_dict_block(ctx, "labeling")
    labeling.setdefault("last_review", base_date)
    labeling.setdefault("prefix", labeling_d.get("prefix", "[AI] "))

    content_d = defaults.get("content", {})
    content = _ensure_dict_block(ctx, "content")
    _apply_defaults(content, content_d, {"title": ""})

    wcag_d = defaults.get("wcag", {})
    wcag = _ensure_dict_block(ctx, "wcag")
    wcag.setdefault("date", base_date)
    wcag.setdefault("tools", wcag_d.get("tools", ""))
    for key in (
        "notes_111",
        "notes_131",
        "notes_143",
        "notes_1410",
        "notes_211",
        "notes_243",
        "notes_247",
        "notes_311",
        "notes_324",
        "notes_331",
        "notes_411",
        "notes_412",
    ):
        wcag.setdefault(key, wcag_d.get(key, ""))


def _apply_engine_defaults(ctx: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Apply engine metadata defaults and static disclaimers."""
    eng_d = defaults.get("engine", {})
    engine = _ensure_dict_block(ctx, "engine")
    engine.setdefault("version", eng_d.get("version", CANONICAL_ENGINE_VERSION))
    # Use the real git commit for reproducibility.
    engine.setdefault("commit", _get_git_commit())
    engine.setdefault("name", eng_d.get("name", CANONICAL_ENGINE_NAME))

    ctx.setdefault("disclaimer_version", defaults.get("disclaimer_version", "1.1"))


def _apply_defaults_from_yaml(ctx: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Apply all string defaults from YAML to context.

    This is the central place where presentation strings are injected.
    No hardcoded strings should exist outside this function.

    For reproducibility, date defaults use the assessment timestamp from the plan
    rather than datetime.now(), ensuring same inputs produce same outputs.
    """
    base_date = _context_base_date(ctx)
    _apply_provider_and_system_defaults(ctx, defaults, base_date)
    _apply_governance_defaults(ctx, defaults, base_date)
    _apply_compliance_workflow_defaults(ctx, defaults, base_date)
    _apply_operational_defaults(ctx, defaults, base_date)
    _apply_engine_defaults(ctx, defaults)

    # Store base_date in context for other default functions to use
    ctx["_base_date"] = base_date


def _apply_incident_defaults(ctx: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Apply incident-related defaults from YAML."""
    inc_d = defaults.get("incident", {})
    base_date = ctx.get("_base_date") or datetime.now(UTC).strftime("%Y-%m-%d")

    # Top-level incident fields
    ctx["incident"].setdefault("severity", inc_d.get("severity", ""))
    ctx["incident"].setdefault("rationale", inc_d.get("rationale", ""))
    ctx["incident"].setdefault("type", "")
    ctx["incident"].setdefault("death", False)

    # --- incident.system ---
    if not isinstance(ctx["incident"].get("system"), dict):
        ctx["incident"]["system"] = {}
    inc_sys = ctx["incident"]["system"]
    inc_sys_d = inc_d.get("system", {})
    inc_sys.setdefault("system_name", ctx["system"].get("name", ""))
    inc_sys.setdefault("system_version", ctx["system"].get("version", ""))
    inc_sys.setdefault("system_description", ctx["system"].get("intended_purpose", ""))
    inc_sys.setdefault("system_model_reference", inc_sys_d.get("system_model_reference", ""))
    inc_sys.setdefault("serial_or_batch_number", inc_sys_d.get("serial_or_batch_number", ""))
    inc_sys.setdefault("software_version", inc_sys_d.get("software_version", ""))
    inc_sys.setdefault("firmware_version", inc_sys_d.get("firmware_version", ""))
    inc_sys.setdefault("classification_category", ctx["system"].get("classification_category", ""))
    inc_sys.setdefault("eu_db_registration_id", inc_sys_d.get("eu_db_registration_id", ""))

    # --- incident.incident ---
    if not isinstance(ctx["incident"].get("incident"), dict):
        ctx["incident"]["incident"] = {}
    inc_inc = ctx["incident"]["incident"]
    inc_inc_d = inc_d.get("incident", {})
    inc_inc.setdefault("incident_start_date", base_date)
    inc_inc.setdefault("incident_end_date", inc_inc_d.get("incident_end_date", ""))
    inc_inc.setdefault("date_uncertainty_notes", inc_inc_d.get("date_uncertainty_notes", ""))
    inc_inc.setdefault("incident_narrative", inc_inc_d.get("incident_narrative", ""))
    inc_inc.setdefault("short_title", inc_inc_d.get("short_title", ""))
    inc_inc.setdefault("estimated_users_affected", inc_inc_d.get("estimated_users_affected", ""))
    inc_inc.setdefault("operator_type_details", inc_inc_d.get("operator_type_details", ""))
    inc_inc.setdefault("remedial_actions_taken", inc_inc_d.get("remedial_actions_taken", ""))
    inc_inc.setdefault("detection_datetime", f"{base_date}T00:00:00Z")

    # --- incident.impact ---
    if not isinstance(ctx["incident"].get("impact"), dict):
        ctx["incident"]["impact"] = {}
    imp_d = inc_d.get("impact", {})
    ctx["incident"]["impact"].setdefault("harm_description", imp_d.get("harm_description", ""))
    ctx["incident"]["impact"].setdefault(
        "victims_or_affected_groups", imp_d.get("victims_or_affected_groups", "")
    )
    ctx["incident"]["impact"].setdefault("near_misses", imp_d.get("near_misses", ""))

    # --- incident.evidence ---
    if not isinstance(ctx["incident"].get("evidence"), dict):
        ctx["incident"]["evidence"] = {}
    ev_d = inc_d.get("evidence", {})
    ctx["incident"]["evidence"].setdefault("description", ev_d.get("description", ""))
    ctx["incident"]["evidence"].setdefault("logs_location", ev_d.get("logs_location", ""))
    ctx["incident"]["evidence"].setdefault("datasets_location", ev_d.get("datasets_location", ""))
    ctx["incident"]["evidence"].setdefault(
        "screenshots_location", ev_d.get("screenshots_location", "")
    )

    # --- incident.response ---
    if not isinstance(ctx["incident"].get("response"), dict):
        ctx["incident"]["response"] = {}
    resp_d = inc_d.get("response", {})
    ctx["incident"]["response"].setdefault(
        "corrective_preventive_actions", resp_d.get("corrective_preventive_actions", "")
    )
    ctx["incident"]["response"].setdefault(
        "provider_recommendations_to_authorities",
        resp_d.get("provider_recommendations_to_authorities", ""),
    )

    # --- incident.cross_reporting ---
    if not isinstance(ctx["incident"].get("cross_reporting"), dict):
        ctx["incident"]["cross_reporting"] = {}
    cr_d = inc_d.get("cross_reporting", {})
    ctx["incident"]["cross_reporting"].setdefault(
        "already_reported_under", cr_d.get("already_reported_under", "")
    )
    ctx["incident"]["cross_reporting"].setdefault(
        "alignment_notes", cr_d.get("alignment_notes", "")
    )

    # --- incident.analysis ---
    if not isinstance(ctx["incident"].get("analysis"), dict):
        ctx["incident"]["analysis"] = {}
    an_d = inc_d.get("analysis", {})
    ctx["incident"]["analysis"].setdefault(
        "root_cause_analysis", an_d.get("root_cause_analysis", "")
    )
    ctx["incident"]["analysis"].setdefault(
        "planned_investigations", an_d.get("planned_investigations", "")
    )

    # --- incident.initial_reporter ---
    if not isinstance(ctx["incident"].get("initial_reporter"), dict):
        ctx["incident"]["initial_reporter"] = {}
    ir_d = inc_d.get("initial_reporter", {})
    for k in ("name", "email", "phone", "role", "address"):
        ctx["incident"]["initial_reporter"].setdefault(k, ir_d.get(k, ""))

    # --- incident.submitter ---
    if not isinstance(ctx["incident"].get("submitter"), dict):
        ctx["incident"]["submitter"] = {}
    sub_d = inc_d.get("submitter", {})
    for k in (
        "other_role",
        "provider_org_name",
        "provider_contact_first_name",
        "provider_contact_last_name",
        "provider_contact_email",
        "provider_contact_phone",
        "provider_address_line_1",
        "provider_address_line_2",
        "provider_address_postal_code",
        "provider_address_city",
        "provider_address_country",
    ):
        ctx["incident"]["submitter"].setdefault(k, sub_d.get(k, ""))

    # --- incident.admin ---
    if not isinstance(ctx["incident"].get("admin"), dict):
        ctx["incident"]["admin"] = {}
    adm_d = inc_d.get("admin", {})
    ctx["incident"]["admin"].setdefault("report_submission_date", base_date)
    ctx["incident"]["admin"].setdefault("internal_reference", adm_d.get("internal_reference", ""))
    ctx["incident"]["admin"].setdefault("msa_country", adm_d.get("msa_country", ""))
    ctx["incident"]["admin"].setdefault("msa_name", adm_d.get("msa_name", ""))
    ctx["incident"]["admin"].setdefault("msa_reference", adm_d.get("msa_reference", ""))
    ctx["incident"]["admin"].setdefault("provider_awareness_date", base_date)
    ctx["incident"]["admin"].setdefault(
        "next_report_due_date", adm_d.get("next_report_due_date", "")
    )
    ctx["incident"]["admin"].setdefault("report_type", adm_d.get("report_type", ""))
    ctx["incident"]["admin"].setdefault(
        "other_classification_details", adm_d.get("other_classification_details", "")
    )

    # --- incident.meta ---
    if not isinstance(ctx["incident"].get("meta"), dict):
        ctx["incident"]["meta"] = {}
    meta_d = inc_d.get("meta", {})
    ctx["incident"]["meta"].setdefault("signatory_name", meta_d.get("signatory_name", ""))
    ctx["incident"]["meta"].setdefault("signatory_role", meta_d.get("signatory_role", ""))
    ctx["incident"]["meta"].setdefault("signatory_date", base_date)


def _apply_auxiliary_blocks(ctx: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Apply timeline, authority, detection, triage, remediation, evidence defaults."""
    base_date = ctx.get("_base_date") or datetime.now(UTC).strftime("%Y-%m-%d")

    # Calculate report due date (+14 days from base)
    try:
        base_dt = datetime.strptime(base_date, "%Y-%m-%d")
        report_due = (base_dt + timedelta(days=14)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        report_due = (datetime.now(UTC) + timedelta(days=14)).strftime("%Y-%m-%d")

    # --- Timeline ---
    tl_d = defaults.get("timeline", {})
    if not isinstance(ctx.get("timeline"), dict):
        ctx["timeline"] = {}
    ctx["timeline"].setdefault("initial_report", tl_d.get("initial_report", ""))
    ctx["timeline"].setdefault("full_report_due", report_due)

    # --- Authority ---
    auth_d = defaults.get("authority", {})
    if not isinstance(ctx.get("authority"), dict):
        ctx["authority"] = {}
    ctx["authority"].setdefault("contacts", auth_d.get("contacts", ""))
    ctx["authority"].setdefault("channel", auth_d.get("channel", ""))

    # --- Detection ---
    det_d = defaults.get("detection", {})
    if not isinstance(ctx.get("detection"), dict):
        ctx["detection"] = {}
    ctx["detection"].setdefault("channels", det_d.get("channels", ""))
    ctx["detection"].setdefault("validation", det_d.get("validation", ""))

    # --- Triage ---
    tri_d = defaults.get("triage", {})
    if not isinstance(ctx.get("triage"), dict):
        ctx["triage"] = {}
    ctx["triage"].setdefault("causal_established", tri_d.get("causal_established", ""))
    ctx["triage"].setdefault("reasonable_likelihood", tri_d.get("reasonable_likelihood", ""))

    # --- Remediation ---
    rem_d = defaults.get("remediation", {})
    if not isinstance(ctx.get("remediation"), dict):
        ctx["remediation"] = {}
    ctx["remediation"].setdefault("immediate", rem_d.get("immediate", ""))
    ctx["remediation"].setdefault("safeguards", rem_d.get("safeguards", ""))
    ctx["remediation"].setdefault("comms", rem_d.get("comms", ""))

    # --- Evidence (chain of custody) ---
    ev_d = defaults.get("evidence", {})
    if not isinstance(ctx.get("evidence"), dict):
        ctx["evidence"] = {}
    ctx["evidence"].setdefault("coc", ev_d.get("coc", ""))
    ctx["evidence"].setdefault("retention", ev_d.get("retention", ""))
    ctx["evidence"].setdefault("repo", ev_d.get("repo", ""))

    # --- Engine ---
    eng_d = defaults.get("engine", {})
    if not isinstance(ctx.get("engine"), dict):
        ctx["engine"] = {}
    ctx["engine"].setdefault("version", eng_d.get("version", CANONICAL_ENGINE_VERSION))
    # Red Team Fix (Fase 3): Use real git commit for reproducibility
    ctx["engine"].setdefault("commit", _get_git_commit())
    ctx["engine"].setdefault("name", eng_d.get("name", CANONICAL_ENGINE_NAME))

    # --- Disclaimer ---
    ctx.setdefault("disclaimer_version", defaults.get("disclaimer_version", "1.1"))


def build_artifact_context(
    plan: dict[str, Any],
    *,
    strict: bool = True,
    framework_path: Path | None = None,
    extra_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extended context for artifact rendering (opt-in for templates).

    Starts from build_base_context and adds common blocks and human-friendly defaults
    expected by evidence and guidance templates.

    All presentation strings come from context_defaults.yml.

    Args:
        plan: Assessment plan dictionary (Plan TypedDict).
        strict: If True, fail on missing template variables.
        framework_path: Optional path to framework directory for loading context defaults.
                        If None, only structural fallback defaults are used.
        extra_metrics: Optional additional metrics to include in context.

    Returns:
        Complete template context dictionary with all nested blocks initialized.

    Example:
        >>> plan = {"flags": ["flag.provider"], "actions": ["action.1"]}
        >>> ctx = build_artifact_context(plan, strict=False)
        >>> "flags" in ctx
        True
        >>> "scope" in ctx
        True
    """
    ctx = build_base_context(plan if isinstance(plan, dict) else {}, extra_metrics=extra_metrics)
    _ensure_artifact_context_sections(ctx)
    _apply_artifact_yaml_defaults(ctx, framework_path)
    _normalize_artifact_actions(ctx)
    _apply_artifact_identity_context(ctx)
    _apply_artifact_rendering_context(ctx, framework_path)
    _apply_artifact_backlog_context(ctx, framework_path)
    return ctx


def get_classification_display(outcome: str | list | None) -> dict[str, Any]:
    """Return display properties for outcome classification.

    Centralize classification display logic.
    The ContextBuilder (Python) must calculate final_outcome display properties.
    Templates should only render variables, never calculate them.

    Args:
        outcome: Plan outcome string (e.g., 'review', 'blocked', 'model_systemic')
                 Can also be a list (legacy format) - takes first element.

    Returns:
        Dict with:
        - emoji: Visual indicator (deprecated; kept for older rendered outputs)
        - label: Localized label in Spanish
        - label_en: English label for i18n
        - css_class: CSS class for styling (danger, warning, info, success)
        - severity: Numeric severity (1=blocked, 2=review, 3=model, 4=limited)
    """
    # Handle list outcome (legacy format) - take first element
    if isinstance(outcome, list):
        outcome = outcome[0] if outcome else ""
    outcome_lower = (outcome or "").lower()

    # Order matters: check most severe first
    if "blocked" in outcome_lower:
        return {
            "emoji": "",
            "label": "BLOCKED",
            "label_en": "BLOCKED",
            "css_class": "danger",
            "severity": 1,
        }
    elif "review" in outcome_lower or "review" in outcome_lower:
        return {
            "emoji": "",
            "label": "REVIEW",
            "label_en": "REVIEW",
            "css_class": "warning",
            "severity": 2,
        }
    elif "model" in outcome_lower:
        # Distinguish systemic vs non-systemic MODEL
        if "systemic" in outcome_lower:
            return {
                "emoji": "",
                "label": "MODEL SISTÉMICO",
                "label_en": "MODEL SYSTEMIC",
                "css_class": "warning",
                "severity": 2,
            }
        return {
            "emoji": "",
            "label": "MODEL",
            "label_en": "MODEL",
            "css_class": "info",
            "severity": 3,
        }
    elif "limited" in outcome_lower or "transparency" in outcome_lower:
        return {
            "emoji": "",
            "label": "RIESGO LIMITADO",
            "label_en": "LIMITED RISK",
            "css_class": "info",
            "severity": 4,
        }
    else:
        # Default: minimal risk or unknown
        return {
            "emoji": "",
            "label": "RIESGO MÍNIMO",
            "label_en": "MINIMAL RISK",
            "css_class": "success",
            "severity": 5,
        }


def get_role_display(active_role: str) -> dict[str, Any]:
    """Return display properties for active role.

    Templates should use pre-computed values, not inline conditionals.

    Args:
        active_role: Role string (e.g., 'provider', 'deployer')

    Returns:
        Dict with:
        - label: Localized label in Spanish
        - label_en: English label
    """
    role_map = {
        "deployer": {"label": "Operator", "label_en": "Operator"},
        "provider": {"label": "Source", "label_en": "Source"},
        "importer": {"label": "Importador", "label_en": "Importer"},
        "distributor": {"label": "Distribuidor", "label_en": "Distributor"},
        "authorized_representative": {
            "label": "Representante Autorizado",
            "label_en": "Authorized Representative",
        },
    }
    role_lower = (active_role or "").lower()
    if role_lower in role_map:
        return role_map[role_lower]
    # Default: capitalize the role
    return {
        "label": (active_role or "Desconocido").title(),
        "label_en": (active_role or "Unknown").title(),
    }


def _compute_deadline_metrics(due_hints: dict[str, str], generated_at: str) -> dict[str, Any]:
    """Compute deadline-related metrics for executive templates.

    Expose computed deadline metrics so templates
    don't need to calculate inline. Enables executive semaphore dashboards.

    Args:
        due_hints: Dict mapping action_id -> due_date (ISO format or '—')
        generated_at: Assessment timestamp (ISO format)

    Returns:
        Dict with:
        - overdue_count: Number of dates already passed
        - overdue_dates: Sorted list of passed dates
        - future_dates: Sorted list of upcoming dates
        - next_deadline: Earliest upcoming date or None
        - total_deadlines: Total count of valid dates
    """
    today = generated_at[:10] if generated_at else "9999-12-31"
    overdue: list[str] = []
    future: list[str] = []

    for date in due_hints.values():
        if date and isinstance(date, str) and date != "—" and len(date) >= 10:
            date_str = date[:10]  # Normalize to YYYY-MM-DD
            if date_str < today:
                overdue.append(date_str)
            else:
                future.append(date_str)

    overdue_sorted = sorted(set(overdue))
    future_sorted = sorted(set(future))

    return {
        "overdue_count": len(overdue_sorted),
        "overdue_dates": overdue_sorted,
        "future_dates": future_sorted,
        "next_deadline": future_sorted[0] if future_sorted else None,
        "oldest_overdue": overdue_sorted[0] if overdue_sorted else None,
        "total_deadlines": len(overdue_sorted) + len(future_sorted),
    }


def _compute_techdoc_stats(ctx: dict[str, Any]) -> dict[str, int]:
    """Compute technical documentation checklist statistics.

    Move checklist completeness logic from templates to context.
    Addresses: "total_items = 15" magic number in techdoc_checklist.md.j2 (L87)

    The template is computed dynamically from
    actions_meta filtered by documentation-related keywords.

    Args:
        ctx: Context dictionary with actions_meta and evidence data

    Returns:
        Dict with: total, completed, pending, progress_pct
    """
    actions_meta = ctx.get("actions_meta", []) or []
    evidence_map = ctx.get("actions_evidence_map", {}) or {}

    # Filter actions related to technical documentation
    techdoc_keywords = {"DOC", "SECTION", "TECHDOC", "DOCUMENTATION"}

    techdoc_actions = []
    for action in actions_meta:
        if not isinstance(action, dict):
            continue
        aid = action.get("id", "") or ""
        articles = action.get("articles", []) or []

        # Match by action ID containing techdoc keywords
        is_techdoc = any(kw in aid.upper() for kw in techdoc_keywords)

        # Or by linked source topics/sections.
        doc_articles = {"TOPIC-11", "SECTION-IV", "SECTION-XI", "TOPIC-18"}
        has_doc_article = any(art in doc_articles for art in articles)

        if is_techdoc or has_doc_article:
            techdoc_actions.append(action)

    # Fallback: if no techdoc actions found, use default count
    total = len(techdoc_actions) if techdoc_actions else 15

    # Count completed actions (those with evidence paths)
    completed = 0
    for action in techdoc_actions:
        aid = action.get("id", "")
        if aid and evidence_map.get(aid):
            completed += 1

    pending = max(0, total - completed)
    progress_pct = round((completed / max(1, total)) * 100)

    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "progress_pct": progress_pct,
    }


def _build_evidence_paths_lookup(ctx: dict[str, Any]) -> dict[str, list[str]]:
    """Build article -> evidence paths lookup from actions_evidence_map.

    Decoupled templates from physical file paths.

    Args:
        ctx: Context with actions_meta and actions_evidence_map

    Returns:
        Dict mapping Article ID (e.g. 'TOPIC-9') to list of evidence relative paths.
    """
    ev_map = ctx.get("actions_evidence_map", {}) or {}
    actions_meta = ctx.get("actions_meta", []) or []

    article_paths: dict[str, list[str]] = {}

    for action in actions_meta:
        if not isinstance(action, dict):
            continue
        aid = action.get("id", "")
        articles = action.get("articles", []) or []
        paths = ev_map.get(aid, []) or []

        if not paths:
            continue

        for art in articles:
            if art not in article_paths:
                article_paths[art] = []
            # Add unique paths to avoid duplicates if multiple actions point to same file
            for p in paths:
                if p not in article_paths[art]:
                    article_paths[art].append(p)

    return article_paths


def _ensure_artifact_context_sections(ctx: dict[str, Any]) -> None:
    """Ensure the artifact-rendering context has all required namespaces."""
    _ensure_minimal_artifact_objects(ctx)

    for key in ("detection", "triage", "timeline", "remediation", "authority", "evidence"):
        _ensure_dict_block(ctx, key)

    cfg = ctx.get("config")
    if isinstance(cfg, dict):
        cfg_keys_raw = cfg.get("keys")
        cfg_keys = cfg_keys_raw if isinstance(cfg_keys_raw, dict) else {}
        ctx["config"] = SimpleNamespace(keys=cfg_keys)
    elif cfg is not None and hasattr(cfg, "keys"):
        if not isinstance(getattr(cfg, "keys", None), dict):
            ctx["config"] = SimpleNamespace(keys={})
    else:
        ctx["config"] = SimpleNamespace(keys={})

    _ensure_dict_block(ctx, "observability")
    _ensure_dict_block(ctx, "security")


def _apply_artifact_yaml_defaults(ctx: dict[str, Any], framework_path: Path | None) -> None:
    """Load and apply YAML-backed defaults for the artifact context."""
    defaults = load_context_defaults(framework_path)
    _apply_defaults_from_yaml(ctx, defaults)
    _apply_incident_defaults(ctx, defaults)
    _apply_auxiliary_blocks(ctx, defaults)


def _normalize_artifact_actions(ctx: dict[str, Any]) -> None:
    """Normalize actions/actions_meta so templates receive a stable shape."""
    raw_actions = ctx.get("actions")

    if isinstance(raw_actions, list) and raw_actions and not isinstance(raw_actions[0], dict):
        meta_by_id: dict[str, dict[str, Any]] = {}
        actions_meta = ctx.get("actions_meta") or []
        if isinstance(actions_meta, list):
            for entry in actions_meta:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    meta_by_id[entry["id"]] = entry

        normalized_actions: list[dict[str, Any]] = []
        for aid in raw_actions:
            if not isinstance(aid, str):
                continue
            meta = meta_by_id.get(aid, {})
            title = meta.get("title", aid) if isinstance(meta, dict) else aid
            priority = meta.get("priority", "medium") if isinstance(meta, dict) else "medium"
            applies_to = meta.get("applies_to", "any") if isinstance(meta, dict) else "any"
            triggered_by = meta.get("when", "—") if isinstance(meta, dict) else "—"
            description = meta.get("description", "") if isinstance(meta, dict) else ""
            normalized_actions.append(
                {
                    "id": aid,
                    "title": title,
                    "priority": priority,
                    "applies_to": applies_to,
                    "action": title,
                    "name": title,
                    "articles": (meta.get("articles") if isinstance(meta, dict) else []) or [],
                    "triggered_by": triggered_by,
                    "trigger": triggered_by,
                    "description": description,
                }
            )

        ctx["actions"] = normalized_actions
    elif not isinstance(raw_actions, list):
        ctx["actions"] = []

    if not isinstance(ctx.get("actions_meta"), list):
        ctx["actions_meta"] = []

    enriched_actions_meta: list[Any] = []
    for meta in ctx.get("actions_meta", []):
        if isinstance(meta, dict):
            enriched = dict(meta)
            when_clause = enriched.get("when", "—")
            if "trigger" not in enriched:
                enriched["trigger"] = when_clause
            if "triggered_by" not in enriched:
                enriched["triggered_by"] = when_clause
            enriched_actions_meta.append(enriched)
        else:
            enriched_actions_meta.append(meta)
    ctx["actions_meta"] = enriched_actions_meta
    ctx["selected_actions"] = ctx["actions_meta"]


def _apply_artifact_identity_context(ctx: dict[str, Any]) -> None:
    """Apply derived identity fields and audit metadata."""
    if not isinstance(ctx.get("due_hints"), dict):
        ctx["due_hints"] = {}
    ctx.setdefault("hitl_required", 0.0)
    ctx.setdefault("hitl_index", 0.0)

    if not isinstance(ctx.get("calendar_events"), list):
        ctx["calendar_events"] = []
    if not isinstance(ctx.get("flags"), list):
        ctx["flags"] = []
    if not isinstance(ctx.get("sources"), list):
        ctx["sources"] = []

    ctx.setdefault("outcome", "")

    arts_overlay = ctx.get("articles_overlay") or {}
    if isinstance(arts_overlay, dict):
        arts = sorted(
            [k for k in arts_overlay if isinstance(k, str) and not k.startswith("SECTION-")]
        )
        sections = sorted(
            [k for k in arts_overlay if isinstance(k, str) and k.startswith("SECTION-")]
        )
        ctx["legal_refs"] = ctx.get("legal_refs") or {
            "articles": arts,
            "sections": sections,
            "notes": "",
        }

    flags = ctx.get("flags") or []
    high_impact_flags: list[str] = []
    blocked_flags: list[str] = []
    if isinstance(flags, list):
        roles_block = _ensure_dict_block(ctx, "roles")
        has_provider = "role.source" in flags
        has_deployer = "role.operator" in flags

        if has_deployer and not has_provider:
            roles_block["active"] = "deployer"
        elif has_provider and not has_deployer:
            roles_block["active"] = "provider"
        elif has_deployer and has_provider:
            roles_block["active"] = "deployer"

        high_impact_flags = sorted(
            f
            for f in flags
            if isinstance(f, str)
            and f.startswith("classification.")
            and f not in {"classification.not_review", "classification.borderline"}
        )
        blocked_flags = sorted(f for f in flags if isinstance(f, str) and f.startswith("blocked."))

    ctx.setdefault("flags_high_impact", high_impact_flags)
    ctx.setdefault("flags_blocked_active", blocked_flags)

    audit = _ensure_dict_block(ctx, "audit")
    audit.setdefault("plan_sha256", ctx["plan"].get("fingerprint", "—"))
    audit.setdefault("rules_sha256", "—")

    evidence_map = ctx.get("evidence_map")
    if evidence_map and isinstance(evidence_map, dict):
        import hashlib

        evidence_hash = hashlib.sha256(
            json.dumps(evidence_map, sort_keys=True).encode(DEFAULT_ENCODING)
        ).hexdigest()
        audit["evidence_map_sha256"] = evidence_hash
    else:
        audit.setdefault("evidence_map_sha256", "—")

    ctx.setdefault("wizard_answers", ctx.get("answers", {}))


def _apply_artifact_rendering_context(ctx: dict[str, Any], framework_path: Path | None) -> None:
    """Apply rendering-only computed fields used by templates."""
    backlog_keywords = load_backlog_keywords(framework_path)
    ctx["engineering_keywords"] = backlog_keywords.get("engineering", [])
    ctx["governance_keywords"] = backlog_keywords.get("governance", [])

    due_hints = ctx.get("due_hints", {})
    generated_at = ctx.get("meta", {}).get("generated_at", "")
    ctx["deadline_metrics"] = _compute_deadline_metrics(
        due_hints if isinstance(due_hints, dict) else {},
        generated_at if isinstance(generated_at, str) else "",
    )

    eq = ctx.get("evidence_quality_summary", {})
    if isinstance(eq, dict):
        ctx["evidence_ready"] = eq.get("ready", 0)
        ctx["evidence_draft"] = eq.get("draft", 0)
        ctx["evidence_placeholder"] = eq.get("placeholder", 0)
        ctx["evidence_total"] = (
            ctx["evidence_ready"] + ctx["evidence_draft"] + ctx["evidence_placeholder"]
        )

    outcome_str = ctx.get("outcome", "") or ""
    ctx["classification"] = get_classification_display(outcome_str)
    ctx["techdoc_stats"] = _compute_techdoc_stats(ctx)
    ctx["evidence_by_article"] = _build_evidence_paths_lookup(ctx)

    active_role = ctx.get("roles", {}).get("active", "deployer") or "deployer"
    ctx["role_display"] = get_role_display(active_role)
    ctx.pop("_base_date", None)


def _apply_artifact_backlog_context(ctx: dict[str, Any], framework_path: Path | None) -> None:
    """Inject pre-classified backlog views for CSV templates."""
    from src.common.jinja_env import resolve_relative_date
    from src.domain.bundles.view_models import backlog_to_dict, build_backlog_views

    actions_meta = ctx.get("actions_meta", []) or []
    due_hints_dict = ctx.get("due_hints", {}) or {}
    meta = ctx.get("meta", {}) or {}
    generated_at = meta.get("generated_at")
    base_date = str(generated_at)[:10] if isinstance(generated_at, str) and generated_at else ""

    backlog_views = build_backlog_views(
        actions_meta=actions_meta,
        due_hints=due_hints_dict,
        base_date=base_date,
        resolve_date_fn=resolve_relative_date,
    )
    ctx["backlog"] = backlog_to_dict(backlog_views)
