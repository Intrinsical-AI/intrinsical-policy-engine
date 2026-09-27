# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Runtime-boundary tests for framework template integrity validation."""

import importlib.util
from pathlib import Path

from intrinsical_policy_engine.app.template_validation import validate_integrity


def test_repository_script_is_a_compatibility_wrapper() -> None:
    wrapper_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "validate_template_integrity.py"
    )
    spec = importlib.util.spec_from_file_location("ipe_template_integrity_wrapper", wrapper_path)
    assert spec is not None and spec.loader is not None
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)

    assert wrapper.validate_integrity is validate_integrity


def test_starter_template_integrity_uses_canonical_runtime_module() -> None:
    report = validate_integrity("frameworks/starter")

    assert report.has_errors is False
    assert report.stats["flags"] > 0
    assert report.stats["actions"] > 0
