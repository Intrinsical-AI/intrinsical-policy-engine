# SPDX-License-Identifier: MPL-2.0
"""Determinism and absence semantics for framework hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout
from intrinsical_policy_engine.common.hashing import (
    EMPTY_HASH,
    compute_framework_pack_hashes,
    sha256_directory,
    sha256_directory_or_absent,
    sha256_file,
    sha256_file_or_absent,
)
from intrinsical_policy_engine.common.io_safety import UnsafeTreePathError


def test_sha256_file_missing_returns_empty_hash(tmp_path: Path) -> None:
    assert sha256_file(tmp_path / "missing.txt") == EMPTY_HASH


def test_sha256_directory_missing_warns_and_returns_empty_hash(
    tmp_path: Path, caplog: object
) -> None:
    missing = tmp_path / "missing"
    with caplog.at_level("WARNING"):  # type: ignore[attr-defined]
        digest = sha256_directory(missing)

    assert digest == EMPTY_HASH
    assert any(
        "hashing.directory.not_found" in record.message
        for record in caplog.records  # type: ignore[attr-defined]
    )


def test_sha256_directory_hashes_relative_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir(parents=True)
    (root / "alpha" / "item.txt").write_text("first", encoding="utf-8")
    (root / "beta" / "item.txt").write_text("second", encoding="utf-8")

    digest = sha256_directory(root)
    expected = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            expected.update(str(path.relative_to(root)).encode("utf-8"))
            expected.update(sha256_file(path).encode("utf-8"))

    assert digest == expected.hexdigest()


def test_directory_hash_is_path_invariant(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "relocated"
    for root in (first, second):
        (root / "nested").mkdir(parents=True)
        (root / "nested" / "item.txt").write_text("same content", encoding="utf-8")

    assert sha256_directory(first) == sha256_directory(second)


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_sha256_directory_rejects_symbolic_links(
    tmp_path: Path,
    link_kind: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    if link_kind == "file":
        target = tmp_path / "outside.txt"
        target.write_text("outside content", encoding="utf-8")
    else:
        target = tmp_path / "outside"
        target.mkdir()
        (target / "item.txt").write_text("outside content", encoding="utf-8")

    link = root / "linked"
    try:
        link.symlink_to(target, target_is_directory=link_kind == "directory")
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(UnsafeTreePathError, match="Symbolic links are forbidden"):
        sha256_directory(root)


def test_absent_directory_uses_distinct_sentinel(tmp_path: Path) -> None:
    assert sha256_directory_or_absent(tmp_path / "absent") == "ABSENT"


def test_framework_pack_hashes_are_manifest_driven(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    (framework_dir / "law" / "core").mkdir(parents=True)
    (framework_dir / "delivery" / "profiles").mkdir(parents=True)
    (framework_dir / "render" / "artifacts").mkdir(parents=True)
    (framework_dir / "render" / "config").mkdir(parents=True)
    (framework_dir / "evidence" / "templates").mkdir(parents=True)
    (framework_dir / "meta" / "schemas").mkdir(parents=True)

    (framework_dir / "law" / "core" / "flags.yml").write_text("registry: []\n", encoding="utf-8")
    (framework_dir / "render" / "artifacts" / "template.md").write_text(
        "template", encoding="utf-8"
    )
    (framework_dir / "render" / "config" / "context_defaults.yml").write_text(
        "defaults: {}\n", encoding="utf-8"
    )
    (framework_dir / "render" / "config" / "backlog_config.yml").write_text(
        "splits: []\n", encoding="utf-8"
    )
    (framework_dir / "evidence" / "templates" / "evidence.md").write_text(
        "evidence", encoding="utf-8"
    )
    (framework_dir / "meta" / "schemas" / "schema.json").write_text("{}", encoding="utf-8")
    (framework_dir / "delivery" / "profiles" / "profile.yml").write_text(
        "profiles: {}\n", encoding="utf-8"
    )
    (framework_dir / "FRAMEWORK_VERSION.yml").write_text(
        "framework:\n  id: neutral-framework\n  version: 0.1.0\n",
        encoding="utf-8",
    )
    (framework_dir / "manifest.yml").write_text(
        "contracts:\n"
        "  flags:\n"
        "    - law/core/flags.yml\n"
        "bundle_profiles:\n"
        "  - delivery/profiles/*.yml\n"
        "templates_dir: render\n"
        "evidence_templates_dir: evidence/templates\n"
        "schemas_dir: meta/schemas\n"
        "runtime_files:\n"
        "  context_defaults: render/config/context_defaults.yml\n"
        "  backlog_config: render/config/backlog_config.yml\n",
        encoding="utf-8",
    )

    hashes = compute_framework_pack_hashes(load_framework_layout(framework_dir))

    assert hashes["framework_id"] == "neutral-framework"
    assert hashes["framework_version"] == "0.1.0"
    assert hashes["render_templates_hash"] == sha256_directory_or_absent(framework_dir / "render")
    assert hashes["evidence_templates_hash"] == sha256_directory_or_absent(
        framework_dir / "evidence" / "templates"
    )
    assert hashes["schemas_hash"] == sha256_directory_or_absent(framework_dir / "meta" / "schemas")
    assert hashes["framework_version_file_hash"] == sha256_file_or_absent(
        framework_dir / "FRAMEWORK_VERSION.yml"
    )
    assert hashes["manifest_file_hash"] == sha256_file_or_absent(framework_dir / "manifest.yml")
    assert hashes["bundle_profiles_hash"] != "ABSENT"
