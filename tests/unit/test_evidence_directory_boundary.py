# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Regression tests for directory entries in framework evidence maps."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout
from intrinsical_policy_engine.api import Engine, ExecutionPolicy, ExportRequest
from intrinsical_policy_engine.app.export.orchestrator import ExportConfig, ExportOrchestrator
from intrinsical_policy_engine.app.template_validation import validate_integrity


def _pack_with_directory_evidence(
    tmp_path: Path,
    *,
    declared_path: str = "review-materials/",
) -> Path:
    pack = tmp_path / "neutral-pack"
    shutil.copytree("frameworks/starter", pack)

    layout = load_framework_layout(pack)
    evidence_map_path = layout.resolve_contract_files("evidence_map")[0]
    evidence_map_path.write_text(
        yaml.safe_dump(
            {
                "TOPIC-STARTER-CONTROLS": [
                    {"path": declared_path, "required": True},
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    collection = layout.evidence_templates_dir / "review-materials"
    collection.mkdir()
    (collection / "README.md").write_text(
        "# Review materials\n\nNeutral evidence template collection.\n",
        encoding="utf-8",
    )
    return pack


def test_template_integrity_expands_declared_evidence_directory(tmp_path: Path) -> None:
    pack = _pack_with_directory_evidence(tmp_path)

    report = validate_integrity(pack)

    assert report.has_errors is False


def test_template_integrity_scans_files_inside_declared_directory(tmp_path: Path) -> None:
    pack = _pack_with_directory_evidence(tmp_path)
    layout = load_framework_layout(pack)
    (layout.evidence_templates_dir / "review-materials" / "control.md").write_text(
        "---\nrelated_actions:\n  - STARTER-UNDECLARED\n---\n\n# Control template\n",
        encoding="utf-8",
    )

    report = validate_integrity(pack)

    assert [(issue.category, issue.location) for issue in report.errors] == [
        ("unknown_action", "review-materials/control.md")
    ]


def test_related_article_labels_are_not_misclassified_as_pack_ids(tmp_path: Path) -> None:
    pack = _pack_with_directory_evidence(tmp_path)
    layout = load_framework_layout(pack)
    (layout.evidence_templates_dir / "review-materials" / "citations.md").write_text(
        "---\n"
        "related_articles:\n"
        "  - External regulation article 5\n"
        "  - Recitals about prohibited practices\n"
        "---\n\n"
        "# Legal citations\n",
        encoding="utf-8",
    )

    report = validate_integrity(pack)

    assert report.has_errors is False


def test_declared_directory_rejects_file_symlink_escape(tmp_path: Path) -> None:
    pack = _pack_with_directory_evidence(tmp_path)
    layout = load_framework_layout(pack)
    external = tmp_path / "external.md"
    external.write_text("# Outside the pack\n", encoding="utf-8")
    link = layout.evidence_templates_dir / "review-materials" / "outside.md"
    try:
        link.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")

    report = validate_integrity(pack)

    assert [(issue.category, issue.location) for issue in report.errors] == [
        ("unsafe_pack_path", "evidence/templates/review-materials/outside.md")
    ]


def test_overlapping_action_and_article_ids_are_validated_exactly(tmp_path: Path) -> None:
    pack = _pack_with_directory_evidence(tmp_path)
    layout = load_framework_layout(pack)
    actions_path = layout.resolve_contract_files("actions")[0]
    articles_path = layout.resolve_contract_files("articles")[0]
    evidence_map_path = layout.resolve_contract_files("evidence_map")[0]

    actions_path.write_text(
        "version: 1.0.0\n"
        "actions:\n"
        "  - id: CONTROL-REVIEW\n"
        "    title: Review control\n"
        "    articles: [TOPIC-CONTROL-REVIEW]\n",
        encoding="utf-8",
    )
    articles_path.write_text(
        "version: 1.0.0\ntaxonomy:\n  - id: TOPIC-CONTROL-REVIEW\n    title: Review topic\n",
        encoding="utf-8",
    )
    evidence_map_path.write_text(
        "TOPIC-CONTROL-REVIEW:\n  - path: review-materials/reference.md\n    required: true\n",
        encoding="utf-8",
    )
    (layout.evidence_templates_dir / "review-materials" / "reference.md").write_text(
        "---\n"
        "related_articles:\n"
        "  - TOPIC-CONTROL-REVIEW\n"
        "related_actions:\n"
        "  - CONTROL-REVIEW\n"
        "---\n\n"
        "# Exact references\n",
        encoding="utf-8",
    )

    report = validate_integrity(pack)

    assert report.has_errors is False


def test_strict_export_api_accepts_exact_overlapping_references(tmp_path: Path) -> None:
    pack = tmp_path / "neutral-overlap-pack"
    shutil.copytree("frameworks/starter", pack)
    layout = load_framework_layout(pack)
    flags_path = layout.resolve_contract_files("flags")[0]
    actions_path = layout.resolve_contract_files("actions")[0]
    articles_path = layout.resolve_contract_files("articles")[0]
    evidence_map_path = layout.resolve_contract_files("evidence_map")[0]

    flags_doc = yaml.safe_load(flags_path.read_text(encoding="utf-8"))
    flags_doc["registry"].append(
        {
            "id": "neutral.reference_test",
            "description": "Inactive flag used for exact reference validation.",
        }
    )
    flags_path.write_text(yaml.safe_dump(flags_doc, sort_keys=False), encoding="utf-8")

    actions_doc = yaml.safe_load(actions_path.read_text(encoding="utf-8"))
    actions_doc["actions"].append(
        {
            "id": "CONTROL-REVIEW",
            "title": "Review control",
            "description": "Neutral inactive control used for reference validation.",
            "applies_to": "any",
            "priority": "medium",
            "when": "has('neutral.reference_test')",
            "articles": ["TOPIC-CONTROL-REVIEW"],
            "legal_refs": ["Source: Review policy"],
            "evidence": ["neutral/reference.md"],
        }
    )
    actions_path.write_text(yaml.safe_dump(actions_doc, sort_keys=False), encoding="utf-8")

    articles_doc = yaml.safe_load(articles_path.read_text(encoding="utf-8"))
    articles_doc["taxonomy"].append(
        {
            "id": "TOPIC-CONTROL-REVIEW",
            "title": "Review topic",
            "description": "Neutral topic with an overlapping action-id suffix.",
        }
    )
    articles_path.write_text(yaml.safe_dump(articles_doc, sort_keys=False), encoding="utf-8")

    evidence_doc = yaml.safe_load(evidence_map_path.read_text(encoding="utf-8"))
    evidence_doc["TOPIC-CONTROL-REVIEW"] = [{"path": "neutral/reference.md", "required": True}]
    evidence_map_path.write_text(yaml.safe_dump(evidence_doc, sort_keys=False), encoding="utf-8")
    neutral_templates = layout.evidence_templates_dir / "neutral"
    neutral_templates.mkdir()
    (neutral_templates / "reference.md").write_text(
        "---\n"
        "related_articles:\n"
        "  - TOPIC-CONTROL-REVIEW\n"
        "related_actions:\n"
        "  - CONTROL-REVIEW\n"
        "---\n\n"
        "# Exact references\n",
        encoding="utf-8",
    )

    lint_problems = YamlContractsAdapter(strict=True).validate(
        str(pack),
        use_framework_schemas=True,
        strict_schemas=True,
    )
    assert lint_problems == []

    result = Engine().export(
        ExportRequest(
            pack=pack,
            answers={
                "answers": {
                    "STARTER_Q1": "yes",
                    "STARTER_Q2": "yes",
                    "STARTER_Q3": "yes",
                },
                "system": {"name": "Example workflow"},
            },
            output_dir=tmp_path / "out",
            policy=ExecutionPolicy(strict=True, skip_gpg_signing=True),
        )
    )

    assert result.success, result.diagnostics
    assert all("unknown_action" not in diagnostic.message for diagnostic in result.diagnostics)


def test_strict_lint_and_export_template_validation_accept_same_directory(
    tmp_path: Path,
) -> None:
    pack = _pack_with_directory_evidence(tmp_path)
    layout = load_framework_layout(pack)

    lint_problems = YamlContractsAdapter(strict=True).validate(
        str(pack),
        use_framework_schemas=True,
        strict_schemas=True,
    )
    orchestrator = ExportOrchestrator(
        ExportConfig(
            plan={},
            contracts_dir=pack,
            outdir=tmp_path / "out",
            save_plan=False,
            templates=None,
            targets=["filesystem"],
            config_path=None,
            strict=True,
            strict_templates=True,
        )
    )
    export_problem = orchestrator._validate_templates_integrity(str(layout.templates_dir))

    assert lint_problems == []
    assert export_problem is None


def test_directory_without_marker_has_same_actionable_diagnostic_at_both_boundaries(
    tmp_path: Path,
) -> None:
    pack = _pack_with_directory_evidence(tmp_path, declared_path="review-materials")
    layout = load_framework_layout(pack)

    lint_problems = YamlContractsAdapter(strict=True).validate(
        str(pack),
        use_framework_schemas=True,
        strict_schemas=True,
    )
    report = validate_integrity(pack)
    orchestrator = ExportOrchestrator(
        ExportConfig(
            plan={},
            contracts_dir=pack,
            outdir=tmp_path / "out",
            save_plan=False,
            templates=None,
            targets=["filesystem"],
            config_path=None,
            strict=True,
            strict_templates=True,
        )
    )
    export_problem = orchestrator._validate_templates_integrity(str(layout.templates_dir))

    assert any("must end with '/'" in problem for problem in lint_problems)
    assert [issue.category for issue in report.errors] == ["template_path_kind"]
    assert export_problem is not None
    assert "Add a trailing '/'" in export_problem
    assert "Errno 21" not in export_problem
