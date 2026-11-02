# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The programmatic and YAML boundaries enforce the same domain contract."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    CompilationError,
    YamlContractsAdapter,
)
from intrinsical_policy_engine.domain.contract_models import (
    CalendarContract,
    ContractBundle,
    RulesContract,
)
from intrinsical_policy_engine.domain.exceptions import ContractValidationError
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle
from intrinsical_policy_engine.domain.services.duedate_service import resolve_calendar


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    root = tmp_path / "pack"
    shutil.copytree("frameworks/starter", root)
    return root


def change(root: Path, relative: str, edit) -> None:
    path = root / relative
    document = yaml.safe_load(path.read_text())
    edit(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False))


@pytest.mark.parametrize("invalid", ["../outside.md", "./review.md", "reviews//review.md"])
@pytest.mark.parametrize("owner", ["action", "map"])
def test_programmatic_evidence_paths_are_rejected_before_evaluation(
    pack, monkeypatch, owner, invalid
):
    raw = YamlContractsAdapter().compile(str(pack)).bundle.model_dump(mode="json")
    if owner == "action":
        raw["actions"]["actions"][0]["evidence"] = [invalid]
    else:
        raw["evidence_map"]["STARTER-CONTROL-REVIEW"] = [{"path": invalid}]

    def evaluation_must_not_run(*_args):
        pytest.fail("invalid evidence path reached evaluation")

    monkeypatch.setattr(
        "intrinsical_policy_engine.domain.services.assess_service.derive_with_trace",
        evaluation_must_not_run,
    )
    with pytest.raises((ValidationError, ContractValidationError)):
        assess_from_bundle(ContractBundle.model_validate(raw), {"flags": []})


def test_programmatic_bundle_rejects_an_incompatible_engine(pack, monkeypatch):
    raw = YamlContractsAdapter().compile(str(pack)).bundle.model_dump(mode="json")
    raw["manifest"]["requires_engine"] = ">=9999.0.0"

    def evaluation_must_not_run(*_args):
        pytest.fail("incompatible bundle reached evaluation")

    monkeypatch.setattr(
        "intrinsical_policy_engine.domain.services.assess_service.derive_with_trace",
        evaluation_must_not_run,
    )
    with pytest.raises((ValidationError, ContractValidationError)):
        assess_from_bundle(ContractBundle.model_validate(raw), {"flags": []})


def test_framework_version_has_one_authoritative_field(pack):
    bundle = YamlContractsAdapter().compile(str(pack)).bundle
    raw = bundle.model_dump(mode="json")
    assert "version" not in raw
    raw["version"] = "forged-version"
    with pytest.raises(ValidationError, match="version"):
        ContractBundle.model_validate(raw)
    plan = assess_from_bundle(bundle, {"flags": []})
    assert plan["trace"]["framework_version"] == bundle.manifest.version


def test_actions_are_selected_once_from_their_own_conditions_in_catalog_order(pack):
    raw = YamlContractsAdapter().compile(str(pack)).bundle.model_dump(mode="json")
    raw["actions"]["actions"] = [
        {"id": "always", "title": "Always", "requires_evidence": False},
        {
            "id": "matched",
            "title": "Matched",
            "when": {"has": "starter.needs_review"},
            "requires_evidence": False,
        },
        {
            "id": "unmatched",
            "title": "Unmatched",
            "when": {"has": "starter.owner_assigned"},
            "requires_evidence": False,
        },
    ]
    raw["articles"] = {"taxonomy": []}
    raw["evidence_map"] = {}
    bundle = ContractBundle.model_validate(raw)
    plan = assess_from_bundle(bundle, {"flags": ["starter.automated_decision"]})
    assert plan["actions"] == ["always", "matched"]
    assert "packs_fired" not in plan["trace"]["rules_applied"]


def test_retired_pack_activation_and_uncaptured_reference_files_are_rejected():
    with pytest.raises(ValidationError, match="packs"):
        RulesContract.model_validate({"packs": []})
    with pytest.raises(ValidationError, match="reference_files"):
        RulesContract.model_validate(
            {
                "regulatory_meta": {
                    "version": "1.0",
                    "source": "Reviewed interpretation",
                    "reference_files": ["../../absent.md"],
                }
            }
        )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("T+999999999999d", id="days-beyond-range"),
        pytest.param("T+120000m", id="months-beyond-range"),
        pytest.param("T+" + "9" * 5000 + "d", id="beyond-integer-digit-limit"),
    ],
)
def test_impossible_relative_magnitudes_are_compilation_errors(pack, value):
    change(
        pack,
        "law/policy/law_calendar.yml",
        lambda document: document.update(calendar={"deadline": value}, base_date="2026-10-18"),
    )
    with pytest.raises(CompilationError) as failure:
        YamlContractsAdapter().compile(str(pack))
    assert any(
        problem.source == "law/policy/law_calendar.yml" and "calendar" in problem.field_path
        for problem in failure.value.diagnostics
    )


@pytest.mark.parametrize("value", ["T+1d", "T+1m"])
def test_calendar_base_overflow_is_checked_at_compile_and_runtime(pack, value):
    change(
        pack,
        "law/policy/law_calendar.yml",
        lambda document: document.update(calendar={"deadline": value}, base_date="9999-12-31"),
    )
    with pytest.raises(CompilationError) as failure:
        YamlContractsAdapter().compile(str(pack))
    assert any(
        problem.source == "law/policy/law_calendar.yml"
        and problem.field_path == "calendar.calendar.deadline"
        for problem in failure.value.diagnostics
    )
    calendar = CalendarContract.model_validate({"calendar": {"deadline": value}})
    with pytest.raises(ValueError, match=r"calendar\.calendar\.deadline.*9999-12-31"):
        resolve_calendar(calendar, date.max)


def test_relative_date_endpoints_and_leading_zeroes_are_canonical():
    calendar = CalendarContract.model_validate(
        {"calendar": {"last": "T+0001d", "month": "T+0001m"}}
    )
    assert calendar.calendar == {"last": "T+1d", "month": "T+1m"}
    assert resolve_calendar(calendar, date(2024, 1, 31)) == {
        "last": "2024-02-01",
        "month": "2024-02-29",
    }


def test_relative_date_full_range_and_long_leading_zeroes_remain_supported():
    calendar = CalendarContract.model_validate(
        {
            "calendar": {
                "days": f"T+{(date.max - date.min).days}d",
                "months": "T+119987m",
                "zeroes": "T+" + "0" * 5000 + "1d",
            }
        }
    )
    assert resolve_calendar(calendar, date.min) == {
        "days": "9999-12-31",
        "months": "9999-12-01",
        "zeroes": "0001-01-02",
    }


def test_question_guard_on_a_derived_flag_warns_without_rejecting_assessment(pack):
    change(
        pack,
        "law/content/questions/00_scope.yml",
        lambda document: document["groups"][0]["questions"][1].update(
            when={"has": "starter.needs_review"}
        ),
    )
    compiled = YamlContractsAdapter().compile(str(pack))
    assert any(
        problem.code == "QUESTION-UNREACHABLE"
        and problem.severity == "warning"
        and problem.source == "law/content/questions/00_scope.yml"
        and problem.field_path == "questions.groups[0].questions[1].when"
        for problem in compiled.problems
    )
    plan = assess_from_bundle(compiled.bundle, {"answers": {"STARTER_Q1": "yes"}})
    assert "starter.needs_review" in plan["flags"]


@pytest.mark.parametrize(
    "condition",
    [
        {"any": [{"has": "starter.automated_decision"}, {"has": "starter.needs_review"}]},
        {"not": {"has": "starter.needs_review"}},
    ],
)
def test_question_analysis_does_not_warn_for_a_possible_or_negative_guard(pack, condition):
    change(
        pack,
        "law/content/questions/00_scope.yml",
        lambda document: document["groups"][0]["questions"][1].update(when=condition),
    )
    compiled = YamlContractsAdapter().compile(str(pack))
    assert not any(problem.code == "QUESTION-UNREACHABLE" for problem in compiled.problems)


@pytest.mark.parametrize(
    "destination",
    [
        "_metadata/summary.json",
        "_metadata/trace.json",
        "evidence/fixture.md",
        "exports/asana/requests.ndjson",
        "backlogs/backlog.csv",
        "compliance.ics",
        "CHECKSUMS.sha256",
    ],
)
def test_compilation_rejects_engine_owned_outputs_even_on_conditional_nodes(pack, destination):
    change(
        pack,
        "delivery/profiles/core_outputs.yml",
        lambda document: document["profiles"]["starter_smoke"]["nodes"][0].update(
            name=destination, when={"has": "starter.owner_assigned"}
        ),
    )
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(
        problem.code == "OUTPUT-COLLISION"
        and problem.source == "delivery/profiles/core_outputs.yml"
        and problem.field_path == "bundle_profiles.starter_smoke.nodes[0].name"
        for problem in problems
    )


def configure_outputs(root: Path, first: dict, second: dict, *, separate_profiles=False) -> None:
    def edit(document):
        profile = document["profiles"]["starter_smoke"]
        original = profile["nodes"][0]
        first_node = {**original, "id": "first", **first}
        second_node = {**original, "id": "second", **second}
        profile["nodes"] = [first_node]
        if separate_profiles:
            document["profiles"]["second_profile"] = {
                **profile,
                "id": "second_profile",
                "nodes": [second_node],
            }
        else:
            profile["nodes"].append(second_node)

    change(root, "delivery/profiles/core_outputs.yml", edit)


@pytest.mark.parametrize("separate_profiles", [False, True])
@pytest.mark.parametrize(
    "first,second",
    [
        ({"name": "shared.md"}, {"name": "shared.md"}),
        ({"name": "shared"}, {"name": "shared", "kind": "dir", "template": None}),
        ({"name": "shared"}, {"name": "shared/child.md"}),
        ({"name": "shared/child.md"}, {"name": "shared"}),
    ],
)
def test_compilation_rejects_unconditional_destination_collisions(
    pack, separate_profiles, first, second
):
    configure_outputs(pack, first, second, separate_profiles=separate_profiles)
    with pytest.raises(CompilationError) as failure:
        YamlContractsAdapter().compile(str(pack))
    assert any(problem.code == "OUTPUT-COLLISION" for problem in failure.value.diagnostics)


@pytest.mark.parametrize("separate_profiles", [False, True])
def test_compilation_accepts_mutually_exclusive_destinations(pack, separate_profiles):
    configure_outputs(
        pack,
        {"name": "shared.md", "when": {"has": "starter.owner_assigned"}},
        {"name": "shared.md", "when": {"not": {"has": "starter.owner_assigned"}}},
        separate_profiles=separate_profiles,
    )
    if separate_profiles:

        def use_profile_guards(document):
            for profile in document["profiles"].values():
                profile["when"] = profile["nodes"][0].pop("when")

        change(pack, "delivery/profiles/core_outputs.yml", use_profile_guards)
    assert YamlContractsAdapter().compile(str(pack)).problems == ()


def test_static_collision_analysis_does_not_evaluate_conditions_against_empty_flags(pack):
    configure_outputs(
        pack,
        {"name": "shared.md"},
        {"name": "shared.md", "when": {"not": {"has": "starter.owner_assigned"}}},
    )
    assert YamlContractsAdapter().compile(str(pack)).problems == ()


def test_static_collision_analysis_respects_parent_guards(pack):
    configure_outputs(
        pack,
        {
            "kind": "dir",
            "name": "shared",
            "template": None,
            "when": {"has": "starter.owner_assigned"},
            "children": [
                {
                    "id": "guarded_child",
                    "kind": "file",
                    "name": "child.md",
                    "template": "artifacts/core/00_START_HERE.md.j2",
                }
            ],
        },
        {"name": "shared/child.md", "when": {"not": {"has": "starter.owner_assigned"}}},
    )
    assert YamlContractsAdapter().compile(str(pack)).problems == ()


def test_format_three_records_the_breaking_selection_contract(pack):
    compiled = YamlContractsAdapter().compile(str(pack))
    assert compiled.bundle.manifest.pack_format_version == 3
    assert compiled.bundle.manifest.version == "0.3.0"
    change(pack, "manifest.yml", lambda document: document.update(pack_format_version=2))
    with pytest.raises(CompilationError) as failure:
        YamlContractsAdapter().compile(str(pack))
    assert any(
        problem.source == "manifest.yml" and problem.field_path == "pack_format_version"
        for problem in failure.value.diagnostics
    )
