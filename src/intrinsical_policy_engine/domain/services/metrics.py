# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Observed coverage counts and explicit pack-owned scoring signals."""

from typing import Any

from intrinsical_policy_engine.domain.conditions import evaluate_condition
from intrinsical_policy_engine.domain.contract_models import FrameworkRuntime


def compute_metrics(plan: dict, runtime: FrameworkRuntime) -> dict:
    actions = plan["actions"]
    metrics: dict[str, Any] = {"action_count": len(actions)}
    if actions:
        for article_id, related in plan["articles_overlay"].items():
            metrics[f"coverage_{article_id}"] = len(related) / len(actions)
    policy = runtime.policies.metrics
    metrics["hitl_index"] = policy.default_score
    metrics["hitl_signal_source"] = policy.default_source
    flags = set(plan["flags"])
    for signal in policy.signals:
        if evaluate_condition(signal.when, flags):
            metrics["hitl_index"] = signal.score
            metrics["hitl_signal_source"] = signal.source
            break
    metrics["sources"] = sorted(
        {ref for action in plan["actions_meta"] for ref in action["legal_refs"]}
    )
    return metrics
