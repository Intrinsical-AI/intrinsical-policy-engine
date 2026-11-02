# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Generate offline Asana task requests and a CSV import file."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import build_task_items
from intrinsical_policy_engine.adapters.export.base.export_helpers import (
    build_common_record,
    map_labels_to_ids,
)
from intrinsical_policy_engine.adapters.export.base.exporters.base_api_exporter import (
    BaseApiExporter,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import AsanaExportConfig
from intrinsical_policy_engine.adapters.export.base.models.shapes import TaskItem
from intrinsical_policy_engine.common.constants import DEFAULT_ENCODING


class AsanaExporter(BaseApiExporter[AsanaExportConfig]):
    """Translate plan actions into Asana requests and helper CSV files."""

    target = "asana"
    config_type = AsanaExportConfig

    def build_request_record(
        self, item: TaskItem, cfg: AsanaExportConfig, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Build Asana API request record."""
        tag_gids = map_labels_to_ids(item.labels, cfg.tag_gids_by_label, cfg.tag_gids)

        body = {
            "data": {
                "name": item.title,
                "notes": item.description_md,
                "due_on": item.due,
                "projects": [cfg.project_gid],
                "tags": tag_gids,
                "external": {
                    "gid": item.uid,
                    "data": f"v={item.version};h={item.content_hash}",
                },
            }
        }

        return build_common_record(
            method="POST",
            url="/tasks",
            base_url=cfg.base_url,
            headers=headers,
            body=body,
            uid=item.uid,
        )

    def export(self, plan: dict[str, Any], out_dir: Path) -> None:
        """Generate offline requests and the Asana import file."""
        items = build_task_items(plan)
        self._export_items(items, out_dir)
        self._export_csv(items, out_dir)

    def _export_csv(self, items: list[TaskItem], out_dir: Path) -> None:
        """Export Asana-friendly CSV import file."""
        base = self._ensure_export_dir(out_dir)
        csv_path = base / "import.csv"

        with csv_path.open("x", newline="", encoding=DEFAULT_ENCODING) as cf:
            w = csv.writer(cf)
            w.writerow(
                [
                    "Name",
                    "Notes",
                    "Due Date",
                    "Section/Column",
                    "Tags",
                    "Assignee",
                    "External Id",
                    "Content Hash",
                    "Version",
                ]
            )
            for it in items:
                first_art = it.article_ids[0] if it.article_ids else "CTRL-Act"
                section = (
                    f"Art.{first_art}" if first_art and first_art != "CTRL-Act" else "CTRL-Act"
                )

                # Convert labels to human tag names
                tag_names: list[str] = []
                for lbl in it.labels:
                    if lbl.isdigit():
                        tag_names.append(f"Art.{lbl}")
                    else:
                        tag_names.append(lbl)

                name = it.title
                notes = it.description_md
                w.writerow(
                    [
                        name,
                        notes,
                        it.due or "",
                        section,
                        ",".join(tag_names),
                        "",
                        it.uid,
                        it.content_hash,
                        it.version,
                    ]
                )

        self._log_safe("export.asana.csv_created", {"items": len(items)})
