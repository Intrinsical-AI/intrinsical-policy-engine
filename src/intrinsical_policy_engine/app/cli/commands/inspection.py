# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Name compiled authoring rules explicitly, including ambiguous cross-family IDs."""

from intrinsical_policy_engine.domain.contract_models import (
    ContractBundle,
    ContractModel,
    Derivation,
    DueRuleEntry,
    OutcomeRule,
    RiskTierRule,
    RoleMapping,
    RoutingRule,
    RuntimeMetricsSignal,
    Stop,
)

Rule = (
    Derivation
    | Stop
    | RoleMapping
    | RiskTierRule
    | OutcomeRule
    | RoutingRule
    | DueRuleEntry
    | RuntimeMetricsSignal
)
INSPECT_USAGE = "Usage: inspect --contracts PACK [rule ID | rule FAMILY ID | action ID]"


def rule_groups(bundle: ContractBundle) -> dict[str, tuple[Rule, ...]]:
    return {
        "derivation": bundle.rules.derivations,
        "stop": bundle.rules.stops,
        "role": bundle.rules.classifiers.role_mapping,
        "tier": bundle.rules.classifiers.risk_tiers,
        "outcome": bundle.rules.classifiers.outcome_rules,
        "routing": bundle.runtime.policies.routing.rules,
        "due": bundle.due_rules.rules,
        "metric": bundle.runtime.policies.metrics.signals,
    }


def inspected_item(bundle: ContractBundle, arguments: list[str]) -> ContractModel:
    if len(arguments) == 2 and arguments[0] == "action":
        item = next(
            (action for action in bundle.actions.actions if action.id == arguments[1]), None
        )
        if item is None:
            raise ValueError(f"Unknown action: {arguments[1]}")
        return item
    groups = rule_groups(bundle)
    if len(arguments) == 3 and arguments[0] == "rule":
        _, family, identifier = arguments
        if family not in groups:
            raise ValueError(f"Unknown rule family: {family}; choose {', '.join(groups)}")
        matches = [(family, rule) for rule in groups[family] if rule.id == identifier]
    elif len(arguments) == 2 and arguments[0] == "rule":
        identifier = arguments[1]
        matches = [
            (family, rule)
            for family, rules in groups.items()
            for rule in rules
            if rule.id == identifier
        ]
    else:
        raise ValueError(INSPECT_USAGE)
    if not matches:
        raise ValueError(f"Unknown rule: {identifier}")
    if len(matches) > 1:
        families = ", ".join(family for family, _rule in matches)
        raise ValueError(
            f"Ambiguous rule: {identifier}; families: {families}. "
            "Use inspect --contracts PACK rule FAMILY ID"
        )
    return matches[0][1]
