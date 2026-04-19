# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Typed export configuration models using Pydantic.

This module provides validated configuration models for all exporters,
replacing the previous dict-based configuration with type-safe models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.domain.exceptions import StrictModeViolation


class ExportConfig(BaseModel):
    """Base configuration for all exporters.

    Attributes:
        strict: Enable strict validation mode (default: True for production safety)
        base_url: Base URL for API endpoints (required in strict mode for API exporters)
        headers: HTTP headers for API requests
        public_base_url: Public base URL for file attachments
    """

    strict: bool = Field(
        default=True,
        description="Enable strict validation mode for production safety",
    )
    base_url: str | None = Field(
        default=None,
        description="Base URL for API endpoints",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP headers for API requests",
    )

    # File attachment URL used by attachment helpers and renderers
    public_base_url: str | None = Field(default=None, description="Public base URL for attachments")

    # Quality report (injected by run_export)
    quality_report: dict[str, Any] | None = Field(
        default=None,
        description="Pre-computed evidence quality report",
    )

    model_config = {"extra": "forbid"}


def _validate_strict_fields(
    *,
    strict: bool,
    target: str,
    prefix: str,
    checks: tuple[tuple[str, bool], ...],
) -> None:
    """Raise a strict-mode violation when required fields are missing."""
    if not strict:
        return

    missing = [name for name, ok in checks if not ok]
    if missing:
        raise StrictModeViolation(
            f"{prefix} requires {', '.join(missing)} in strict mode",
            missing_keys=missing,
            target=target,
        )


class AsanaExportConfig(ExportConfig):
    """Asana-specific export configuration.

    Attributes:
        project_gid: Asana project GID (required in strict mode)
        tag_gids: List of tag GIDs to apply to all tasks
        tag_gids_by_label: Mapping of label names to tag GIDs
    """

    project_gid: str | None = Field(
        default=None,
        description="Asana project GID",
    )
    tag_gids: list[str] = Field(
        default_factory=list,
        description="Tag GIDs to apply to all tasks",
    )
    tag_gids_by_label: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of label names to tag GIDs",
    )

    @model_validator(mode="after")
    def validate_strict_requirements(self) -> AsanaExportConfig:
        """Validate strict mode requirements for Asana."""
        _validate_strict_fields(
            strict=self.strict,
            target="asana",
            prefix="Asana exporter",
            checks=(
                ("base_url", bool(self.base_url)),
                ("project_gid", bool(self.project_gid)),
            ),
        )
        return self


class JiraExportConfig(ExportConfig):
    """Jira-specific export configuration.

    Attributes:
        project_key: Jira project key (required in strict mode)
        issue_type: Issue type for created issues (default: "Task")
        customfields: Custom field mappings
    """

    project_key: str | None = Field(
        default=None,
        description="Jira project key",
    )
    issue_type: str = Field(
        default="Task",
        description="Issue type for created issues",
    )
    customfields: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom field mappings",
    )

    @model_validator(mode="after")
    def validate_strict_requirements(self) -> JiraExportConfig:
        """Validate strict mode requirements for Jira."""
        _validate_strict_fields(
            strict=self.strict,
            target="jira",
            prefix="Jira exporter",
            checks=(
                ("base_url", bool(self.base_url)),
                ("project_key", bool(self.project_key)),
            ),
        )
        return self


class LinearExportConfig(ExportConfig):
    """Linear-specific export configuration.

    Attributes:
        teamId: Linear team ID (required in strict mode)
        projectId: Linear project ID (optional)
        labelIds: List of label IDs to apply to all issues
        labelIds_by_label: Mapping of label names to label IDs
    """

    teamId: str | None = Field(  # noqa: N815
        default=None,
        description="Linear team ID",
    )
    projectId: str | None = Field(  # noqa: N815
        default=None,
        description="Linear project ID",
    )
    labelIds: list[str] = Field(  # noqa: N815
        default_factory=list,
        description="Label IDs to apply to all issues",
    )
    labelIds_by_label: dict[str, str] = Field(  # noqa: N815
        default_factory=dict,
        description="Mapping of label names to label IDs",
    )

    @model_validator(mode="after")
    def validate_strict_requirements(self) -> LinearExportConfig:
        """Validate strict mode requirements for Linear."""
        _validate_strict_fields(
            strict=self.strict,
            target="linear",
            prefix="Linear exporter",
            checks=(
                ("base_url", bool(self.base_url)),
                ("teamId", bool(self.teamId)),
            ),
        )
        return self


class FilesystemExportConfig(ExportConfig):
    """Filesystem-specific export configuration.

    For filesystem exporter, strict mode affects template and evidence requirements
    but doesn't require API credentials.
    """

    pass


def config_from_dict(data: dict[str, Any], target: str) -> ExportConfig:
    """Create appropriate config model based on target.

    Args:
        data: Configuration dictionary
        target: Export target name

    Returns:
        Appropriate ExportConfig subclass instance

    Raises:
        ValueError: If target is unknown
    """
    config_map: dict[str, type[ExportConfig]] = {
        "asana": AsanaExportConfig,
        "jira": JiraExportConfig,
        "linear": LinearExportConfig,
        "filesystem": FilesystemExportConfig,
    }

    config_class: type[ExportConfig] = config_map.get(target, ExportConfig)
    return config_class(**data)
