# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.

from pathlib import Path

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle


def test_starter_pack_loads_and_selects_control_review() -> None:
    bundle = YamlContractsAdapter().load("frameworks/starter")
    plan = assess_from_bundle(
        bundle,
        {
            "answers": {
                "STARTER_Q1": "yes",
                "STARTER_Q2": "yes",
                "STARTER_Q3": "yes",
            }
        },
        templates_hash="starter-test",
    )

    assert "STARTER-CONTROL-REVIEW" in plan["actions"]
    assert "starter.needs_review" in plan["flags"]
    assert plan["trace"]["actions"]


def test_starter_demo_answers_exist() -> None:
    assert Path("demos/starter/basic/answers.json").is_file()
