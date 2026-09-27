# SPDX-License-Identifier: MPL-2.0
"""Portable export namespace contracts."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from intrinsical_policy_engine.app.export.orchestrator import ExportConfig, ExportOrchestrator
from intrinsical_policy_engine.domain.types import Plan


def _orchestrator(contracts_dir: Path, pack_hash: str) -> ExportOrchestrator:
    return ExportOrchestrator(
        ExportConfig(
            plan=cast(Plan, {"trace": {"framework_pack_hash": pack_hash}}),
            contracts_dir=contracts_dir,
            outdir=contracts_dir / "out",
            save_plan=False,
            templates=None,
            targets=None,
            config_path=None,
            strict=False,
        )
    )


def test_export_uid_namespace_is_independent_of_pack_checkout_path(tmp_path: Path) -> None:
    first_root = tmp_path / "first-location"
    second_root = tmp_path / "second-location"
    first_root.mkdir()
    second_root.mkdir()

    first = _orchestrator(first_root, "same-portable-pack-hash")
    second = _orchestrator(second_root, "same-portable-pack-hash")

    first_namespace = first._ensure_export_context_namespace()["export_context"]["uid_namespace"]
    second_namespace = second._ensure_export_context_namespace()["export_context"]["uid_namespace"]
    assert first_namespace == second_namespace


def test_export_uid_namespace_changes_with_pack_provenance(tmp_path: Path) -> None:
    contracts_dir = tmp_path / "pack"
    contracts_dir.mkdir()

    first = _orchestrator(contracts_dir, "pack-hash-one")
    second = _orchestrator(contracts_dir, "pack-hash-two")

    assert (
        first._ensure_export_context_namespace()["export_context"]["uid_namespace"]
        != second._ensure_export_context_namespace()["export_context"]["uid_namespace"]
    )
