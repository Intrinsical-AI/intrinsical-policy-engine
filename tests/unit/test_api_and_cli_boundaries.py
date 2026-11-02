"""Regression boundaries exercised through real CLI and offline exporters."""

from __future__ import annotations

import copy
import csv
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import build_task_items
from intrinsical_policy_engine.adapters.export.base.models.export_config import ExportSettings
from intrinsical_policy_engine.app.cli.main import main
from intrinsical_policy_engine.common.package_integrity import load_checksums, verify_payload

DESCRIPTION = 'Authored action instructions: verify "owner", inputs and evidence.\nSecond step: ñ.'
CONFIG = {
    "uid_namespace": "Delivery Project Exact Case",
    "asana": {"base_url": "https://asana.example.invalid", "project_gid": "asana-project"},
    "jira": {"base_url": "https://jira.example.invalid", "project_key": "JIRA"},
    "linear": {"base_url": "https://linear.example.invalid", "teamId": "linear-team"},
}


@pytest.fixture
def inputs(tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    source = pack / "law/core/actions.yml"
    actions = yaml.safe_load(source.read_text())
    actions["actions"][0]["description"] = DESCRIPTION
    source.write_text(yaml.safe_dump(actions, sort_keys=False))
    answers = tmp_path / "answers.json"
    answers.write_text(Path("demos/starter/basic/answers.json").read_text())
    config = tmp_path / "config.json"
    config.write_text(json.dumps(CONFIG))
    return pack, answers, config


def invoke(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["ipe", *map(str, arguments)])
    return main()


@pytest.mark.parametrize("command", ["assess", "export"])
def test_cli_rejects_dangling_output_symlink(monkeypatch, inputs, tmp_path, command):
    pack, answers, _config = inputs
    referent = tmp_path / "unowned-output"
    output = tmp_path / "output-link"
    output.symlink_to(referent, target_is_directory=True)
    assert (
        invoke(monkeypatch, command, "--contracts", pack, "--answers", answers, "--out", output)
        == 1
    )
    assert output.is_symlink()
    assert not referent.exists()


def test_cli_rejects_dangling_zip_output_symlink(monkeypatch, inputs, tmp_path):
    pack, answers, _config = inputs
    source = tmp_path / "export"
    assert (
        invoke(
            monkeypatch,
            "export",
            "--contracts",
            pack,
            "--answers",
            answers,
            "--out",
            source,
            "--evidence-dir",
            "demos/starter/basic/evidence",
            "--check-ci",
        )
        == 0
    )
    referent = tmp_path / "unowned.zip"
    output = tmp_path / "zip-link"
    output.symlink_to(referent)
    assert invoke(monkeypatch, "seal", "--export-dir", source, "--output", output, "--no-sign") == 1
    assert output.is_symlink()
    assert not referent.exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://:PASSWORD@example.invalid",
        "https://@example.invalid",
        "https://user:PASSWORD@example.invalid",
        "https://example.invalid:notnumeric",
        "https://example.invalid:65536",
        "https://exa mple.invalid",
        "https://.invalid",
        "https://example.invalid:",
    ],
)
@pytest.mark.parametrize("target", ["asana", "jira", "linear"])
def test_invalid_api_authority_never_exports(monkeypatch, inputs, tmp_path, target, url):
    pack, answers, config = inputs
    settings = copy.deepcopy(CONFIG)
    settings[target]["base_url"] = url
    config.write_text(json.dumps(settings))
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
            "--config",
            config,
            "--target",
            target,
        )
        == 1
    )
    assert not output.exists()


@pytest.mark.parametrize("target", ["asana", "jira", "linear"])
@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid:PRIVATE_CANARY_VALUE",
        "https://[PRIVATE_CANARY_VALUE::]",
    ],
)
def test_invalid_url_diagnostics_do_not_echo_submitted_values(
    monkeypatch, inputs, tmp_path, target, url, capsys
):
    pack, answers, config = inputs
    settings = copy.deepcopy(CONFIG)
    settings[target]["base_url"] = url
    config.write_text(json.dumps(settings))
    output = tmp_path / "private-url-output"
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
            target,
        )
        == 1
    )
    diagnostic = capsys.readouterr().err
    assert "PRIVATE_CANARY_VALUE" not in diagnostic
    assert "base_url" in diagnostic
    assert not output.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "https://api.example.invalid:443/v1",
        "https://[::1]:8443",
        "http://127.0.0.1:8000",
        "https://münich.example/v1",
    ],
)
def test_valid_api_authorities_remain_supported(url):
    settings = copy.deepcopy(CONFIG)
    settings["asana"]["base_url"] = url
    assert ExportSettings.model_validate(settings).asana.base_url == url


def test_unused_public_base_url_is_rejected_as_unknown_config():
    settings = copy.deepcopy(CONFIG)
    settings["asana"]["public_base_url"] = "https://unused.example.invalid"
    with pytest.raises(ValidationError):
        ExportSettings.model_validate(settings)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_jira_customfield_numbers_are_finite_before_assessment(
    monkeypatch, inputs, tmp_path, value
):
    pack, answers, config = inputs
    settings = copy.deepcopy(CONFIG)
    settings["jira"]["customfields"] = {"customfield_10001": {"values": [value]}}
    config = config.with_suffix(".yml")
    config.write_text(yaml.safe_dump(settings))
    from intrinsical_policy_engine.app.cli.commands import core

    def must_not_evaluate(*args, **kwargs):
        pytest.fail("invalid export configuration reached assessment")

    monkeypatch.setattr(core, "assess_from_bundle", must_not_evaluate)
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
            "--config",
            config,
            "--target",
            "jira",
        )
        == 1
    )
    assert not output.exists()


def exported_records(output):
    result = {}
    for target in ("asana", "jira", "linear"):
        records = (output / "exports" / target / "requests.ndjson").read_text().splitlines()
        result[target] = json.loads(records[0])
        index = json.loads((output / "exports" / target / "index.json").read_text())
        assert index == {"target": target, "count": len(records)}
    return result


def descriptions(records):
    adf = records["jira"]["body"]["fields"]["description"]
    jira = "\n".join(
        "".join(item["text"] for item in paragraph["content"]) for paragraph in adf["content"]
    )
    return {
        "asana": records["asana"]["body"]["data"]["notes"],
        "jira": jira,
        "linear": records["linear"]["body"]["variables"]["input"]["description"],
    }


@pytest.fixture
def exported(monkeypatch, inputs, tmp_path):
    pack, answers, config = inputs
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
            "--config",
            config,
            "--target",
            "filesystem",
            "asana",
            "jira",
            "linear",
        )
        == 0
    )
    return output


@pytest.mark.parametrize("target", ["asana", "jira", "linear"])
def test_authored_description_reaches_each_real_api_payload(exported, target):
    records = exported_records(exported)
    assert DESCRIPTION in descriptions(records)[target]


def test_authored_description_reaches_asana_csv(exported):
    with (exported / "exports/asana/import.csv").open(newline="") as stream:
        records = list(csv.DictReader(stream))
    assert DESCRIPTION in records[0]["Notes"]


def test_description_changes_content_hash_without_changing_uid(exported):
    summary = json.loads((exported / "_metadata/summary.json").read_text())
    before = build_task_items(summary)[0]
    summary["actions_meta"][0]["description"] = "Changed authored instructions"
    after = build_task_items(summary)[0]
    assert before.content_hash != after.content_hash
    assert before.uid == after.uid


def test_authored_hash_marker_is_part_of_content_hash(exported):
    summary = json.loads((exported / "_metadata/summary.json").read_text())
    summary["actions_meta"][0]["description"] = "- plan_hash: `authored instructions A`"
    before = build_task_items(summary)[0]
    summary["actions_meta"][0]["description"] = "- plan_hash: `authored instructions B`"
    after = build_task_items(summary)[0]
    assert "- plan_hash: `authored instructions A`" in before.description_md
    assert "- plan_hash: `authored instructions B`" in after.description_md
    assert before.content_hash != after.content_hash
    assert before.uid == after.uid


def test_real_api_outputs_share_identity_content_and_closed_baseline(exported):
    records = exported_records(exported)
    texts = descriptions(records)
    assert len(set(texts.values())) == 1
    assert len({record["uid"] for record in records.values()}) == 1
    asana = records["asana"]["body"]["data"]
    properties = {value["key"]: value["value"] for value in records["jira"]["body"]["properties"]}
    with (exported / "exports/asana/import.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["Notes"] == texts["asana"]
    assert row["External Id"] == properties["externalId"] == asana["external"]["gid"]
    assert f"h={row['Content Hash']}" in asana["external"]["data"]
    assert row["Content Hash"] == properties["contentHash"]
    _, baseline = load_checksums(exported)
    assert all(f"exports/{target}/requests.ndjson" in baseline for target in records)
    assert "exports/asana/import.csv" in baseline
    verify_payload(exported, baseline)
