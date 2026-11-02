# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Finite monotonic least fixed point of positive flag derivations."""

from __future__ import annotations

from dataclasses import dataclass

from intrinsical_policy_engine.domain.conditions import evaluate_condition
from intrinsical_policy_engine.domain.contract_models import RulesContract


@dataclass(frozen=True)
class DerivationResult:
    final_flags: frozenset[str]
    derived_flags: frozenset[str]
    derivations_applied: tuple[dict, ...] = ()
    rules_evaluated: int = 0


def derive_with_trace(flags: set[str], rules: RulesContract) -> DerivationResult:
    result = set(flags)
    pending = list(rules.derivations)
    applied: list[dict] = []
    evaluated = 0
    while pending:
        remaining = []
        additions: set[str] = set()
        for rule in pending:
            evaluated += 1
            if evaluate_condition(rule.when, result):
                added = set(rule.set_flags) - result
                additions.update(added)
                applied.append(
                    {
                        "id": rule.id,
                        "when": rule.when.model_dump(mode="json"),
                        "flags_set": sorted(added),
                    }
                )
            else:
                remaining.append(rule)
        if not additions:
            break
        result.update(additions)
        pending = remaining
    return DerivationResult(frozenset(result), frozenset(result - flags), tuple(applied), evaluated)
