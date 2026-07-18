# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Lint and load must agree on the canonical typed contract surface."""

from pathlib import Path

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)


def test_typed_lint_reports_unknown_rule_fields(tmp_path: Path) -> None:
    contracts = {
        "flags": {"version": "1.0.0", "registry": []},
        "actions": {"version": "1.0.0", "actions": []},
        "rules": {
            "version": "1.0.0",
            "derivations": [],
            "packs": [],
            "stops": [],
            "legacy_router": {"enabled": True},
        },
        "articles": {"version": "1.0.0", "taxonomy": []},
        "runtime": {
            "semantics": {"framework_id": "typed-lint-test"},
            "policies": {},
            "presentation": {},
        },
    }

    errors = YamlContractsAdapter._validate_typed_contracts(contracts, tmp_path, {})

    assert len(errors) == 1
    assert errors[0].startswith("[MODEL][ERROR]")
    assert "legacy_router" in errors[0]
