"""Nonstarter pack variations exercise producer and consumer contracts together."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml
from jinja2 import UndefinedError
from pydantic import ValidationError

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    CompilationError,
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import (
    build_task_items,
    stable_uid,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import ExportSettings
from intrinsical_policy_engine.adapters.export.bundles.bundle_exporter import BundleExporter
from intrinsical_policy_engine.adapters.export.filesystem.ics import write_ics
from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.app.config.context import build_artifact_context
from intrinsical_policy_engine.app.evidence import EvidenceSnapshot
from intrinsical_policy_engine.app.export.orchestrator import export_plan
from intrinsical_policy_engine.app.export.projection import project_plan
from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.context_builders import legal_backlog_builder
from intrinsical_policy_engine.domain.bundles.coverage import profile_coverage
from intrinsical_policy_engine.domain.bundles.models import BacklogConfig
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle
from intrinsical_policy_engine.domain.services.integrity import compute_plan_hash


@pytest.fixture
def variation(tmp_path):
    root = tmp_path / "variation"
    shutil.copytree("frameworks/starter", root)
    flags = root / "law/core/flags.yml"
    document = yaml.safe_load(flags.read_text())
    document["registry"].append({"id": "variant.enabled"})
    flags.write_text(yaml.safe_dump(document, sort_keys=False))
    template = root / "render/artifacts/core/variant.md.j2"
    template.write_text("## Actual variation\n{{ framework_name }}\n")
    return root


def profiles(root: Path, nodes: list[dict], *, when=None) -> None:
    profile = {"id": "variation", "kind": "test", "root_dir": ".", "nodes": nodes}
    if when is not None:
        profile["when"] = when
    (root / "delivery/profiles/core_outputs.yml").write_text(
        yaml.safe_dump({"profiles": {"variation": profile}}, sort_keys=False)
    )


def leaf(**changes):
    return {
        "id": "document",
        "kind": "file",
        "name": "control.md",
        "template": "artifacts/core/variant.md.j2",
        "trace_back_to": {
            "actions": ["STARTER-CONTROL-REVIEW"],
            "articles": ["TOPIC-STARTER-CONTROLS"],
        },
        **changes,
    }


def assembler(compiled):
    prefix = compiled.layout.templates_dir.relative_to(compiled.layout.framework_dir).as_posix()
    sources = {
        name.removeprefix(prefix + "/"): content
        for name, content in compiled.layout.source_bytes.items()
        if name.startswith(prefix + "/")
    }
    return ArtifactAssembler(sources)


def assessed(compiled, **options):
    return assess_from_bundle(compiled.bundle, {"flags": ["starter.automated_decision"]}, **options)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("guard", ["node", "parent", "profile"])
@pytest.mark.parametrize("counts_for_coverage", [False, True])
def test_renderer_and_coverage_share_selection_and_coverage_opt_out(
    variation, tmp_path, enabled, guard, counts_for_coverage
):
    nodes = [leaf(counts_for_coverage=counts_for_coverage)]
    when = {"has": "variant.enabled"}
    if guard == "parent":
        nodes = [{"id": "parent", "kind": "dir", "name": "nested", "when": when, "children": nodes}]
        profiles(variation, nodes)
    elif guard == "profile":
        profiles(variation, nodes, when=when)
    else:
        nodes[0]["when"] = when
        profiles(variation, nodes)
    compiled = YamlContractsAdapter().compile(str(variation))
    plan = assessed(compiled)
    if enabled:
        plan["flags"].append("variant.enabled")
    coverage = profile_coverage(compiled.bundle_profiles, plan)
    context = EvalContext(
        plan=plan, extras=build_artifact_context(plan, defaults=compiled.context_defaults)
    )
    target = tmp_path / "rendered"
    target.mkdir()
    BundleExporter(assembler(compiled)).export_profile(
        compiled.bundle_profiles["variation"], context, target
    )
    rendered = [path for path in target.rglob("*") if path.is_file()]
    assert bool(rendered) is enabled
    credited = enabled and counts_for_coverage
    assert coverage.covered_actions == ({"STARTER-CONTROL-REVIEW"} if credited else set())
    assert coverage.covered_articles == ({"TOPIC-STARTER-CONTROLS"} if credited else set())
    assert coverage.has_critical_gaps() is not credited
    if not enabled:
        assert list(target.iterdir()) == []


def test_copy_node_uses_captured_bytes_after_source_changes(variation, tmp_path):
    source = "render/artifacts/core/variant.md.j2"
    profiles(
        variation, [{"id": "copy", "kind": "copy", "source": source, "target": "copied-source.bin"}]
    )
    compiled = YamlContractsAdapter().compile(str(variation))
    captured = compiled.layout.source_bytes[source]
    (variation / source).write_bytes(b"changed after compilation")
    plan = assessed(compiled)
    context = EvalContext(plan=plan, extras={"source_bytes": compiled.layout.source_bytes})
    target = tmp_path / "rendered"
    target.mkdir()
    BundleExporter(assembler(compiled)).export_profile(
        compiled.bundle_profiles["variation"], context, target
    )
    assert (target / "copied-source.bin").read_bytes() == captured


def test_binary_copy_source_is_delivered_without_text_decoding(variation, tmp_path):
    source = variation / "render/assets/logo.bin"
    source.parent.mkdir()
    source.write_bytes(b"\xff\x00\x80captured asset")
    relative = source.relative_to(variation).as_posix()
    profiles(variation, [{"id": "asset", "kind": "copy", "source": relative, "target": "logo.bin"}])
    compiled = YamlContractsAdapter().compile(str(variation))
    source.write_bytes(b"changed after compilation")
    destination = tmp_path / "delivery"
    export_plan(
        plan=assessed(compiled),
        pack=compiled,
        destination=destination,
        targets=["filesystem"],
        settings=ExportSettings(),
        evidence=EvidenceSnapshot(),
    )
    assert (destination / "logo.bin").read_bytes() == b"\xff\x00\x80captured asset"


def test_library_evidence_snapshot_cannot_escape_staging(variation, tmp_path):
    profiles(variation, [leaf()])
    compiled = YamlContractsAdapter().compile(str(variation))
    destination = tmp_path / "delivery"
    outside = tmp_path / "escaped.bin"
    evidence = EvidenceSnapshot(files={"../../escaped.bin": b"invalid path"})
    with pytest.raises(ValueError):
        export_plan(
            plan=assessed(compiled),
            pack=compiled,
            destination=destination,
            targets=["filesystem"],
            settings=ExportSettings(),
            evidence=evidence,
        )
    assert not destination.exists()
    assert not outside.exists()


@pytest.mark.parametrize(
    "source", ["./render/artifacts/core/variant.md.j2", "render//artifacts/core/variant.md.j2"]
)
def test_copy_source_alias_is_rejected_at_compilation(variation, source):
    profiles(variation, [{"id": "copy", "kind": "copy", "source": source, "target": "copied.bin"}])
    with pytest.raises(CompilationError):
        YamlContractsAdapter().compile(str(variation))


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "../escape.md"),
        ("name", "/escape.md"),
        ("template", "../outside.j2"),
        ("template", "./artifacts/core/variant.md.j2"),
    ],
)
def test_output_and_template_paths_are_canonical_before_rendering(variation, field, value):
    profiles(variation, [leaf(**{field: value})])
    with pytest.raises(CompilationError):
        YamlContractsAdapter().compile(str(variation))


@pytest.mark.parametrize("included", [False, True])
def test_referenced_non_j2_templates_are_checked_for_syntax(variation, included):
    malformed = variation / "render/artifacts/core/malformed.md"
    malformed.write_text("{{")
    if included:
        (variation / "render/artifacts/core/variant.md.j2").write_text(
            '{% include "artifacts/core/malformed.md" %}'
        )
        profiles(variation, [leaf()])
    else:
        profiles(variation, [leaf(template="artifacts/core/malformed.md")])
    with pytest.raises(CompilationError) as failure:
        YamlContractsAdapter().compile(str(variation))
    assert any("malformed.md" in problem.source for problem in failure.value.diagnostics)


def test_valid_non_j2_template_is_rendered_from_snapshot(variation, tmp_path):
    source = variation / "render/artifacts/core/note.txt"
    source.write_text("Rendered {{ framework_name }}")
    profiles(variation, [leaf(template="artifacts/core/note.txt")])
    compiled = YamlContractsAdapter().compile(str(variation))
    source.write_text("changed after compilation")
    destination = tmp_path / "delivery"
    export_plan(
        plan=assessed(compiled),
        pack=compiled,
        destination=destination,
        targets=["filesystem"],
        settings=ExportSettings(),
        evidence=EvidenceSnapshot(),
    )
    assert (destination / "control.md").read_text() == "Rendered Starter Pack"


def test_library_snapshot_cannot_claim_ready_for_placeholder_bytes(variation, tmp_path):
    profiles(variation, [leaf()])
    compiled = YamlContractsAdapter().compile(str(variation))
    plan = assessed(compiled)
    name = plan["actions_evidence_map"][plan["actions"][0]][0]
    evidence = EvidenceSnapshot(
        files={name: b"TODO supply actual evidence"}, quality={name: "ready"}, supplied=True
    )
    destination = tmp_path / "delivery"
    with pytest.raises(ValueError, match="CI-00"):
        export_plan(
            plan=plan,
            pack=compiled,
            destination=destination,
            targets=["filesystem"],
            settings=ExportSettings(),
            evidence=evidence,
            check_ci_enabled=True,
            strict=False,
        )
    assert not destination.exists()


def test_failed_render_does_not_publish_partial_output(variation, tmp_path):
    profiles(variation, [leaf()])
    (variation / "render/artifacts/core/variant.md.j2").write_text("{{ absent_value }}")
    compiled = YamlContractsAdapter().compile(str(variation))
    destination = tmp_path / "delivery"
    with pytest.raises(UndefinedError, match="absent_value"):
        export_plan(
            plan=assessed(compiled),
            pack=compiled,
            destination=destination,
            targets=["filesystem"],
            settings=ExportSettings(),
            evidence=EvidenceSnapshot(),
            strict=False,
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".delivery.ipe-*"))


def test_profile_context_keeps_computed_metadata(variation, tmp_path):
    profiles(variation, [leaf()])
    (variation / "render/artifacts/core/variant.md.j2").write_text("{{ meta.generated_at }}")
    compiled = YamlContractsAdapter().compile(str(variation))
    plan = assessed(compiled)
    destination = tmp_path / "delivery"
    export_plan(
        plan=plan,
        pack=compiled,
        destination=destination,
        targets=["filesystem"],
        settings=ExportSettings(),
        evidence=EvidenceSnapshot(),
    )
    assert (destination / "control.md").read_text() == plan["assessment_timestamp"]


@pytest.mark.parametrize("question_id", ["id", "customer_id", "normal_question"])
def test_question_identifier_cannot_bypass_default_redaction(variation, question_id):
    questions = variation / "law/content/questions/00_scope.yml"
    document = yaml.safe_load(questions.read_text())
    document["groups"][0]["questions"].append(
        {"id": question_id, "type": "text", "text": "Private free text"}
    )
    questions.write_text(yaml.safe_dump(document, sort_keys=False))
    compiled = YamlContractsAdapter().compile(str(variation))
    private = "PRIVATE-USER-TEXT-928"
    plan = assess_from_bundle(
        compiled.bundle, {"answers": {question_id: private}}, include_full_trace=True
    )
    projected = project_plan(plan)
    assert private not in json.dumps(projected)
    assert compute_plan_hash(plan) == compute_plan_hash(projected)
    assert private in json.dumps(project_plan(plan, include_raw_input=True))


def test_semantic_identity_and_action_uid_survive_redaction_and_date_changes(variation):
    compiled = YamlContractsAdapter().compile(str(variation))
    plan = assessed(compiled)
    plan["system"]["name"] = "Private subject name"
    plan["system_profile"]["name"] = "Private subject name"
    plan["export_context"] = {"uid_namespace": "case-sensitive project"}
    projected = project_plan(plan)
    assert compute_plan_hash(plan) == compute_plan_hash(projected)
    assert plan["system"]["name"] == "Private subject name"
    first = build_task_items(projected)[0]
    changed = copy.deepcopy(projected)
    changed["due_hints"][first.action_id] = "2029-01-01"
    second = build_task_items(changed)[0]
    assert first.uid == second.uid
    assert first.content_hash != second.content_hash
    assert first.uid == stable_uid("case-sensitive project", first.action_id)
    assert first.uid != stable_uid("Case-sensitive project", first.action_id)


def test_config_typos_are_rejected_independently_of_tolerant_export():
    with pytest.raises(ValidationError):
        ExportSettings.model_validate(
            {
                "asana": {
                    "base_url": "https://example.invalid",
                    "project_gid": "project",
                    "tag_gid": ["typo"],
                }
            }
        )
    with pytest.raises(ValidationError):
        ExportSettings.model_validate({"uidNamespace": "removed-alias"})


@pytest.mark.parametrize("field", ["project", "summary", "duedate", "description"])
def test_jira_customfields_cannot_override_generated_contract_fields(field):
    with pytest.raises(ValidationError, match="cannot override"):
        ExportSettings.model_validate(
            {
                "jira": {
                    "base_url": "https://example.invalid",
                    "project_key": "TEAM",
                    "customfields": {field: "unexpected override"},
                }
            }
        )


def test_rendered_profile_keeps_computed_metrics(variation, tmp_path):
    profiles(variation, [leaf()])
    (variation / "render/artifacts/core/variant.md.j2").write_text(
        "{{ metrics.action_count }} / {{ metrics.hitl_index }}"
    )
    compiled = YamlContractsAdapter().compile(str(variation))
    plan = assessed(compiled)
    destination = tmp_path / "delivery"
    export_plan(
        plan=plan,
        pack=compiled,
        destination=destination,
        targets=["filesystem"],
        settings=ExportSettings(),
        evidence=EvidenceSnapshot(),
    )
    assert (destination / "control.md").read_text() == (
        f"{plan['metrics']['action_count']} / {plan['metrics']['hitl_index']}"
    )


def test_front_matter_cannot_claim_readiness_for_placeholder_bytes():
    data = b"---\nquality: ready\nstatus: complete\n---\nTODO real evidence\n"
    assert QualityEngine().classify_bytes(data, ".MD") == "placeholder"


def test_explicit_backlog_fallback_keeps_unmatched_actions():
    config = BacklogConfig.model_validate(
        {
            "fallback_split_id": "governance",
            "splits": [
                {"id": "engineering", "filename": "engineering.csv", "keywords": ["BUILD"]},
                {"id": "governance", "filename": "governance.csv", "keywords": []},
            ],
        }
    )
    plan = {
        "actions_meta": [
            {"id": "build", "title": "Build a model"},
            {"id": "review", "title": "Review owners"},
        ]
    }
    context = legal_backlog_builder(EvalContext(plan=plan, extras={"backlog_config": config}))
    assert [action["id"] for action in context["actions_meta"]] == ["review"]


@pytest.mark.parametrize("due", ["0001-01-01", "2026-11-12", "9999-12-31"])
def test_calendar_supports_the_full_canonical_date_range(tmp_path, due):
    plan = {"due_hints": {"review": due}, "export_context": {"uid_namespace": "calendar"}}
    calendar = write_ics(plan, tmp_path, prodid="calendar regression")
    assert calendar is not None
    content = calendar.read_bytes().decode("utf-8")
    assert f"DTSTART;VALUE=DATE:{due.replace('-', '')}\r\n" in content
    assert "DURATION:P1D\r\n" in content
    assert "DTEND" not in content
