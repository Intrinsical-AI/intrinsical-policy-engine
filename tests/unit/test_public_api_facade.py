# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Contract tests for the supported 3.x embedding facade."""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest
import yaml

import intrinsical_policy_engine as public_root
import intrinsical_policy_engine.api as public_api
from intrinsical_policy_engine import __version__
from intrinsical_policy_engine.api import (
    AssessmentRequest,
    Engine,
    EngineConfig,
    ExecutionPolicy,
    ExportRequest,
    GateStatus,
    PackDescriptor,
    PackProvider,
    PackValidationRequest,
    ProductIdentity,
    SealRequest,
)

STARTER = Path("frameworks/starter")
STARTER_ANSWERS = Path("demos/starter/basic/answers.json")


def _answers() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(STARTER_ANSWERS.read_text(encoding="utf-8")),
    )


def test_public_exports_are_explicit_and_owned_by_api_package() -> None:
    expected = {
        "AssessmentRequest",
        "AssessmentResult",
        "Diagnostic",
        "DiagnosticSeverity",
        "Engine",
        "EngineConfig",
        "ExecutionPolicy",
        "ExportRequest",
        "ExportResult",
        "GateCheck",
        "GateDecision",
        "GateReport",
        "GateStatus",
        "PackCompatibilityError",
        "PackCompatibilityMetadataError",
        "PackDescriptor",
        "PackError",
        "PackLicenseMetadataError",
        "PackMetadataError",
        "PackProvider",
        "PackValidationRequest",
        "PackValidationResult",
        "ProductIdentity",
        "SealRequest",
        "SealResult",
        "evaluate_gate",
    }

    assert set(public_api.__all__) == expected
    assert all(
        getattr(public_api, name).__module__.startswith("intrinsical_policy_engine.api")
        for name in public_api.__all__
    )
    assert public_root.Engine is public_api.Engine
    assert set(public_api.__all__).issubset(public_root.__all__)


def test_default_provider_resolves_stable_pack_identity() -> None:
    descriptor = Engine().describe_pack(STARTER)

    assert descriptor.id == "starter"
    assert descriptor.version == "0.1.0"
    assert descriptor.root == STARTER.resolve()
    assert descriptor.format_version == 1
    assert descriptor.compatible_engine_versions == (">=1.0.0",)
    assert descriptor.engine_version == "3.0.0a1"
    assert descriptor.manifest_timestamp == "2026-04-19T00:00:00Z"
    with pytest.raises(FrozenInstanceError):
        descriptor.version = "changed"  # type: ignore[misc]


def test_descriptor_exposes_validated_distribution_metadata(tmp_path: Path) -> None:
    pack_root = tmp_path / "neutral-pack"
    shutil.copytree(STARTER, pack_root)
    (pack_root / "LICENSE").write_text("Example license\n", encoding="utf-8")
    manifest_path = pack_root / "manifest.yml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + "\nlicense: Example-1.0\nlicense_file: LICENSE\nattribution: Example Authors\n",
        encoding="utf-8",
    )

    descriptor = Engine().describe_pack(pack_root)

    assert descriptor.license == "Example-1.0"
    assert descriptor.license_file == "LICENSE"
    assert descriptor.attribution == "Example Authors"


def test_engine_accepts_a_resolve_only_pack_provider() -> None:
    descriptor = PackDescriptor(
        id="alias",
        version="1.2.3",
        root=STARTER.resolve(),
        compatible_engine_versions=(">=3.0.0a1,<4.0.0",),
        engine_version="3.0.0a1",
    )

    class AliasProvider:
        def resolve(self, reference: str | Path) -> PackDescriptor:
            assert reference == "registry:starter"
            return descriptor

    provider = AliasProvider()
    assert isinstance(provider, PackProvider)
    engine = Engine(EngineConfig(pack_provider=provider))

    resolved = engine.describe_pack("registry:starter")

    assert resolved.id == descriptor.id
    assert resolved.root == descriptor.root
    assert resolved.engine_version == "3.0.0a1"


def test_validate_pack_is_a_public_lint_boundary() -> None:
    result = Engine().validate_pack(PackValidationRequest(pack=STARTER))

    assert result.success
    assert result.gate.status is GateStatus.PASSED
    assert result.pack is not None
    assert result.pack.id == "starter"
    assert result.diagnostics == ()
    assert not hasattr(result, "contract_bundle")


def test_assessment_returns_plan_without_exposing_loaded_bundle() -> None:
    result = Engine().assess(AssessmentRequest(pack=STARTER, answers=_answers()))

    assert result.success
    assert result.gate.status is GateStatus.PASSED
    assert result.pack is not None
    assert result.plan is not None
    assert "STARTER-CONTROL-REVIEW" in result.plan["actions"]
    assert not hasattr(result, "contract_bundle")


def test_assessment_records_code_version_and_explicit_demo_policy() -> None:
    result = Engine().assess(
        AssessmentRequest(
            pack=STARTER,
            answers=_answers(),
            policy=ExecutionPolicy(demo_mode=True),
        )
    )

    assert result.success
    assert result.plan is not None
    assert result.plan["demo_mode"] is True
    assert result.plan["trace"]["engine_version"] == __version__
    assert result.plan["trace"]["framework_version"] == "0.1.0"
    assert result.plan["trace"]["contracts_version"] == "0.1.0"


def test_assessment_uses_pack_owned_legal_identity(tmp_path: Path) -> None:
    pack_root = tmp_path / "legal-pack"
    shutil.copytree(STARTER, pack_root)
    version_path = pack_root / "FRAMEWORK_VERSION.yml"
    version_data = yaml.safe_load(version_path.read_text(encoding="utf-8"))
    version_data["framework"]["legal_basis"] = {
        "eli": "https://example.invalid/eli/policy/42",
        "entry_into_force": "2026-02-03",
    }
    version_path.write_text(
        yaml.safe_dump(version_data, sort_keys=False),
        encoding="utf-8",
    )

    result = Engine().assess(AssessmentRequest(pack=pack_root, answers=_answers()))

    assert result.success, result.diagnostics
    assert result.plan is not None
    assert result.plan["legal_token"] == {
        "eli": ["https://example.invalid/eli/policy/42"],
        "date": "2026-02-03",
    }


def test_resolution_failure_is_a_blocked_public_result(tmp_path: Path) -> None:
    missing = tmp_path / "missing-pack"

    result = Engine().assess(AssessmentRequest(pack=missing))

    assert not result.success
    assert result.plan is None
    assert result.gate.status is GateStatus.BLOCKED
    assert result.diagnostics[0].code == "PACK_RESOLUTION_FAILED"


def test_export_uses_public_requests_and_results_only(tmp_path: Path) -> None:
    result = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=tmp_path / "export",
            policy=ExecutionPolicy(strict=False),
            product=ProductIdentity(name="consumer-shell", version="7.2.0"),
        )
    )

    assert result.success
    assert result.gate.status in {GateStatus.PASSED, GateStatus.WARNED}
    assert result.output_dir == (tmp_path / "export").resolve()
    summary_path = result.output_dir / "_metadata" / "summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["product_name"] == "consumer-shell"
    assert summary["product_version"] == "7.2.0"
    assert summary["artifact_schema_version"] == "3.0.0a1"
    assert summary["pack"]["id"] == "starter"
    assert summary["pack"]["version"] == "0.1.0"
    assert not (result.output_dir / "_metadata" / "wizard_answers.json").exists()
    evidence_manifest_path = result.output_dir / "_metadata" / "evidence_manifest.json"
    if evidence_manifest_path.exists():
        evidence_manifest = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
        assert "root_abs" not in evidence_manifest
        assert str(STARTER.resolve()) not in evidence_manifest_path.read_text(encoding="utf-8")


def test_export_includes_raw_answers_only_with_explicit_opt_in(tmp_path: Path) -> None:
    result = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=tmp_path / "raw-export",
            include_raw_answers=True,
            policy=ExecutionPolicy(strict=False),
        )
    )

    assert result.success
    raw_answers = json.loads(
        (result.output_dir / "_metadata" / "wizard_answers.json").read_text(encoding="utf-8")
    )
    assert raw_answers == _answers()


def test_default_export_removes_raw_answers_from_reused_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "reused-export"
    first = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=output_dir,
            include_raw_answers=True,
            policy=ExecutionPolicy(strict=False),
        )
    )
    persisted_answers = output_dir / "_metadata" / "wizard_answers.json"
    assert first.success
    assert persisted_answers.is_file()

    second = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=output_dir,
            include_raw_answers=False,
            policy=ExecutionPolicy(strict=False),
        )
    )

    assert second.success
    assert not persisted_answers.exists()
    sealed_manifest = output_dir / "_metadata" / "manifest.json"
    if sealed_manifest.exists():
        assert "wizard_answers.json" not in sealed_manifest.read_text(encoding="utf-8")


def test_export_builds_evidence_zip_independently_of_process_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pack = STARTER.resolve()
    answers = _answers()
    foreign_cwd = tmp_path / "foreign-cwd"
    foreign_cwd.mkdir()
    monkeypatch.chdir(foreign_cwd)

    result = Engine().export(
        ExportRequest(
            pack=pack,
            answers=answers,
            output_dir=tmp_path / "portable-export",
            policy=ExecutionPolicy(strict=False),
        )
    )

    assert result.success, result.diagnostics
    evidence_zip = result.output_dir / "exports" / "evidence.zip"
    assert evidence_zip.is_file()
    with zipfile.ZipFile(evidence_zip) as archive:
        assert "starter/control-review.md" in archive.namelist()


def test_release_export_rejects_security_bypass_before_writing(tmp_path: Path) -> None:
    output_dir = tmp_path / "release"

    result = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=output_dir,
            release=True,
            policy=ExecutionPolicy(
                strict=True,
                allow_incomplete_coverage=True,
                skip_gpg_signing=True,
            ),
        )
    )

    assert not result.success
    assert result.gate.status is GateStatus.BLOCKED
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "RELEASE_COVERAGE_BYPASS_FORBIDDEN",
        "RELEASE_UNSIGNED_EXPORT_FORBIDDEN",
    }
    assert not output_dir.exists()


@pytest.mark.parametrize("output_position", ["same", "inside", "contains"])
def test_export_rejects_pack_and_output_overlap(
    tmp_path: Path,
    output_position: str,
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(STARTER, pack)
    if output_position == "same":
        output_dir = pack
    elif output_position == "inside":
        output_dir = pack / "generated"
    else:
        output_dir = tmp_path

    result = Engine().export(
        ExportRequest(
            pack=pack,
            answers=_answers(),
            output_dir=output_dir,
            policy=ExecutionPolicy(strict=False),
        )
    )

    assert not result.success
    assert result.gate.status is GateStatus.BLOCKED
    assert "OUTPUT_PACK_PATH_OVERLAP" in {item.code for item in result.diagnostics}
    if output_position == "inside":
        assert not output_dir.exists()


def test_export_rejects_symlink_output_root_before_external_write(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    output_link = tmp_path / "linked-output"
    try:
        output_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")

    result = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=output_link,
            policy=ExecutionPolicy(strict=False),
        )
    )

    assert not result.success
    assert result.gate.status is GateStatus.BLOCKED
    assert "UNSAFE_OUTPUT_TREE" in {item.code for item in result.diagnostics}
    assert list(outside.iterdir()) == []


def test_export_rejects_symlink_output_parent_before_external_write(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")

    result = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=linked_parent / "new-output",
            policy=ExecutionPolicy(strict=False),
        )
    )

    assert not result.success
    assert "UNSAFE_OUTPUT_TREE" in {item.code for item in result.diagnostics}
    assert list(outside.iterdir()) == []


def test_release_export_rejects_non_strict_policy_before_writing(tmp_path: Path) -> None:
    output_dir = tmp_path / "release-tolerant"

    result = Engine().export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=output_dir,
            release=True,
            policy=ExecutionPolicy(strict=False),
        )
    )

    assert not result.success
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "RELEASE_REQUIRES_STRICT_POLICY"
    }
    assert not output_dir.exists()


def test_seal_missing_directory_does_not_create_it(tmp_path: Path) -> None:
    missing = tmp_path / "missing-export"

    result = Engine().seal(SealRequest(export_dir=missing))

    assert not result.success
    assert result.gate.status is GateStatus.BLOCKED
    assert result.diagnostics[0].code == "EXPORT_DIRECTORY_NOT_FOUND"
    assert not missing.exists()


def test_non_strict_seal_returns_warnings_through_public_gate(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "artifact.md").write_text("# Example\n", encoding="utf-8")

    result = Engine().seal(SealRequest(export_dir=export_dir, strict=False))

    assert result.success
    assert result.files_validated == 1
    assert result.gate.status is GateStatus.WARNED
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"SEAL_WARNING"}
