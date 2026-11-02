# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Assessment metadata and JSON artifacts contain only finite numbers."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.app.cli.main import main
from intrinsical_policy_engine.app.export.artifacts import write_json
from intrinsical_policy_engine.domain.input_models import AssessmentInput
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle

NONFINITE = [float("nan"), float("inf"), -float("inf")]
NUMBER_IDS = ["nan", "positive-infinity", "negative-infinity"]


@pytest.mark.parametrize("number", NONFINITE, ids=NUMBER_IDS)
@pytest.mark.parametrize(
    "section", ["system", "provider", "owner", "declared_by", "approvals", "meta"]
)
def test_input_rejects_nested_nonfinite_metadata(section: str, number: float) -> None:
    raw = {"flags": [], section: {"nested": [{"values": [1, {"measure": number}]}]}}
    with pytest.raises(ValidationError, match="finite"):
        AssessmentInput.model_validate(raw)


@pytest.mark.parametrize("number", NONFINITE, ids=NUMBER_IDS)
def test_library_assessment_rejects_nested_nonfinite_metadata(number: float) -> None:
    bundle = YamlContractsAdapter().compile("frameworks/starter").bundle
    raw = {"flags": [], "meta": {"nested": [{"measure": number}]}}
    with pytest.raises(ValidationError, match="finite"):
        assess_from_bundle(bundle, raw, include_full_trace=True)


@pytest.mark.parametrize("command", ["assess", "export"])
@pytest.mark.parametrize(
    "suffix,content",
    [
        (".yaml", "flags: []\nmeta:\n  nested:\n    - measure: .nan\n"),
        (".yaml", "flags: []\nmeta:\n  nested:\n    - measure: .inf\n"),
        (".yaml", "flags: []\nmeta:\n  nested:\n    - measure: -.inf\n"),
        (".json", '{"flags":[],"meta":{"nested":[{"measure":1e1000}]}}'),
        (".json", '{"flags":[],"meta":{"nested":[{"measure":-1e1000}]}}'),
    ],
    ids=[
        "yaml-nan",
        "yaml-infinity",
        "yaml-negative-infinity",
        "json-overflow",
        "json-negative-overflow",
    ],
)
def test_cli_rejects_nonfinite_raw_input_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    suffix: str,
    content: str,
) -> None:
    answers = tmp_path / f"answers{suffix}"
    answers.write_text(content, encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ipe",
            command,
            "--contracts",
            "frameworks/starter",
            "--answers",
            str(answers),
            "--out",
            str(output),
            "--include-raw-input",
        ],
    )
    assert main() == 1
    assert "finite" in capsys.readouterr().err
    assert set(tmp_path.iterdir()) == {answers}


@pytest.mark.parametrize("number", NONFINITE, ids=NUMBER_IDS)
def test_json_writer_rejects_nonfinite_without_creating_destination(
    tmp_path: Path, number: float
) -> None:
    destination = tmp_path / "metadata" / "summary.json"
    with pytest.raises(ValueError):
        write_json(destination, {"nested": [{"measure": number}]})
    assert not destination.parent.exists()


def test_finite_nested_metadata_and_nonfinite_strings_roundtrip(tmp_path: Path) -> None:
    metadata = {"nested": [{"values": [0, 1e308, -1e308, "NaN", "Infinity", None, True]}]}
    validated = AssessmentInput.model_validate({"flags": [], "meta": metadata})
    destination = tmp_path / "metadata" / "summary.json"
    write_json(destination, validated.meta.model_dump())
    assert json.loads(destination.read_text(encoding="utf-8")) == metadata
