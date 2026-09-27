# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exporter that emits Jira issue creation requests."""

from __future__ import annotations

from typing import Any

from intrinsical_policy_engine.adapters.export.base.exporters.base_api_exporter import (
    BaseApiExporter,
)
from intrinsical_policy_engine.adapters.export.base.models.config_constants import JiraConfig


class JiraExporter(BaseApiExporter):
    """Materialize Jira issues from plan actions."""

    def get_target_name(self) -> str:
        """Identifier used in export CLI/config."""
        return "jira"

    def _validate_strict_config(self, cfg: dict) -> None:
        """Validate Jira-specific strict requirements."""
        self._require_config_value(cfg, JiraConfig.BASE_URL, "jira")
        self._require_config_value(cfg, JiraConfig.PROJECT_KEY, "jira")

    def get_log_context(self, cfg: dict) -> dict[str, Any]:
        """Get Jira-specific logging context."""
        return {
            "base_url": bool(cfg.get(JiraConfig.BASE_URL)),
            "has_project_key": bool(cfg.get(JiraConfig.PROJECT_KEY)),
            "issue_type": cfg.get(JiraConfig.ISSUE_TYPE, "Task"),
            "customfields": bool(cfg.get(JiraConfig.CUSTOM_FIELDS)),
        }

    @staticmethod
    def _text_to_adf(text: str) -> dict[str, Any]:
        """Convert plain text to Atlassian Document Format (ADF).

        Jira Cloud REST API v3 requires the description field in ADF format,
        not plain string. This creates a minimal ADF document with paragraphs
        per line.

        Args:
            text: Plain text to convert

        Returns:
            ADF document structure
        """
        content: list[dict[str, Any]] = []
        for line in (text or "").splitlines():
            paragraph: dict[str, Any] = {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}] if line else [],
            }
            content.append(paragraph)
        if not content:
            content = [{"type": "paragraph", "content": []}]
        return {"type": "doc", "version": 1, "content": content}

    def build_request_record(
        self, item: Any, description: str, cfg: dict, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Build Jira API request record."""
        # Get configuration values
        base_url = cfg.get(JiraConfig.BASE_URL)
        project_key = cfg.get(JiraConfig.PROJECT_KEY)
        issue_type = cfg.get(JiraConfig.ISSUE_TYPE) or "Task"
        customfields = cfg.get(JiraConfig.CUSTOM_FIELDS) or {}

        fields = {
            "summary": item.title,
            "description": self._text_to_adf(description),
            "duedate": item.due,
            "labels": item.labels,
            **({"project": {"key": project_key}} if project_key else {}),
            "issuetype": {"name": issue_type},
        }

        # Add custom fields if provided
        if isinstance(customfields, dict):
            fields.update(customfields)

        body = {
            "fields": fields,
            "properties": [
                {"key": "externalId", "value": item.uid},
                {"key": "contentHash", "value": item.content_hash},
                {"key": "version", "value": item.version},
            ],
        }

        return self.build_common_record(
            method="POST",
            url="/rest/api/3/issue",
            base_url=base_url,
            headers=headers,
            body=body,
            uid=item.uid,
        )
