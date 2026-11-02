# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Profile article traces agree across compilation, coverage and CLI gates."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.app.cli.main import main
from intrinsical_policy_engine.domain.bundles.coverage import profile_coverage
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle

ACTION = "STARTER-CONTROL-REVIEW"
ARTICLE = "TOPIC-STARTER-CONTROLS"
PROFILE = "delivery/profiles/core_outputs.yml"


def replace_trace(pack: Path, trace: dict[str, list[str]]) -> None:
    path = pack / PROFILE
    document = yaml.safe_load(path.read_text())
    document["profiles"]["starter_smoke"]["nodes"][0]["trace_back_to"] = trace
    path.write_text(yaml.safe_dump(document, sort_keys=False))


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    root = tmp_path / "pack"
    shutil.copytree("frameworks/starter", root)
    replace_trace(root, {"actions": [ACTION], "articles": [ARTICLE]})
    return root


def test_compiled_profile_covers_declared_articles(pack: Path) -> None:
    compiled = YamlContractsAdapter().compile(str(pack))
    assert compiled.problems == ()
    plan = assess_from_bundle(compiled.bundle, {"flags": ["starter.automated_decision"]})
    coverage = profile_coverage(compiled.bundle_profiles, plan)
    assert coverage.required_articles == coverage.covered_articles == {ARTICLE}
    assert coverage.missing_articles == set()
    assert not coverage.has_critical_gaps()


@pytest.mark.parametrize("reference", [ACTION, "UNDECLARED-ARTICLE"])
def test_lint_rejects_nonarticle_trace_references(pack: Path, reference: str) -> None:
    replace_trace(pack, {"actions": [ACTION], "articles": [reference]})
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(
        problem.code == "UNKNOWN-REFERENCE"
        and problem.severity == "error"
        and problem.source == PROFILE
        and problem.field_path.endswith("trace_back_to.articles")
        and problem.msg == f"unknown article: {reference}"
        for problem in problems
    )


@pytest.mark.parametrize("keep_articles", [False, True])
def test_legacy_profile_trace_field_is_rejected(pack: Path, keep_articles: bool) -> None:
    trace = {"actions": [ACTION], "evidences": [ARTICLE]}
    if keep_articles:
        trace["articles"] = [ARTICLE]
    replace_trace(pack, trace)
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(
        problem.code == "CONTRACT-TYPE" and problem.field_path.endswith("trace_back_to.evidences")
        for problem in problems
    )


def test_assess_and_export_report_the_same_missing_article(
    pack: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_trace(pack, {"actions": [ACTION]})
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"flags": ["starter.automated_decision"]}))
    for command in ("assess", "export"):
        destination = tmp_path / command
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "ipe",
                command,
                "--contracts",
                str(pack),
                "--answers",
                str(answers),
                "--evidence-dir",
                "demos/starter/basic/evidence",
                "--check-ci",
                "--strict",
                "--out",
                str(destination),
            ],
        )
        assert main() == 1
        problems = [
            line.strip() for line in capsys.readouterr().err.splitlines() if "CI-004:" in line
        ]
        assert problems == [f"CI-004: profile coverage missing actions=[] articles=['{ARTICLE}']"]
        assert not destination.exists()
