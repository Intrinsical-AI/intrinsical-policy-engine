# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exercise useful CLI feedback, private diagnostics and opt-in logging safety."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from intrinsical_policy_engine.adapters.logging import FsLogger
from intrinsical_policy_engine.app.cli.main import main

PACK = Path("frameworks/starter").resolve()
INPUT = Path("demos/starter/basic/answers.json").resolve()
EVIDENCE = Path("demos/starter/basic/evidence").resolve()
SECRET = "private-reviewer-text-CLI-EXPERIENCE"


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["ipe", *map(str, args)])
    return main()


def assessment_args(command, answers, output):
    return [command, "--contracts", PACK, "--answers", answers, "--out", output]


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_clean_lint_confirms_success_without_polluting_jsonl(monkeypatch, capsys):
    assert invoke(monkeypatch, "lint", "--contracts", PACK, "--strict") == 0
    result = capsys.readouterr()
    assert result.out == ""
    assert "Pack valid:" in result.err and "0 errors, 0 warnings" in result.err


def test_inspect_exposes_authoring_inputs_and_paths(monkeypatch, capsys):
    assert invoke(monkeypatch, "inspect", "--contracts", PACK) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["requires_engine"]
    assert "starter.needs_review" in {flag["id"] for flag in data["flags"]}
    question = data["questions"][0]["questions"][0]
    assert question["id"] == "STARTER_Q1"
    assert {option["value"] for option in question["options"]} == {"yes", "no"}
    assert data["action_evidence"]["STARTER-CONTROL-REVIEW"] == ["starter/control-review.md"]


@pytest.mark.parametrize("command", ["lint", "assess", "export", "seal", "inspect"])
def test_command_help_explains_logging_and_relevant_defaults(monkeypatch, capsys, command):
    with pytest.raises(SystemExit) as stopped:
        invoke(monkeypatch, command, "--help")
    assert stopped.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--log-jsonl FILE" in help_text and "default: no log" in help_text
    if command in {"assess", "export"}:
        assert "must not exist" in help_text and "default: redacted" in help_text
        assert "no implicit current date" in help_text
    if command == "export":
        assert all(target in help_text for target in ("filesystem", "asana", "jira", "linear"))
        assert "default: filesystem" in help_text


@pytest.mark.parametrize(
    "suffix,content,field",
    [
        ("json", json.dumps({"answers": {"STARTER_Q1": {"private": SECRET}}}), "STARTER_Q1"),
        ("yaml", f"flags: []\nowner: !{SECRET} data\n", "line 2, column"),
        ("json", json.dumps({"answers": {"STARTER_Q1": SECRET}}), "STARTER_Q1"),
        ("json", json.dumps({"flags": [SECRET]}), "unknown flags"),
    ],
)
def test_invalid_input_has_source_hint_and_private_failure_log(
    monkeypatch, capsys, tmp_path, suffix, content, field
):
    source = tmp_path / f"input.{suffix}"
    source.write_text(content)
    output = tmp_path / "output"
    log = tmp_path / "events.jsonl"
    assert invoke(monkeypatch, *assessment_args("assess", source, output), "--log-jsonl", log) == 1
    captured = capsys.readouterr()
    assert field in captured.err and "Hint:" in captured.err
    assert SECRET not in captured.err + captured.out + log.read_text()
    assert "Traceback" not in captured.err
    assert not output.exists()
    events = records(log)
    assert [item["event"] for item in events] == ["command.start", "command.failed"]
    assert len({item["run_id"] for item in events}) == 1
    terminal = events[-1]["data"]
    assert terminal["exit_code"] == 1 and terminal["duration_ms"] >= 0
    assert terminal["error_type"] in {"DocumentError", "AssessmentError"}
    if suffix == "yaml" or "private" in content:
        assert str(source) in captured.err


@pytest.mark.parametrize(
    "settings,field",
    [
        (
            {"asana": {"project_gid": {"private": SECRET}, "base_url": "https://example.test"}},
            "project_gid",
        ),
        (
            {"asana": {"project_gid": "fictional", "base_url": f"https://example.test:{SECRET}"}},
            "base_url",
        ),
        (
            {"asana": {"project_gid": "fictional", "base_url": f"https://:{SECRET}@example.test"}},
            "base_url",
        ),
    ],
)
def test_config_errors_hide_values_before_evaluation(
    monkeypatch, capsys, tmp_path, settings, field
):
    # The missing pack proves configuration is rejected before any assessment.
    source = tmp_path / "config.json"
    source.write_text(json.dumps(settings))
    output = tmp_path / "output"
    log = tmp_path / "events.jsonl"
    assert (
        invoke(
            monkeypatch,
            "export",
            "--contracts",
            tmp_path / "absent-pack",
            "--answers",
            INPUT,
            "--config",
            source,
            "--target",
            "asana",
            "--out",
            output,
            "--log-jsonl",
            log,
        )
        == 1
    )
    captured = capsys.readouterr()
    assert str(source) in captured.err and field in captured.err and "export --help" in captured.err
    assert SECRET not in captured.err + captured.out + log.read_text()
    assert not output.exists()
    assert records(log)[-1]["data"]["error_type"] == "DocumentError"


@pytest.mark.parametrize("protected", ["input", "config", "output"])
def test_log_sidecar_cannot_replace_an_input_or_output(monkeypatch, capsys, tmp_path, protected):
    log = tmp_path / "events.jsonl"
    sidecar = tmp_path / ".events.jsonl.lock"
    source = tmp_path / "input.json"
    config = tmp_path / "config.json"
    output = tmp_path / "output"
    source.write_text('{"flags":[]}')
    config.write_text("{}")
    if protected == "input":
        source = sidecar
        source.write_text('{"flags":[]}')
    elif protected == "config":
        config = sidecar
        config.write_text("{}")
    else:
        output = sidecar
    before = sidecar.read_bytes() if sidecar.exists() else None
    assert (
        invoke(
            monkeypatch,
            *assessment_args("export", source, output),
            "--config",
            config,
            "--log-jsonl",
            log,
        )
        == 1
    )
    assert "Operational logs must be outside" in capsys.readouterr().err
    assert not log.exists()
    assert (sidecar.read_bytes() if sidecar.exists() else None) == before


def test_log_symlink_rejected_without_touching_target(monkeypatch, capsys, tmp_path):
    target = tmp_path / "untouched"
    link = tmp_path / "events.jsonl"
    link.symlink_to(target)
    assert invoke(monkeypatch, "lint", "--contracts", PACK, "--log-jsonl", link) == 1
    assert "Cannot append operational log" in capsys.readouterr().err
    assert not target.exists()


def test_log_inside_pack_rejected_before_modifying_sources(monkeypatch, capsys, tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    assert (
        invoke(monkeypatch, "inspect", "--contracts", pack, "--log-jsonl", pack / "events.jsonl")
        == 1
    )
    assert "Operational logs must be outside" in capsys.readouterr().err
    assert not (pack / "events.jsonl").exists()
    assert not (pack / ".events.jsonl.lock").exists()


def test_export_log_appends_distinct_private_runs(monkeypatch, tmp_path, capsys):
    log = tmp_path / "events.jsonl"
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"answers": {"STARTER_Q1": "yes"}, "owner": {"name": SECRET}}))
    for run in range(2):
        output = tmp_path / f"output-{run}"
        assert (
            invoke(monkeypatch, *assessment_args("export", source, output), "--log-jsonl", log) == 0
        )
        assert not list(output.rglob("*.jsonl"))
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + log.read_text()
    events = records(log)
    run_ids = {item["run_id"] for item in events}
    assert len(run_ids) == 2
    for run_id in run_ids:
        lifecycle = [
            item
            for item in events
            if item["run_id"] == run_id and item["event"].startswith("command.")
        ]
        assert [item["event"] for item in lifecycle] == ["command.start", "command.complete"]
        assert lifecycle[-1]["data"]["exit_code"] == 0


def test_warnings_are_visible_with_structured_lint_diagnostics(monkeypatch, capsys, tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    template = pack / "render/artifacts/core/unused.md.j2"
    template.write_text("{{ missing_author_context }}")
    assert invoke(monkeypatch, "lint", "--contracts", pack) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["code"] == "TPL_UNDEFINED_ROOT"
    assert "0 errors, 1 warnings" in captured.err
    assert invoke(monkeypatch, "inspect", "--contracts", pack) == 0
    assert "Warning: [TPL_UNDEFINED_ROOT]" in capsys.readouterr().err
    assert invoke(monkeypatch, "lint", "--contracts", pack, "--strict") == 1
    assert "Pack invalid:" in capsys.readouterr().err


def test_tolerant_seal_shows_quality_warnings(monkeypatch, capsys, tmp_path):
    evidence = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, evidence)
    (evidence / "starter/control-review.md").write_text("TODO")
    output = tmp_path / "output"
    assert (
        invoke(monkeypatch, *assessment_args("export", INPUT, output), "--evidence-dir", evidence)
        == 0
    )
    capsys.readouterr()
    assert invoke(monkeypatch, "seal", "--export-dir", output, "--no-sign") == 0
    captured = capsys.readouterr()
    assert "Warning: Quality warning:" in captured.err
    assert "Signature: unsigned" in captured.out
    assert invoke(monkeypatch, "seal", "--export-dir", output, "--no-sign", "--strict") == 1


def test_logging_failure_after_publication_reports_existing_output(monkeypatch, capsys, tmp_path):
    original = FsLogger.info

    def fail_terminal(self, event, extra=None):
        if event == "command.complete":
            raise OSError("Cannot append operational log: storage unavailable")
        original(self, event, extra)

    monkeypatch.setattr(FsLogger, "info", fail_terminal)
    output = tmp_path / "output"
    log = tmp_path / "events.jsonl"
    assert invoke(monkeypatch, *assessment_args("export", INPUT, output), "--log-jsonl", log) == 1
    result = capsys.readouterr()
    assert "output may already exist" in result.err and "check directory permissions" in result.err
    assert "Traceback" not in result.err
    assert (output / "CHECKSUMS.sha256").is_file()
    lifecycle = [item for item in records(log) if item["event"].startswith("command.")]
    assert [item["event"] for item in lifecycle] == ["command.start", "command.failed"]
    assert lifecycle[-1]["data"]["error_type"] == "OSError"


@pytest.mark.parametrize("command", ["lint", "inspect"])
@pytest.mark.parametrize("source_name", ["manifest.yml", "law/core/actions.yml"])
@pytest.mark.parametrize(
    "invalid_yaml", [f"private_probe: [{SECRET}\n", f"private_probe: !{SECRET} value\n"]
)
def test_pack_yaml_diagnostics_preserve_location_without_source_values(
    monkeypatch, capsys, tmp_path, command, source_name, invalid_yaml
):
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    source = pack / source_name
    source.write_text(source.read_text() + "\n" + invalid_yaml)
    log = tmp_path / "events.jsonl"
    assert invoke(monkeypatch, command, "--contracts", pack, "--log-jsonl", log) == 1
    captured = capsys.readouterr()
    diagnostic = captured.out + captured.err
    assert SECRET not in diagnostic + log.read_text()
    assert SECRET[:20] not in diagnostic
    assert "private_probe" not in diagnostic
    assert "Traceback" not in diagnostic
    assert "CONTRACT-SOURCE" in diagnostic
    assert source_name in diagnostic
    assert "line " in diagnostic and "column " in diagnostic
    if command == "lint":
        problems = [json.loads(line) for line in captured.out.splitlines()]
        problem = next(problem for problem in problems if problem["code"] == "CONTRACT-SOURCE")
        assert problem["source"] == source_name
    else:
        assert captured.out == ""
        assert "CONTRACT-SOURCE" in captured.err
