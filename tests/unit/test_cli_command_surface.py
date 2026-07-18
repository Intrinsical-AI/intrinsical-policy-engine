# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Contracts for the public CLI commands backed by maintained runtime modules."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import intrinsical_policy_engine.app.use_cases.seal as seal_module
from intrinsical_policy_engine.adapters.ui import server
from intrinsical_policy_engine.app.cli.commands import core, dev, ops
from intrinsical_policy_engine.app.cli.main import main


def _command_parser(register) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipe")
    subparsers = parser.add_subparsers(dest="command")
    register(subparsers)
    return parser


def test_ops_help_only_lists_runtime_backed_render(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _command_parser(ops.register)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["ops", "--help"])

    assert exc_info.value.code == 0
    assert "{render}" in capsys.readouterr().out


@pytest.mark.parametrize("retired_command", ["pdf", "package", "drift"])
def test_ops_rejects_retired_script_commands(retired_command: str) -> None:
    parser = _command_parser(ops.register)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["ops", retired_command])

    assert exc_info.value.code == 2


def test_dev_help_only_lists_maintained_commands(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _command_parser(dev.register)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["dev", "--help"])

    assert exc_info.value.code == 0
    assert "{build-framework,graph}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "retired_command",
    ["generate-template", "concat", "dead-code", "profile"],
)
def test_dev_rejects_retired_script_commands(retired_command: str) -> None:
    parser = _command_parser(dev.register)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["dev", retired_command])

    assert exc_info.value.code == 2


def test_ui_reports_the_installed_extra_when_flask_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def missing_dependency(*args: object, **kwargs: object) -> None:
        raise ImportError("flask not installed")

    monkeypatch.setattr(server, "run_ui_server", missing_dependency)
    monkeypatch.setenv("UI_CSRF_SECRET", "test-only-secret")
    args = argparse.Namespace(
        contracts="frameworks/starter",
        answers=None,
        host="127.0.0.1",
        port=8000,
    )

    assert core._handle_ui(args) == 1
    assert "pip install 'intrinsical-policy-engine[ui]'" in capsys.readouterr().err


def test_invalid_release_policy_preflights_before_debug_or_log_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "release-output"
    log_path = output_dir / "events.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ipe",
            "export",
            "--contracts",
            str(tmp_path / "pack-that-must-not-be-read"),
            "--answers",
            str(tmp_path / "answers-that-must-not-be-read.json"),
            "--out",
            str(output_dir),
            "--log-jsonl",
            str(log_path),
            "--debug",
            "--release",
            "--no-strict",
        ],
    )

    assert main() == 1
    assert "RELEASE_REQUIRES_STRICT_POLICY" in capsys.readouterr().err
    assert not output_dir.exists()
    assert not log_path.exists()


def test_export_cli_rejects_pack_output_overlap_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    output_dir = pack / "generated"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ipe",
            "export",
            "--contracts",
            str(pack),
            "--answers",
            "demos/starter/basic/answers.json",
            "--out",
            str(output_dir),
            "--log-jsonl",
            "events.jsonl",
            "--debug",
        ],
    )

    assert main() == 1
    assert "must not overlap" in capsys.readouterr().err
    assert not output_dir.exists()
    assert not (pack / "_metadata").exists()


def test_lint_cli_blocks_incompatible_pack_even_in_tolerant_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    manifest = pack / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '  - ">=1.0.0"',
            '  - "<2.0"',
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ipe", "lint", "--contracts", str(pack), "--no-strict"],
    )

    assert main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL"
    assert "installed engine is 3.0.0a1" in payload["problems"][0]


def test_assess_cli_blocks_incompatible_pack_before_creating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    manifest = pack / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '  - ">=1.0.0"',
            '  - "<2.0"',
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "assessment-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ipe",
            "assess",
            "--contracts",
            str(pack),
            "--answers",
            "demos/starter/basic/answers.json",
            "--out",
            str(output_dir),
            "--log-jsonl",
            "events.jsonl",
            "--debug",
            "--no-strict",
        ],
    )

    assert main() == 1
    assert "installed engine is 3.0.0a1" in capsys.readouterr().err
    assert not output_dir.exists()


@pytest.mark.parametrize(
    "command_args",
    [
        ["validate", "contracts", "--contracts", "{pack}"],
        ["validate", "templates", "--contracts", "{pack}"],
        ["validate", "all", "--contracts", "{pack}"],
        ["inspect", "--contracts", "{pack}", "rule", "STARTER-RULE"],
        ["ui", "--contracts", "{pack}"],
        ["dev", "graph", "--contracts", "{pack}"],
        ["dev", "build-framework", "--framework", "{pack}"],
    ],
)
def test_other_pack_consuming_cli_surfaces_reject_incompatible_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command_args: list[str],
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    manifest = pack / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '  - ">=1.0.0"',
            '  - "<2.0"',
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("IPE_DEV_MODE", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        ["ipe", *(item.format(pack=pack) for item in command_args)],
    )

    assert main() == 1
    assert "installed engine is 3.0.0a1" in capsys.readouterr().err


def test_validate_evidence_rejects_incompatible_hardcoded_starter_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack = tmp_path / "frameworks" / "starter"
    pack.parent.mkdir(parents=True)
    shutil.copytree("frameworks/starter", pack)
    manifest = pack / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '  - ">=1.0.0"',
            '  - "<2.0"',
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ipe", "validate", "evidence", "--root", str(tmp_path)],
    )

    assert main() == 1
    assert "installed engine is 3.0.0a1" in capsys.readouterr().err


def test_seal_cli_environment_can_explicitly_disable_signing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    seen: dict[str, bool] = {}

    def fake_seal_and_package(**kwargs: object) -> SimpleNamespace:
        seen["sign"] = bool(kwargs["sign"])
        return SimpleNamespace(
            success=True,
            warnings=[],
            errors=[],
            seal_report=SimpleNamespace(files_validated=0),
        )

    monkeypatch.setattr(seal_module, "seal_and_package", fake_seal_and_package)
    args = argparse.Namespace(
        export_dir=str(export_dir),
        seal_output=None,
        evidence_dir=None,
        no_sign=False,
        strict=True,
        _ipe_environment=SimpleNamespace(skip_gpg_signing=True),
    )

    assert core._handle_seal(args) == 0
    assert seen == {"sign": False}


def test_seal_cli_maps_strict_signing_failure_to_controlled_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()

    def fail_seal(**_kwargs: object) -> None:
        raise RuntimeError("GPG signing required but no secret key exists")

    monkeypatch.setattr(seal_module, "seal_and_package", fail_seal)
    monkeypatch.setattr(
        sys,
        "argv",
        ["ipe", "seal", "--export-dir", str(export_dir), "--strict"],
    )

    assert main() == 1
    assert "Seal failed: GPG signing required" in capsys.readouterr().err


def test_export_cli_writes_canonical_artifact_and_pack_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "starter-export"
    monkeypatch.setenv("IPE_SKIP_GPG_SIGNING", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ipe",
            "export",
            "--contracts",
            "frameworks/starter",
            "--answers",
            "demos/starter/basic/answers.json",
            "--out",
            str(output_dir),
            "--strict",
            "--strict-templates",
        ],
    )

    assert main() == 0
    summary = json.loads((output_dir / "_metadata" / "summary.json").read_text(encoding="utf-8"))
    assert summary["artifact_schema_version"] == "3.0.0a1"
    assert summary["pack"] == {
        "id": "starter",
        "version": "0.1.0",
        "manifest_timestamp": "2026-04-19T00:00:00Z",
        "license": None,
        "attribution": None,
    }
