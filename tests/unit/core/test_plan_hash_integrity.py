# SPDX-License-Identifier: MPL-2.0
"""Regression coverage for the public plan-integrity boundary."""

from __future__ import annotations

from intrinsical_policy_engine.domain.services.integrity import compute_plan_hash


def test_export_provenance_does_not_change_assessment_plan_hash() -> None:
    assessment_plan = {
        "flags": {"is_applicable": True},
        "actions": [{"id": "ACTION-1"}],
        "trace": {"framework_pack_hash": "pack-hash"},
    }
    exported_plan = {
        **assessment_plan,
        "artifact_schema_version": "3.0.0a1",
        "pack": {
            "id": "starter",
            "version": "0.1.0",
            "manifest_timestamp": "2026-07-18T00:00:00Z",
        },
        "product": {"name": "lexops", "version": "3.0.0a1"},
        "product_name": "lexops",
        "product_version": "3.0.0a1",
    }

    assert compute_plan_hash(exported_plan) == compute_plan_hash(assessment_plan)


def test_semantic_plan_changes_still_change_assessment_plan_hash() -> None:
    first_plan = {
        "flags": {"is_applicable": True},
        "actions": [{"id": "ACTION-1"}],
    }
    changed_plan = {
        **first_plan,
        "actions": [{"id": "ACTION-2"}],
    }

    assert compute_plan_hash(changed_plan) != compute_plan_hash(first_plan)


def test_volatile_names_nested_in_semantic_data_are_still_hashed() -> None:
    first_plan = {
        "actions": [{"id": "ACTION-1"}],
        "constraints": {"product": "medical-device"},
    }
    changed_plan = {
        **first_plan,
        "constraints": {"product": "credit-scoring"},
    }

    assert compute_plan_hash(changed_plan) != compute_plan_hash(first_plan)
