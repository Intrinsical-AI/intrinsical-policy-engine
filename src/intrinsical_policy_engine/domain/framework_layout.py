# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Resolved pack layout and captured bytes used by a closed snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from intrinsical_policy_engine.domain.contract_models import PackManifest


@dataclass(frozen=True)
class FrameworkLayout:
    framework_dir: Path
    manifest: PackManifest
    templates_dir: Path
    context_defaults_path: Path
    backlog_config_path: Path
    contract_files: dict[str, tuple[Path, ...]]
    bundle_profile_files: tuple[Path, ...]
    runtime_section_files: dict[str, tuple[Path, ...]]
    source_bytes: dict[str, bytes]

    def resolve_contract_files(self, section: str) -> tuple[Path, ...]:
        return self.contract_files.get(section, ())

    def resolve_runtime_files(self, section: str) -> tuple[Path, ...]:
        return self.runtime_section_files.get(section, ())
