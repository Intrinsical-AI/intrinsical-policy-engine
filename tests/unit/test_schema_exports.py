# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Generated schemas are deterministic, referentially complete and drift checked."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.export_schemas import SCHEMA_MODELS, check_artifacts, main, schema_artifacts


def references(value: Any):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def test_committed_schemas_match_current_authoritative_models():
    artifacts = schema_artifacts()
    assert artifacts == schema_artifacts()
    assert check_artifacts(Path("docs/schemas"), artifacts) == []
    index = json.loads(artifacts["index.json"])
    assert len(index["schemas"]) == len(SCHEMA_MODELS)
    assert index["validation_limits"]
    for entry in index["schemas"]:
        assert hashlib.sha256(artifacts[entry["file"]]).hexdigest() == entry["sha256"]


@pytest.mark.parametrize("name", sorted(SCHEMA_MODELS))
def test_schemas_are_standalone_and_references_resolve(name):
    document = json.loads(schema_artifacts()[f"{name}.schema.json"])
    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert document["$id"].endswith(":" + name)
    for reference in references(document):
        assert reference.startswith("#/")
        target = document
        for part in reference.removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        assert isinstance(target, dict)


def test_check_reports_missing_directory_without_creating_it(tmp_path, capsys):
    output = tmp_path / "absent"
    assert main(["--out-dir", str(output), "--check"]) == 1
    assert "missing: pack-manifest.schema.json" in capsys.readouterr().out
    assert not output.exists()


def test_check_detects_drift_and_obsolete_schemas_without_repairing_them(tmp_path, capsys):
    output = tmp_path / "schemas"
    assert main(["--out-dir", str(output)]) == 0
    assert main(["--out-dir", str(output), "--check"]) == 0
    (output / "pack-manifest.schema.json").write_text("{}\n")
    (output / "obsolete.schema.json").write_text("{}\n")
    (output / "calendar.schema.json").unlink()
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    assert main(["--out-dir", str(output), "--check"]) == 1
    after = {path.name: path.read_bytes() for path in output.iterdir()}
    assert before == after
    diagnostics = capsys.readouterr().out
    assert "changed: pack-manifest.schema.json" in diagnostics
    assert "obsolete: obsolete.schema.json" in diagnostics
    assert "missing: calendar.schema.json" in diagnostics


def test_structural_schemas_keep_closed_models_and_document_semantic_limits():
    artifacts = schema_artifacts()
    for name in ("pack-manifest", "assessment-input", "export-settings", "contract-bundle"):
        schema = json.loads(artifacts[f"{name}.schema.json"])
        assert schema["additionalProperties"] is False
    manifest = json.loads(artifacts["pack-manifest.schema.json"])
    assert manifest["properties"]["pack_format_version"]["const"] == 3
    profiles = json.loads(artifacts["bundle-profiles.schema.json"])
    trace = profiles["$defs"]["TraceBackTo"]
    assert set(trace["properties"]) == {"actions", "articles"}
    assert trace["additionalProperties"] is False
    limits = " ".join(json.loads(artifacts["index.json"])["validation_limits"])
    assert "exactly one" in limits
    assert "Python field or model validators" in limits
    assert "filesystem containment" in limits
