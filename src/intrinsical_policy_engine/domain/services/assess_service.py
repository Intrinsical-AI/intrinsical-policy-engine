# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Assessment service: pure business logic without I/O dependencies."""

import contextlib
from datetime import UTC, date, datetime
from typing import Any

from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.common.cache import cached_medium
from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION
from intrinsical_policy_engine.domain.constants import (
    DATE_FALLBACK_SNAPSHOT,
    LEGAL_REF_ELI,
    MAX_ACTION_LOG_ENTRIES,
    PREFIX_SECTION,
)
from intrinsical_policy_engine.domain.core.subject_profile import (
    SubjectProfile,
    build_subject_profile,
)
from intrinsical_policy_engine.domain.ports import ContractBundle
from intrinsical_policy_engine.domain.services.article_overlay import overlay
from intrinsical_policy_engine.domain.services.coherence_checker import (
    compute_flag_coherence_warnings,
    compute_role_warnings,
)
from intrinsical_policy_engine.domain.services.dedup_service import (
    DedupResult,
    dedupe_ids_with_trace,
)
from intrinsical_policy_engine.domain.services.derivation_closure import (
    DerivationResult,
    derive_with_trace,
)
from intrinsical_policy_engine.domain.services.duedate_service import (
    apply_due_hints,
    flatten_calendar,
    resolve_calendar,
)
from intrinsical_policy_engine.domain.services.flag_article_index import build_flag_article_index
from intrinsical_policy_engine.domain.services.integrity import (
    compute_bundle_hash,
    compute_plan_hash,
)
from intrinsical_policy_engine.domain.services.metrics import compute_metrics
from intrinsical_policy_engine.domain.services.outcome_classifier import (
    classify_outcome_axes,
    classify_outcome_from_axes,
)
from intrinsical_policy_engine.domain.services.routing_router import resolve_routing_route
from intrinsical_policy_engine.domain.services.rule_engine import (
    apply_packs,
    apply_role_filter,
    evaluate_stops,
    select_actions,
)
from intrinsical_policy_engine.domain.services.tracer import build_trace
from intrinsical_policy_engine.domain.types import Plan

# =============================================================================
# PUBLIC API
# =============================================================================


def assess_from_bundle(
    bundle: ContractBundle,
    raw_answers: dict,
    logger: StructuredLogger | None = None,
    include_full_trace: bool = False,
    templates_hash: str | None = None,
    framework_pack_hashes: dict[str, str] | None = None,
    base_date: date | None = None,
) -> Plan:
    """Assess compliance plan from bundle and user answers.

    This is the main entry point for the domain logic.

    Traceability & Reproducibility (docs/invariants/ENGINE-ARCHITECTURE-v1.md):
      This function computes a deterministic `plan_hash` and captures the `bundle_hash`
      in the trace to ensure that the plan can be tied back to the exact set of rules
      used for assessment.

      It also requires `templates_hash` to be injected if you want to track the state
      of the artifact templates at assessment time (INV-05). If missing, the trace
      will lack this link, which may degrade snapshot reproducibility guarantees.

    Args:
        bundle: Contract bundle with rules and actions
        raw_answers: User answers or direct flags
        logger: Optional logger for detailed tracing
        include_full_trace: Whether to include detailed answers in trace
        templates_hash: Optional hash of templates for traceability
        framework_pack_hashes: Optional framework pack hashes for traceability
        base_date: Optional base date for resolving relative calendar offsets.
            If None, uses ai_act.entry_into_force from calendar or datetime.now().
            The caller may override this for demo/test reproducibility.

    Returns:
        Plan dict with flags, actions, due_hints, routing, etc.

    Example:
        >>> from intrinsical_policy_engine.domain.bundles.context_builders import (
        ...     build_bundle_from_path,
        ... )
        >>> bundle = build_bundle_from_path("frameworks/starter")
        >>> answers = {"S1_Q1": "yes", "BIO_Q2": "1:N_post"}
        >>> plan = assess_from_bundle(bundle, answers)
        >>> "flags" in plan
        True
        >>> "actions" in plan
        True
        >>> "trace" in plan
        True
    """
    if logger:
        logger.info("assess.start", {"answers_count": len(raw_answers)})

    # 1. Determine initial flags from answers
    flags_emitted = _determine_initial_flags(bundle, raw_answers)

    # C-01: Fail-fast type validation
    _validate_flags_types(flags_emitted)

    if logger:
        _log_initial_flags(logger, flags_emitted, raw_answers)

    # 2. Expand flags (derivation closure) and check stops
    final_flags, stop_outcome, derivation_result = _expand_flags_and_stops(
        flags_emitted, bundle.rules
    )

    if logger:
        _log_expansion(logger, flags_emitted, final_flags, stop_outcome)

    # 3. Choose actions (packs + individual rules)
    action_ids, packs_fired, halted, dedup_result = _choose_actions(
        final_flags, bundle, stop_outcome, logger
    )

    if logger:
        _log_actions(logger, action_ids, packs_fired, halted, stop_outcome)

    # 4. Enrich plan (routing, due dates, evidence maps, etc.)
    plan = _enrich_plan(
        bundle,
        final_flags,
        action_ids,
        packs_fired,
        halted,
        stop_outcome,
        raw_answers,
        flags_emitted,
        derivation_result=derivation_result,
        dedup_result=dedup_result,
        include_full_trace=include_full_trace,
        templates_hash=templates_hash,
        framework_pack_hashes=framework_pack_hashes,
        base_date_override=base_date,
    )

    if logger:
        _log_completion(logger, plan)

    return plan


# =============================================================================
# FLAG PROCESSING
# =============================================================================


def _validate_flags_types(flags: set[Any]) -> None:
    """Validate that all flags are strings to prevent silent failures downstream.

    See Audit C-01.

    Raises:
        AssessmentError: If any flag is not a string.
    """
    from intrinsical_policy_engine.domain.exceptions import AssessmentError

    for f in flags:
        if not isinstance(f, str):
            raise AssessmentError(f"Flag must be string, got {type(f).__name__}: {f!r}")


def _determine_initial_flags(bundle: ContractBundle, raw_answers: dict) -> set[str]:
    """Determine initial flags from answers or direct input."""
    return _get_initial_flags(bundle, raw_answers)


def _expand_flags_and_stops(
    initial_flags: set[str], rules
) -> tuple[set[str], dict | None, DerivationResult]:
    """Derive final flags and check stop conditions.

    Args:
        initial_flags: Set of initial flags from questionnaire/direct input.
        rules: RulesContract (typed) containing derivations, packs, and stops.

    Returns:
        Tuple of (final_flags, stop_outcome, derivation_result).
    """
    derivation_result = derive_with_trace(initial_flags, rules)
    final_flags = set(derivation_result.final_flags)
    stop_outcome = evaluate_stops(final_flags, rules)
    return final_flags, stop_outcome, derivation_result


# =============================================================================
# ACTION SELECTION
# =============================================================================


def _choose_actions(
    final_flags: set[str],
    bundle: ContractBundle,
    stop_outcome: dict | None,
    logger: StructuredLogger | None = None,
) -> tuple[list[str], list[str], bool, DedupResult | None]:
    """Select actions based on flags and stop outcome."""
    if stop_outcome:
        # CRITICAL (INV-S1): A stop is not automatically a blocking outcome.
        # Only the blocked stop outcome should inject A_STOP.
        stop_type = stop_outcome.get("outcome") if isinstance(stop_outcome, dict) else None
        if stop_type == "blocked":
            return ["A_STOP"], [], True, None
        # Non-blocking stops (out_of_scope, excluded, legacy_grandfathered, etc.)
        # halt evaluation without injecting blocking actions.
        return [], [], True, None
    return _select_and_filter_actions(final_flags, bundle, logger)


def _filter_actions_by_routing(action_ids: list[str], routing_decision: dict) -> list[str]:
    """Filter actions based on routing decision enforcement."""
    filtered_action_ids = list(action_ids)

    # Check if we should enforce specific action sets based on route
    # Enforce if explicitly set OR if safety_override is active (implies conservative route)
    enforce_flag = routing_decision.get("enforce")
    safety_override = routing_decision.get("safety_override")
    should_enforce = enforce_flag or safety_override

    if should_enforce:
        excluded_actions = set(routing_decision.get("excluded_actions", []) or [])
        filtered_action_ids = [aid for aid in filtered_action_ids if aid not in excluded_actions]
    return filtered_action_ids


# =============================================================================
# PLAN ENRICHMENT
# =============================================================================


def _build_evidence_maps(
    bundle: ContractBundle, action_ids: list[str], article_overlay: dict
) -> tuple[dict, dict]:
    """Build evidence maps for articles and actions."""
    articles_in_plan = set(article_overlay.keys())
    full_ev_map = bundle.evidence_map

    def _norm_list(lst):
        out = []
        for e in lst or []:
            if isinstance(e, dict) and e.get("path"):
                out.append(str(e.get("path")))
            else:
                out.append(str(e))
        return out

    articles_evidence_map = {
        a: _norm_list(full_ev_map.get(a, [])) for a in sorted(articles_in_plan)
    }

    id2arts = {a.id: set(a.articles) for a in bundle.actions.actions}
    actions_evidence_map = {}
    for aid in action_ids:
        evidence_paths: set[str] = set()
        for art in sorted(id2arts.get(aid, [])):
            for e in articles_evidence_map.get(art, []):
                evidence_paths.add(str(e))
        if evidence_paths:
            actions_evidence_map[aid] = sorted(evidence_paths)

    return articles_evidence_map, actions_evidence_map


def _determine_calendar_base_date(calendar_map: dict | None) -> date:
    """Determine the base date for resolving relative calendar offsets."""
    if not isinstance(calendar_map, dict):
        # Use local date instead of UTC to avoid "tomorrow" issues late at night
        # unless timezone is explicitly handled.
        return datetime.now().date()

    for key, value in calendar_map.items():
        if not isinstance(value, str):
            continue
        if "entry_into_force" not in str(key) and "effective" not in str(key):
            continue
        try:
            return date.fromisoformat(value)
        except ValueError:
            continue

    return datetime.now().date()


def _build_legal_token(bundle: ContractBundle, calendar_map: dict[str, Any]) -> dict[str, Any]:
    """Build legal identity from pack metadata, with neutral legacy fallbacks."""
    snapshot_date = DATE_FALLBACK_SNAPSHOT
    for key, value in calendar_map.items():
        if not isinstance(value, str):
            continue
        if any(token in str(key) for token in ("entry_into_force", "effective", "transparency")):
            snapshot_date = value
            break

    reference = LEGAL_REF_ELI
    framework = bundle.metadata.get("framework") if isinstance(bundle.metadata, dict) else None
    legal_basis = framework.get("legal_basis") if isinstance(framework, dict) else None
    if isinstance(legal_basis, dict):
        declared_reference = legal_basis.get("eli")
        if isinstance(declared_reference, str) and declared_reference.strip():
            reference = declared_reference.strip()
        declared_date = legal_basis.get("entry_into_force")
        if isinstance(declared_date, str) and declared_date.strip():
            snapshot_date = declared_date.strip()

    regulatory_meta = bundle.rules.regulatory_meta
    if regulatory_meta is not None:
        if reference == LEGAL_REF_ELI and regulatory_meta.source.strip():
            reference = regulatory_meta.source.strip()
        if snapshot_date == DATE_FALLBACK_SNAPSHOT and regulatory_meta.effective_date.strip():
            snapshot_date = regulatory_meta.effective_date.strip()

    return {"eli": [reference], "date": snapshot_date}


def _get_engine_version() -> str:
    """Return the version of the code executing this assessment."""
    return CANONICAL_ENGINE_VERSION


def _enrich_plan(  # noqa: C901
    bundle: ContractBundle,
    final_flags: set[str],
    action_ids: list[str],
    packs_fired: list[str],
    halted: bool,
    stop_outcome: dict | None,
    raw_answers: dict,
    flags_emitted: set[str],
    *,
    derivation_result: DerivationResult | None = None,
    dedup_result: DedupResult | None = None,
    include_full_trace: bool = False,
    templates_hash: str | None = None,
    framework_pack_hashes: dict[str, str] | None = None,
    base_date_override: date | None = None,
) -> Plan:
    """Build the final assessment plan with all enrichments."""
    # 1. Outcome Classification (framework-driven)
    classifiers_config = bundle.rules.classifiers if bundle.rules.classifiers else None
    outcome_axes = classify_outcome_axes(final_flags, stop_outcome, classifiers_config)
    outcomes_set = classify_outcome_from_axes(
        outcome_axes,
        final_flags,
        stop_outcome,
        classifiers_config,
    )
    outcome = sorted(list(outcomes_set))

    # 2. Routing Router
    id2action = bundle.get_actions_dict()
    prelim_selected_articles: set[str] = set()
    for aid in action_ids:
        action = id2action.get(aid)
        if action:
            for article in action.articles:
                prelim_selected_articles.add(str(article))

    routing_decision = resolve_routing_route(
        final_flags,
        prelim_selected_articles,
        bundle.rules,
        outcome_axes,
        bundle.runtime,
    )

    action_ids = _filter_actions_by_routing(action_ids, routing_decision)

    # 3. Due Dates & Overlay
    # Allow the CLI to inject a base date for demos and deterministic tests.
    calendar_map_raw = flatten_calendar(bundle.calendar)
    base_date = base_date_override or _determine_calendar_base_date(calendar_map_raw)
    calendar_map = resolve_calendar(calendar_map_raw, base_date)
    due_hints = apply_due_hints(action_ids, bundle.due_rules, calendar_map, bundle.runtime)
    # Use typed actions directly instead of converting to dicts
    article_overlay = overlay(bundle.actions.actions, bundle.articles, set(action_ids))
    actions_meta = _get_actions_metadata(bundle, action_ids)

    # 4. Evidence Maps
    articles_evidence_map, actions_evidence_map = _build_evidence_maps(
        bundle, action_ids, article_overlay
    )

    # 5. Derived Index
    # Re-build id2action for full catalog to be safe
    id2action_full = bundle.get_actions_dict()
    selected_actions_dicts = [a.model_dump() for a in id2action_full.values() if a.id in action_ids]
    # build_flag_article_index expects dicts - bundle.articles/flags are always Pydantic models
    articles_dict: dict = bundle.articles.model_dump()
    flags_dict: dict = bundle.flags.model_dump()
    raw_index = build_flag_article_index(selected_actions_dicts, articles_dict, flags_dict)
    flag_article_index = {f: sorted(list(v)) for f, v in raw_index.items() if f in final_flags}

    # 6. Trace & Outcomes
    section_refs_by_flag = {
        f: [a for a in section_articles if str(a).startswith(PREFIX_SECTION)]
        for f, section_articles in flag_article_index.items()
        if any(str(a).startswith(PREFIX_SECTION) for a in section_articles)
    }

    legal_token = _build_legal_token(bundle, calendar_map)

    # 7. System Profile
    # Derive the display name from answers so exports keep the submitted system identity.
    system_name = bundle.runtime.policies.subject_profile.default_name or "Unnamed Subject"
    if isinstance(raw_answers, dict):
        # Try nested "system.name" first (standard format)
        if isinstance(raw_answers.get("system"), dict):
            system_name = raw_answers["system"].get("name") or system_name
        # Also try top-level "system_name" (alternative format)
        elif raw_answers.get("system_name"):
            system_name = raw_answers["system_name"]
    system_profile = build_subject_profile(
        bundle.runtime,
        final_flags,
        outcome_axes,
        name=system_name,
    )

    # Capture non-sensitive metadata for exports (required context fields).
    system_meta: dict[str, Any] = {"name": system_name}
    provider_meta: dict[str, Any] = {}
    if isinstance(raw_answers, dict):
        raw_system = raw_answers.get("system")
        if isinstance(raw_system, dict):
            system_meta = dict(raw_system)
            system_meta.setdefault("name", system_name)

        raw_provider = raw_answers.get("provider")
        if isinstance(raw_provider, dict):
            provider_meta = dict(raw_provider)
        if not provider_meta.get("name"):
            alt_name = raw_answers.get("provider_name") or raw_answers.get("provider_legal_name")
            if isinstance(alt_name, str) and alt_name:
                provider_meta["name"] = alt_name

    rules_applied = {
        "packs_fired": packs_fired,
        "halted": halted,
        "stops": stop_outcome,
        "routing": {
            # Trace records the base (router) decision; plan['routing'] keeps the final one.
            "route": routing_decision.get("base_route") or routing_decision.get("route"),
            "source": routing_decision.get("source"),
            "reason": routing_decision.get("reason"),
            "enforced": bool(routing_decision.get("enforce")),
            "safety_override": bool(routing_decision.get("safety_override")),
            "override_flags": routing_decision.get("override_flags", []),
            "alternative_route": routing_decision.get("alternative_route"),
        },
    }

    # 8. Metrics (HITL & Coverage)
    # Metrics are now domain-native
    # We pass a partial plan to compute_metrics, which is safe as it only reads
    # fields appearing before this point (actions, articles_overlay, flags, outcome_axes)

    plan_so_far = {
        "flags": sorted(final_flags),
        "actions": action_ids,
        "actions_meta": actions_meta,
        "articles_overlay": article_overlay,
        "outcome_axes": outcome_axes,
    }
    from typing import cast

    metrics = compute_metrics(cast(Plan, plan_so_far), bundle.runtime)

    flag_warnings = _compute_flag_coherence_warnings(final_flags)
    role_warnings = _compute_role_warnings(outcome_axes.get("roles", []))
    warnings: dict[str, list[str]] = {}
    if flag_warnings:
        warnings["flags"] = flag_warnings
    if role_warnings:
        warnings["roles"] = role_warnings
    if warnings:
        rules_applied["warnings"] = warnings

    # Build extended trace
    flags_derived = set(derivation_result.derived_flags) if derivation_result else None
    derivations_applied = list(derivation_result.derivations_applied) if derivation_result else None
    rules_evaluated = derivation_result.rules_evaluated if derivation_result else None
    actions_deduped = None
    if dedup_result:
        actions_deduped = {
            "aliases_resolved": dedup_result.aliases_resolved,
            "duplicates_removed": list(dedup_result.duplicates_removed),
        }

    # Build trace WITHOUT volatile fields first (for deterministic plan_hash)
    trace = build_trace(
        raw_answers,
        flags_emitted,
        final_flags,
        rules_applied,
        action_ids,
        due_hints,
        article_overlay,
        flags_derived=flags_derived,
        derivations_applied=derivations_applied,
        rules_evaluated=rules_evaluated,
        actions_deduped=actions_deduped,
        engine_version=_get_engine_version(),
        contracts_version=bundle.version,
        include_full_trace=include_full_trace,
        templates_hash=templates_hash,
    )

    if framework_pack_hashes:
        trace["framework_pack_hash"] = framework_pack_hashes.get("framework_pack_hash")
        trace["pack_hashes"] = {
            k: v for k, v in framework_pack_hashes.items() if k != "framework_pack_hash"
        }

    # Add bundle metadata for snapshot manifest (ENGINE-ARCHITECTURE-v1.md)
    bundle_hash = compute_bundle_hash(bundle)
    trace["framework_version"] = bundle.version
    trace["bundle_hash"] = bundle_hash

    # Build plan WITHOUT volatile fields first to compute deterministic plan_hash.
    # Per ENGINE-ARCHITECTURE-v1.md same inputs must produce the same plan_hash.
    plan_without_volatile: dict = {
        "flags": sorted(final_flags),
        "actions": action_ids,
        "actions_meta": actions_meta,
        "due_hints": due_hints,
        "articles_overlay": {k: sorted(v) for k, v in article_overlay.items()},
        "flag_article_index": flag_article_index,
        "articles_evidence_map": articles_evidence_map,
        "actions_evidence_map": actions_evidence_map,
        "section_refs_by_flag": section_refs_by_flag,
        "legal_token": legal_token,
        "routing": {
            **routing_decision,
            "enforced": bool(routing_decision.get("enforce")),
        },
        "trace": trace,
        "outcome": outcome,
        "outcome_axes": outcome_axes,
        "risk_tier": outcome_axes.get("risk_tier"),
        "system_profile": system_profile.to_dict(),
        "metrics": metrics,
    }

    # Compute deterministic plan_hash BEFORE adding assessment timestamp
    plan_hash = compute_plan_hash(plan_without_volatile)
    trace["plan_hash"] = plan_hash

    # Use real timestamp for auditability
    # plan_hash excludes this field, so determinism is preserved for hash comparison
    assessment_timestamp = datetime.now(UTC).isoformat()
    trace["assessment_timestamp"] = assessment_timestamp

    return {
        **plan_without_volatile,
        "system": system_meta,
        "provider": provider_meta,
        "assessment_timestamp": assessment_timestamp,
    }


# =============================================================================
# SYSTEM PROFILE
# =============================================================================


def _build_system_profile(
    final_flags: set[str],
    outcome_axes: dict,
    runtime,
    system_name: str = "Unnamed Subject",
) -> SubjectProfile:
    """Compatibility wrapper around the runtime-driven SubjectProfile builder."""

    return build_subject_profile(runtime, final_flags, outcome_axes, name=system_name)


# =============================================================================
# LOGGING HELPERS
# =============================================================================


def _log_initial_flags(logger: StructuredLogger, flags_emitted, raw_answers):
    """Emit telemetry for the initial flag set and emitted count."""
    with contextlib.suppress(TypeError, ValueError):
        logger.info(
            "assess.flags.initial",
            {
                "flags_emitted": sorted(flags_emitted),
                "answers_keys": list(raw_answers.keys()) if isinstance(raw_answers, dict) else [],
            },
        )
    logger.info(
        "assess.flags.emitted",
        {"count": len(flags_emitted), "flags": sorted(flags_emitted)},
    )


def _log_expansion(logger: StructuredLogger, flags_emitted, final_flags, stop_outcome):
    """Log derived flags and stop outcomes produced during expansion."""
    derived_count = len(final_flags - flags_emitted)
    logger.info(
        "assess.flags.derived",
        {
            "initial_count": len(flags_emitted),
            "final_count": len(final_flags),
            "derived_count": derived_count,
            "derived_flags": sorted(final_flags - flags_emitted) if derived_count > 0 else [],
        },
    )
    if stop_outcome:
        logger.warning(
            "assess.stop.detected",
            {"outcome": stop_outcome.get("outcome"), "reason": stop_outcome.get("stop_id")},
        )


def _log_actions(logger: StructuredLogger, action_ids, packs_fired, halted, stop_outcome):
    """Log action selection stats or stop-outcome-induced clearing."""
    if stop_outcome:
        logger.info("assess.actions.cleared", {"reason": "stop_outcome"})
    else:
        logger.info(
            "assess.actions.selected",
            {
                "action_count": len(action_ids),
                "packs_fired": packs_fired,
                "halted": halted,
                "actions": action_ids[:MAX_ACTION_LOG_ENTRIES]
                if len(action_ids) > MAX_ACTION_LOG_ENTRIES
                else action_ids,
            },
        )


def _log_completion(logger: StructuredLogger, plan):
    """Log final plan summary and additional insight for special outcomes."""
    outcome_axes = plan.get("outcome_axes", {})
    logger.info(
        "assess.bundle.complete",
        {
            "flags_final": len(plan.get("flags", [])),
            "actions": len(plan.get("actions", [])),
            "articles": len(plan.get("articles_overlay", {})),
            "due_hints": len(plan.get("due_hints", {})),
            "outcome": plan.get("outcome"),
            "outcome_risk_tier": outcome_axes.get("risk_tier"),
        },
    )
    if "other_regulated" in plan.get("outcome", []):
        try:
            final_flags = set(plan.get("flags", []))
            sample_flags = sorted(list(final_flags))[:20]
            dominant_groups = {
                "has_classification": any(f.startswith("classification.") for f in final_flags),
                "has_model": any(f.startswith("model.") for f in final_flags),
                "has_transparency": any(
                    f.startswith("transparency.") or f == "gen.public_output" for f in final_flags
                ),
                "has_impact_review_required": "impact_review.required" in final_flags,
                "has_public_service": "entity.public_service" in final_flags
                or "service.public" in final_flags,
            }
            logger.info(
                "assess.outcome.other_regulated",
                {
                    "flags_sample": sample_flags,
                    "risk_tier": outcome_axes.get("risk_tier"),
                    "flag_groups": dominant_groups,
                },
            )
        except Exception:  # noqa: BLE001
            pass


def _get_initial_flags(bundle, raw_answers: dict) -> set[str]:
    """Extract or compute initial flags from raw answers.

    This function is pure domain logic: it does NOT perform I/O.
    Questions must be pre-loaded in bundle.questions by the adapter (app layer).

    Args:
        bundle: ContractBundle with pre-loaded questions
        raw_answers: Either {"flags": [...]} for direct flags,
            or {"answers": {...}} for questionnaire

    Returns:
        Set of initial flags derived from answers or provided directly.

    Raises:
        AssessmentError: If questionnaire mode but bundle.questions is missing/empty.
    """
    from intrinsical_policy_engine.domain.exceptions import AssessmentError

    # Fast path: direct flags provided (bypasses questionnaire)
    if "flags" in raw_answers:
        return set(raw_answers["flags"])

    import logging

    from intrinsical_policy_engine.domain.services.questionnaire_engine import (
        eval_questions,
        sanitize_answers_dict,
    )

    logger = logging.getLogger(__name__)

    # Questions must be pre-loaded in bundle (loaded by YamlContractsAdapter in app layer)
    # FAIL-HARD: Missing questions is a configuration error that must not be silently ignored.
    # This prevents incorrect "out of scope" classifications from propagating downstream.
    questionnaire_doc = getattr(bundle, "questions", None)
    if not questionnaire_doc:
        logger.error(
            "assess.questions.missing",
            extra={
                "reason": "missing_questions",
                "bundle_path": getattr(bundle, "path", "<unknown>"),
                "bundle_version": getattr(bundle, "version", "<unknown>"),
            },
        )
        raise AssessmentError(
            "Cannot assess: bundle.questions is missing or empty. "
            "The questionnaire file must be loaded before assessment. "
            "If using direct flags, pass {'flags': [...]} instead of {'answers': {...}}."
        )

    raw = raw_answers.get("answers", {}) or {}

    # Also fail if answers dict is provided but empty - likely a misconfiguration
    if not raw:
        logger.warning(
            "assess.answers.empty",
            extra={
                "reason": "empty_answers",
                "raw_answers_keys": list(raw_answers.keys()),
            },
        )
        # Empty answers is allowed but logged - might be intentional for "default" assessment

    cleaned = sanitize_answers_dict(questionnaire_doc, raw)
    return eval_questions(questionnaire_doc, cleaned)


def _select_and_filter_actions(
    final_flags: set[str], bundle, logger: StructuredLogger | None = None
) -> tuple[list[str], list[str], bool, DedupResult]:
    """Select actions from packs and direct rules, then deduplicate and filter by role."""
    pack_actions, packs_fired, halted = apply_packs(final_flags, bundle.rules)

    # Use typed actions directly instead of converting to dicts
    actions_catalog = bundle.actions.actions
    id2action = {a.id: a for a in actions_catalog}

    # Post-filter pack actions by their own 'when' where metadata exists; keep unknowns
    subset = [id2action[aid] for aid in pack_actions if aid in id2action]
    passing = set(select_actions(final_flags, subset))

    pack_actions_filtered = []
    for aid in pack_actions:
        if aid not in id2action:
            pack_actions_filtered.append(aid)  # Unknown action, keep
        elif aid in passing:
            pack_actions_filtered.append(aid)  # Passed own when
        else:
            # C-07: Log removal for audit trail
            if logger:
                action = id2action[aid]
                logger.warning(
                    "assess.pack_action.filtered_by_own_when",
                    {
                        "action_id": aid,
                        "when_condition": str(getattr(action, "when", None)),
                    },
                )

    # Direct rules (not from packs)
    direct_actions = [] if halted else select_actions(final_flags, actions_catalog)

    # Use dedupe_ids_with_trace for audit trail  (see CONTRACTS.md Level 4)
    dedup_result = dedupe_ids_with_trace(pack_actions_filtered + direct_actions, bundle.dedups)
    combined = list(dedup_result.canonical_ids)
    filtered = apply_role_filter(combined, actions_catalog, final_flags)

    return filtered, packs_fired, halted, dedup_result


@cached_medium
def _build_id2meta_cached(
    bundle_path: str, bundle_version: str, actions_json: str
) -> dict[str, dict]:
    """Build and cache id->metadata mapping from actions catalog."""
    import json

    actions = json.loads(actions_json)
    return {a["id"]: a for a in actions.get("actions", [])}


def _get_actions_metadata(bundle, action_ids: list[str]) -> list[dict]:
    """Retrieve cached metadata for selected actions."""
    import json

    # Serialize actions contract to JSON for caching key
    actions_json = json.dumps(bundle.actions.model_dump(), sort_keys=True)
    id2meta = _build_id2meta_cached(bundle.path, bundle.version, actions_json)
    return [id2meta[aid] for aid in action_ids if aid in id2meta]


# =============================================================================
# CACHE MANAGEMENT
# =============================================================================


def clear_caches() -> None:
    """Clear all LRU caches used by the assessment service."""
    with contextlib.suppress(AttributeError):
        _build_id2meta_cached.cache_clear()


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

# Coherence warnings now use centralized implementation from coherence_checker.py
# to avoid maintaining duplicate logic. The functions below are thin wrappers
# that delegate to the canonical implementations.


def _compute_flag_coherence_warnings(final_flags: set[str]) -> list[str]:
    """Compute warnings for incoherent flag combinations.

    Delegates to coherence_checker.compute_flag_coherence_warnings.
    """
    return compute_flag_coherence_warnings(final_flags)


def _compute_role_warnings(roles: list[str]) -> list[str]:
    """Compute warnings for role assignments.

    Delegates to coherence_checker.compute_role_warnings.
    """
    return compute_role_warnings(roles)
