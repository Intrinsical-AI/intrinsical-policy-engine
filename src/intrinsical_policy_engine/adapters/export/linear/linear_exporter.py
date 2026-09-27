# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exporter that emits Linear GraphQL mutation payloads."""

from __future__ import annotations

from typing import Any

from intrinsical_policy_engine.adapters.export.base.exporters.base_api_exporter import (
    BaseApiExporter,
)
from intrinsical_policy_engine.adapters.export.base.models.config_constants import LinearConfig


class LinearExporter(BaseApiExporter):
    """Translate plan actions into Linear issues."""

    def get_target_name(self) -> str:
        """Identifier used when selecting exporters."""
        return "linear"

    def _validate_strict_config(self, cfg: dict) -> None:
        """Validate Linear-specific strict requirements."""
        self._require_config_value(cfg, LinearConfig.BASE_URL, "linear")
        self._require_config_value(cfg, LinearConfig.TEAM_ID, "linear")

    def get_log_context(self, cfg: dict) -> dict[str, Any]:
        """Get Linear-specific logging context."""
        return {
            "base_url": bool(cfg.get(LinearConfig.BASE_URL)),
            "has_team": bool(cfg.get(LinearConfig.TEAM_ID)),
            "has_project": bool(cfg.get(LinearConfig.PROJECT_ID)),
        }

    def build_request_record(
        self, item: Any, description: str, cfg: dict, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Build Linear GraphQL API request record."""
        # Get configuration values
        base_url = cfg.get(LinearConfig.BASE_URL)
        team_id = cfg.get(LinearConfig.TEAM_ID)
        project_id = cfg.get(LinearConfig.PROJECT_ID)

        # Map labels to label IDs
        raw_extra = cfg.get(LinearConfig.LABEL_IDS)
        extra_labels = [str(x) for x in raw_extra] if isinstance(raw_extra, list) else []
        raw_map = cfg.get(LinearConfig.LABEL_IDS_BY_LABEL)
        map_by_label: dict[str, str] = (
            {str(k): str(v) for k, v in raw_map.items()} if isinstance(raw_map, dict) else {}
        )

        # Use centralized label mapping
        mapping_result = self.map_labels_to_ids(item.labels, map_by_label, extra_labels)
        label_ids = mapping_result["mapped_ids"]

        body = {
            "query": (
                "mutation CreateIssue($input: IssueCreateInput!) "
                "{ issueCreate(input: $input) { success } }"
            ),
            "variables": {
                "input": {
                    "title": item.title,
                    "description": description,
                    "dueDate": item.due,
                    "labelIds": label_ids,
                    **({"teamId": team_id} if team_id else {}),
                    **({"projectId": project_id} if project_id else {}),
                    "clientMutationId": item.uid,
                    "externalId": item.uid,
                }
            },
        }

        return self.build_common_record(
            method="POST",
            url="/graphql",
            base_url=base_url,
            headers=headers,
            body=body,
            uid=item.uid,
        )
