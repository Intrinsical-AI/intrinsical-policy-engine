# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Round-trip contracts between the public export and seal boundaries."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import cast

from intrinsical_policy_engine.api import (
    Engine,
    ExecutionPolicy,
    ExportRequest,
    GateStatus,
    SealRequest,
)

STARTER = Path("frameworks/starter")
STARTER_ANSWERS = Path("demos/starter/basic/answers.json")


def _answers() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(STARTER_ANSWERS.read_text(encoding="utf-8")),
    )


def test_strict_seal_accepts_output_created_by_public_export(tmp_path: Path) -> None:
    engine = Engine()
    exported = engine.export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=tmp_path / "export",
            save_plan=True,
            policy=ExecutionPolicy(
                strict=False,
                strict_templates=True,
                skip_gpg_signing=True,
            ),
        )
    )

    assert exported.success, exported.diagnostics

    output_zip = tmp_path / "sealed.zip"
    sealed = engine.seal(
        SealRequest(export_dir=exported.output_dir, output_zip=output_zip, strict=True)
    )

    assert sealed.success, sealed.diagnostics
    assert sealed.gate.status in {GateStatus.PASSED, GateStatus.WARNED}
    assert (exported.output_dir / "_metadata" / "CHECKSUMS.sha256").is_file()
    lock_path = exported.output_dir / "plans" / "index.json.lock"
    assert lock_path.is_file()
    sealed_manifest = json.loads(
        (exported.output_dir / "_metadata" / "manifest_sealed.json").read_text(encoding="utf-8")
    )
    assert "plans/index.json.lock" not in sealed_manifest["checksums"]
    with zipfile.ZipFile(output_zip) as archive:
        assert "plans/index.json.lock" not in archive.namelist()


def test_reseal_reads_metadata_checksums_and_blocks_summary_mutation(tmp_path: Path) -> None:
    engine = Engine()
    exported = engine.export(
        ExportRequest(
            pack=STARTER,
            answers=_answers(),
            output_dir=tmp_path / "export",
            policy=ExecutionPolicy(strict=False, skip_gpg_signing=True),
        )
    )
    first_seal = engine.seal(SealRequest(export_dir=exported.output_dir, strict=True))
    assert first_seal.success, first_seal.diagnostics

    summary = exported.output_dir / "_metadata" / "summary.json"
    summary.write_text(summary.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    second_seal = engine.seal(SealRequest(export_dir=exported.output_dir, strict=True))

    assert not second_seal.success
    assert second_seal.gate.status is GateStatus.BLOCKED
    assert any("Immutable file modified" in item.message for item in second_seal.diagnostics)


def test_seal_rejects_file_symlink_escape_before_reading_or_packaging(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    external = tmp_path / "outside-secret.txt"
    external.write_text("must not enter the seal", encoding="utf-8")
    link = export_dir / "linked-secret.txt"
    try:
        link.symlink_to(external)
    except OSError as exc:
        import pytest

        pytest.skip(f"File symlinks are unavailable: {exc}")

    output_zip = tmp_path / "sealed.zip"
    result = Engine().seal(SealRequest(export_dir=export_dir, output_zip=output_zip, strict=False))

    assert not result.success
    assert result.gate.status is GateStatus.BLOCKED
    assert result.diagnostics[0].code == "SEAL_FAILED"
    assert "Symbolic links are forbidden" in result.diagnostics[0].message
    assert not output_zip.exists()
    assert not (export_dir / "_metadata").exists()


def test_seal_rejects_zip_destination_inside_export(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "artifact.txt").write_text("sealed content", encoding="utf-8")
    output_zip = export_dir / "sealed.zip"

    result = Engine().seal(SealRequest(export_dir=export_dir, output_zip=output_zip, strict=False))

    assert not result.success
    assert result.gate.status is GateStatus.BLOCKED
    assert result.diagnostics[0].code == "SEAL_FAILED"
    assert "outside the export directory" in result.diagnostics[0].message
    assert not output_zip.exists()
