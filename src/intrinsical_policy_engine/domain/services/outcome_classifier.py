# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Classification with explicit unmatched and terminal outcomes."""

from __future__ import annotations

from collections.abc import Set

from intrinsical_policy_engine.domain.conditions import evaluate_condition
from intrinsical_policy_engine.domain.contract_models import ClassifiersContract, FrameworkSemantics


def classify_outcome_axes(
    final_flags: Set[str],
    stop_outcome: dict[str, str] | None,
    classifiers_config: ClassifiersContract,
    semantics: FrameworkSemantics,
) -> dict:
    if stop_outcome is not None:
        return {
            "risk_tier": None,
            "roles": [],
            "regimes": [],
            "blocked": stop_outcome["outcome"] in semantics.blocked_outcomes,
        }
    roles = {
        role
        for rule in classifiers_config.role_mapping
        if evaluate_condition(rule.when, final_flags)
        for role in rule.roles
    }
    tier = None
    regimes: tuple[str, ...] = ()
    for rule in sorted(classifiers_config.risk_tiers, key=lambda item: -item.priority):
        if evaluate_condition(rule.when, final_flags):
            tier = rule.output.tier
            regimes = rule.output.regimes
            break
    return {"risk_tier": tier, "roles": sorted(roles), "regimes": list(regimes), "blocked": False}


def classify_outcome_from_axes(
    axes: dict,
    final_flags: Set[str],
    stop_outcome: dict[str, str] | None,
    classifiers_config: ClassifiersContract,
    semantics: FrameworkSemantics,
) -> set[str]:
    if stop_outcome is not None:
        outcomes = {stop_outcome["outcome"]}
    else:
        outcomes = {
            rule.outcome
            for rule in classifiers_config.outcome_rules
            if evaluate_condition(rule.when, final_flags)
        }
    axes["blocked"] = bool(outcomes.intersection(semantics.blocked_outcomes))
    return outcomes
