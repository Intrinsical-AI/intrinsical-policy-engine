# SPDX-License-Identifier: MPL-2.0
"""Runtime validation contracts for the public Plan TypedDict shape."""

from __future__ import annotations

from typing import cast

from intrinsical_policy_engine.domain.types import Plan, validate_plan_keys


def test_valid_plan_keys_return_no_warnings() -> None:
    plan: Plan = {
        "flags": ["scope.active"],
        "actions": ["CONTROL-REVIEW"],
        "routing": {"route": "manual_review"},
    }

    assert validate_plan_keys(plan) == []


def test_export_envelope_provenance_keys_are_part_of_the_plan_shape() -> None:
    plan: Plan = {
        "artifact_schema_version": "3.0.0a1",
        "pack": {"id": "starter", "version": "0.1.0"},
        "product": {"name": "consumer", "version": "7.2.0"},
        "product_name": "consumer",
        "product_version": "7.2.0",
    }

    assert validate_plan_keys(plan) == []


def test_typo_is_reported_with_suggestion() -> None:
    plan = cast(Plan, {"routng": {"route": "manual_review"}})

    warnings = validate_plan_keys(plan)

    assert len(warnings) == 1
    assert "routng" in warnings[0]
    assert "routing" in warnings[0]


def test_unknown_key_without_similar_match_has_no_suggestion() -> None:
    plan = cast(Plan, {"unknown_extension_key": "value"})

    warnings = validate_plan_keys(plan)

    assert len(warnings) == 1
    assert "unknown_extension_key" in warnings[0]
    assert "did you mean" not in warnings[0]


def test_multiple_unknown_keys_are_sorted() -> None:
    plan = cast(
        Plan,
        {
            "zzz_unknown": 1,
            "aaa_unknown": 2,
            "flags": [],
        },
    )

    warnings = validate_plan_keys(plan)

    assert len(warnings) == 2
    assert "aaa_unknown" in warnings[0]
    assert "zzz_unknown" in warnings[1]


def test_empty_plan_is_valid() -> None:
    assert validate_plan_keys(Plan()) == []


def test_common_key_typos_receive_a_suggestion() -> None:
    for typo in ("routng", "outcome_ax", "system_profi", "actions_meta_list"):
        warnings = validate_plan_keys(cast(Plan, {typo: "value"}))
        assert len(warnings) == 1
        assert "did you mean" in warnings[0]
