# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exporter that emits Jira issue creation requests."""

from __future__ import annotations

from typing import Any

from intrinsical_policy_engine.adapters.export.base.export_helpers import build_common_record
from intrinsical_policy_engine.adapters.export.base.exporters.base_api_exporter import (
    BaseApiExporter,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import JiraExportConfig
from intrinsical_policy_engine.adapters.export.base.models.shapes import TaskItem


class JiraExporter(BaseApiExporter[JiraExportConfig]):
    """Materialize Jira issues from plan actions."""

    config_type = JiraExportConfig

    def get_target_name(self) -> str:
        """Identifier used in export CLI/config."""
        return "jira"

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
        self, item: TaskItem, description: str, cfg: JiraExportConfig, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Build Jira API request record."""
        fields = {
            "summary": item.title,
            "description": self._text_to_adf(description),
            "duedate": item.due,
            "labels": item.labels,
            "project": {"key": cfg.project_key},
            "issuetype": {"name": cfg.issue_type},
            **cfg.customfields,
        }

        body = {
            "fields": fields,
            "properties": [
                {"key": "externalId", "value": item.uid},
                {"key": "contentHash", "value": item.content_hash},
                {"key": "version", "value": item.version},
            ],
        }

        return build_common_record(
            method="POST",
            url="/rest/api/3/issue",
            base_url=cfg.base_url,
            headers=headers,
            body=body,
            uid=item.uid,
        )
