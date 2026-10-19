# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Current snapshots are versioned and keep their two trace copies coherent."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.app.export.artifacts import write_assessment, write_json
from intrinsical_policy_engine.app.use_cases import seal
from intrinsical_policy_engine.common.document_io import parse_document
from intrinsical_policy_engine.common.output_safety import fresh_output
from intrinsical_policy_engine.common.package_integrity import write_checksums
from intrinsical_policy_engine.domain.contract_models import ContractBundle
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle
from intrinsical_policy_engine.domain.services.integrity import compute_plan_hash
from intrinsical_policy_engine.domain.snapshot_contract import CHECKSUMS_NAME, SIGNATURE_NAME


def make_snapshot(root: Path) -> Path:
    write_assessment({"actions": [], "trace": {"plan_hash": "abc", "count": 1}}, root)
    write_checksums(root)
    return root


def package_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def edit_metadata(root: Path, name: str, edit) -> None:
    path = root / "_metadata" / f"{name}.json"
    value = json.loads(path.read_text())
    edit(value)
    path.write_text(json.dumps(value))
    (root / CHECKSUMS_NAME).unlink()
    write_checksums(root)


def test_writer_versions_both_documents_without_mutating_the_plan(tmp_path):
    plan = {"actions": [], "trace": {"plan_hash": "abc", "nested": {"flags": []}}}
    original = copy.deepcopy(plan)
    write_assessment(plan, tmp_path)
    summary = json.loads((tmp_path / "_metadata/summary.json").read_text())
    trace = json.loads((tmp_path / "_metadata/trace.json").read_text())
    assert summary["snapshot_format_version"] == trace["snapshot_format_version"] == 1
    assert summary["trace"] == trace
    assert plan == original


def test_snapshot_format_is_transport_metadata_for_semantic_plan_hash():
    plan = {"actions": ["review"], "trace": {"plan_hash": "ignored"}}
    versioned = {**plan, "snapshot_format_version": 1}
    assert compute_plan_hash(plan) == compute_plan_hash(versioned)


@pytest.mark.parametrize("name", ["summary", "trace"])
@pytest.mark.parametrize("version", [None, 2, True, "1", 1.0])
@pytest.mark.parametrize("strict", [False, True])
def test_seal_rejects_missing_or_unsupported_snapshot_versions_without_writes(
    tmp_path, monkeypatch, name, version, strict
):
    root = make_snapshot(tmp_path / "snapshot")

    def edit(value):
        if version is None:
            value.pop("snapshot_format_version", None)
        else:
            value["snapshot_format_version"] = version

    edit_metadata(root, name, edit)

    class UnexpectedSigner:
        def __init__(self):
            pytest.fail("invalid metadata must be rejected before signature work")

    monkeypatch.setattr(seal, "GpgSigner", UnexpectedSigner)
    before = package_bytes(root)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, strict=strict)
    assert not result.success
    assert any("snapshot_format_version" in error and name in error for error in result.errors)
    assert package_bytes(root) == before
    assert not target.exists()
    assert not (root / SIGNATURE_NAME).exists()


@pytest.mark.parametrize(
    "edit",
    [
        lambda summary: summary["trace"].update(plan_hash="different"),
        lambda summary: summary["trace"].update(count=True),
        lambda summary: summary["trace"].update(count=1.0),
        lambda summary: summary.pop("trace"),
    ],
    ids=["different-hash", "boolean-is-not-integer", "float-is-not-integer", "missing-trace"],
)
@pytest.mark.parametrize("strict", [False, True])
def test_seal_rejects_summary_trace_disagreement_without_repair(tmp_path, edit, strict):
    root = make_snapshot(tmp_path / "snapshot")
    edit_metadata(root, "summary", edit)
    before = package_bytes(root)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, sign=False, strict=strict)
    assert not result.success
    assert any("summary.trace" in error for error in result.errors)
    assert package_bytes(root) == before
    assert not target.exists()


def test_current_snapshot_can_be_sealed_without_rewriting_metadata(tmp_path):
    root = make_snapshot(tmp_path / "snapshot")
    before = package_bytes(root)
    result = seal.seal_and_package(root, output_zip=tmp_path / "delivery.zip", sign=False)
    assert result.success, result.errors
    assert package_bytes(root) == before


def test_authored_depth_boundary_round_trips_through_summary_and_seal(tmp_path: Path) -> None:
    fixtures = Path(__file__).resolve().parents[2]
    raw = YamlContractsAdapter().load(str(fixtures / "frameworks/starter")).model_dump(mode="json")
    condition: dict[str, Any] = {"has": "starter.automated_decision"}
    for _ in range(30):
        condition = {"all": [condition]}
    raw["rules"]["derivations"][0]["when"] = condition
    # This is a valid authored document exactly at the public depth boundary.
    parse_document(json.dumps(raw["rules"]), ".json")
    plan = assess_from_bundle(
        ContractBundle.model_validate(raw), {"flags": ["starter.automated_decision"]}
    )
    assert plan["trace"]["derivations_applied"][0]["when"] == condition
    root = tmp_path / "snapshot"
    write_assessment(plan, root)
    shutil.copytree(fixtures / "demos/starter/basic/evidence", root / "evidence")
    write_json(
        root / "_metadata/evidence_manifest.json",
        {"included": ["starter/control-review.md"], "missing": []},
    )
    write_checksums(root)
    before = package_bytes(root)
    result = seal.seal_and_package(root, output_zip=tmp_path / "delivery.zip", sign=False)
    assert result.success, result.errors
    assert package_bytes(root) == before


@pytest.mark.parametrize("dimension", ["depth", "nodes"])
def test_excessive_snapshot_metadata_is_rejected_before_output_publication(
    tmp_path: Path, dimension: str
) -> None:
    excessive: Any
    if dimension == "depth":
        excessive = {}
        for _ in range(129):
            excessive = {"nested": excessive}
    else:
        shared = [0] * 1000
        excessive = [shared] * 1000
    destination = tmp_path / "assessment"
    with (
        pytest.raises(ValueError, match=r"nesting|expanded values"),
        fresh_output(destination) as staging,
    ):
        write_assessment({"actions": [], "trace": {}, "extra": excessive}, staging)
    assert not destination.exists()


def test_auxiliary_snapshot_json_obeys_the_same_metadata_budget(tmp_path: Path) -> None:
    nested: dict[str, Any] = {}
    for _ in range(129):
        nested = {"nested": nested}
    destination = tmp_path / "export"
    with pytest.raises(ValueError, match="nesting"), fresh_output(destination) as staging:
        write_json(staging / "_metadata/actions.json", [{"when": nested}])
    assert not destination.exists()


def test_snapshot_node_budget_allows_wrapper_amplification_without_relaxing_authoring(
    tmp_path: Path,
) -> None:
    document = {"values": [0] * 100_001}
    with pytest.raises(ValueError, match="100000 expanded values"):
        parse_document(json.dumps(document), ".json")
    path = tmp_path / "_metadata/diagnostics.json"
    write_json(path, document)
    assert seal._metadata_document(path, "_metadata/diagnostics.json") == document
