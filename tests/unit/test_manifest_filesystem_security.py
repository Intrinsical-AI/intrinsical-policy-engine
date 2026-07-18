# SPDX-License-Identifier: MPL-2.0
"""Filesystem and signing regressions for export manifests and sealing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import intrinsical_policy_engine.adapters.export.filesystem.strategies.manifest as manifest_module
import intrinsical_policy_engine.app.use_cases.seal as seal_module
from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
    FilesystemExporter,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.base import ExportContext
from intrinsical_policy_engine.adapters.export.filesystem.strategies.manifest import (
    ManifestStrategy,
)
from intrinsical_policy_engine.app.export.orchestrator import ExportConfig, ExportOrchestrator
from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.common.io_safety import UnsafeTreePathError
from intrinsical_policy_engine.domain.services.seal_service import (
    SealReport,
)
from intrinsical_policy_engine.domain.services.seal_service import (
    SealResult as DomainSealResult,
)
from intrinsical_policy_engine.domain.types import Plan


class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any] | None]] = []

    def info(self, event: str, extra: dict[str, Any] | None = None) -> None:
        pass

    def warning(self, event: str, extra: dict[str, Any] | None = None) -> None:
        self.warnings.append((event, extra))

    def error(self, event: str, extra: dict[str, Any] | None = None) -> None:
        pass

    def debug(self, event: str, extra: dict[str, Any] | None = None) -> None:
        pass


class _BrokenSigner:
    def __init__(
        self,
        signature_path: Path | None,
        *,
        available: bool = True,
        has_key: bool = True,
    ) -> None:
        self._signature_path = signature_path
        self._available = available
        self._has_key = has_key

    def is_available(self) -> bool:
        return self._available

    def has_secret_key(self) -> bool:
        return self._has_key

    def sign_file(self, path: Path) -> Path | None:
        assert path.is_file()
        return self._signature_path


def _manifest_fixture(
    tmp_path: Path,
    *,
    strict: bool,
    release: bool = False,
) -> tuple[FilesystemExporter, ExportContext, _RecordingLogger]:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "00_START_HERE.md").write_text("# Start\n", encoding="utf-8")

    templates_dir = tmp_path / "templates"
    checksums_template = templates_dir / "artifacts" / "exports" / "CHECKSUMS.sha256.j2"
    checksums_template.parent.mkdir(parents=True)
    checksums_template.write_text("checksums\n", encoding="utf-8")

    logger = _RecordingLogger()
    exporter = FilesystemExporter()
    exporter.setup(logger, {})
    context = ExportContext(
        plan=cast(Plan, {}),
        ctx={},
        out_dir=out_dir,
        templates_dir=templates_dir,
        assembler=ArtifactAssembler(templates_dir, strict=False),
        config={"release": release},
        strict=strict,
    )
    return exporter, context, logger


def test_orchestrator_rejects_reused_output_symlink_before_external_mutation(
    tmp_path: Path,
) -> None:
    contracts_dir = tmp_path / "pack"
    contracts_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "wizard_answers.json"
    sentinel.write_text('{"secret": "unchanged"}\n', encoding="utf-8")

    try:
        (out_dir / "_metadata").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    orchestrator = ExportOrchestrator(
        ExportConfig(
            plan=cast(Plan, {}),
            contracts_dir=contracts_dir,
            outdir=out_dir,
            save_plan=False,
            templates=None,
            targets=["filesystem"],
            config_path=None,
            strict=False,
        )
    )

    with pytest.raises(UnsafeTreePathError, match="Symbolic links are forbidden"):
        orchestrator.run()

    assert sentinel.read_text(encoding="utf-8") == '{"secret": "unchanged"}\n'
    assert not (outside / "summary.json").exists()


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_manifest_rejects_symlink_in_reused_output_before_reading(
    tmp_path: Path,
    link_kind: str,
) -> None:
    exporter, context, _logger = _manifest_fixture(tmp_path, strict=False)
    if link_kind == "file":
        target = tmp_path / "outside.md"
        target.write_text("must not be read", encoding="utf-8")
        link = context.out_dir / "LEGAL_NOTICE.md"
    else:
        target = tmp_path / "outside-tools"
        target.mkdir()
        (target / "secret.txt").write_text("must not be read", encoding="utf-8")
        link = context.out_dir / "tools"

    try:
        link.symlink_to(target, target_is_directory=link_kind == "directory")
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(UnsafeTreePathError, match="Symbolic links are forbidden"):
        ManifestStrategy().execute(exporter, context)


@pytest.mark.parametrize(("strict", "release"), [(True, False), (False, True)])
@pytest.mark.parametrize("signature_result", ["none", "missing"])
def test_manifest_blocks_when_signer_does_not_create_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict: bool,
    release: bool,
    signature_result: str,
) -> None:
    exporter, context, _logger = _manifest_fixture(
        tmp_path,
        strict=strict,
        release=release,
    )
    signature_path = None if signature_result == "none" else tmp_path / "missing.asc"
    monkeypatch.setattr(
        manifest_module,
        "GpgSigner",
        lambda: _BrokenSigner(signature_path),
    )

    with pytest.raises(RuntimeError, match="signature file was not created"):
        ManifestStrategy()._generate_checksums(exporter, context)


def test_manifest_warns_without_signature_in_tolerant_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter, context, logger = _manifest_fixture(tmp_path, strict=False)
    monkeypatch.setattr(
        manifest_module,
        "GpgSigner",
        lambda: _BrokenSigner(None),
    )

    checksums_path, signature_path = ManifestStrategy()._generate_checksums(exporter, context)

    assert checksums_path is not None and checksums_path.is_file()
    assert signature_path is None
    assert [event for event, _extra in logger.warnings] == ["export.gpg.signing_failed"]


@pytest.mark.parametrize("strict", [True, False])
def test_seal_handles_signer_that_does_not_create_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict: bool,
) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "artifact.txt").write_text("content", encoding="utf-8")

    def _successful_seal(_snapshot: object, *, strict: bool) -> DomainSealResult:
        assert isinstance(strict, bool)
        return DomainSealResult(
            success=True,
            manifest_sealed={"status": "sealed", "checksums": {}},
            seal_report=SealReport(
                status="success",
                timestamp="2026-01-01T00:00:00+00:00",
                files_validated=1,
            ),
        )

    monkeypatch.setattr(seal_module, "seal_export", _successful_seal)
    monkeypatch.setattr(seal_module, "GpgSigner", lambda: _BrokenSigner(None))

    if strict:
        with pytest.raises(RuntimeError, match="manifest signature was not created"):
            seal_module.seal_and_package(export_dir, strict=True)
        report = json.loads(
            (export_dir / "_metadata" / "seal_report.json").read_text(encoding="utf-8")
        )
        assert report["status"] == "failed"
        assert report["errors"] == ["GPG signing failed - manifest signature was not created"]
    else:
        result = seal_module.seal_and_package(export_dir, strict=False)
        assert result.success
        assert result.warnings == ["GPG signing failed - manifest signature was not created"]
        report = json.loads(
            (export_dir / "_metadata" / "seal_report.json").read_text(encoding="utf-8")
        )
        assert report["status"] == "warnings"
        assert report["warnings"] == result.warnings


@pytest.mark.parametrize(("available", "has_key"), [(False, True), (True, False)])
@pytest.mark.parametrize("strict", [True, False])
def test_seal_requires_available_gpg_key_when_signing_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict: bool,
    available: bool,
    has_key: bool,
) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "artifact.txt").write_text("content", encoding="utf-8")

    def _successful_seal(_snapshot: object, *, strict: bool) -> DomainSealResult:
        assert isinstance(strict, bool)
        return DomainSealResult(
            success=True,
            manifest_sealed={"status": "sealed", "checksums": {}},
            seal_report=SealReport(
                status="success",
                timestamp="2026-01-01T00:00:00+00:00",
                files_validated=1,
            ),
        )

    monkeypatch.setattr(seal_module, "seal_export", _successful_seal)
    monkeypatch.setattr(
        seal_module,
        "GpgSigner",
        lambda: _BrokenSigner(None, available=available, has_key=has_key),
    )

    expected = "GPG signing required but GPG is unavailable or no secret key exists"
    if strict:
        with pytest.raises(RuntimeError, match="GPG signing required"):
            seal_module.seal_and_package(export_dir, strict=True)
        report = json.loads(
            (export_dir / "_metadata" / "seal_report.json").read_text(encoding="utf-8")
        )
        assert report["status"] == "failed"
        assert report["errors"] == [expected]
    else:
        result = seal_module.seal_and_package(export_dir, strict=False)
        assert result.success
        assert result.warnings == [expected]
        report = json.loads(
            (export_dir / "_metadata" / "seal_report.json").read_text(encoding="utf-8")
        )
        assert report["status"] == "warnings"
        assert report["warnings"] == [expected]
