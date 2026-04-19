# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Asana-specific exporter for pushing plan actions to tasks and CSV imports."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.adapters.export.base.evidence.task_graph import build_task_items
from src.adapters.export.base.exporters.base_api_exporter import BaseApiExporter
from src.adapters.export.base.models.config_constants import AsanaConfig
from src.app.config.constants import DEFAULT_ENCODING, EXPORTS_DIR, REQUESTS_NDJSON
from src.app.config.context import get_plan_fingerprint
from src.domain.exceptions import StrictModeViolation


class AsanaExporter(BaseApiExporter):
    """Translate plan actions into Asana requests and helper CSV files."""

    def get_target_name(self) -> str:
        """Identifier used by the export orchestrator."""
        return "asana"

    def _validate_strict_config(self, cfg: dict) -> None:
        """Validate Asana-specific strict requirements.

        In strict mode, malformed config raises StrictModeViolation.
        """
        try:
            strict_value = (cfg or {}).get("strict", True)
            if not bool(strict_value):
                return
        except (ValueError, TypeError, AttributeError) as e:
            # P0.2b Fix: Malformed config in strict mode should fail, not silently pass
            raise StrictModeViolation(f"asana: malformed config under strict mode: {e}") from e
        self._require_config_value(cfg, AsanaConfig.BASE_URL, "asana")
        self._require_config_value(cfg, AsanaConfig.PROJECT_GID, "asana")

    def get_log_context(self, cfg: dict) -> dict[str, Any]:
        """Get Asana-specific logging context."""
        return {
            "has_project_gid": bool(cfg.get(AsanaConfig.PROJECT_GID)),
            "base_url": bool(cfg.get(AsanaConfig.BASE_URL)),
        }

    def build_request_record(
        self, item: Any, description: str, cfg: dict, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Build Asana API request record."""
        # Get configuration values
        base_url = cfg.get(AsanaConfig.BASE_URL)
        project_gid = cfg.get(AsanaConfig.PROJECT_GID)

        # Map labels to tag GIDs
        raw_extra = cfg.get(AsanaConfig.TAG_GIDS)
        extra_tags = [str(x) for x in raw_extra] if isinstance(raw_extra, list) else []
        raw_map = cfg.get(AsanaConfig.TAG_GIDS_BY_LABEL)
        map_by_label: dict[str, str] = (
            {str(k): str(v) for k, v in raw_map.items()} if isinstance(raw_map, dict) else {}
        )

        # Use centralized label mapping
        mapping_result = self.map_labels_to_ids(item.labels, map_by_label, extra_tags)
        tag_gids = mapping_result["mapped_ids"]

        body = {
            "data": {
                "name": item.title,
                "notes": description,
                "due_on": item.due,
                "projects": ([project_gid] if project_gid else []),
                "tags": tag_gids,
                "external": {
                    "gid": item.uid,
                    "data": f"v={item.version};h={item.content_hash}",
                },
            }
        }

        return self.build_common_record(
            method="POST",
            url="/tasks",
            base_url=base_url,
            headers=headers,
            body=body,
            uid=item.uid,
        )

    def export(
        self,
        plan: dict[str, Any],
        templates_dir: str | None = None,
        out_dir: str | None = None,
        config: dict | None = None,
        dry_run: bool | None = None,
    ) -> None:
        """Export Asana payloads and write a top-level NDJSON copy.

        Accepts keyword arguments for compatibility with tests:
        - config: exporter configuration (base_url, project_gid, headers, etc.)
        - out_dir: output directory
        - dry_run: accepted for API parity (no network calls are made)
        - templates_dir: optional templates root (unused here)
        """
        out_dir = out_dir or "."
        templates_dir = templates_dir or "."
        # Merge provided config over existing config
        # Ensure default strict=False when unspecified
        current = self._cfg()
        merged = {**(current or {})}
        if isinstance(config, dict):
            merged.update(config)
        # P0.2a Fix: Removed strict=False default override
        # Asana now inherits strict=True from BaseApiExporter._is_strict() like Jira/Linear
        self.setup(config=merged)

        # Standard API NDJSON export via base class
        super().export(plan, templates_dir, out_dir)

        # Write a top-level NDJSON copy for convenience in tests/tools
        try:
            base = Path(out_dir) / EXPORTS_DIR / self.get_target_name()
            src = base / REQUESTS_NDJSON
            dst = Path(out_dir) / f"{self.get_target_name()}_requests.ndjson"
            if src.exists():
                dst.write_text(src.read_text(encoding=DEFAULT_ENCODING), encoding=DEFAULT_ENCODING)
        except OSError:
            pass

        # Add Asana-specific CSV import file
        self._export_csv(plan, out_dir)

    def _export_csv(self, plan: dict[str, Any], out_dir: str) -> None:
        """Export Asana-friendly CSV import file."""
        items = build_task_items(plan)
        base = self._ensure_export_dir(out_dir, "asana")
        csv_path = base / "import.csv"

        # Reuse centralized fingerprint/meta via get_plan_fingerprint
        fp = get_plan_fingerprint(plan if isinstance(plan, dict) else {})

        with csv_path.open("w", newline="", encoding=DEFAULT_ENCODING) as cf:
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
                for lbl in list(it.labels):
                    if isinstance(lbl, str) and lbl.isdigit():
                        tag_names.append(f"Art.{lbl}")
                    elif isinstance(lbl, str) and lbl.startswith("P") and len(lbl) == 2:
                        tag_names.append(f"Prioridad:{lbl}")
                    else:
                        tag_names.append(str(lbl))

                name = it.title
                notes = it.description_md + f"\n\nFingerprint: {fp}\n"
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

        self._log_safe("export.asana.csv_created", {"csv": str(csv_path)})
