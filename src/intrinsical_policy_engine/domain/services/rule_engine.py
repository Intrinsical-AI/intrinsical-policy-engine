# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Selection over conditions compiled once at the contract boundary."""

from __future__ import annotations

from collections.abc import Sequence, Set

from intrinsical_policy_engine.domain.conditions import evaluate_condition
from intrinsical_policy_engine.domain.contract_models import ActionDefinition, RulesContract


def select_actions(flags: Set[str], actions: Sequence[ActionDefinition]) -> list[str]:
    return [action.id for action in actions if evaluate_condition(action.when, flags)]


def evaluate_stops(flags: Set[str], rules: RulesContract) -> dict[str, str] | None:
    for stop in rules.stops:
        if evaluate_condition(stop.when, flags):
            return {"stop_id": stop.id, "outcome": stop.outcome}
    return None


def apply_role_filter(
    ids: list[str], actions: Sequence[ActionDefinition], roles: Set[str]
) -> list[str]:
    catalog = {action.id: action for action in actions}
    return [
        action_id
        for action_id in ids
        if catalog[action_id].applies_to == "any"
        or bool(set(catalog[action_id].applies_to) & roles)
    ]
