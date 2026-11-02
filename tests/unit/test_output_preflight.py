"""Selected output ownership is checked before staging or rendering begins."""

from dataclasses import replace
from pathlib import Path

import pytest

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import ExportSettings
from intrinsical_policy_engine.app.evidence import collect_evidence
from intrinsical_policy_engine.app.export.orchestrator import export_plan
from intrinsical_policy_engine.domain.conditions import Condition
from intrinsical_policy_engine.domain.output_inventory import OutputCollisionError, OutputInventory
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle


def _export(pack, output):
    plan = assess_from_bundle(pack.bundle, {"flags": ["starter.automated_decision"]})
    return export_plan(
        plan=plan,
        pack=pack,
        destination=output,
        targets=["filesystem"],
        settings=ExportSettings(),
        evidence=collect_evidence(plan, Path("demos/starter/basic/evidence")),
        strict=True,
        check_ci_enabled=True,
    )


@pytest.mark.parametrize(
    "path",
    [
        "_metadata/custom.json",
        "evidence/review.md",
        "exports/asana/requests.ndjson",
        "backlogs/custom.csv",
        "CHECKSUMS.sha256",
        "CHECKSUMS.sha256.asc",
        "evidence.zip",
        "compliance.ics",
    ],
)
def test_reserved_paths_fail_before_any_output_is_prepared(tmp_path, monkeypatch, path):
    pack = YamlContractsAdapter().compile("frameworks/starter")
    profile = next(iter(pack.bundle_profiles.values()))
    node = profile.nodes[0].model_copy(update={"name": path})
    pack = replace(
        pack, bundle_profiles={profile.id: profile.model_copy(update={"nodes": (node,)})}
    )

    def staging_must_not_start(*_args, **_kwargs):
        pytest.fail("output staging began before ownership validation")

    monkeypatch.setattr(
        "intrinsical_policy_engine.app.export.orchestrator.fresh_output", staging_must_not_start
    )
    with pytest.raises(OutputCollisionError, match="reserved"):
        _export(pack, tmp_path / "new-parent" / "export")
    assert not (tmp_path / "new-parent").exists()


def test_selected_conditional_nodes_cannot_claim_the_same_file(tmp_path):
    pack = YamlContractsAdapter().compile("frameworks/starter")
    profile = next(iter(pack.bundle_profiles.values()))
    node = profile.nodes[0]
    condition = Condition.model_validate({"has": "starter.automated_decision"})
    nodes = tuple(
        node.model_copy(update={"id": name, "name": "same.md", "when": condition})
        for name in ("first", "second")
    )
    pack = replace(pack, bundle_profiles={profile.id: profile.model_copy(update={"nodes": nodes})})
    with pytest.raises(OutputCollisionError, match=r"first.*second"):
        _export(pack, tmp_path / "export")
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("reverse", [False, True])
def test_file_and_descendant_collisions_are_order_independent(reverse):
    paths = ["report", "report/body.md"]
    if reverse:
        paths.reverse()
    inventory = OutputInventory()
    inventory.add_file(paths[0], "first")
    with pytest.raises(OutputCollisionError):
        inventory.add_file(paths[1], "second")


def test_multiple_producers_can_share_an_output_directory():
    inventory = OutputInventory()
    inventory.add_directory("reports", "first profile")
    inventory.add_file("reports/a.md", "first")
    inventory.add_directory("reports", "second profile")
    inventory.add_file("reports/b.md", "second")
    assert set(inventory.files) == {"reports/a.md", "reports/b.md"}


def test_optional_regulatory_context_has_a_stable_empty_shape(tmp_path):
    pack = YamlContractsAdapter().compile("frameworks/starter")
    source = "render/artifacts/core/00_START_HERE.md.j2"
    pack.layout.source_bytes[source] = (
        b"Warnings: {{ regulatory_warnings | length }}; present: {{ regulatory_meta is not none }}"
    )
    _export(pack, tmp_path / "export")
    assert (tmp_path / "export/00_START_HERE.md").read_text() == "Warnings: 0; present: False"
