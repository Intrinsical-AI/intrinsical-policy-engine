import hashlib
import json
from pathlib import Path

import pytest

from intrinsical_policy_engine.common.output_safety import (
    fresh_output,
    output_lock_path,
    publish_file_no_replace,
)
from intrinsical_policy_engine.common.package_integrity import (
    load_checksums,
    parse_checksums,
    write_checksums,
)
from intrinsical_policy_engine.domain.contract_models import FrameworkPresentation
from intrinsical_policy_engine.domain.snapshot_contract import CHECKSUMS_NAME, PackageIntegrityError


def metadata(root: Path) -> None:
    (root / "_metadata").mkdir(parents=True)
    for name in ("summary.json", "trace.json"):
        (root / "_metadata" / name).write_text(json.dumps({"a": 1, "b": 2, "c": 3}))


@pytest.mark.parametrize("empty", [False, True])
def test_existing_output_is_preserved(tmp_path, empty):
    target = tmp_path / "output"
    target.mkdir()
    if not empty:
        (target / "owned.txt").write_text("preserve")
    with pytest.raises(FileExistsError), fresh_output(target):
        pytest.fail("existing output must never be yielded")
    assert target.is_dir()
    if not empty:
        assert (target / "owned.txt").read_text() == "preserve"


def test_failed_preparation_does_not_publish(tmp_path):
    target = tmp_path / "output"
    with pytest.raises(ValueError), fresh_output(target) as staging:
        (staging / "incomplete.txt").write_text("incomplete")
        raise ValueError("writer failed")
    assert not target.exists()
    assert not staging.exists()


def test_successful_preparation_publishes_complete_directory(tmp_path):
    target = tmp_path / "output"
    with fresh_output(target) as staging:
        (staging / "complete.bin").write_bytes(b"complete")
        assert not target.exists()
    assert (target / "complete.bin").read_bytes() == b"complete"
    assert not staging.exists()
    assert output_lock_path(target).is_file()
    assert output_lock_path(target).parent == target.parent


def test_competing_output_created_after_preflight_is_preserved(tmp_path):
    target = tmp_path / "output"
    with pytest.raises(FileExistsError), fresh_output(target) as staging:
        (staging / "our.bin").write_bytes(b"ours")
        target.mkdir()
        (target / "their.bin").write_bytes(b"theirs")
    assert (target / "their.bin").read_bytes() == b"theirs"
    assert not (target / "our.bin").exists()
    assert not staging.exists()


def test_file_publication_does_not_replace_racing_destination(tmp_path):
    prepared = tmp_path / "prepared"
    target = tmp_path / "target"
    prepared.write_bytes(b"ours")
    target.write_bytes(b"theirs")
    with pytest.raises(FileExistsError):
        publish_file_no_replace(prepared, target)
    assert target.read_bytes() == b"theirs"
    assert prepared.read_bytes() == b"ours"


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "a/../b",
        "a//b",
        "./a",
        "a\\b",
        "a:b",
        "a\tb",
        "a\rb",
        "CHECKSUMS.sha256",
    ],
)
def test_checksum_parser_rejects_unsafe_paths(path):
    with pytest.raises(PackageIntegrityError):
        parse_checksums(f"{'0' * 64}  {path}\n".encode())


@pytest.mark.parametrize(
    "content", [b"", b"bad", b"\xff", b"0" * 64 + b"  x\n\n", ("0" * 64 + "  x\n").encode() * 2]
)
def test_checksum_parser_rejects_malformed_or_ambiguous_baselines(content):
    with pytest.raises(PackageIntegrityError):
        parse_checksums(content)


def test_final_baseline_covers_every_derivative_and_nested_control_name(tmp_path):
    root = tmp_path / "output"
    metadata(root)
    (root / "exports").mkdir()
    payload = {
        "exports/api.bin": b"API bytes",
        "exports/CHECKSUMS.sha256": b"evidence control",
        "artifact.md": b"assembled bytes",
    }
    for name, content in payload.items():
        (root / name).write_bytes(content)
    baseline = write_checksums(root)
    data, expected = load_checksums(root)
    assert baseline == root / CHECKSUMS_NAME
    assert set(expected) == set(payload) | {"_metadata/summary.json", "_metadata/trace.json"}
    assert data == b"".join(f"{expected[path]}  {path}\n".encode() for path in sorted(expected))
    for name, content in payload.items():
        assert expected[name] == hashlib.sha256(content).hexdigest()
    with pytest.raises(PackageIntegrityError):
        write_checksums(root)


def test_required_metadata_has_one_exact_location(tmp_path):
    root = tmp_path / "output"
    (root / "archive/_metadata").mkdir(parents=True)
    for name in ("summary.json", "trace.json"):
        (root / "archive/_metadata" / name).write_text("{}")
    with pytest.raises(PackageIntegrityError, match="Required metadata"):
        write_checksums(root)
    assert not (root / CHECKSUMS_NAME).exists()


def test_symlink_payload_cannot_read_outside_package(tmp_path):
    root = tmp_path / "output"
    metadata(root)
    outside = tmp_path / "private"
    outside.write_bytes(b"secret")
    (root / "linked.bin").symlink_to(outside)
    with pytest.raises(PackageIntegrityError, match="Symbolic link"):
        write_checksums(root)
    assert not (root / CHECKSUMS_NAME).exists()
    assert outside.read_bytes() == b"secret"


def test_unreadable_payload_does_not_create_fake_hash(monkeypatch, tmp_path):
    from intrinsical_policy_engine.common import package_integrity

    root = tmp_path / "output"
    metadata(root)
    broken = root / "broken.bin"
    broken.write_bytes(b"cannot read")
    original = package_integrity.os.open

    def open_file(path, *args, **kwargs):
        if Path(path) == broken:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(package_integrity.os, "open", open_file)
    with pytest.raises(PermissionError):
        write_checksums(root)
    assert not (root / CHECKSUMS_NAME).exists()


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_lock_cannot_truncate_an_existing_link_target(tmp_path, link):
    target = tmp_path / "output"
    private = tmp_path / "private.txt"
    private.write_text("preserve private content")
    lock = output_lock_path(target)
    if link == "symbolic":
        lock.symlink_to(private)
    else:
        lock.hardlink_to(private)
    with pytest.raises(OSError), fresh_output(target):
        pytest.fail("unsafe lock cannot be acquired")
    assert private.read_text() == "preserve private content"
    assert not target.exists()


def test_calendar_uid_depends_on_namespace_and_action_only(tmp_path):
    from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import stable_uid
    from intrinsical_policy_engine.adapters.export.base.exporters.base_exporter import BaseExporter

    root = tmp_path / "first"
    other = tmp_path / "second"
    root.mkdir()
    other.mkdir()
    plan = {
        "due_hints": {"action.one": "2026-12-01"},
        "export_context": {"uid_namespace": "Project;with Case"},
        "trace": {"hash": "old"},
    }
    first = BaseExporter().export_ics(plan, root, prodid=FrameworkPresentation().ics_prodid)
    plan["due_hints"] = {"action.one": "2027-02-01"}
    plan["trace"] = {"hash": "new"}
    second = BaseExporter().export_ics(plan, other, prodid=FrameworkPresentation().ics_prodid)
    assert first is not None and second is not None
    for path in (first, second):
        unfolded = path.read_bytes().replace(b"\r\n ", b"").decode()
        assert f"UID:{stable_uid('Project;with Case', 'action.one')}\r\n" in unfolded


@pytest.mark.parametrize("date", ["2026-1-02", "2026-02-30", "", "not-a-date"])
def test_calendar_does_not_silently_drop_invalid_dates(tmp_path, date):
    from intrinsical_policy_engine.adapters.export.base.exporters.base_exporter import BaseExporter

    plan = {"due_hints": {"action.one": date}, "export_context": {"uid_namespace": "project"}}
    with pytest.raises(ValueError):
        BaseExporter().export_ics(plan, tmp_path, prodid=FrameworkPresentation().ics_prodid)
    assert not list(tmp_path.glob("*.ics"))


def test_calendar_folding_is_unicode_safe_and_uses_crlf(tmp_path):
    from intrinsical_policy_engine.adapters.export.base.exporters.base_exporter import BaseExporter

    action = "acción;extrañísima" * 30
    plan = {"due_hints": {action: "2026-12-01"}, "export_context": {"uid_namespace": "project"}}
    path = BaseExporter().export_ics(plan, tmp_path, prodid=FrameworkPresentation().ics_prodid)
    assert path is not None
    data = path.read_bytes()
    assert data.endswith(b"\r\n")
    assert all(len(line) <= 75 for line in data.split(b"\r\n"))
    data.decode("utf-8")
    assert b"\\;" in data


def test_calendar_uses_configured_prodid_without_property_injection(tmp_path):
    from intrinsical_policy_engine.adapters.export.base.exporters.base_exporter import BaseExporter

    plan = {"due_hints": {"action": "2026-12-01"}, "export_context": {"uid_namespace": "project"}}
    prodid = "-//Custom Calendar Ñ;" * 12 + "\r\nX-BAD:inject,"
    path = BaseExporter().export_ics(plan, tmp_path, prodid=prodid)
    assert path is not None
    data = path.read_bytes()
    assert all(len(line) <= 75 for line in data.split(b"\r\n"))
    unfolded = data.replace(b"\r\n ", b"").decode()
    assert "PRODID:-//Custom Calendar Ñ\\;" in unfolded
    assert "\\nX-BAD:inject\\," in unfolded
    assert "\r\nX-BAD:" not in unfolded
