# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Behavior shared by lint, load, library assessment and source snapshots."""

from __future__ import annotations

import copy
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
from intrinsical_policy_engine.common.hashing import compute_framework_pack_hashes
from intrinsical_policy_engine.domain.contract_models import (
    CalendarContract,
    ContractBundle,
    QuestionnaireContract,
)
from intrinsical_policy_engine.domain.exceptions import AssessmentError, ContractValidationError
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle
from intrinsical_policy_engine.domain.services.duedate_service import resolve_calendar
from intrinsical_policy_engine.domain.services.integrity import compute_plan_hash
from intrinsical_policy_engine.domain.services.questionnaire_engine import eval_questions


@pytest.fixture
def pack(tmp_path):
    directory = tmp_path / "pack"
    shutil.copytree("frameworks/starter", directory)
    return directory


def change(pack: Path, file: str, edit):
    path = pack / file
    document = yaml.safe_load(path.read_text())
    edit(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False))


@pytest.mark.parametrize(
    "file,edit",
    [
        ("law/core/actions.yml", lambda d: d["actions"][0].update(priorty="high")),
        (
            "law/core/actions.yml",
            lambda d: d["actions"][0].update(when={"has": "starter.needs_reveiw"}),
        ),
        (
            "law/core/rules.yml",
            lambda d: d["derivations"][0].update(
                when={"not": {"has": "starter.automated_decision"}}
            ),
        ),
        (
            "law/core/rules.yml",
            lambda d: d["classifiers"]["risk_tiers"][0].update(when={"has": 42}),
        ),
        (
            "law/core/rules.yml",
            lambda d: d.update(stops=[{"id": "s", "when": {"wat": "x"}, "outcome": "pending"}]),
        ),
        (
            "law/content/questions/00_scope.yml",
            lambda d: d["groups"][0]["questions"][0].update(when="has('starter.needs_review')"),
        ),
        (
            "law/content/questions/00_scope.yml",
            lambda d: d["groups"][0]["questions"][0].update(type="typo"),
        ),
        (
            "runtime/policies.yml",
            lambda d: d["metrics"].update(
                signals=[
                    {"id": "m", "when": {"has": "starter.typo"}, "score": 0.4, "source": "test"}
                ]
            ),
        ),
        (
            "delivery/profiles/core_outputs.yml",
            lambda d: d["profiles"]["starter_smoke"]["nodes"][0].update(
                when={"has": "starter.typo"}
            ),
        ),
    ],
)
def test_lint_and_load_share_the_same_errors(pack, file, edit):
    change(pack, file, edit)
    adapter = YamlContractsAdapter()
    problems = adapter.validate_detailed(str(pack))
    assert problems
    with pytest.raises(CompilationError) as failure:
        adapter.load(str(pack))
    assert [p.to_dict() for p in problems] == failure.value.problems
    assert any(file in p.source for p in problems)


def test_duplicate_yaml_keys_are_rejected(pack):
    path = pack / "law/core/actions.yml"
    path.write_text(path.read_text() + "actions: []\n")
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any("Duplicate key" in problem.msg for problem in problems)


def test_engine_requirement_is_enforced(pack):
    change(pack, "manifest.yml", lambda d: d.update(requires_engine=">=9999.0.0"))
    with pytest.raises(CompilationError, match="compilation"):
        YamlContractsAdapter().compile(str(pack))


def test_yaml_and_quoted_dates_have_the_same_canonical_model():
    assert CalendarContract.model_validate(
        {"calendar": {"x": date(2024, 2, 29)}}
    ) == CalendarContract.model_validate({"calendar": {"x": "2024-02-29"}})
    with pytest.raises(ValidationError):
        CalendarContract.model_validate({"calendar": {"x": "2024-02-30"}})


def test_relative_dates_require_an_explicit_base_and_clamp_month_end():
    calendar = CalendarContract.model_validate({"calendar": {"x": "T+1m"}})
    with pytest.raises(ValueError, match="explicit base_date"):
        resolve_calendar(calendar)
    assert resolve_calendar(calendar, date(2024, 1, 31)) == {"x": "2024-02-29"}


def test_stop_is_terminal_and_has_an_explicit_blocked_status(pack):
    change(
        pack,
        "runtime/semantics.yml",
        lambda d: (d["outcomes"].append("denied"), d.update(blocked_outcomes=["denied"])),
    )
    change(
        pack,
        "law/core/rules.yml",
        lambda d: d.update(
            stops=[{"id": "terminal", "when": {"has": "starter.needs_review"}, "outcome": "denied"}]
        ),
    )
    plan = assess_from_bundle(
        YamlContractsAdapter().load(str(pack)), {"flags": ["starter.automated_decision"]}
    )
    assert plan["outcome"] == ["denied"]
    assert plan["actions"] == []
    assert plan["risk_tier"] is None
    assert plan["outcome_axes"]["blocked"] is True


def test_no_match_and_literal_none_tier_are_distinct(pack):
    empty = assess_from_bundle(YamlContractsAdapter().load(str(pack)), {"flags": []})
    assert empty["risk_tier"] is None
    change(pack, "runtime/semantics.yml", lambda d: d["tiers"].append("none"))
    change(
        pack,
        "law/core/rules.yml",
        lambda d: d["classifiers"]["risk_tiers"][0]["output"].update(tier="none"),
    )
    plan = assess_from_bundle(
        YamlContractsAdapter().load(str(pack)), {"flags": ["starter.automated_decision"]}
    )
    assert plan["risk_tier"] == "none"
    assert plan["system_profile"]["risk_tier"] == "none"


def test_library_rejects_unknown_and_malformed_input(pack):
    bundle = YamlContractsAdapter().load(str(pack))
    with pytest.raises(AssessmentError, match="unknown flags"):
        assess_from_bundle(bundle, {"flags": ["starter.needs_reveiw"]})
    for bad in (
        {"flags": "starter.automated_decision"},
        {"flags": {"starter.automated_decision": False}},
        {"answers": {"STARTER_Q1": "invalid"}},
    ):
        with pytest.raises((ValidationError, AssessmentError)):
            assess_from_bundle(bundle, bad)


@pytest.mark.parametrize(
    "edit,field_path",
    [
        (
            lambda d: d["rules"].update(
                derivations=[
                    {
                        "id": "invalid-library-derivation",
                        "when": {"all": []},
                        "set_flags": ["starter.undeclared"],
                    }
                ]
            ),
            "rules.derivations[0].set_flags",
        ),
        (
            lambda d: d["actions"]["actions"][0].update(when={"has": "starter.undeclared"}),
            "actions.actions[0].when",
        ),
        (
            lambda d: d["rules"]["classifiers"]["risk_tiers"][0]["output"].update(
                tier="undeclared-tier"
            ),
            "rules.classifiers.risk_tiers[0].output.tier",
        ),
        (
            lambda d: d["actions"]["actions"][0].update(articles=["UNDECLARED-ARTICLE"]),
            "actions.actions[0].articles",
        ),
    ],
)
def test_direct_typed_bundles_reject_invalid_references_before_evaluation(
    pack, monkeypatch, edit, field_path
):
    raw = YamlContractsAdapter().load(str(pack)).model_dump(mode="json")
    edit(raw)
    bundle = ContractBundle.model_validate(raw)

    def evaluation_must_not_run(*_args):
        pytest.fail("invalid bundle reached policy evaluation")

    monkeypatch.setattr(
        "intrinsical_policy_engine.domain.services.assess_service.eval_questions",
        evaluation_must_not_run,
    )
    monkeypatch.setattr(
        "intrinsical_policy_engine.domain.services.assess_service.derive_with_trace",
        evaluation_must_not_run,
    )
    with pytest.raises(ContractValidationError, match="bundle validation failed") as failure:
        assess_from_bundle(bundle, {"answers": {}})
    assert any(
        problem["code"] == "UNKNOWN-REFERENCE"
        and problem["source"] == "bundle"
        and problem["field_path"] == field_path
        for problem in failure.value.problems
    )


def test_question_conditions_are_ordered_and_invalid_answers_are_not_dropped():
    doc = QuestionnaireContract.model_validate(
        {
            "groups": [
                {
                    "id": "g",
                    "questions": [
                        {
                            "id": "first",
                            "text": "First",
                            "type": "one_of",
                            "options": [{"value": "yes", "label": "Yes"}],
                            "map_to_flags": {"yes": ["f"]},
                        },
                        {
                            "id": "second",
                            "text": "Second",
                            "type": "multi",
                            "when": {"not": {"has": "f"}},
                            "options": [{"value": "x", "label": "X"}],
                            "map_to_flags": {"x": ["g"]},
                        },
                    ],
                }
            ]
        }
    )
    assert eval_questions(doc, {"second": ["x"]}) == {"g"}
    with pytest.raises(AssessmentError, match="inactive"):
        eval_questions(doc, {"first": "yes", "second": ["x"]})
    with pytest.raises(AssessmentError, match="unknown question"):
        eval_questions(doc, {"typo": "yes"})


def test_pack_hash_uses_captured_bytes_after_source_changes(pack):
    compiled = YamlContractsAdapter().compile(str(pack))
    before = compute_framework_pack_hashes(compiled.layout)
    (pack / "law/core/actions.yml").write_text("broken: source\n")
    assert compute_framework_pack_hashes(compiled.layout) == before
    assert compiled.source_bytes["law/core/actions.yml"].startswith(b"actions:")


def test_semantic_hash_survives_metadata_redaction(pack):
    bundle = YamlContractsAdapter().load(str(pack))
    plan = assess_from_bundle(
        bundle, {"flags": ["starter.automated_decision"], "system": {"name": "Private"}}
    )
    redacted = copy.deepcopy(plan)
    redacted["system_profile"].pop("name")
    for key in ("system", "provider", "owner", "declared_by", "approvals", "meta"):
        redacted.pop(key)
    redacted["trace"].pop("answers_raw")
    assert compute_plan_hash(plan) == compute_plan_hash(redacted)


def test_semantic_hash_does_not_depend_on_source_formatting_or_trace(pack):
    first = YamlContractsAdapter().compile(str(pack))
    plan = assess_from_bundle(first.bundle, {"flags": ["starter.automated_decision"]})
    source = pack / "law/core/rules.yml"
    source.write_text("# Same rules, different source bytes\n" + source.read_text())
    second = YamlContractsAdapter().compile(str(pack))
    second_plan = assess_from_bundle(second.bundle, {"flags": ["starter.automated_decision"]})
    second_plan["trace"]["derivations_applied"] = []
    second_plan["trace"]["framework_pack_hash"] = "different-source-fingerprint"
    assert first.source_bytes != second.source_bytes
    assert compute_plan_hash(plan) == compute_plan_hash(second_plan)


@pytest.mark.parametrize(
    "invalid",
    [
        "./artifacts/core/00_START_HERE.md.j2",
        "artifacts//core/00_START_HERE.md.j2",
        "artifacts/core/../core/00_START_HERE.md.j2",
        "bad:name",
        "bad\x00name",
        "bad\nname",
    ],
)
def test_noncanonical_template_paths_fail_during_compilation(pack, invalid):
    change(
        pack,
        "delivery/profiles/core_outputs.yml",
        lambda d: d["profiles"]["starter_smoke"]["nodes"][0].update(template=invalid),
    )
    with pytest.raises(CompilationError):
        YamlContractsAdapter().compile(str(pack))


def test_copy_sources_are_captured_for_export_without_reopening(pack):
    source = pack / "delivery" / "source.txt"
    source.write_bytes(b"captured-copy")
    change(
        pack,
        "delivery/profiles/core_outputs.yml",
        lambda d: d["profiles"]["starter_smoke"]["nodes"].append(
            {
                "id": "copied",
                "kind": "copy",
                "source": "delivery/source.txt",
                "target": "copied.txt",
            }
        ),
    )
    compiled = YamlContractsAdapter().compile(str(pack))
    source.write_bytes(b"changed-after-compilation")
    assert compiled.source_bytes["delivery/source.txt"] == b"captured-copy"


def test_template_warnings_are_shared_by_compile_and_lint(pack):
    source = pack / "render" / "artifacts" / "core" / "00_START_HERE.md.j2"
    source.write_text(source.read_text() + "\n{{ missing_root }}\n")
    adapter = YamlContractsAdapter()
    compiled = adapter.compile(str(pack))
    assert compiled.problems
    assert all(problem.severity == "warning" for problem in compiled.problems)
    assert list(compiled.problems) == adapter.validate_detailed(str(pack))


def test_template_syntax_errors_are_load_errors(pack):
    source = pack / "render" / "artifacts" / "core" / "00_START_HERE.md.j2"
    source.write_text("{% if %}")
    adapter = YamlContractsAdapter()
    with pytest.raises(CompilationError):
        adapter.load(str(pack))
    assert any(problem.code == "TPL_SYNTAX" for problem in adapter.validate_detailed(str(pack)))


@pytest.mark.parametrize("content", [b"{{", b"\xff\xfe"])
def test_node_templates_with_arbitrary_extensions_are_validated(pack, content):
    source = pack / "render" / "artifacts" / "core" / "template.md"
    source.write_bytes(content)
    change(
        pack,
        "delivery/profiles/core_outputs.yml",
        lambda d: d["profiles"]["starter_smoke"]["nodes"][0].update(
            template="artifacts/core/template.md"
        ),
    )
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(
        problem.code == "TPL_SYNTAX" and problem.source == "render/artifacts/core/template.md"
        for problem in problems
    )


@pytest.mark.parametrize(
    "reference",
    [
        "{% include 'artifacts/core/included.md' %}",
        "{% extends 'artifacts/core/included.md' %}",
        "{% import 'artifacts/core/included.md' as included %}",
        "{% from 'artifacts/core/included.md' import example %}",
    ],
)
def test_static_template_references_are_validated_from_the_snapshot(pack, reference):
    source = pack / "render" / "artifacts" / "core" / "00_START_HERE.md.j2"
    source.write_text(reference)
    included = source.parent / "included.md"
    included.write_bytes(b"\xff")
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(
        problem.code == "TPL_SYNTAX" and problem.source.endswith("included.md")
        for problem in problems
    )


def test_missing_required_template_reference_fails_during_compilation(pack):
    source = pack / "render" / "artifacts" / "core" / "00_START_HERE.md.j2"
    source.write_text("{% include 'artifacts/core/missing.md' %}")
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(problem.code == "TPL_REFERENCE" for problem in problems)


def test_optional_and_fallback_template_references_preserve_jinja_semantics(pack):
    source = pack / "render" / "artifacts" / "core" / "00_START_HERE.md.j2"
    source.write_text(
        "{% include 'missing.md' ignore missing %}\n"
        "{% include ['missing.md', 'artifacts/core/fallback.md'] %}"
    )
    (source.parent / "fallback.md").write_text("Static fallback")
    compiled = YamlContractsAdapter().compile(str(pack))
    assert compiled.problems == ()


def test_unrelated_binary_assets_remain_valid_and_captured(pack):
    asset = pack / "render" / "artifacts" / "core" / "image.bin"
    asset.write_bytes(b"\xff\x00\xfe")
    compiled = YamlContractsAdapter().compile(str(pack))
    assert compiled.source_bytes["render/artifacts/core/image.bin"] == b"\xff\x00\xfe"


def test_backlog_split_filenames_are_canonical(pack):
    change(
        pack,
        "render/config/backlog_config.yml",
        lambda d: d["splits"][0].update(filename="./backlog.csv"),
    )
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(
        problem.code == "CONTRACT-PATH" and "filename" in problem.field_path for problem in problems
    )


@pytest.mark.parametrize("context", ["engineering_backlog", "legal_backlog"])
def test_backlog_builder_contexts_require_their_pack_split(pack, context):
    change(
        pack,
        "delivery/profiles/core_outputs.yml",
        lambda d: d["profiles"]["starter_smoke"]["nodes"][0].update(context=context),
    )
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(problem.code == "BACKLOG-CONTEXT" for problem in problems)


@pytest.mark.parametrize("value", [".nan", ".inf", "2024-01-01", "!!set {x: null}"])
def test_context_defaults_must_have_finite_json_values(pack, value):
    source = pack / "render" / "config" / "context_defaults.yml"
    source.write_text(f"invalid: {value}\n")
    problems = YamlContractsAdapter().validate_detailed(str(pack))
    assert any(problem.source == "render/config/context_defaults.yml" for problem in problems)
