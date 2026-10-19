# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Validate every input, stage a closed snapshot, then publish it safely."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import CompiledPack
from intrinsical_policy_engine.adapters.export.base.exporters.base_api_exporter import (
    BaseApiExporter,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import (
    ApiExportConfig,
    ExportSettings,
    FilesystemExportConfig,
)
from intrinsical_policy_engine.adapters.export.base.models.registry import (
    OfflineExporter,
    get_exporter,
)
from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
    FilesystemExporter,
)
from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.adapters.quality.bundle_evidence_validator import (
    BundleEvidenceValidator,
)
from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.app.evidence import EvidenceSnapshot, check_ci
from intrinsical_policy_engine.app.export.artifacts import write_assessment, write_json
from intrinsical_policy_engine.app.export.inventory import plan_outputs
from intrinsical_policy_engine.app.export.projection import project_plan
from intrinsical_policy_engine.common.output_safety import fresh_output
from intrinsical_policy_engine.common.package_integrity import write_checksums
from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.services.evidence_requirements import (
    missing_evidence_requirements,
)
from intrinsical_policy_engine.domain.snapshot_contract import canonical_path


@dataclass(frozen=True)
class ExportResult:
    outdir: Path
    targets: tuple[str, ...]
    files: int


def _validated_evidence(evidence: EvidenceSnapshot, plan: dict[str, Any]) -> EvidenceSnapshot:
    quality_engine = QualityEngine()
    for name, data in evidence.files.items():
        canonical_path(name)
        if not isinstance(data, bytes):
            raise ValueError(f"Evidence snapshot must contain bytes: {name}")
    return EvidenceSnapshot(
        files=evidence.files,
        quality={
            name: quality_engine.classify_bytes(data, Path(name).suffix)
            for name, data in evidence.files.items()
        },
        problems=tuple(
            dict.fromkeys(
                [*evidence.problems, *missing_evidence_requirements(plan, evidence.files)]
            )
        ),
        supplied=evidence.supplied,
    )


def _configured_exporters(
    targets: list[str],
    settings: ExportSettings,
    pack: CompiledPack,
    profiles: dict[str, BundleProfile],
    logger: StructuredLogger | None,
) -> dict[str, OfflineExporter]:
    exporters: dict[str, OfflineExporter] = {}
    for target in targets:
        config = settings.for_target(target)
        exporter = get_exporter(target)
        if isinstance(exporter, FilesystemExporter) and isinstance(config, FilesystemExportConfig):
            exporter.setup(logger=logger, config={"compiled_pack": pack, "profiles": profiles})
        elif isinstance(exporter, BaseApiExporter) and isinstance(config, ApiExportConfig):
            exporter.setup(logger=logger, config=config)
        else:
            raise ValueError(f"Configuration and exporter do not agree: {target}")
        exporters[target] = exporter
    return exporters


def export_plan(
    *,
    plan: dict[str, Any],
    pack: CompiledPack,
    destination: Path,
    targets: list[str],
    settings: ExportSettings,
    evidence: EvidenceSnapshot,
    include_raw_input: bool = False,
    check_ci_enabled: bool = False,
    strict: bool = False,
    logger: StructuredLogger | None = None,
) -> ExportResult:
    evidence = _validated_evidence(evidence, plan)
    if strict and pack.problems:
        raise ValueError("\n".join(problem.msg for problem in pack.problems))
    if not targets or len(targets) != len(set(targets)):
        raise ValueError("Export targets must be a nonempty list without duplicates")
    profiles = dict(pack.bundle_profiles)
    if settings.profiles is not None:
        if len(settings.profiles) != len(set(settings.profiles)):
            raise ValueError("Profile selection cannot contain duplicates")
        unknown = set(settings.profiles) - set(profiles)
        if unknown:
            raise ValueError(f"Unknown profiles: {', '.join(sorted(unknown))}")
        profiles = {key: profiles[key] for key in settings.profiles}
    coverage = BundleEvidenceValidator().validate_coverage(profiles, plan)
    if check_ci_enabled:
        problems = check_ci(plan, evidence)
        if "filesystem" in targets and coverage.has_critical_gaps():
            problems.append(
                f"CI-004: profile coverage missing actions={sorted(coverage.missing_actions)} "
                f"evidences={sorted(coverage.missing_evidences)}"
            )
        if problems:
            raise ValueError("\n".join(problems))
    projected = project_plan(plan, include_raw_input=include_raw_input)
    projected["export_context"] = {
        "uid_namespace": settings.uid_namespace or pack.bundle.runtime.semantics.framework_id
    }
    inventory = plan_outputs(pack, profiles, projected, targets, evidence.files)
    exporters = _configured_exporters(targets, settings, pack, profiles, logger)
    with fresh_output(destination) as staging:
        write_assessment(projected, staging)
        quality = {
            "quality_by_file": evidence.quality,
            "problems": list(evidence.problems),
        }
        write_json(staging / "_metadata" / "evidence_quality.json", quality)
        write_json(
            staging / "_metadata" / "evidence_manifest.json",
            {"included": sorted(evidence.files), "missing": list(evidence.problems)},
        )
        for name, data in evidence.files.items():
            path = staging / "evidence" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
        if evidence.files:
            with zipfile.ZipFile(
                staging / "evidence.zip", mode="x", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for name, data in sorted(evidence.files.items()):
                    archive.writestr(name, data)
        # Order makes all local evidence available to every offline payload writer.
        ordered = [target for target in targets if target == "filesystem"] + [
            target for target in targets if target != "filesystem"
        ]
        for target in ordered:
            exporter = exporters[target]
            exporter.export(projected, str(pack.layout.templates_dir), str(staging))
        actual = {
            path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file()
        }
        if actual != set(inventory.files):
            raise ValueError("Export producers did not match the planned output inventory")
        write_checksums(staging)
        count = sum(path.is_file() for path in staging.rglob("*"))
    return ExportResult(destination, tuple(targets), count)
