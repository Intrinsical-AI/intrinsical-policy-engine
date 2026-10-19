# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Authoring inspection explains every rule family and never guesses across IDs."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.app.cli.main import main

DEMO = Path(__file__).resolve().parents[2] / "demos/process-review"


def invoke(monkeypatch: pytest.MonkeyPatch, pack: Path, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["ipe", "inspect", "--contracts", str(pack), *arguments])
    return main()


@pytest.mark.parametrize(
    "family,identifier",
    [
        ("derivation", "process.derive_review"),
        ("stop", "process.stop_owner_missing"),
        ("role", "process.coordinator_role"),
        ("tier", "process.stage_review"),
        ("outcome", "process.outcome_review"),
        ("routing", "process.route_review"),
        ("due", "process.due_review"),
    ],
)
def test_inspect_rule_families_match_independently_authored_records(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    family: str,
    identifier: str,
) -> None:
    corpus = json.loads((DEMO / "expected/inspection.json").read_text())
    expected = next(record for record in corpus["rules"][family] if record["id"] == identifier)
    assert invoke(monkeypatch, DEMO / "pack", "rule", identifier) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert invoke(monkeypatch, DEMO / "pack", "rule", family, identifier) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_inspect_overview_includes_the_complete_rule_catalog(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = json.loads((DEMO / "expected/inspection.json").read_text())
    assert invoke(monkeypatch, DEMO / "pack") == 0
    overview = json.loads(capsys.readouterr().out)
    assert overview["rules"] == {
        family: [record["id"] for record in records] for family, records in corpus["rules"].items()
    }
    assert overview["calendar"] == corpus["calendar"]


def test_duplicate_cross_family_rule_id_requires_explicit_family(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(DEMO / "pack", pack)
    rules_path = pack / "contracts/rules.yml"
    rules = yaml.safe_load(rules_path.read_text())
    rules["stops"][0]["id"] = rules["derivations"][0]["id"]
    rules_path.write_text(yaml.safe_dump(rules))
    identifier = "process.derive_intake"
    assert invoke(monkeypatch, pack, "rule", identifier) == 1
    error = capsys.readouterr().err
    assert "Ambiguous rule" in error and "derivation" in error and "stop" in error
    assert invoke(monkeypatch, pack, "rule", "stop", identifier) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "process.blocked"


def test_inspect_unknown_family_is_a_controlled_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert invoke(monkeypatch, DEMO / "pack", "rule", "unknown", "process.derive_review") == 1
    error = capsys.readouterr().err
    assert "Unknown rule family" in error and "Traceback" not in error
