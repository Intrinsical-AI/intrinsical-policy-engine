# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Authored article labels survive the offline Asana CSV boundary."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.export.asana.asana_exporter import AsanaExporter
from intrinsical_policy_engine.adapters.export.base.models.export_config import AsanaExportConfig
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle


@pytest.mark.parametrize("article_id", ["P1", "PA"])
@pytest.mark.parametrize("priority", ["critical", "high", "medium", "low"])
def test_article_labels_are_not_rewritten_as_priorities(
    tmp_path: Path, article_id: str, priority: str
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    for path in pack.rglob("*.yml"):
        path.write_text(path.read_text().replace("TOPIC-STARTER-CONTROLS", article_id))
    actions = pack / "law/core/actions.yml"
    document = yaml.safe_load(actions.read_text())
    document["actions"][0]["priority"] = priority
    actions.write_text(yaml.safe_dump(document, sort_keys=False))
    compiled = YamlContractsAdapter().compile(str(pack))
    assert compiled.problems == ()
    plan = assess_from_bundle(compiled.bundle, {"flags": ["starter.automated_decision"]})
    plan["export_context"] = {"uid_namespace": "asana-label-regression"}
    destination = tmp_path / "export"
    AsanaExporter(
        AsanaExportConfig(base_url="https://asana.example.invalid", project_gid="project")
    ).export(plan, destination)
    with (destination / "exports/asana/import.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["Tags"].split(",") == ["CTRL-Act", article_id, priority]
