import hashlib
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from intrinsical_policy_engine.adapters.security.gpg_signer import GpgSigner, SigningError
from intrinsical_policy_engine.app.export.artifacts import write_assessment
from intrinsical_policy_engine.app.use_cases import seal
from intrinsical_policy_engine.common.io_safety import acquire_lock
from intrinsical_policy_engine.common.output_safety import output_lock_path
from intrinsical_policy_engine.common.package_integrity import load_checksums, write_checksums
from intrinsical_policy_engine.domain.snapshot_contract import CHECKSUMS_NAME, SIGNATURE_NAME


def make_package(root: Path) -> Path:
    write_assessment({"classification": "limited", "trace": {"hash": "abc", "version": "3"}}, root)
    (root / "LEGAL_NOTICE.md").write_text("## Purpose\n" + "Actual assessed obligations. " * 70)
    (root / "exports").mkdir()
    (root / "exports" / "evidence.zip").write_bytes(b"real evidence bytes")
    write_checksums(root)
    return root


def file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class TestSigner:
    __test__ = False

    def sign_file(self, path: Path) -> Path:
        signature = path.with_name(path.name + ".asc")
        signature.write_bytes(b"valid test signature")
        return signature

    def verify_file(self, path: Path, signature: Path) -> str:
        assert path.read_bytes()
        if signature.read_bytes() != b"valid test signature":
            raise SigningError("invalid test signature")
        return "A" * 40


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("change", ["modify", "remove", "add"])
def test_all_payload_drift_fails_even_tolerant(tmp_path, strict, change):
    root = make_package(tmp_path / "export")
    if change == "modify":
        (root / "exports/evidence.zip").write_bytes(b"changed evidence")
    elif change == "remove":
        (root / "LEGAL_NOTICE.md").unlink()
    else:
        (root / "exports/new.bin").write_bytes(b"new")
    result = seal.seal_and_package(root, sign=False, strict=strict)
    assert not result.success
    assert any("File " in error for error in result.errors)
    assert not (root / SIGNATURE_NAME).exists()


def test_missing_baseline_cannot_be_rebased(tmp_path):
    root = make_package(tmp_path / "export")
    (root / CHECKSUMS_NAME).unlink()
    before = file_bytes(root)
    assert not seal.seal_and_package(root, sign=False, strict=False).success
    assert file_bytes(root) == before


def test_unsigned_seal_has_no_writes_in_package(tmp_path):
    root = make_package(tmp_path / "export")
    before = file_bytes(root)
    assert seal.seal_and_package(root, sign=False).success
    assert file_bytes(root) == before
    assert output_lock_path(root).is_file()
    assert output_lock_path(root).parent == root.parent


@pytest.mark.parametrize("name,content", [("bad.MD", "TODO"), ("bad.JSON", "{")])
def test_quality_does_not_ignore_uppercase_suffixes(tmp_path, name, content):
    root = make_package(tmp_path / "export")
    (root / CHECKSUMS_NAME).unlink()
    (root / "evidence").mkdir()
    (root / "evidence" / name).write_text(content)
    write_checksums(root)
    strict = seal.seal_and_package(root, sign=False)
    assert not strict.success
    assert any(name in error for error in strict.errors)
    tolerant = seal.seal_and_package(root, sign=False, strict=False)
    assert tolerant.success and tolerant.warnings


def test_zip_is_exact_closed_inventory(tmp_path):
    root = make_package(tmp_path / "export")
    baseline, expected = load_checksums(root)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, sign=False)
    assert result.success, result.errors
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == set(expected) | {CHECKSUMS_NAME}
        assert archive.read(CHECKSUMS_NAME) == baseline
        for name, digest in expected.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest


def test_zip_inside_source_is_rejected(tmp_path):
    root = make_package(tmp_path / "export")
    before = file_bytes(root)
    result = seal.seal_and_package(root, output_zip=root / "recursive.zip", sign=False)
    assert not result.success and "outside" in result.errors[0]
    assert file_bytes(root) == before


def test_zip_destination_is_never_overwritten(tmp_path):
    root = make_package(tmp_path / "export")
    target = tmp_path / "delivery.zip"
    target.write_bytes(b"another owner's output")
    assert not seal.seal_and_package(root, output_zip=target, sign=False).success
    assert target.read_bytes() == b"another owner's output"


@pytest.mark.parametrize("strict", [False, True])
def test_signature_failure_leaves_no_signature_or_zip(monkeypatch, tmp_path, strict):
    class FailingSigner(TestSigner):
        def sign_file(self, path):
            super().sign_file(path)
            raise SigningError("injected signature failure")

    monkeypatch.setattr(seal, "GpgSigner", FailingSigner)
    root = make_package(tmp_path / "export")
    before = file_bytes(root)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, strict=strict)
    assert not result.success
    assert file_bytes(root) == before
    assert not target.exists()
    assert not list(tmp_path.glob(".ipe-*"))


def test_existing_signature_is_verified_and_reused_even_unsigned(monkeypatch, tmp_path):
    class ReuseSigner(TestSigner):
        def sign_file(self, path):
            pytest.fail("existing signature must not be replaced")

    monkeypatch.setattr(seal, "GpgSigner", ReuseSigner)
    root = make_package(tmp_path / "export")
    (root / SIGNATURE_NAME).write_bytes(b"valid test signature")
    before = file_bytes(root)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, sign=False)
    assert result.success, result.errors
    assert result.signature_fingerprint == "A" * 40
    assert file_bytes(root) == before
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == set(before)
        assert archive.read(SIGNATURE_NAME) == before[SIGNATURE_NAME]


def test_invalid_existing_signature_cannot_downgrade(monkeypatch, tmp_path):
    monkeypatch.setattr(seal, "GpgSigner", TestSigner)
    root = make_package(tmp_path / "export")
    (root / SIGNATURE_NAME).write_bytes(b"invalid signature")
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, sign=False)
    assert not result.success
    assert not target.exists()
    assert (root / SIGNATURE_NAME).read_bytes() == b"invalid signature"


@pytest.mark.parametrize("changed", ["payload", "baseline"])
def test_source_mutation_during_signing_prevents_publication(monkeypatch, tmp_path, changed):
    root = make_package(tmp_path / "export")

    class MutatingSigner(TestSigner):
        def sign_file(self, path):
            signature = super().sign_file(path)
            target = root / ("exports/evidence.zip" if changed == "payload" else CHECKSUMS_NAME)
            target.write_bytes(b"changed concurrently")
            return signature

    monkeypatch.setattr(seal, "GpgSigner", MutatingSigner)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target)
    assert not result.success
    assert not target.exists()
    assert not (root / SIGNATURE_NAME).exists()


def test_hashes_are_checked_on_bytes_copied_to_zip(monkeypatch, tmp_path):
    root = make_package(tmp_path / "export")
    original = zipfile.ZipFile.open

    def changing_open(archive, name, mode="r", *args, **kwargs):
        if name == "exports/evidence.zip" and mode == "w":
            (root / name).write_bytes(b"substituted during copy")
        return original(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", changing_open)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, sign=False)
    assert not result.success
    assert any("during packaging" in error for error in result.errors)
    assert not target.exists()


def test_zip_publication_failure_can_leave_complete_verified_signature(monkeypatch, tmp_path):
    monkeypatch.setattr(seal, "GpgSigner", TestSigner)
    root = make_package(tmp_path / "export")
    target = tmp_path / "delivery.zip"
    original = seal.publish_file_no_replace

    def publish(prepared, destination):
        if destination == target:
            raise FileExistsError("competing zip writer")
        original(prepared, destination)

    monkeypatch.setattr(seal, "publish_file_no_replace", publish)
    result = seal.seal_and_package(root, output_zip=target)
    assert not result.success
    assert result.signature_path == str(root / SIGNATURE_NAME)
    assert (root / SIGNATURE_NAME).read_bytes() == b"valid test signature"
    assert not target.exists()


def test_held_sidecar_lock_prevents_sealing(tmp_path):
    root = make_package(tmp_path / "export")
    with acquire_lock(output_lock_path(root)):
        result = seal.seal_and_package(root, sign=False)
    assert not result.success
    assert not (root / SIGNATURE_NAME).exists()


@pytest.fixture
def isolated_gpg(monkeypatch):
    binary = shutil.which("gpg")
    if binary is None:
        pytest.skip("GPG executable is absent")
    key = "audit@example.invalid"
    # GPG creates several Unix sockets; long pytest paths exceed their limit.
    with tempfile.TemporaryDirectory(prefix="ipe-gpg-", dir="/tmp") as directory:
        home = Path(directory)
        monkeypatch.setenv("GNUPGHOME", str(home))
        try:
            generated = subprocess.run(
                [
                    binary,
                    "--batch",
                    "--pinentry-mode",
                    "loopback",
                    "--passphrase",
                    "",
                    "--quick-generate-key",
                    f"Isolated audit <{key}>",
                    "ed25519",
                    "sign",
                    "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert generated.returncode == 0, generated.stderr
            yield key
        finally:
            gpgconf = shutil.which("gpgconf")
            if gpgconf:
                subprocess.run(
                    [gpgconf, "--homedir", str(home), "--kill", "gpg-agent"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )


def test_real_gpg_signing_and_reuse_isolated_from_user_keys(monkeypatch, tmp_path, isolated_gpg):
    root = make_package(tmp_path / "export")
    monkeypatch.setattr(seal, "GpgSigner", lambda: GpgSigner(key_id=isolated_gpg))
    first = seal.seal_and_package(root, output_zip=tmp_path / "signed.zip")
    assert first.success, first.errors
    assert first.signature_fingerprint
    before = file_bytes(root)
    reused = seal.seal_and_package(root, output_zip=tmp_path / "reused.zip", sign=False)
    assert reused.success, reused.errors
    assert reused.signature_fingerprint == first.signature_fingerprint
    assert file_bytes(root) == before
    (root / "exports/evidence.zip").write_bytes(b"replaced payload")
    baseline, expected = load_checksums(root)
    expected["exports/evidence.zip"] = hashlib.sha256(b"replaced payload").hexdigest()
    (root / CHECKSUMS_NAME).write_bytes(
        b"".join(f"{digest}  {name}\n".encode() for name, digest in expected.items())
    )
    assert not seal.seal_and_package(root, sign=False).success
    (root / "exports/evidence.zip").write_bytes(before["exports/evidence.zip"])
    (root / CHECKSUMS_NAME).write_bytes(baseline)
    signature = root / SIGNATURE_NAME
    signature.write_bytes(signature.read_bytes().replace(b"-----BEGIN", b"-----BROKEN", 1))
    assert not seal.seal_and_package(root, sign=False).success


def test_gpg_timeout_is_a_signing_failure_without_partial_signature(monkeypatch, tmp_path):
    path = tmp_path / CHECKSUMS_NAME
    path.write_bytes(b"exact baseline")
    signer = GpgSigner(timeout=0.01)
    monkeypatch.setattr(signer, "_gpg_path", "/isolated/gpg")

    def timeout(arguments, **kwargs):
        assert "--no-auto-key-retrieve" in arguments
        assert kwargs["timeout"] == 0.01
        path.with_name(path.name + ".asc").write_bytes(b"partial")
        raise subprocess.TimeoutExpired(arguments, 0.01)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(SigningError, match="did not complete"):
        signer.sign_file(path)
    assert not path.with_name(path.name + ".asc").exists()


def test_missing_gpg_is_fatal_when_signature_is_demanded(monkeypatch, tmp_path):
    root = make_package(tmp_path / "export")
    signer = GpgSigner()
    monkeypatch.setattr(signer, "_gpg_path", None)
    monkeypatch.setattr(seal, "GpgSigner", lambda: signer)
    target = tmp_path / "delivery.zip"
    result = seal.seal_and_package(root, output_zip=target, strict=False)
    assert not result.success
    assert any("GPG is unavailable" in error for error in result.errors)
    assert not target.exists()
    assert not (root / SIGNATURE_NAME).exists()


def test_generated_metadata_and_api_payloads_are_not_operational_evidence(tmp_path):
    root = make_package(tmp_path / "export")
    (root / CHECKSUMS_NAME).unlink()
    (root / "_metadata/actions.json").write_text('[{"id": "action.one"}]')
    (root / "_metadata/evidence_manifest.json").write_text('{"included": [], "missing": []}')
    (root / "exports/requests.json").write_text('[{"method": "POST", "body": {}}]')
    (root / "README.md").write_text("Short authored informational note")
    write_checksums(root)
    result = seal.seal_and_package(root, sign=False)
    assert result.success, result.errors


@pytest.mark.parametrize(
    "metadata,content",
    [
        ("summary.json", "[]"),
        ("trace.json", "null"),
        ("actions.json", "{}"),
        ("evidence_manifest.json", "{}"),
        ("evidence_quality.json", "{}"),
        ("trace.json", '{"a": 1, "a": 2}'),
        ("summary.json", "{"),
    ],
)
def test_metadata_structure_is_required_even_when_tolerant(tmp_path, metadata, content):
    root = make_package(tmp_path / "export")
    (root / CHECKSUMS_NAME).unlink()
    (root / "_metadata" / metadata).write_text(content)
    write_checksums(root)
    assert not seal.seal_and_package(root, sign=False, strict=False).success
