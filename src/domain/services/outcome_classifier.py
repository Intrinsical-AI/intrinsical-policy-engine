# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Outcome classification driven by framework pack YAML."""

from __future__ import annotations

from typing import Any

from src.domain.exceptions import AssessmentError
from src.domain.services.rule_engine import eval_ast, parse_when
from src.domain.types import Flag


def _classify_stop_outcome_axes(stop_outcome: dict) -> dict[str, Any]:
    """Map stop outcomes to a neutral axes shape."""

    outcome = str(stop_outcome.get("outcome") or "unknown")
    if outcome == "blocked":
        tier = "blocked"
    elif outcome in {"out_of_scope", "out_of_scope_territorial", "excluded"}:
        tier = "none"
    else:
        tier = "none"
    return {"risk_tier": tier, "roles": [], "regimes": [outcome]}


def _extract_roles_from_yaml(
    final_flags: set[Flag], role_mapping: list[dict[str, str]]
) -> list[str]:
    roles = [entry["role"] for entry in role_mapping if entry.get("flag") in final_flags]
    return sorted(set(roles))


def _evaluate_risk_tiers_from_yaml(
    final_flags: set[Flag],
    stop_outcome: dict | None,
    risk_tiers: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    if stop_outcome:
        axes = _classify_stop_outcome_axes(stop_outcome)
        return str(axes["risk_tier"]), [str(item) for item in axes["regimes"]]

    sorted_tiers = sorted(risk_tiers, key=lambda item: -int(item.get("priority", 0)))
    tier = "none"
    regimes: list[str] = []

    for tier_rule in sorted_tiers:
        when = tier_rule.get("when")
        try:
            ast = parse_when(when)
            if eval_ast(ast, final_flags):
                output = tier_rule.get("output", {}) or {}
                if tier == "none":
                    tier = str(output.get("tier") or "none")
                regimes.extend(str(regime) for regime in output.get("regimes", []) or [])
        except Exception as exc:  # noqa: BLE001
            raise AssessmentError(
                f"[RULE-ERR-01] Failed to evaluate risk tier rule '{when}': {exc}"
            ) from exc

    if tier == "none" and final_flags:
        tier = "other"
    if not regimes:
        regimes = ["none"]
    return tier, sorted(set(regimes))


def _classify_axes_from_yaml(
    final_flags: set[Flag],
    stop_outcome: dict | None,
    classifiers_config: dict[str, Any],
) -> dict[str, Any]:
    role_mapping = classifiers_config.get("role_mapping", []) or []
    risk_tiers = classifiers_config.get("risk_tiers", []) or []
    roles = _extract_roles_from_yaml(final_flags, role_mapping)
    tier, regimes = _evaluate_risk_tiers_from_yaml(final_flags, stop_outcome, risk_tiers)
    return {"risk_tier": tier, "roles": roles, "regimes": regimes}


def _matches_outcome_rule(
    rule: dict[str, Any],
    axes: dict[str, Any],
    final_flags: set[Flag],
    stop_outcome: dict | None,
) -> bool:
    when_stop_outcome = rule.get("when_stop_outcome")
    if when_stop_outcome is not None:
        if not stop_outcome:
            return False
        if str(when_stop_outcome) != str(stop_outcome.get("outcome")):
            return False

    when_tier = rule.get("when_tier")
    if when_tier is not None and str(axes.get("risk_tier")) != str(when_tier):
        return False

    when_roles_raw = rule.get("when_roles")
    if isinstance(when_roles_raw, list):
        axes_roles = {str(role) for role in axes.get("roles") or []}
        required_roles = {str(role) for role in when_roles_raw}
        # Explicit semantics: `when_roles: []` (empty list) means the subject
        # MUST have zero roles. A non-empty list means all listed roles must be
        # present. A missing/None value means "don't care about roles".
        if not required_roles:
            if axes_roles:
                return False
        elif not required_roles.issubset(axes_roles):
            return False

    when_flags = rule.get("when_flags")
    if when_flags:
        try:
            ast = parse_when(when_flags)
            if not eval_ast(ast, final_flags):
                return False
        except Exception as exc:  # noqa: BLE001
            raise AssessmentError(
                f"[RULE-ERR-02] Failed to evaluate outcome rule "
                f"'{rule.get('id', '<unknown>')}': {exc}"
            ) from exc

    return True


def _classify_outcome_from_axes(
    axes: dict[str, Any],
    final_flags: set[Flag],
    stop_outcome: dict | None,
    classifiers_config: dict[str, Any] | None,
) -> set[str]:
    outcome_rules = (
        (classifiers_config or {}).get("outcome_rules", [])
        if isinstance(classifiers_config, dict)
        else []
    )
    outcomes: set[str] = set()

    for rule in outcome_rules or []:
        if not isinstance(rule, dict):
            continue
        if _matches_outcome_rule(rule, axes, final_flags, stop_outcome):
            outcome = rule.get("outcome")
            if outcome:
                outcomes.add(str(outcome))

    if outcomes:
        return outcomes

    if stop_outcome and stop_outcome.get("outcome"):
        return {str(stop_outcome["outcome"])}

    return set()


def classify_outcome_from_axes(
    axes: dict[str, Any],
    final_flags: set[Flag],
    stop_outcome: dict | None,
    classifiers_config: dict[str, Any] | None = None,
) -> set[str]:
    """Derive outcome labels from precomputed axes."""

    return _classify_outcome_from_axes(axes, final_flags, stop_outcome, classifiers_config)


def classify_outcome(
    final_flags: set[Flag],
    stop_outcome: dict | None,
    classifiers_config: dict[str, Any] | None = None,
) -> set[str]:
    """Derive high-level outcome labels from YAML classifiers."""

    axes = classify_outcome_axes(final_flags, stop_outcome, classifiers_config)
    return classify_outcome_from_axes(axes, final_flags, stop_outcome, classifiers_config)


def classify_outcome_axes(
    final_flags: set[Flag],
    stop_outcome: dict | None,
    classifiers_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive tier/roles/regimes from YAML classifiers."""

    if stop_outcome:
        return _classify_stop_outcome_axes(stop_outcome)
    if classifiers_config and classifiers_config.get("risk_tiers"):
        return _classify_axes_from_yaml(final_flags, stop_outcome, classifiers_config)
    return {"risk_tier": "none", "roles": [], "regimes": ["none"]}
