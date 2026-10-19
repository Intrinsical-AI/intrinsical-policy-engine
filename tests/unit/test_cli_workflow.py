# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""CLI regressions exercise rejection, closed output and producer/consumer parity."""

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from intrinsical_policy_engine.app.cli.main import main
from intrinsical_policy_engine.common.document_io import read_document


@pytest.fixture
def fixtures(tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    evidence = tmp_path / "evidence-source"
    shutil.copytree("demos/starter/basic/evidence", evidence)
    answers = tmp_path / "answers.json"
    data = read_document(Path("demos/starter/basic/answers.json"))
    data["system"] = {"name": "Private workflow", "id": "Private identity"}
    data["owner"] = {"name": "Private owner"}
    answers.write_text(json.dumps(data))
    return pack, evidence, answers


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["ipe", *map(str, args)])
    return main()


@pytest.mark.parametrize("command", ["assess", "export"])
def test_check_ci_rejects_absent_and_unusable_evidence(monkeypatch, fixtures, tmp_path, command):
    pack, evidence, answers = fixtures
    output = tmp_path / "output"
    base = [command, "--contracts", pack, "--answers", answers, "--out", output, "--check-ci"]
    assert invoke(monkeypatch, *base) == 1
    assert not output.exists()
    file = evidence / "starter" / "control-review.md"
    file.write_text("---\nstatus: ready\n---\n## Review\nIncomplete.")
    assert invoke(monkeypatch, *base, "--evidence-dir", evidence) == 1
    assert not output.exists()


def test_closed_export_seal_zip_and_redaction(monkeypatch, fixtures, tmp_path):
    pack, evidence, answers = fixtures
    output = tmp_path / "output"
    assert (
        invoke(
            monkeypatch,
            "export",
            "--contracts",
            pack,
            "--answers",
            answers,
            "--out",
            output,
            "--evidence-dir",
            evidence,
            "--check-ci",
            "--strict",
        )
        == 0
    )
    source = evidence / "starter" / "control-review.md"
    assert (
        output / "evidence" / "starter" / "control-review.md"
    ).read_bytes() == source.read_bytes()
    summary_path = output / "_metadata" / "summary.json"
    summary = read_document(summary_path)
    text = summary_path.read_text()
    assert (
        "Private workflow" not in text
        and "Private owner" not in text
        and "Private identity" not in text
    )
    assert (
        summary["trace"]["answers_raw"]["source_file_sha256"]
        == hashlib.sha256(answers.read_bytes()).hexdigest()
    )
    assert summary["trace"] == read_document(output / "_metadata" / "trace.json")
    assert not (output / "fingerprint.json").exists()
    archive = tmp_path / "export.zip"
    before = {
        p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()
    }
    assert (
        invoke(
            monkeypatch,
            "seal",
            "--export-dir",
            output,
            "--output",
            archive,
            "--no-sign",
            "--strict",
        )
        == 0
    )
    assert before == {
        p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()
    }
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == set(before)
        assert all(bundle.read(name) == data for name, data in before.items())
    assert (
        invoke(monkeypatch, "export", "--contracts", pack, "--answers", answers, "--out", output)
        == 1
    )
    assert summary_path.read_bytes() == before["_metadata/summary.json"]


@pytest.mark.parametrize(
    "data",
    [
        {"answers": {}, "flags": []},
        {"flags": ["unknown"]},
        {"flags": "starter.in_scope"},
        {"answers": {"unknown_question": "yes"}},
        {"answers": {}, "system": {"name": 1}},
    ],
)
def test_invalid_input_is_fatal_without_strict(monkeypatch, fixtures, tmp_path, data):
    pack, _evidence, answers = fixtures
    answers.write_text(json.dumps(data))
    output = tmp_path / "output"
    assert (
        invoke(monkeypatch, "assess", "--contracts", pack, "--answers", answers, "--out", output)
        == 1
    )
    assert not output.exists()


def test_unknown_target_and_inspect_missing_are_fatal(monkeypatch, fixtures, tmp_path):
    pack, _evidence, answers = fixtures
    output = tmp_path / "output"
    assert (
        invoke(
            monkeypatch,
            "export",
            "--contracts",
            pack,
            "--answers",
            answers,
            "--out",
            output,
            "--target",
            "jinja",
        )
        == 1
    )
    assert not output.exists()
    assert invoke(monkeypatch, "inspect", "--contracts", pack, "rule", "missing") == 1


def test_raw_input_opt_in_preserves_identity(monkeypatch, fixtures, tmp_path):
    pack, _evidence, answers = fixtures
    output = tmp_path / "output"
    assert (
        invoke(
            monkeypatch,
            "assess",
            "--contracts",
            pack,
            "--answers",
            answers,
            "--out",
            output,
            "--include-raw-input",
        )
        == 0
    )
    summary = read_document(output / "_metadata" / "summary.json")
    assert summary["system"]["name"] == "Private workflow"
    assert summary["trace"]["answers_raw"]["input"]["answers"] == read_document(answers)["answers"]


def test_duplicate_json_and_invalid_config_rejected(monkeypatch, fixtures, tmp_path):
    pack, _evidence, answers = fixtures
    output = tmp_path / "output"
    answers.write_text('{"flags": [], "flags": []}')
    assert (
        invoke(monkeypatch, "assess", "--contracts", pack, "--answers", answers, "--out", output)
        == 1
    )
    config = tmp_path / "config.yml"
    config.write_text("asana:\n  project_gid: 123\n  base_url: nope\n")
    assert (
        invoke(
            monkeypatch,
            "export",
            "--contracts",
            pack,
            "--answers",
            answers,
            "--out",
            output,
            "--config",
            config,
            "--target",
            "asana",
        )
        == 1
    )
    assert not output.exists()
