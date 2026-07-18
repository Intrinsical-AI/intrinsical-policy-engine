# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.

import importlib.util
from pathlib import Path

import pytest

_GUARD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_public_release.py"
_GUARD_SPEC = importlib.util.spec_from_file_location("ipe_public_release_guard", _GUARD_PATH)
assert _GUARD_SPEC is not None and _GUARD_SPEC.loader is not None
guard = importlib.util.module_from_spec(_GUARD_SPEC)
_GUARD_SPEC.loader.exec_module(guard)


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


@pytest.mark.parametrize(
    "root_file",
    [".gitattributes", "SECURITY.md", "CHANGELOG.md", "MANIFEST.in"],
)
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


def test_public_release_guard_rejects_unrecognized_file_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    violations = _scan_file(monkeypatch, tmp_path, "docs/archive.bin", b"plain text")
    assert violations == ["unsupported file type: docs/archive.bin"]


def test_public_release_guard_rejects_binary_content_with_text_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    violations = _scan_file(monkeypatch, tmp_path, "docs/disguised.md", b"text\x01binary")
    assert violations == ["binary content in text file: docs/disguised.md"]


def test_public_release_guard_rejects_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "outside.md"
    target.write_text("Public engine", encoding="utf-8")
    link = tmp_path / "docs" / "linked.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "_candidate_files", lambda: [Path("docs/linked.md")])

    assert guard.scan_public_tree() == ["blocked symlink: docs/linked.md"]


def test_public_release_guard_candidate_walk_includes_dangling_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    link = tmp_path / "docs" / "dangling.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "missing.md")
    monkeypatch.setattr(guard, "ROOT", tmp_path)

    assert guard._candidate_files() == [Path("docs/dangling.md")]
    assert guard.scan_public_tree() == ["blocked symlink: docs/dangling.md"]


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


def test_public_release_guard_walks_tree_without_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "README.md").write_text("Public engine", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard._candidate_files() == [Path("README.md")]
    assert guard.scan_public_tree() == []
