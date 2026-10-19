# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Public examples produce readable, private, local starter snapshots."""

import copy
import json
import re
import shutil
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import (
    ApiExportConfig,
    ExportSettings,
)
from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.app.evidence import check_ci, collect_evidence
from intrinsical_policy_engine.app.export.orchestrator import export_plan
from intrinsical_policy_engine.app.use_cases.seal import seal_and_package
from intrinsical_policy_engine.common.document_io import read_document
from intrinsical_policy_engine.common.hashing import compute_framework_pack_hashes
from intrinsical_policy_engine.common.package_integrity import load_checksums
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle

INPUTS = (
    "demos/starter/basic/answers.json",
    "demos/starter/basic/answers.yml",
    "demos/starter/basic/flags.json",
    "demos/starter/basic/flags.yml",
)
CONFIGS = (
    ("filesystem.yml", ["filesystem"]),
    ("asana.yml", ["filesystem", "asana"]),
    ("jira.yml", ["filesystem", "jira"]),
    ("linear.yml", ["filesystem", "linear"]),
    ("offline-all.json", ["filesystem", "asana", "jira", "linear"]),
)


@pytest.fixture
def compiled():
    pack = YamlContractsAdapter().compile("frameworks/starter")
    assert pack.problems == ()
    return pack


def assess(pack, raw):
    return assess_from_bundle(
        pack.bundle,
        raw,
        include_full_trace=True,
        framework_pack_hashes=compute_framework_pack_hashes(pack.layout),
    )


def export(pack, raw, output, settings=None, targets=None, evidence_root=None):
    plan = assess(pack, raw)
    evidence = collect_evidence(plan, evidence_root or Path("demos/starter/basic/evidence"))
    assert check_ci(plan, evidence) == []
    export_plan(
        plan=plan,
        pack=pack,
        destination=output,
        targets=targets or ["filesystem"],
        settings=settings or ExportSettings(),
        evidence=evidence,
        check_ci_enabled=True,
        strict=True,
    )
    return plan, evidence


def assert_local_links(root):
    for name in ("README.md", "00_START_HERE.md", "LEGAL_NOTICE.md"):
        document = root / name
        text = document.read_text()
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            url = urlsplit(target)
            assert not url.scheme and not url.netloc, (name, target)
            assert url.path and not Path(url.path).is_absolute(), (name, target)
            resolved = (document.parent / url.path).resolve()
            assert resolved.is_relative_to(root.resolve()), (name, target)
            assert resolved.is_file(), (name, target)


@pytest.mark.parametrize("input_path", INPUTS)
def test_json_yaml_answers_and_flags_describe_the_same_example(compiled, input_path):
    plan = assess(compiled, read_document(Path(input_path)))
    reference = assess(compiled, read_document(Path(INPUTS[0])))
    for field in ("flags", "actions", "outcome", "due_hints", "actions_evidence_map"):
        assert plan[field] == reference[field]
    assert plan["flags"] == [
        "starter.automated_decision",
        "starter.needs_review",
        "starter.owner_assigned",
        "starter.sensitive_data",
    ]
    assert plan["actions"] == ["STARTER-CONTROL-REVIEW"]
    assert plan["due_hints"] == {}


@pytest.mark.parametrize("config_name,targets", CONFIGS)
def test_configuration_samples_generate_local_payloads_without_credentials(
    compiled, tmp_path, monkeypatch, config_name, targets
):
    def network_is_not_part_of_export(*_args, **_kwargs):
        pytest.fail("offline export attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", network_is_not_part_of_export)
    settings = ExportSettings.model_validate(
        read_document(Path("demos/starter/configs") / config_name)
    )
    assert settings.profiles == ["starter_smoke"]
    for target in targets:
        config = settings.for_target(target)
        if target != "filesystem":
            assert isinstance(config, ApiExportConfig)
            assert urlsplit(config.base_url).hostname.endswith(".example.test")
            assert config.headers == {}
    root = tmp_path / "export"
    export(
        compiled,
        read_document(Path(INPUTS[0])),
        root,
        settings=settings,
        targets=targets,
    )
    assert_local_links(root)
    for target in set(targets) - {"filesystem"}:
        requests = root / "exports" / target / "requests.ndjson"
        records = [json.loads(line) for line in requests.read_text().splitlines()]
        assert len(records) == 1
        assert ".example.test" in records[0]["base_url"]
        assert records[0]["url"].startswith("/")
        assert records[0]["headers"] == {}
    assert not (root / "compliance.ics").exists()
    assert seal_and_package(root, sign=False, strict=True).success


def test_rendered_review_guides_use_real_evidence_and_redacted_input(compiled, tmp_path):
    raw = copy.deepcopy(read_document(Path(INPUTS[0])))
    raw["system"] = {"name": "PRIVATE-WORKFLOW", "id": "PRIVATE-IDENTITY"}
    raw["owner"] = {"name": "PRIVATE-OWNER"}
    root = tmp_path / "export"
    plan, evidence = export(compiled, raw, root)
    assert_local_links(root)
    for name in ("README.md", "00_START_HERE.md", "LEGAL_NOTICE.md"):
        text = (root / name).read_text()
        assert "PRIVATE-" not in text
        assert "{{" not in text and "{%" not in text
    overview = (root / "README.md").read_text()
    start = (root / "00_START_HERE.md").read_text()
    assert "Document a control review" in overview
    assert compiled.bundle.actions.actions[0].description in overview
    assert "evidence/starter/control-review.md" in overview
    assert "starter.needs_review" in start
    assert "no assigned deadlines" in overview
    assert "ipe seal --export-dir . --no-sign --strict" in overview
    assert plan["trace"]["plan_hash"] in overview
    for name, data in evidence.files.items():
        assert (root / "evidence" / name).read_bytes() == data
    summary = read_document(root / "_metadata" / "summary.json")
    assert summary["system"]["name"] == "[redacted]"
    assert set(summary["trace"]["answers_raw"]["input"]["answers"].values()) == {"[redacted]"}
    _, baseline = load_checksums(root)
    assert "evidence/starter/control-review.md" in baseline


def test_no_action_export_explains_the_empty_result(compiled, tmp_path):
    root = tmp_path / "export"
    export(compiled, {"flags": []}, root)
    assert_local_links(root)
    overview = (root / "README.md").read_text()
    start = (root / "00_START_HERE.md").read_text()
    assert "No outcome selected" in overview
    assert "Not assigned" in overview
    assert "No action was selected" in overview and "No action was selected" in start
    assert "No flags are present" in start
    assert not (root / "compliance.ics").exists()


def test_documents_link_the_calendar_only_when_dates_are_assigned(tmp_path):
    source = tmp_path / "pack"
    shutil.copytree("frameworks/starter", source)
    (source / "law" / "policy" / "law_calendar.yml").write_text(
        yaml.safe_dump({"calendar": {"review": "2026-11-12"}})
    )
    (source / "law" / "policy" / "due_rules.yml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "demo-review-date",
                        "ids": ["STARTER-CONTROL-REVIEW"],
                        "calendar_keys": ["review"],
                    }
                ]
            }
        )
    )
    pack = YamlContractsAdapter().compile(str(source))
    assert pack.problems == ()
    root = tmp_path / "export"
    export(pack, read_document(Path(INPUTS[0])), root)
    assert_local_links(root)
    assert "2026-11-12" in (root / "README.md").read_text()
    assert "2026-11-12" in (root / "00_START_HERE.md").read_text()
    assert "DTSTART;VALUE=DATE:20261112" in (root / "compliance.ics").read_text()


def test_authoring_guide_cannot_be_mistaken_for_completed_evidence():
    draft = Path("frameworks/starter/evidence/templates/starter/control-review.md")
    fixture = Path("demos/starter/basic/evidence/starter/control-review.md")
    text = draft.read_text()
    assert "status: draft" in text and "status: ready" not in text
    assert "TODO:" in text and "{{" not in text
    engine = QualityEngine()
    assert engine.classify_file(draft) != "ready"
    assert engine.classify_file(fixture) == "ready"
    assert "synthetic" in fixture.read_text()
