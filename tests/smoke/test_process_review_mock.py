"""Independent expected scenarios exercise an external mock pack through the CLI."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from intrinsical_policy_engine.app.cli.main import main

DEMO = Path(__file__).resolve().parents[2] / "demos/process-review"
PACK = DEMO / "pack"
SCENARIOS = json.loads((DEMO / "expected/scenarios.json").read_text(encoding="utf-8"))


def invoke(monkeypatch: pytest.MonkeyPatch, *arguments: str | Path) -> int:
    monkeypatch.setattr(sys, "argv", ["ipe", *map(str, arguments)])
    return main()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def conclusions(plan: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: plan[key]
        for key in (
            "flags",
            "actions",
            "outcome",
            "risk_tier",
            "outcome_axes",
            "routing",
            "calendar",
            "due_hints",
            "actions_evidence_map",
            "articles_evidence_map",
            "articles_overlay",
        )
    }
    trace = plan["trace"]
    result.update(
        emitted_flags=trace["flags"]["emitted"],
        derived_flags=trace["flags"]["derived"],
        derivation_ids=[rule["id"] for rule in trace["derivations_applied"]],
        stop=trace["rules_applied"]["stops"],
    )
    return result


def test_mock_pack_is_relocatable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    relocated = tmp_path / "external-pack"
    shutil.copytree(PACK, relocated)
    assert invoke(monkeypatch, "lint", "--contracts", relocated, "--strict") == 0
    output = tmp_path / "assessment"
    assert (
        invoke(
            monkeypatch,
            "assess",
            "--contracts",
            relocated,
            "--answers",
            DEMO / "inputs/review.answers.json",
            "--out",
            output,
        )
        == 0
    )
    expected = next(
        scenario["expected"] for scenario in SCENARIOS if scenario["id"] == "review-answers"
    )
    assert conclusions(read_json(output / "_metadata/summary.json")) == expected


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario["id"])
def test_mock_expected_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    scenario: dict[str, Any],
) -> None:
    common: list[str | Path] = [
        "--contracts",
        PACK,
        "--answers",
        DEMO / scenario["input_file"],
    ]
    assert read_json(DEMO / scenario["input_file"]) == scenario["input"]
    if "base_date" in scenario:
        common.extend(["--base-date", scenario["base_date"]])
    assessment = tmp_path / "assessment"
    assert invoke(monkeypatch, "assess", *common, "--out", assessment) == int("error" in scenario)
    if "error" in scenario:
        assert not assessment.exists()
        assert scenario["error"] in capsys.readouterr().err
        return

    expected = scenario["expected"]
    plan = read_json(assessment / "_metadata/summary.json")
    assert conclusions(plan) == expected
    if "equivalent_input" in scenario:
        equivalent = tmp_path / "equivalent"
        assert (
            invoke(
                monkeypatch,
                "assess",
                "--contracts",
                PACK,
                "--answers",
                DEMO / scenario["equivalent_input"],
                "--out",
                equivalent,
            )
            == 0
        )
        equivalent_plan = read_json(equivalent / "_metadata/summary.json")
        assert conclusions(equivalent_plan) == expected
        assert equivalent_plan["trace"]["plan_hash"] == plan["trace"]["plan_hash"]

    exported = tmp_path / "export"
    source = DEMO / scenario["evidence"]
    assert invoke(
        monkeypatch,
        "export",
        *common,
        "--evidence-dir",
        source,
        "--out",
        exported,
        "--check-ci",
        "--strict",
    ) == int("export_error" in scenario)
    if "export_error" in scenario:
        assert not exported.exists()
        assert scenario["export_error"] in capsys.readouterr().err
        return

    assert conclusions(read_json(exported / "_metadata/summary.json")) == expected
    assert "EU AI Act process mock" in (exported / "README.md").read_text(encoding="utf-8")
    assert "no legal fidelity" in (exported / "README.md").read_text(encoding="utf-8")
    expected_evidence = sorted(
        {path for paths in expected["actions_evidence_map"].values() for path in paths}
    )
    assert read_json(exported / "_metadata/evidence_manifest.json")["included"] == expected_evidence
    for relative in expected_evidence:
        assert (exported / "evidence" / relative).read_bytes() == (source / relative).read_bytes()
    calendar = exported / "compliance.ics"
    assert calendar.exists() is bool(expected["due_hints"])
    if calendar.exists():
        content = calendar.read_text(encoding="utf-8")
        for deadline in expected["due_hints"].values():
            assert deadline.replace("-", "") in content

    archive = tmp_path / "snapshot.zip"
    assert (
        invoke(
            monkeypatch,
            "seal",
            "--export-dir",
            exported,
            "--output",
            archive,
            "--strict",
            "--no-sign",
        )
        == 0
    )
    files = {
        path.relative_to(exported).as_posix(): path.read_bytes()
        for path in exported.rglob("*")
        if path.is_file()
    }
    with zipfile.ZipFile(archive) as packaged:
        assert set(packaged.namelist()) == set(files)
        for relative, content in files.items():
            assert packaged.read(relative) == content
