# SPDX-License-Identifier: MPL-2.0
"""Strict filesystem-layout contracts for framework packs."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout


def _write_minimal_pack(framework_dir: Path) -> None:
    (framework_dir / "law" / "core").mkdir(parents=True)
    (framework_dir / "delivery" / "profiles").mkdir(parents=True)
    (framework_dir / "render" / "artifacts").mkdir(parents=True)
    (framework_dir / "render" / "config").mkdir(parents=True)
    (framework_dir / "evidence" / "templates").mkdir(parents=True)
    (framework_dir / "meta" / "schemas").mkdir(parents=True)

    (framework_dir / "FRAMEWORK_VERSION.yml").write_text(
        "framework:\n  id: neutral-framework\n  version: 0.0.1\n", encoding="utf-8"
    )
    (framework_dir / "manifest.yml").write_text(
        "\n".join(
            [
                "contracts:",
                "  flags:",
                "    - law/core/flags.yml",
                "bundle_profiles:",
                "  - delivery/profiles/*.yml",
                "templates_dir: render",
                "evidence_templates_dir: evidence/templates",
                "schemas_dir: meta/schemas",
                "runtime_files:",
                "  context_defaults: render/config/context_defaults.yml",
                "  backlog_config: render/config/backlog_config.yml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (framework_dir / "law" / "core" / "flags.yml").write_text("registry: []\n", encoding="utf-8")
    (framework_dir / "delivery" / "profiles" / "core.yml").write_text(
        "profiles: {}\n", encoding="utf-8"
    )
    (framework_dir / "render" / "config" / "context_defaults.yml").write_text(
        "defaults: {}\n", encoding="utf-8"
    )
    (framework_dir / "render" / "config" / "backlog_config.yml").write_text(
        "splits: []\n", encoding="utf-8"
    )


def test_framework_layout_resolves_manifest_paths(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)

    layout = load_framework_layout(framework_dir)

    assert layout.templates_dir == framework_dir / "render"
    assert layout.render_artifacts_dir == framework_dir / "render" / "artifacts"
    assert layout.evidence_templates_dir == framework_dir / "evidence" / "templates"
    assert layout.schemas_dir == framework_dir / "meta" / "schemas"
    assert layout.context_defaults_path == (
        framework_dir / "render" / "config" / "context_defaults.yml"
    )
    assert layout.backlog_config_path == (
        framework_dir / "render" / "config" / "backlog_config.yml"
    )
    assert layout.resolve_contract_files("flags") == (framework_dir / "law" / "core" / "flags.yml",)
    assert layout.resolve_bundle_profile_files() == (
        framework_dir / "delivery" / "profiles" / "core.yml",
    )


def test_framework_layout_rejects_missing_runtime_file(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)
    (framework_dir / "render" / "config" / "context_defaults.yml").unlink()

    with pytest.raises(FileNotFoundError):
        load_framework_layout(framework_dir)


def test_framework_layout_rejects_missing_contract_file(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)
    (framework_dir / "law" / "core" / "flags.yml").unlink()

    with pytest.raises(FileNotFoundError):
        load_framework_layout(framework_dir)


def _rewrite_manifest(framework_dir: Path, update: dict[str, object]) -> None:
    manifest_path = framework_dir / "manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest.update(update)
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def test_framework_layout_rejects_directory_traversal(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)
    outside_render = tmp_path / "outside-render"
    (outside_render / "artifacts").mkdir(parents=True)
    _rewrite_manifest(framework_dir, {"templates_dir": "../outside-render"})

    with pytest.raises(ValueError, match="escapes framework pack root"):
        load_framework_layout(framework_dir)


def test_framework_layout_rejects_symlinked_render_template(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)
    outside_template = tmp_path / "outside-template.j2"
    outside_template.write_text("host content must not render\n", encoding="utf-8")
    linked_template = framework_dir / "render" / "artifacts" / "linked.j2"
    try:
        linked_template.symlink_to(outside_template)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")

    with pytest.raises(ValueError, match="symbolic links"):
        load_framework_layout(framework_dir)


def test_framework_layout_rejects_absolute_runtime_file(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)
    outside_config = tmp_path / "outside.yml"
    outside_config.write_text("defaults: {}\n", encoding="utf-8")
    _rewrite_manifest(
        framework_dir,
        {
            "runtime_files": {
                "context_defaults": str(outside_config.resolve()),
                "backlog_config": "render/config/backlog_config.yml",
            }
        },
    )

    with pytest.raises(ValueError, match="escapes framework pack root"):
        load_framework_layout(framework_dir)


def test_framework_layout_rejects_contract_entry_traversal(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)
    outside_contract = tmp_path / "outside-flags.yml"
    outside_contract.write_text("registry: []\n", encoding="utf-8")
    _rewrite_manifest(framework_dir, {"contracts": {"flags": ["../outside-flags.yml"]}})

    with pytest.raises(ValueError, match="escapes framework pack root"):
        load_framework_layout(framework_dir)


def test_framework_layout_rejects_symlinked_bundle_profile_escape(tmp_path: Path) -> None:
    framework_dir = tmp_path / "framework"
    _write_minimal_pack(framework_dir)
    outside_profile = tmp_path / "outside-profile.yml"
    outside_profile.write_text("profiles: {}\n", encoding="utf-8")
    linked_profile = framework_dir / "delivery" / "profiles" / "linked.yml"
    try:
        linked_profile.symlink_to(outside_profile)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")

    with pytest.raises(ValueError, match="symbolic links"):
        load_framework_layout(framework_dir)
