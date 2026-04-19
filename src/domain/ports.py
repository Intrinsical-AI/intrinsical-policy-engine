# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Domain ports describing contract loading/export/persistence APIs.

The ContractBundle is the main runtime container for compliance contracts.
It uses Pydantic models for type safety and validation at load time.
"""

from pathlib import Path
from typing import Literal, Protocol

# Re-export ContractBundle from contract_models (the typed Pydantic version)
from src.domain.bundles.models import BacklogConfig
from src.domain.contract_models import ContractBundle
from src.domain.framework_layout import FrameworkLayout
from src.domain.services.seal_service import SealInput

__all__ = [
    "ContractBundle",
    "ContractsPort",
    "ExporterPort",
    "PlanStorePort",
    "QualityPort",
    "GitMetadataPort",
    "FrameworkPackSourcePort",
    "BacklogConfigPort",
    "SealStorePort",
]

Reason = Literal[
    "ok",
    "absent",
    "binary_empty",
    "md_too_short",
    "md_placeholder",
    "md_read_error",
    "json_parse_error",
    "json_insufficient_keys",
    "yaml_parse_error",
    "yaml_insufficient_keys",
    "csv_too_short",
    "csv_invalid_header",
    "dir_requirement_missing",
    "parse_error",
    "unsupported_extension",
]


class ContractsPort(Protocol):
    """Port definition for loading/validating contract bundles."""

    def load(self, path: str) -> ContractBundle:
        """Load a bundle from disk and return a typed ContractBundle."""

    def validate(self, path: str) -> list[str]:
        """Run lints/schema validation for a bundle path."""


class ExporterPort(Protocol):
    """Port definition for exporters invoked by the orchestrator."""

    def export(self, plan: dict, templates_dir: str, out_dir: str) -> None:
        """Export a plan using templates into out_dir."""


class PlanStorePort(Protocol):
    """Port definition for persisting assessment plans."""

    def save(self, plan_id: str, data: dict) -> None:
        """Persist the plan document identified by `plan_id`."""


class SigningPort(Protocol):
    """Interfaz para servicios de firma criptográfica."""

    def sign_file(self, path: Path) -> Path | None:
        """Firma un archivo y devuelve la ruta de la firma generada.

        Returns:
            Path al archivo de firma (ej. file.asc) si éxito, None si falló/no disponible.
        """
        ...

    def is_available(self) -> bool:
        """Devuelve True si el backend de firma está operativo."""
        ...


class QualityPort(Protocol):
    """Port for quality analysis logic."""

    def diagnose_file(self, path: Path) -> tuple[bool, Reason]:
        """Diagnose a single file for quality issues."""
        ...


class GitMetadataPort(Protocol):
    """Port for source-control metadata used by presentation/context layers."""

    def current_revision(self) -> str:
        """Return the current git tag/commit or a deterministic fallback."""
        ...


class FrameworkPackSourcePort(Protocol):
    """Port for loading a validated framework pack layout from infrastructure."""

    def load_layout(self, framework_dir: Path) -> FrameworkLayout:
        """Load and validate framework pack layout metadata."""
        ...


class BacklogConfigPort(Protocol):
    """Port for loading backlog split configuration from infrastructure."""

    def load_backlog_config(self, framework_dir: Path) -> BacklogConfig:
        """Return pre-parsed backlog configuration for a framework pack."""
        ...


class SealStorePort(Protocol):
    """Port for scanning filesystem state into a pure sealing snapshot."""

    def collect_seal_input(self, export_dir: Path, evidence_dir: Path | None = None) -> SealInput:
        """Build the pure seal snapshot used by the domain service."""
        ...
