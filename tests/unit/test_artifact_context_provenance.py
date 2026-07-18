# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Artifact presentation must not invent risk or host-build provenance."""

from __future__ import annotations

from intrinsical_policy_engine.app.config.context import (
    build_artifact_context,
    get_classification_display,
)


def test_unknown_classification_is_not_presented_as_the_lowest_tier() -> None:
    display = get_classification_display("pack-specific-outcome")

    assert display["label_en"] == "UNCLASSIFIED"
    assert display["severity"] == 0


def test_pack_can_supply_display_for_an_opaque_classification_key() -> None:
    display = get_classification_display(
        "pack-specific-outcome",
        classification_key="tier-zeta",
        display_map={
            "tier-zeta": {
                "label": "REVISIÓN REQUERIDA",
                "label_en": "REVIEW REQUIRED",
                "css_class": "warning",
                "severity": 2,
            }
        },
    )

    assert display == {
        "emoji": "",
        "label": "REVISIÓN REQUERIDA",
        "label_en": "REVIEW REQUIRED",
        "css_class": "warning",
        "severity": 2,
    }


def test_malformed_pack_display_severity_is_treated_as_unspecified() -> None:
    display = get_classification_display(
        "pack-specific-outcome",
        classification_key="tier-zeta",
        display_map={
            "tier-zeta": {
                "label": "REVIEW REQUIRED",
                "severity": "critical",
            }
        },
    )

    assert display["severity"] == 0


def test_engine_provenance_comes_from_plan_trace_not_the_host_checkout(tmp_path) -> None:
    context = build_artifact_context(
        {
            "trace": {
                "engine_version": "3.0.0a1",
                "framework_version": "8.4.1",
                "framework_pack_hash": "abc123",
            },
            "artifact_schema_version": "3.0.0a1",
        },
        framework_path=tmp_path,
    )

    assert context["engine"] == {
        "name": "intrinsical-policy-engine",
        "version": "3.0.0a1",
        "commit": "unknown",
    }
    assert context["meta"]["framework_version"] == "8.4.1"
    assert context["meta"]["pack_hash"] == "abc123"
