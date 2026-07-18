# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Registry for declarative predicates used by bundle profiles."""

from __future__ import annotations

from collections.abc import Callable

from intrinsical_policy_engine.domain.bundles.context import EvalContext

Predicate = Callable[[EvalContext], bool]


class PredicateRegistry:
    """Registry of named predicates for use in bundles."""

    def __init__(self) -> None:
        self._predicates: dict[str, Predicate] = {}

    def register(self, name: str, func: Predicate) -> None:
        self._predicates[name] = func

    def _runtime_predicates(self, context: EvalContext) -> dict[str, object]:
        runtime = context.extras.get("runtime")
        predicate_defs = getattr(getattr(runtime, "policies", None), "predicates", None)
        if not predicate_defs:
            return {}
        return {predicate.name: predicate.rule for predicate in predicate_defs}

    def _evaluate_runtime_rule(  # noqa: C901
        self,
        rule,
        context: EvalContext,
        *,
        stack: set[str] | None = None,
    ) -> bool:
        roles = set(context.system_profile.roles or [])
        regimes = set(context.system_profile.regimes or [])
        tier = context.system_profile.classification_tier
        flags = {name for name, active in context.flags.items() if active}
        outcomes_raw = context.plan.get("outcome", [])
        if isinstance(outcomes_raw, list):
            outcomes = {str(item) for item in outcomes_raw}
        elif outcomes_raw:
            outcomes = {str(outcomes_raw)}
        else:
            outcomes = set()

        if getattr(rule, "predicate", None):
            predicate_name = str(rule.predicate)
            if stack and predicate_name in stack:
                raise ValueError(f"Cyclic runtime predicate reference: {predicate_name}")
            return self.evaluate(predicate_name, context, stack=(stack or set()) | {predicate_name})

        if getattr(rule, "roles_any", None) and not roles.intersection(rule.roles_any):
            return False
        if getattr(rule, "roles_all", None) and not set(rule.roles_all).issubset(roles):
            return False
        if getattr(rule, "regimes_any", None) and not regimes.intersection(rule.regimes_any):
            return False
        if getattr(rule, "tier_in", None) and tier not in set(rule.tier_in):
            return False
        if getattr(rule, "flags_any", None) and not flags.intersection(rule.flags_any):
            return False
        if getattr(rule, "flags_all", None) and not set(rule.flags_all).issubset(flags):
            return False
        if getattr(rule, "outcomes_any", None) and not outcomes.intersection(rule.outcomes_any):
            return False
        if getattr(rule, "attribute_equals", None):
            for key, value in rule.attribute_equals.items():
                if context.system_profile.get_attribute(key) != value:
                    return False
        if getattr(rule, "any_of", None) and not any(
            self._evaluate_runtime_rule(child, context, stack=stack) for child in rule.any_of
        ):
            return False
        return not (
            getattr(rule, "all_of", None)
            and not all(
                self._evaluate_runtime_rule(child, context, stack=stack) for child in rule.all_of
            )
        )

    def evaluate(self, name: str, context: EvalContext, *, stack: set[str] | None = None) -> bool:
        if name in self._predicates:
            return self._predicates[name](context)

        runtime_rule = self._runtime_predicates(context).get(name)
        if runtime_rule is None:
            raise ValueError(f"Unknown predicate: '{name}'.")
        return self._evaluate_runtime_rule(runtime_rule, context, stack=stack)

    def evaluate_all(self, names: list[str], context: EvalContext) -> bool:
        return all(self.evaluate(name, context, stack={name}) for name in names)

    def list_predicates(self) -> list[str]:
        return sorted(set(self._predicates) | set())


def _predicate_true(ctx: EvalContext) -> bool:
    return True


PREDICATES = PredicateRegistry()
PREDICATES.register("true", _predicate_true)
