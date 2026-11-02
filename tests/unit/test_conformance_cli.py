"""Conformance observes an external executable against independent expectations."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from scripts import check_conformance as conformance

FIXTURES = Path(__file__).resolve().parents[2]


def invoke(
    monkeypatch: pytest.MonkeyPatch, fixtures: Path, workdir: Path, artifact: Path | None = None
) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_conformance.py",
            "--ipe",
            str(Path(sys.executable).parent / "ipe"),
            "--fixtures",
            str(fixtures),
            "--workdir",
            str(workdir),
            *(["--artifact", str(artifact)] if artifact is not None else []),
        ],
    )
    return conformance.main()


def test_full_independent_corpus_through_external_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = conformance.fixture_fingerprint(FIXTURES)
    workdir = tmp_path / "conformance"
    assert invoke(monkeypatch, FIXTURES, workdir) == 0
    receipt = json.loads((workdir / "conformance.json").read_text())
    assert receipt["schema"] == "ipe.conformance/1"
    assert receipt["status"] == "passed"
    assert receipt["scenarios"] == 10
    assert receipt["fixtures"]["verified_unchanged"] is True
    assert conformance.fixture_fingerprint(FIXTURES) == before
    labels = {record["check"] for record in receipt["checks"]}
    assert {
        "relocated mock lint",
        "inspect mock overview",
        "inspect routing process.route_review",
        "inspect tier process.stage_review",
        "inspect due process.due_review",
        "review-answers: equivalent flags",
        "approval: seal",
        "idle: seal",
        "owner-missing: export rejected",
        "missing-evidence: export rejected",
        "inactive-question: assess rejected",
        "inactive-question: export rejected",
        "invalid-option: assess rejected",
        "invalid-option: export rejected",
        "date-override: seal",
    } <= labels
    for record in receipt["checks"]:
        assert Path(record["command"][0]).is_absolute()
        assert str(FIXTURES / conformance.DEMO) not in json.dumps(record["command"])
    assert (workdir / "relocated-process-review/pack/manifest.yml").is_file()
    assert not (workdir / "scenarios/owner-missing/export").exists()
    assert not (workdir / "scenarios/missing-evidence/export").exists()


def test_conformance_detects_wrong_independent_trace_expectation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES / conformance.DEMO, fixtures / conformance.DEMO)
    shutil.copyfile(FIXTURES / "pyproject.toml", fixtures / "pyproject.toml")
    scenario_file = fixtures / conformance.DEMO / "expected/scenarios.json"
    scenario = json.loads(scenario_file.read_text())[0]
    scenario["expected"]["emitted_flags"] = []
    scenario_file.write_text(json.dumps([scenario]))
    workdir = tmp_path / "wrong-expected"
    assert invoke(monkeypatch, fixtures, workdir) == 1
    assert "emitted_flags" in capsys.readouterr().err
    receipt = json.loads((workdir / "conformance.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["error_type"] == "AcceptanceError"


def test_conclusions_do_not_accept_numeric_values_in_place_of_booleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = json.loads((FIXTURES / conformance.DEMO / "expected/scenarios.json").read_text())[0][
        "expected"
    ]
    plan = {key: expected[key] for key in conformance.CONCLUSION_FIELDS}
    plan["outcome_axes"] = {**expected["outcome_axes"], "blocked": 0}
    plan["trace"] = {
        "flags": {"emitted": expected["emitted_flags"], "derived": expected["derived_flags"]},
        "derivations_applied": [{"id": value} for value in expected["derivation_ids"]],
        "rules_applied": {"stops": expected["stop"]},
    }
    monkeypatch.setattr(conformance.acceptance, "json_object", lambda _path: plan)
    with pytest.raises(conformance.acceptance.AcceptanceError, match="outcome_axes"):
        conformance.check_plan(tmp_path, expected, "strict JSON conclusions")


@pytest.mark.parametrize("relative", ["../outside.json", "/outside.json", "folder\\file.json"])
def test_scenario_cannot_escape_relocated_fixtures(tmp_path: Path, relative: str) -> None:
    with pytest.raises(
        conformance.acceptance.AcceptanceError, match="unsafe scenario fixture path"
    ):
        conformance.fixture_path(tmp_path, relative)


def test_conformance_rejects_existing_workdir_without_modifying_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workdir = tmp_path / "occupied"
    workdir.mkdir()
    sentinel = workdir / "keep"
    sentinel.write_text("retain")
    assert invoke(monkeypatch, FIXTURES, workdir) == 1
    assert list(workdir.iterdir()) == [sentinel]
    assert sentinel.read_text() == "retain"


def test_conformance_receipt_binds_the_checked_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = tmp_path / "candidate.whl"
    payload = b"artifact identity fixture; structural scanning belongs to the release gate"
    artifact.write_bytes(payload)
    monkeypatch.setattr(conformance, "run_conformance", lambda _runner, _fixtures: 10)
    workdir = tmp_path / "artifact-receipt"
    assert invoke(monkeypatch, FIXTURES, workdir, artifact) == 0
    receipt = json.loads((workdir / "conformance.json").read_text())
    assert receipt["artifact"] == {
        "path": str(artifact),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    assert receipt["artifact_verified_unchanged"] is True


def test_changed_artifact_cannot_receive_successful_conformance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = tmp_path / "candidate.whl"
    artifact.write_bytes(b"original candidate")

    def changed(_runner, _fixtures):
        artifact.write_bytes(b"different candidate")
        return 10

    monkeypatch.setattr(conformance, "run_conformance", changed)
    workdir = tmp_path / "changed-artifact"
    assert invoke(monkeypatch, FIXTURES, workdir, artifact) == 1
    assert "artifact changed" in capsys.readouterr().err
    receipt = json.loads((workdir / "conformance.json").read_text())
    assert receipt["status"] == "failed" and receipt["artifact_verified_unchanged"] is False


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_unreadable_artifact_fails_before_any_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    artifact = tmp_path / "candidate.whl"
    if kind == "directory":
        artifact.mkdir()

    def no_scenarios(_runner, _fixtures):
        pytest.fail("invalid artifact reached conformance execution")

    monkeypatch.setattr(conformance, "run_conformance", no_scenarios)
    workdir = tmp_path / "invalid-artifact"
    assert invoke(monkeypatch, FIXTURES, workdir, artifact) == 1
    receipt = json.loads((workdir / "conformance.json").read_text())
    assert receipt["status"] == "failed" and receipt["checks"] == []
