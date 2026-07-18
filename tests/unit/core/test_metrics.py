# SPDX-License-Identifier: MPL-2.0
"""Framework-neutral metric calculations."""

from __future__ import annotations

from intrinsical_policy_engine.domain.services.metrics import (
    HITL_SOURCE_CLASSIFICATION,
    HITL_SOURCE_DEFAULT,
    HITL_SOURCE_TRANSPARENCY,
    calculate_techdoc_stats,
    compute_hitl_signal,
    compute_metrics,
)
from intrinsical_policy_engine.domain.types import Plan


def test_classification_flags_raise_review_signal() -> None:
    score, source = compute_hitl_signal(["classification.sensitive", "classification.material"])
    assert score == 0.85
    assert source == HITL_SOURCE_CLASSIFICATION


def test_not_review_classification_uses_default_signal() -> None:
    score, source = compute_hitl_signal(["classification.sensitive", "classification.not_review"])
    assert score == 0.10
    assert source == HITL_SOURCE_DEFAULT


def test_transparency_flags_raise_low_review_signal() -> None:
    score, source = compute_hitl_signal(["transparency.notice"])
    assert score == 0.25
    assert source == HITL_SOURCE_TRANSPARENCY


def test_compute_metrics_uses_plan_structure() -> None:
    plan: Plan = {
        "articles_overlay": {"TOPIC-A": ["CONTROL-A"], "TOPIC-B": ["CONTROL-B", "CONTROL-C"]},
        "actions": ["CONTROL-A", "CONTROL-B", "CONTROL-C", "HITL-REVIEW"],
        "actions_meta": [
            {"id": "CONTROL-A", "legal_refs": ["Source A"]},
            {"id": "CONTROL-B", "legal_refs": ["Source B"]},
        ],
        "flags": ["classification.material"],
    }

    metrics = compute_metrics(plan)

    assert metrics["coverage_TOPIC-A"] == 0.25
    assert metrics["coverage_TOPIC-B"] == 0.5
    assert metrics["hitl_required"] == 0.25
    assert metrics["hitl_index"] == 0.85
    assert metrics["sources"] == ["Source A", "Source B"]


def test_techdoc_stats_use_backlog_completion() -> None:
    stats = calculate_techdoc_stats(
        {
            "backlog": {
                "primary": [
                    {"status": "Done"},
                    {"status": "In Progress"},
                ],
                "secondary": [{"status": "Completed"}],
            }
        }
    )

    assert stats == {"progress_pct": 66, "completed": 2, "total": 3}
