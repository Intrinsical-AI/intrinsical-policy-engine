import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.adapters.export.filesystem.strategies.manifest import ManifestStrategy
from src.app.config.artifact_names import LOCKFILE_NAME
from src.app.use_cases import seal
from src.app.use_cases.ops_fs import clean_out_dir
from src.common.io_safety import acquire_lock
from src.domain.services.seal_service import SealReport, SealResult


def test_cleanup_keeps_held_lock_inode(tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    lock = out / LOCKFILE_NAME
    (out / "old.txt").write_text("old")
    with acquire_lock(lock):
        inode = lock.stat().st_ino
        clean_out_dir(out)
        assert lock.stat().st_ino == inode
        assert not (out / "old.txt").exists()
        with pytest.raises(BlockingIOError), acquire_lock(lock, timeout=0):
            pass


@pytest.mark.parametrize("available", [True, False])
def test_strict_signature_failure_prevents_zip(monkeypatch, tmp_path, available):
    result = SealResult(True, {}, SealReport("success", "now", 0))
    monkeypatch.setattr(seal, "collect_seal_input", lambda *a, **k: None)
    monkeypatch.setattr(seal, "seal_export", lambda *a, **k: result)
    monkeypatch.setattr(
        seal,
        "GpgSigner",
        lambda: SimpleNamespace(
            is_available=lambda: available, has_secret_key=lambda: True, sign_file=lambda p: None
        ),
    )
    out = tmp_path / "out.zip"
    assert not seal.seal_and_package(tmp_path, output_zip=out).success
    assert not out.exists()
    manifest = json.loads((tmp_path / "_metadata" / "manifest_sealed.json").read_text())
    assert manifest["status"] == "seal_failed"
    report = json.loads((tmp_path / "_metadata" / "seal_report.json").read_text())
    assert report["status"] == "failed"
    assert report["errors"]


def test_strict_export_rejects_unreadable_file(monkeypatch, tmp_path):
    template = tmp_path / "templates" / "artifacts/exports/CHECKSUMS.sha256.j2"
    template.parent.mkdir(parents=True)
    template.write_text("test")
    out = tmp_path / "out"
    out.mkdir()
    bad = out / "manifest.md"
    bad.write_text("test")
    original = Path.read_bytes

    def read(path):
        if path == bad:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    ctx = SimpleNamespace(out_dir=out, templates_dir=tmp_path / "templates", strict=True)
    with pytest.raises(RuntimeError, match=r"cannot hash manifest\.md"):
        ManifestStrategy()._generate_checksums(Mock(), ctx)
    assert not (out / "CHECKSUMS.sha256").exists()


def test_strict_checksum_signature_failure_is_rejected(monkeypatch, tmp_path):
    from src.adapters.export.filesystem.strategies import manifest

    template = tmp_path / "templates/artifacts/exports/CHECKSUMS.sha256.j2"
    template.parent.mkdir(parents=True)
    template.write_text("test")
    out = tmp_path / "out"
    out.mkdir()
    (out / "manifest.md").write_text("test")
    strategy = ManifestStrategy()
    monkeypatch.setattr(strategy, "_validate_ssot_constraints", lambda *a: None)
    monkeypatch.delenv("IPE_SKIP_GPG_SIGNING", raising=False)
    monkeypatch.delenv("LEXOPS_SKIP_GPG_SIGNING", raising=False)
    monkeypatch.setattr(
        manifest,
        "GpgSigner",
        lambda: SimpleNamespace(
            is_available=lambda: True, has_secret_key=lambda: True, sign_file=lambda p: None
        ),
    )
    ctx = SimpleNamespace(
        out_dir=out,
        templates_dir=tmp_path / "templates",
        strict=True,
        ctx={},
        assembler=SimpleNamespace(assemble=lambda *a: "checksums"),
    )
    exporter = SimpleNamespace(write_text=lambda p, text: p.write_text(text))
    with pytest.raises(RuntimeError, match="GPG signing failed"):
        strategy._generate_checksums(exporter, ctx)
