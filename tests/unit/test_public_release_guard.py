# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

import scripts.check_public_release as guard


def _scan_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative_path: str,
    content: bytes,
) -> list[str]:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "_candidate_files", lambda: [Path(relative_path)])
    return guard.scan_public_tree()


def test_public_release_guard_accepts_current_tree() -> None:
    violations = guard.scan_public_tree()
    assert violations == []


@pytest.mark.parametrize("root_file", [".gitattributes", "SECURITY.md", "CHANGELOG.md"])
def test_public_release_guard_allows_release_metadata(root_file: str) -> None:
    assert guard._is_allowed_path(Path(root_file))


def test_public_release_guard_rejects_unexpected_root_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    violations = _scan_file(monkeypatch, tmp_path, "private.txt", b"content")
    assert violations == ["blocked path: private.txt"]


def test_public_release_guard_rejects_binary_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    violations = _scan_file(monkeypatch, tmp_path, "docs/image.png", b"not-an-image")
    assert violations == ["blocked binary-like asset: docs/image.png"]


def test_typing_marker_is_scanned_as_text_without_allowing_other_typed_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    relative = "src/intrinsical_policy_engine/py.typed"
    assert _scan_file(monkeypatch, tmp_path, relative, b"") == []
    reserved_term = "high" + "_" + "risk"
    assert _scan_file(monkeypatch, tmp_path, relative, reserved_term.encode()) == [
        f"blocked term in {relative}: {reserved_term}"
    ]
    other = "src/intrinsical_policy_engine/private.typed"
    assert _scan_file(monkeypatch, tmp_path, other, b"content") == [f"blocked file type: {other}"]


def test_public_release_guard_rejects_reserved_term(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reserved_term = "high" + "_" + "risk"
    violations = _scan_file(
        monkeypatch,
        tmp_path,
        "docs/example.md",
        reserved_term.encode("utf-8"),
    )
    assert violations == [f"blocked term in docs/example.md: {reserved_term}"]


def test_public_release_guard_rejects_non_utf8_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    violations = _scan_file(monkeypatch, tmp_path, "docs/example.md", b"\xff\xfe")
    assert violations == ["non-utf8 text file: docs/example.md"]


def test_public_release_guard_scans_its_own_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reserved_term = "high" + "_" + "risk"
    relative = "scripts/check_public_release.py"
    violations = _scan_file(monkeypatch, tmp_path, relative, reserved_term.encode("utf-8"))
    assert violations == [f"blocked term in {relative}: {reserved_term}"]


@pytest.mark.parametrize("suffix", [".html", ".zip"])
def test_public_release_guard_rejects_unscanned_file_types(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, suffix: str
) -> None:
    violations = _scan_file(monkeypatch, tmp_path, f"docs/hidden{suffix}", b"content")
    assert violations == [f"blocked file type: docs/hidden{suffix}"]


@pytest.mark.parametrize(
    "member",
    [
        "intrinsical_policy_engine/hidden.html",
        "intrinsical_policy_engine/nested.zip",
        "../private.py",
    ],
)
def test_public_release_guard_rejects_hidden_or_escaping_wheel_payload(
    tmp_path: Path, member: str
) -> None:
    wheel = tmp_path / "intrinsical_policy_engine-3.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(member, "content")
    assert guard.scan_distribution(wheel)


def test_public_release_guard_scans_wheel_text(tmp_path: Path) -> None:
    wheel = tmp_path / "intrinsical_policy_engine-3.0.0-py3-none-any.whl"
    reserved_term = "high" + "_" + "risk"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("intrinsical_policy_engine/policy.py", reserved_term)
    assert guard.scan_distribution(wheel) == [
        f"blocked term in {wheel.name}:intrinsical_policy_engine/policy.py: {reserved_term}"
    ]


def test_public_release_guard_scans_source_archive_text(tmp_path: Path) -> None:
    sdist = tmp_path / "intrinsical_policy_engine-3.0.0.tar.gz"
    reserved_term = "high" + "_" + "risk"
    body = reserved_term.encode("utf-8")
    member_path = "intrinsical_policy_engine-3.0.0/docs/example.md"
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo(member_path)
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    assert guard.scan_distribution(sdist) == [
        f"blocked term in {sdist.name}:{member_path}: {reserved_term}"
    ]


def test_public_release_guard_rejects_source_archive_links(tmp_path: Path) -> None:
    sdist = tmp_path / "intrinsical_policy_engine-3.0.0.tar.gz"
    member_path = "intrinsical_policy_engine-3.0.0/src/private.py"
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo(member_path)
        member.type = tarfile.SYMTYPE
        member.linkname = "/private.py"
        archive.addfile(member)
    assert guard.scan_distribution(sdist) == [f"blocked archive entry: {sdist.name}:{member_path}"]


def test_public_release_guard_walks_tree_without_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "README.md").write_text("Public engine", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard._candidate_files() == [Path("README.md")]
    assert guard.scan_public_tree() == []


def test_public_release_guard_rejects_retired_provenance_file() -> None:
    assert not guard._is_allowed_path(Path("PROVENANCE.md"))


@pytest.mark.parametrize("filename", ["café.md", 'quoted"name.md', "line\nbreak.md"])
@pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
def test_public_release_guard_scans_git_paths_without_quoting_or_line_splitting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, filename: str, tracked: bool
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "core.quotePath", "true"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("Public engine", encoding="utf-8")
    relative = Path("docs") / filename
    (tmp_path / relative).parent.mkdir()
    reserved_term = "high" + "_" + "risk"
    (tmp_path / relative).write_text(reserved_term, encoding="utf-8")
    if tracked:
        subprocess.run(["git", "add", "--", "README.md", str(relative)], cwd=tmp_path, check=True)
    monkeypatch.setattr(guard, "ROOT", tmp_path)

    assert set(guard._git_files()) == {Path("README.md"), relative}
    assert guard.scan_public_tree() == [f"blocked term in {relative.as_posix()}: {reserved_term}"]
