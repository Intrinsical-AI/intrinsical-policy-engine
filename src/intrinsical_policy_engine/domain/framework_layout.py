# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Framework pack layout value object.

Filesystem loading and manifest parsing live in adapter code. Domain/runtime
consumers use this object after pack paths have been resolved and validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrameworkLayout:
    """Resolved filesystem layout for a framework pack."""

    framework_dir: Path
    manifest_path: Path
    framework_version_path: Path
    templates_dir: Path
    render_artifacts_dir: Path
    evidence_templates_dir: Path
    schemas_dir: Path
    context_defaults_path: Path
    backlog_config_path: Path
    contract_entries: dict[str, tuple[str, ...]]
    contract_files: dict[str, tuple[Path, ...]]
    bundle_profile_entries: tuple[str, ...]
    bundle_profile_files: tuple[Path, ...]
    runtime_entries: dict[str, tuple[str, ...]]
    runtime_section_files: dict[str, tuple[Path, ...]]
    runtime_files: dict[str, str]

    def resolve_contract_files(self, section: str) -> tuple[Path, ...]:
        """Resolve concrete files for a semantic contract section."""
        return self.contract_files.get(section, ())

    def resolve_bundle_profile_files(self) -> tuple[Path, ...]:
        """Resolve concrete bundle profile files."""
        return self.bundle_profile_files

    def resolve_runtime_files(self, section: str) -> tuple[Path, ...]:
        """Resolve concrete files for a runtime section."""
        return self.runtime_section_files.get(section, ())
