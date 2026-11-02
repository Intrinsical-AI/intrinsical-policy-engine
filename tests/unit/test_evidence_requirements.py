# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Evidence attribution and completeness across assessment, CI and sealing."""

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import build_task_items
from intrinsical_policy_engine.app.cli.main import main
from intrinsical_policy_engine.app.evidence import EvidenceSnapshot, check_ci, collect_evidence
from intrinsical_policy_engine.app.use_cases.seal import seal_and_package
from intrinsical_policy_engine.common.package_integrity import write_checksums
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle

ACTION = "STARTER-CONTROL-REVIEW"
ARTICLE = "TOPIC-STARTER-CONTROLS"
OTHER_ARTICLE = "TOPIC-STARTER-SECOND"
READY = Path("demos/starter/basic/evidence/starter/control-review.md").read_bytes()


@pytest.fixture
def pack(tmp_path):
    root = tmp_path / "pack"
    shutil.copytree("frameworks/starter", root)
    return root


def update(root, name, edit):
    path = root / name
    data = yaml.safe_load(path.read_text())
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def relations(pack, relation):
    update(
        pack,
        "law/core/actions.yml",
        lambda d: d["actions"][0].update(
            evidence=[], articles=[] if relation == "inverse" else [ARTICLE]
        ),
    )
    if relation == "inverse":
        update(
            pack,
            "law/content/articles.yml",
            lambda d: d["taxonomy"][0].update(related_actions=[ACTION]),
        )
    if relation == "mixed":
        update(
            pack,
            "law/content/articles.yml",
            lambda d: d["taxonomy"].append(
                {"id": OTHER_ARTICLE, "title": "Second", "related_actions": [ACTION]}
            ),
        )
        update(
            pack,
            "law/policy/evidence_map.yml",
            lambda d: d.update({OTHER_ARTICLE: [{"path": "starter/second.md"}]}),
        )
        update(
            pack,
            "delivery/profiles/core_outputs.yml",
            lambda d: d["profiles"]["starter_smoke"]["nodes"][0]["trace_back_to"][
                "articles"
            ].append(OTHER_ARTICLE),
        )


def plan_for(pack):
    return assess_from_bundle(
        YamlContractsAdapter().compile(str(pack)).bundle, {"flags": ["starter.automated_decision"]}
    )


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["ipe", *map(str, args)])
    return main()


def export(monkeypatch, pack, output, tmp_path, evidence=None, check=False, strict=False):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"flags": ["starter.automated_decision"]}))
    args = ["export", "--contracts", pack, "--answers", answers, "--out", output]
    if evidence is not None:
        args.extend(["--evidence-dir", evidence])
    if check:
        args.append("--check-ci")
    if strict:
        args.append("--strict")
    return invoke(monkeypatch, *args)


@pytest.mark.parametrize("relation", ["direct", "inverse", "mixed"])
def test_article_evidence_uses_the_complete_overlay_in_every_consumer(pack, relation):
    relations(pack, relation)
    assert YamlContractsAdapter().validate_detailed(str(pack)) == []
    plan = plan_for(pack)
    expected_articles = {ARTICLE, OTHER_ARTICLE} if relation == "mixed" else {ARTICLE}
    expected_paths = {"starter/control-review.md"}
    if relation == "mixed":
        expected_paths.add("starter/second.md")
    assert set(plan["actions_evidence_map"][ACTION]) == expected_paths
    assert set(plan["flag_article_index"]["starter.needs_review"]) == expected_articles
    plan["export_context"] = {"uid_namespace": "test"}
    assert set(build_task_items(plan)[0].article_ids) == expected_articles


def minimal_plan(paths):
    return {
        "actions_meta": [{"id": ACTION, "requires_evidence": True}],
        "actions_evidence_map": {ACTION: paths},
    }


def test_ci_accepts_a_canonical_directory_requirement(tmp_path):
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    plan = minimal_plan(["starter"])
    snapshot = collect_evidence(plan, evidence)
    assert check_ci(plan, snapshot) == []


def test_ci_requires_every_declared_path_even_for_library_snapshots():
    snapshot = EvidenceSnapshot(
        files={"starter/control-review.md": READY},
        quality={"starter/control-review.md": "ready"},
        supplied=True,
    )
    problems = check_ci(minimal_plan(["starter/control-review.md", "starter/second.md"]), snapshot)
    assert any("starter/second.md" in problem for problem in problems)


def test_ci_does_not_credit_a_directory_lookalike():
    snapshot = EvidenceSnapshot(
        files={"starter-other/control-review.md": READY},
        quality={"starter-other/control-review.md": "ready"},
        supplied=True,
    )
    assert check_ci(minimal_plan(["starter"]), snapshot)


def test_missing_optional_evidence_is_not_reported_as_a_requirement(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    plan = minimal_plan(["optional.md"])
    plan["actions_meta"][0]["requires_evidence"] = False
    snapshot = collect_evidence(plan, root)
    assert snapshot.problems == ()
    assert check_ci(plan, snapshot) == []


def test_missing_evidence_is_reported_once_and_identically_by_assess_and_export(
    monkeypatch, pack, tmp_path, capsys
):
    empty = tmp_path / "empty-evidence"
    empty.mkdir()
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, empty) == 0
    manifest = json.loads((output / "_metadata" / "evidence_manifest.json").read_text())
    assert manifest["missing"] == [f"Missing evidence: starter/control-review.md (action {ACTION})"]
    capsys.readouterr()
    reports = {}
    for command in ("assess", "export"):
        code = invoke(
            monkeypatch,
            command,
            "--contracts",
            pack,
            "--answers",
            tmp_path / "answers.json",
            "--out",
            tmp_path / f"ci-{command}",
            "--evidence-dir",
            empty,
            "--check-ci",
        )
        assert code == 1
        reports[command] = [
            line for line in capsys.readouterr().err.splitlines() if line.startswith("  ")
        ]
    assert reports["assess"] == reports["export"]
    assert len(reports["assess"]) == len(set(reports["assess"])) == 2


@pytest.mark.parametrize("relation", ["direct", "inverse", "mixed"])
def test_export_ci_and_seal_accept_all_declared_evidence(monkeypatch, pack, tmp_path, relation):
    relations(pack, relation)
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    if relation == "mixed":
        (evidence / "starter" / "second.md").write_bytes(READY)
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, evidence, check=True) == 0
    for source in (evidence / "starter").iterdir():
        assert (output / "evidence" / "starter" / source.name).read_bytes() == source.read_bytes()
    assert seal_and_package(output, sign=False).success


def test_export_ci_accepts_directory_requirements(monkeypatch, pack, tmp_path):
    update(pack, "law/core/actions.yml", lambda d: d["actions"][0].update(evidence=["starter"]))
    update(pack, "law/policy/evidence_map.yml", lambda d: d.clear())
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, evidence, check=True) == 0
    assert seal_and_package(output, sign=False).success


@pytest.mark.parametrize("strict", [False, True])
def test_seal_rejects_an_export_missing_required_evidence(monkeypatch, pack, tmp_path, strict):
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path) == 0
    result = seal_and_package(output, sign=False, strict=strict)
    assert not result.success
    assert any("starter/control-review.md" in error for error in result.errors)
    manifest = json.loads((output / "_metadata" / "evidence_manifest.json").read_text())
    assert any("starter/control-review.md" in reason for reason in manifest["missing"])


def test_seal_cross_checks_the_evidence_manifest_against_real_files(monkeypatch, pack, tmp_path):
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, evidence, check=True) == 0
    manifest = output / "_metadata" / "evidence_manifest.json"
    data = json.loads(manifest.read_text())
    data["included"] = ["starter/invented.md"]
    manifest.write_text(json.dumps(data))
    (output / "CHECKSUMS.sha256").unlink()
    write_checksums(output)
    assert not seal_and_package(output, sign=False).success


def rebase(output):
    (output / "CHECKSUMS.sha256").unlink()
    write_checksums(output)


def test_seal_requires_every_declared_evidence_file(monkeypatch, pack, tmp_path):
    update(
        pack,
        "law/core/actions.yml",
        lambda d: d["actions"][0]["evidence"].append("starter/second.md"),
    )
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, evidence) == 0
    result = seal_and_package(output, sign=False, strict=False)
    assert not result.success
    assert any("starter/second.md" in error for error in result.errors)


def test_seal_reconstructs_inverse_requirements_if_the_action_map_is_incomplete(
    monkeypatch, pack, tmp_path
):
    relations(pack, "inverse")
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, evidence, check=True) == 0
    (output / "evidence" / "starter" / "control-review.md").unlink()
    for filename, edit in (
        ("summary.json", lambda d: d["actions_evidence_map"].update({ACTION: []})),
        ("evidence_manifest.json", lambda d: d.update(included=[], missing=[])),
        ("evidence_quality.json", lambda d: d.update(quality_by_file={})),
    ):
        path = output / "_metadata" / filename
        data = json.loads(path.read_text())
        edit(data)
        path.write_text(json.dumps(data))
    rebase(output)
    result = seal_and_package(output, sign=False, strict=False)
    assert not result.success
    assert any("starter/control-review.md" in error for error in result.errors)


def test_seal_does_not_credit_a_directory_lookalike(monkeypatch, pack, tmp_path):
    update(pack, "law/core/actions.yml", lambda d: d["actions"][0].update(evidence=["starter"]))
    update(pack, "law/policy/evidence_map.yml", lambda d: d.clear())
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, evidence, check=True) == 0
    (output / "evidence" / "starter").rename(output / "evidence" / "starter-other")
    for filename, field in (
        ("evidence_manifest.json", "included"),
        ("evidence_quality.json", "quality_by_file"),
    ):
        path = output / "_metadata" / filename
        data = json.loads(path.read_text())
        data[field] = (
            ["starter-other/control-review.md"]
            if field == "included"
            else {"starter-other/control-review.md": "ready"}
        )
        path.write_text(json.dumps(data))
    rebase(output)
    result = seal_and_package(output, sign=False)
    assert not result.success
    assert any("Missing evidence: starter" in error for error in result.errors)


def test_seal_rejects_action_metadata_that_disagrees_with_the_summary(monkeypatch, pack, tmp_path):
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    output = tmp_path / "output"
    assert export(monkeypatch, pack, output, tmp_path, evidence, check=True) == 0
    (output / "_metadata" / "actions.json").write_text("[]")
    rebase(output)
    result = seal_and_package(output, sign=False)
    assert not result.success
    assert any("Action metadata differs" in error for error in result.errors)


@pytest.mark.parametrize("relation", ["direct", "inverse", "mixed"])
def test_ready_direct_evidence_does_not_replace_missing_article_evidence(
    monkeypatch, pack, tmp_path, relation
):
    relations(pack, relation)
    update(
        pack,
        "law/core/actions.yml",
        lambda d: d["actions"][0].update(evidence=["starter/control-review.md"]),
    )
    update(
        pack,
        "law/policy/evidence_map.yml",
        lambda d: d.update({ARTICLE: [{"path": "starter/article-review.md"}]}),
    )
    evidence = tmp_path / "source"
    (evidence / "starter").mkdir(parents=True)
    (evidence / "starter" / "control-review.md").write_bytes(READY)
    assert invoke(monkeypatch, "lint", "--contracts", pack, "--strict") == 0
    rejected = tmp_path / "rejected"
    assert export(monkeypatch, pack, rejected, tmp_path, evidence, check=True, strict=True) == 1
    assert not rejected.exists()
    output = tmp_path / "reviewable"
    assert export(monkeypatch, pack, output, tmp_path, evidence) == 0
    manifest = json.loads((output / "_metadata" / "evidence_manifest.json").read_text())
    assert "starter/control-review.md" in manifest["included"]
    assert any("starter/article-review.md" in reason for reason in manifest["missing"])
    result = seal_and_package(output, sign=False, strict=True)
    assert not result.success
    assert any("starter/article-review.md" in error for error in result.errors)
