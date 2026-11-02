# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure assessment from a compiled pack and validated input envelope."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION
from intrinsical_policy_engine.domain.contract_models import ContractBundle, canonical_date
from intrinsical_policy_engine.domain.core.subject_profile import build_subject_profile
from intrinsical_policy_engine.domain.exceptions import AssessmentError, ContractValidationError
from intrinsical_policy_engine.domain.input_models import AssessmentInput
from intrinsical_policy_engine.domain.services.article_overlay import articles_by_action, overlay
from intrinsical_policy_engine.domain.services.contract_validation import validate_bundle
from intrinsical_policy_engine.domain.services.derivation_closure import derive_with_trace
from intrinsical_policy_engine.domain.services.duedate_service import (
    apply_due_hints,
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
from intrinsical_policy_engine.domain.services.questionnaire_engine import eval_questions
from intrinsical_policy_engine.domain.services.routing_router import resolve_routing_route
from intrinsical_policy_engine.domain.services.rule_engine import (
    apply_role_filter,
    evaluate_stops,
    select_actions,
)


def assess_from_bundle(
    bundle: ContractBundle,
    raw_answers: dict,
    logger=None,
    include_full_trace: bool = False,
    templates_hash: str | None = None,
    framework_pack_hashes: dict[str, str] | None = None,
    base_date: date | str | None = None,
) -> dict:
    problems = validate_bundle(bundle, {})
    errors = [problem for problem in problems if problem.severity == "error"]
    if errors:
        raise ContractValidationError(
            "bundle validation failed",
            problems=[{**problem.to_dict(), "source": "bundle"} for problem in errors],
        )
    if base_date is not None:
        base_date = date.fromisoformat(canonical_date(base_date))
    input_data = AssessmentInput.model_validate(raw_answers)
    if input_data.flags is not None:
        emitted = set(input_data.flags)
    elif input_data.answers is not None:
        emitted = eval_questions(bundle.questions, input_data.answers)
    else:
        raise AssessmentError("assessment input contains no flags or answers")
    known_flags = {flag.id for flag in bundle.flags.registry}
    unknown = emitted - known_flags
    if unknown:
        raise AssessmentError(f"unknown flags: {len(unknown)} undeclared identifier(s)")
    derivation = derive_with_trace(emitted, bundle.rules)
    flags = set(derivation.final_flags)
    stop = evaluate_stops(flags, bundle.rules)
    semantics = bundle.runtime.semantics
    axes = classify_outcome_axes(flags, stop, bundle.rules.classifiers, semantics)
    outcomes = sorted(
        classify_outcome_from_axes(axes, flags, stop, bundle.rules.classifiers, semantics)
    )
    routing = resolve_routing_route(flags, bundle.runtime)
    if stop:
        selected: list[str] = []
    else:
        selected = select_actions(flags, bundle.actions.actions)
        selected = apply_role_filter(selected, bundle.actions.actions, set(axes["roles"]))
        excluded = set(routing["excluded_actions"])
        selected = [action_id for action_id in selected if action_id not in excluded]
    resolved_calendar = resolve_calendar(bundle.calendar, base_date)
    due_hints = apply_due_hints(selected, bundle.due_rules, resolved_calendar)
    article_actions = overlay(bundle.actions.actions, bundle.articles, set(selected))
    action_articles = articles_by_action(article_actions)
    articles_overlay = {key: sorted(values) for key, values in article_actions.items()}
    catalog = bundle.get_actions_dict()
    selected_models = [catalog[action_id] for action_id in selected]
    actions_meta = [action.model_dump(mode="json") for action in selected_models]
    index = build_flag_article_index(selected_models, bundle.flags, action_articles)
    articles_evidence_map = {
        article: [requirement.path for requirement in bundle.evidence_map.get(article, ())]
        for article in articles_overlay
    }
    actions_evidence_map = {
        action_id: list(
            dict.fromkeys(
                [
                    *catalog[action_id].evidence,
                    *[requirement.path for requirement in bundle.evidence_map.get(action_id, ())],
                    *[
                        path
                        for article in sorted(action_articles.get(action_id, set()))
                        for path in articles_evidence_map.get(article, [])
                    ],
                ]
            )
        )
        for action_id in selected
    }
    metadata: dict[str, dict[str, Any]] = {
        key: getattr(input_data, key).model_dump(mode="json")
        for key in ("system", "provider", "owner", "declared_by", "approvals", "meta")
    }
    name = metadata["system"].get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise AssessmentError("system.name must be a nonempty string")
    profile = build_subject_profile(bundle.runtime, axes, name=name)
    effective_base = base_date.isoformat() if base_date is not None else bundle.calendar.base_date
    semantic_input: dict[str, Any] = (
        {"flags": input_data.flags}
        if input_data.flags is not None
        else {"answers": input_data.answers}
    )
    trace: dict[str, Any] = {
        "answers_raw": {
            "answers_hash": hashlib.sha256(
                json.dumps(semantic_input, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        },
        "flags": {
            "emitted": sorted(emitted),
            "final": sorted(flags),
            "derived": sorted(derivation.derived_flags),
        },
        "rules_applied": {"stops": stop, "routing": routing},
        "derivations_applied": list(derivation.derivations_applied),
        "rules_evaluated": derivation.rules_evaluated,
        "actions": {"selected": selected},
        "engine_version": CANONICAL_ENGINE_VERSION,
        "framework_version": bundle.manifest.version,
        "bundle_hash": compute_bundle_hash(bundle),
        "base_date": effective_base,
    }
    if problems:
        trace["contract_warnings"] = [
            {**problem.to_dict(), "source": "bundle"} for problem in problems
        ]
    if include_full_trace:
        trace["answers_raw"]["input"] = semantic_input
    if templates_hash is not None:
        trace["templates_hash"] = templates_hash
    if framework_pack_hashes is not None:
        trace["framework_pack_hash"] = framework_pack_hashes["framework_pack_hash"]
        trace["pack_hashes"] = {
            key: value
            for key, value in framework_pack_hashes.items()
            if key != "framework_pack_hash"
        }
    timestamp = datetime.now(UTC).isoformat()
    plan: dict[str, Any] = {
        "flags": sorted(flags),
        "actions": selected,
        "actions_meta": actions_meta,
        "due_hints": due_hints,
        "calendar": resolved_calendar,
        "articles_overlay": articles_overlay,
        "flag_article_index": {
            flag: sorted(values) for flag, values in index.items() if flag in flags
        },
        "articles_evidence_map": articles_evidence_map,
        "actions_evidence_map": actions_evidence_map,
        "routing": routing,
        "outcome": outcomes,
        "outcome_axes": axes,
        "risk_tier": axes["risk_tier"],
        "system_profile": profile.to_dict(),
        "trace": trace,
        "assessment_timestamp": timestamp,
        **metadata,
    }
    plan["metrics"] = compute_metrics(plan, bundle.runtime)
    trace["plan_hash"] = compute_plan_hash(plan)
    trace["assessment_timestamp"] = timestamp
    if logger is not None:
        logger.info(
            "assess.complete",
            {"flags": len(flags), "actions": len(selected), "blocked": axes["blocked"]},
        )
    return plan
