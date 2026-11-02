"""Structured provisional evidence fails both CI and strict sealing."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.app.cli.main import main


def invoke(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["ipe", *map(str, arguments)])
    return main()


@pytest.mark.parametrize(
    "suffix,content,ready",
    [
        (".json", '{"ownership":"TODO","evidence":"TODO","review":"TODO"}', False),
        (".yml", "ownership: TODO\nevidence: TODO\nreview: TODO\n", False),
        (".csv", "ownership,evidence\n" + "TODO,TODO\n" * 5, False),
        (".json", '{"ownership":" ","evidence":"\\t","review":"\\n"}', False),
        (".yml", 'ownership: " "\nevidence: "\\t"\nreview: "\\n"\n', False),
        (".json", '{"ownership":{"value":null},"evidence":[null],"review":[""]}', False),
        (".yml", 'ownership: {value: null}\nevidence: [null]\nreview: [""]\n', False),
        (".json", '{"ownership":0,"evidence":false,"review":{"value":false}}', True),
        (".yml", "ownership: 0\nevidence: false\nreview: {value: false}\n", True),
        (".json", '{"ownership":"Todos","evidence":"metodología","review":"complete"}', True),
        (".yml", "ownership: Todos\nevidence: metodología\nreview: complete\n", True),
        (".csv", "ownership,evidence\n" + "Todos,metodología\n" * 5, True),
    ],
)
def test_structured_evidence_has_matching_ci_and_seal_quality(
    monkeypatch, tmp_path: Path, suffix, content, ready
):
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    relative = "starter/review" + suffix
    actions_path = pack / "law/core/actions.yml"
    actions = yaml.safe_load(actions_path.read_text())
    actions["actions"][0]["evidence"] = [relative]
    actions_path.write_text(yaml.safe_dump(actions))
    (pack / "law/policy/evidence_map.yml").write_text(
        yaml.safe_dump({"TOPIC-STARTER-CONTROLS": [{"path": relative}]})
    )
    evidence = tmp_path / "evidence"
    (evidence / "starter").mkdir(parents=True)
    (evidence / relative).write_text(content)
    common = (
        "--contracts",
        pack,
        "--answers",
        "demos/starter/basic/answers.json",
        "--evidence-dir",
        evidence,
    )
    assert invoke(monkeypatch, "lint", "--contracts", pack, "--strict") == 0
    ci_output = tmp_path / "ci-output"
    expected = 0 if ready else 1
    assert (
        invoke(monkeypatch, "export", *common, "--out", ci_output, "--check-ci", "--strict")
        == expected
    )
    assert ci_output.exists() is ready
    snapshot = tmp_path / "snapshot"
    assert invoke(monkeypatch, "export", *common, "--out", snapshot) == 0
    archive = tmp_path / "snapshot.zip"
    assert (
        invoke(
            monkeypatch,
            "seal",
            "--export-dir",
            snapshot,
            "--output",
            archive,
            "--no-sign",
            "--strict",
        )
        == expected
    )
    assert archive.exists() is ready


@pytest.mark.parametrize("marker", ["TODO", "lorem", "[FILL: owner]", "[REQUIRED: review]"])
@pytest.mark.parametrize("suffix", [".json", ".yaml"])
def test_nested_structured_values_cannot_hide_placeholders(suffix, marker):
    content = {"ownership": {"reviewers": [marker]}, "evidence": "reviewed", "review": "complete"}
    encoded = json.dumps(content) if suffix == ".json" else yaml.safe_dump(content)
    assert QualityEngine().classify_bytes(encoded.encode(), suffix) == "placeholder"


def test_complete_spanish_evidence_does_not_match_marker_inside_words():
    body = "## Revisión completada\n" + (
        "Todos los controles siguen una metodología documentada con responsables y resultados. "
        * 15
    )
    assert QualityEngine().classify_bytes(body.encode(), ".md") == "ready"


@pytest.mark.parametrize("characters,valid", [(131_072, True), (131_073, False)])
def test_csv_cell_limit_has_controlled_diagnostic(characters, valid):
    data = ("review,result\n" + ("A" * characters + ",complete\n") * 5).encode()
    if valid:
        assert QualityEngine().classify_bytes(data, ".csv") == "ready"
    else:
        with pytest.raises(ValueError, match=r"CSV.*131072"):
            QualityEngine().classify_bytes(data, ".csv")


def test_csv_parser_failure_is_an_operational_cli_error(monkeypatch, tmp_path, capsys):
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    relative = "starter/review.csv"
    actions_path = pack / "law/core/actions.yml"
    actions = yaml.safe_load(actions_path.read_text())
    actions["actions"][0]["evidence"] = [relative]
    actions_path.write_text(yaml.safe_dump(actions))
    (pack / "law/policy/evidence_map.yml").write_text(
        yaml.safe_dump({"TOPIC-STARTER-CONTROLS": [{"path": relative}]})
    )
    evidence = tmp_path / "evidence"
    (evidence / "starter").mkdir(parents=True)
    (evidence / relative).write_text("review,result\n" + "A" * 131_073 + ",complete\n")
    output = tmp_path / "output"
    assert (
        invoke(
            monkeypatch,
            "assess",
            "--contracts",
            pack,
            "--answers",
            "demos/starter/basic/answers.json",
            "--evidence-dir",
            evidence,
            "--out",
            output,
            "--check-ci",
        )
        == 1
    )
    assert not output.exists()
    error = capsys.readouterr().err
    assert "CSV" in error and "131072" in error and "Traceback" not in error
