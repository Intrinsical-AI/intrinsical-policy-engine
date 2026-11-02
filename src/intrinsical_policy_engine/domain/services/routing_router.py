# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Ordered routing policy using the same compiled conditions as selection."""

from __future__ import annotations

from collections.abc import Set

from intrinsical_policy_engine.domain.conditions import evaluate_condition
from intrinsical_policy_engine.domain.contract_models import FrameworkRuntime


def resolve_routing_route(final_flags: Set[str], runtime: FrameworkRuntime) -> dict:
    policy = runtime.policies.routing
    for rule in policy.rules:
        if evaluate_condition(rule.when, final_flags):
            return {
                "route": rule.route,
                "rule_id": rule.id,
                "excluded_actions": list(rule.exclude_actions),
            }
    return {"route": policy.default_route, "rule_id": None, "excluded_actions": []}
