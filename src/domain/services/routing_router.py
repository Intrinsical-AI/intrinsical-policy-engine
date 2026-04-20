# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Framework-driven routing routing logic."""

from __future__ import annotations

from typing import Any

from src.domain.contract_models import FrameworkRuntime, RuntimeRoutingPolicy
from src.domain.exceptions import AssessmentError
from src.domain.services.rule_engine import eval_ast, parse_when
from src.domain.types import Flag


def _resolve_route_labels(
    policy: RuntimeRoutingPolicy,
    runtime: FrameworkRuntime | None,
) -> tuple[str, str, str | None, str]:
    """Resolve route labels from runtime semantics when available.

    The framework pack owns the canonical route names. Policy defaults remain
    as a compatibility fallback for incomplete test fixtures.
    """

    semantic_routes: list[str] = []
    if runtime is not None:
        semantic_routes = [str(route) for route in getattr(runtime.semantics, "routes", [])]

    preferred_route = semantic_routes[0] if len(semantic_routes) >= 1 else policy.preferred_route
    alternative_route = (
        semantic_routes[1] if len(semantic_routes) >= 2 else policy.alternative_route
    )
    impact_review_only_route = (
        semantic_routes[2] if len(semantic_routes) >= 3 else policy.impact_review_only_route
    )

    if not preferred_route or not alternative_route:
        raise AssessmentError("routing route labels are not configured")

    source = "runtime" if semantic_routes else "default"
    return preferred_route, alternative_route, impact_review_only_route, source


def resolve_routing_route(
    final_flags: set[Flag],
    selected_articles: set[str],
    rules_contract,
    outcome_axes: dict[str, Any] | None = None,
    runtime: FrameworkRuntime | None = None,
) -> dict[str, Any]:
    """Resolve the active routing route for the selected framework."""

    if not hasattr(rules_contract, "routing_router"):
        raise TypeError("rules_contract must be a RulesContract")

    router_config = rules_contract.routing_router

    policy = runtime.policies.routing if runtime is not None else None
    if policy is None:
        policy = RuntimeRoutingPolicy()

    (
        preferred_route,
        alternative_route,
        impact_review_only_route,
        route_source,
    ) = _resolve_route_labels(policy, runtime)
    review_tiers = set(policy.review_tiers)
    impact_review_flags = set(policy.impact_review_required_flags)

    axes = outcome_axes or {}
    risk_tier = str(axes.get("risk_tier") or "none")
    has_review = risk_tier in review_tiers
    impact_review_required = bool(impact_review_flags.intersection(final_flags))

    if impact_review_required and not has_review:
        return {
            "route": impact_review_only_route,
            "base_route": impact_review_only_route,
            "source": route_source,
            "reason": "impact_review_only",
            "enforce": False,
            "enforced": False,
            "safety_override": False,
            "override_flags": [],
            "alternative_route": None,
            "excluded_actions": [],
        }

    prefer_primary = False
    reason = "default"
    source = "default"
    enforce = False

    article_ids = {str(article) for article in selected_articles or set()}
    runtime_prefer_primary_flags = list(policy.prefer_primary_if_flags)
    runtime_prefer_primary_articles = list(policy.prefer_primary_if_articles)
    yaml_prefer_primary_articles: list[str] = []

    if router_config is not None:
        source = "yaml"
        if hasattr(router_config, "enforce"):
            enforce = bool(router_config.enforce)
            prefer_primary_exprs = router_config.prefer_primary_if_flags or []
            yaml_prefer_primary_articles = list(router_config.prefer_primary_if_topics or [])
            forcing_flags = list(router_config.force_alternative_if_flags or [])
        else:
            raise TypeError("rules_contract.routing_router must be a RoutingRouterConfig")

        for expr in prefer_primary_exprs:
            try:
                ast = parse_when(expr)
                if eval_ast(ast, final_flags):
                    prefer_primary = True
                    reason = "yaml_flags"
                    break
            except Exception as exc:
                raise AssessmentError(
                    f"Invalid routing_router flag condition '{expr}': {exc}"
                ) from exc
    else:
        forcing_flags = []

    if (
        not prefer_primary
        and yaml_prefer_primary_articles
        and any(
            any(article.startswith(prefix) for article in article_ids)
            for prefix in yaml_prefer_primary_articles
        )
    ):
        prefer_primary = True
        source = "yaml"
        reason = "yaml_articles"

    if not prefer_primary and runtime_prefer_primary_flags:
        prefer_primary = any(flag in final_flags for flag in runtime_prefer_primary_flags)
        if prefer_primary:
            source = route_source
            reason = "heuristic"

    if (
        not prefer_primary
        and runtime_prefer_primary_articles
        and any(
            any(article.startswith(prefix) for article in article_ids)
            for prefix in runtime_prefer_primary_articles
        )
    ):
        prefer_primary = True
        source = route_source
        reason = "heuristic"

    base_route = preferred_route if prefer_primary else alternative_route
    forcing = {flag for flag in final_flags if flag in set(forcing_flags)}
    safety_override = bool(forcing)

    if safety_override:
        route = alternative_route
        reason = "safety_override"
        alternative = preferred_route
    else:
        route = base_route
        alternative = None

    route_action_exclusions = policy.route_action_exclusions if policy is not None else {}
    excluded_actions = list(route_action_exclusions.get(route, []))

    return {
        "route": route,
        "base_route": base_route,
        "source": source,
        "reason": reason,
        "enforce": enforce,
        "enforced": bool(enforce),
        "safety_override": safety_override,
        "override_flags": sorted(forcing),
        "alternative_route": alternative,
        "excluded_actions": excluded_actions,
    }
