# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Configuration constants for API exporters."""


class ExporterConfig:
    """Base configuration keys common to all exporters."""

    BASE_URL = "base_url"
    STRICT = "strict"
    HEADERS = "headers"

    # Common file/URL configuration
    PUBLIC_BASE_URL = "public_base_url"


class AsanaConfig(ExporterConfig):
    """Asana-specific configuration keys."""

    PROJECT_GID = "project_gid"
    TAG_GIDS = "tag_gids"
    TAG_GIDS_BY_LABEL = "tag_gids_by_label"


class JiraConfig(ExporterConfig):
    """Jira-specific configuration keys."""

    PROJECT_KEY = "project_key"
    ISSUE_TYPE = "issue_type"
    CUSTOM_FIELDS = "customfields"


class LinearConfig(ExporterConfig):
    """Linear-specific configuration keys."""

    TEAM_ID = "teamId"
    PROJECT_ID = "projectId"
    LABEL_IDS = "labelIds"
    LABEL_IDS_BY_LABEL = "labelIds_by_label"
