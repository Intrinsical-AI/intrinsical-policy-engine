"""Offline payload privacy and configuration checks at the public boundaries."""

from __future__ import annotations

import copy
import json
import sys

import pytest
from pydantic import ValidationError

from intrinsical_policy_engine.adapters.export.base.models.export_config import ExportSettings
from intrinsical_policy_engine.app.cli.main import main

CONFIG = {
    "uid_namespace": "Delivery Project Exact Case",
    "asana": {"base_url": "https://asana.example.invalid", "project_gid": "asana-project"},
    "jira": {"base_url": "https://jira.example.invalid", "project_key": "JIRA"},
    "linear": {"base_url": "https://linear.example.invalid", "teamId": "linear-team"},
}


def test_every_offline_header_value_is_withheld_from_all_targets(monkeypatch, tmp_path):
    secret = "PRIVATE-HEADER-VALUE-93617"
    settings = copy.deepcopy(CONFIG)
    names = ["Authorization", "X-Auth-Token", "Private-Token", "Content-Type", "X-Custom"]
    for target in ("asana", "jira", "linear"):
        settings[target]["headers"] = dict.fromkeys(names, secret)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(settings))
    output = tmp_path / "export"
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
            "--evidence-dir",
            "demos/starter/basic/evidence",
            "--strict",
            "--check-ci",
            "--config",
            str(config),
            "--out",
            str(output),
            "--target",
            "asana",
            "jira",
            "linear",
        ],
    )
    assert main() == 0
    for target in ("asana", "jira", "linear"):
        lines = (output / "exports" / target / "requests.ndjson").read_text().splitlines()
        assert lines
        assert all(
            json.loads(line)["headers"] == dict.fromkeys(names, "<redacted>") for line in lines
        )
    assert all(
        secret.encode() not in path.read_bytes() for path in output.rglob("*") if path.is_file()
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("", "safe"),
        ("Header Name", "safe"),
        ("Header:Name", "safe"),
        ("Header\r\nInjected", "safe"),
        ("Héader", "safe"),
        ("X-Safe", "value\r\nInjected: header"),
        ("X-Safe", "value\x00"),
        ("X-Safe", "value\t"),
        ("X-Safe", "value\x7f"),
        ("X-Safe", "value\x85"),
    ],
)
@pytest.mark.parametrize("target", ["asana", "jira", "linear"])
def test_invalid_http_headers_fail_configuration(target, name, value):
    settings = copy.deepcopy(CONFIG)
    settings[target]["headers"] = {name: value}
    with pytest.raises(ValidationError):
        ExportSettings.model_validate(settings)


@pytest.mark.parametrize(
    "value", ["", "   ", "control\nvalue", "control\x00", "control\x7f", "control\x85"]
)
@pytest.mark.parametrize(
    "path",
    [
        ("uid_namespace",),
        ("profiles", 0),
        ("asana", "project_gid"),
        ("asana", "tag_gids", 0),
        ("asana", "tag_gids_by_label", "P1"),
        ("jira", "project_key"),
        ("jira", "issue_type"),
        ("linear", "teamId"),
        ("linear", "projectId"),
        ("linear", "labelIds", 0),
        ("linear", "labelIds_by_label", "P1"),
    ],
)
def test_empty_or_control_bearing_identifiers_are_rejected(path, value):
    settings = copy.deepcopy(CONFIG)
    if len(path) == 1:
        settings[path[0]] = value
    elif len(path) == 2:
        if isinstance(path[1], int):
            settings[path[0]] = [value]
        else:
            settings[path[0]][path[1]] = value
    else:
        settings[path[0]][path[1]] = [value] if isinstance(path[2], int) else {path[2]: value}
    with pytest.raises(ValidationError):
        ExportSettings.model_validate(settings)


@pytest.mark.parametrize(
    "target,field", [("asana", "tag_gids_by_label"), ("linear", "labelIds_by_label")]
)
@pytest.mark.parametrize("label", ["", "  ", "P1\nprivate"])
def test_invalid_label_mapping_keys_are_rejected(target, field, label):
    settings = copy.deepcopy(CONFIG)
    settings[target][field] = {label: "provider-id"}
    with pytest.raises(ValidationError):
        ExportSettings.model_validate(settings)


def test_configuration_selection_preserves_typed_models():
    settings = ExportSettings.model_validate(CONFIG)
    for target in ("filesystem", "asana", "jira", "linear"):
        assert settings.for_target(target) is getattr(settings, target)
