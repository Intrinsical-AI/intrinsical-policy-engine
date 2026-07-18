# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.

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
