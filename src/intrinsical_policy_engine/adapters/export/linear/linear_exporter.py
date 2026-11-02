# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exporter that emits Linear GraphQL mutation payloads."""

from __future__ import annotations

from typing import Any

from intrinsical_policy_engine.adapters.export.base.export_helpers import (
    build_common_record,
    map_labels_to_ids,
)
from intrinsical_policy_engine.adapters.export.base.exporters.base_api_exporter import (
    BaseApiExporter,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import LinearExportConfig
from intrinsical_policy_engine.adapters.export.base.models.shapes import TaskItem


class LinearExporter(BaseApiExporter[LinearExportConfig]):
    """Translate plan actions into Linear issues."""

    target = "linear"
    config_type = LinearExportConfig

    def build_request_record(
        self, item: TaskItem, cfg: LinearExportConfig, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Build Linear GraphQL API request record."""
        label_ids = map_labels_to_ids(item.labels, cfg.labelIds_by_label, cfg.labelIds)

        body = {
            "query": (
                "mutation CreateIssue($input: IssueCreateInput!) "
                "{ issueCreate(input: $input) { success } }"
            ),
            "variables": {
                "input": {
                    "title": item.title,
                    "description": item.description_md,
                    "dueDate": item.due,
                    "labelIds": label_ids,
                    "teamId": cfg.teamId,
                    **({"projectId": cfg.projectId} if cfg.projectId is not None else {}),
                }
            },
        }

        return build_common_record(
            method="POST",
            url="/graphql",
            base_url=cfg.base_url,
            headers=headers,
            body=body,
            uid=item.uid,
        )
