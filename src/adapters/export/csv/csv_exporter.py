# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""CSV exporter for machine-readable backlog output.

The exporter writes the normalized task rows used by backlog-oriented workflows.
"""

import csv
from pathlib import Path
from typing import Any

from src.adapters.export.base.evidence.task_graph import build_task_items
from src.app.config.artifact_names import BACKLOG_CSV_FILE
from src.app.config.constants import DEFAULT_ENCODING


class CsvExporter:
    """Minimal exporter that materializes task records as a CSV file."""

    def export(self, plan: dict[str, Any], out_dir: str, *, strict: bool | None = None) -> None:
        """Write backlog CSV using normalized task items."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        items = build_task_items(plan)

        # Map action_id -> meta to drive filtering and validation
        actions_meta = plan.get("actions_meta") or []
        meta_map = {str(a.get("id")): a for a in actions_meta if isinstance(a, dict)}

        def _is_task_action(aid: str | None) -> bool:
            meta = meta_map.get(str(aid)) or {}
            # Explicit opt-out for non-task/advisory actions
            if str(meta.get("type", "")).lower() in {"advisory", "reminder"}:
                return False
            is_task = meta.get("is_task")
            if isinstance(is_task, bool) and not is_task:
                return False
            # Heuristic: titles starting with "Recordatorio:" are reminders, not tasks
            title = str(meta.get("title") or "").strip().lower()
            return not title.startswith("recordatorio:")

        # Hardening: drop advisory/reminder rows from machine-readable backlog
        items = [it for it in items if _is_task_action(it.action_id)]

        # Deterministic ordering: by first article (numeric if possible), then by action_id
        def _first_art_num(it) -> tuple[int, object]:
            first = it.article_ids[0] if it.article_ids else None
            if isinstance(first, str) and first.isdigit():
                try:
                    return (0, int(first))
                except ValueError:
                    pass
            return (1, str(first) if first is not None else "~")

        items.sort(key=lambda it: (_first_art_num(it), str(it.action_id)))
        if strict:
            if not items:
                raise ValueError("csv: strict mode requires at least one task item")
            # Enforce due dates for critical-priority tasks
            missing_due: list[str] = []
            for it in items:
                meta = meta_map.get(str(it.action_id)) or {}
                priority = str(meta.get("priority") or "").lower()
                if priority == "critical" and not it.due:
                    missing_due.append(str(it.action_id))
            if missing_due:
                missing_list = ", ".join(sorted(missing_due))
                raise ValueError(
                    "csv: strict mode requires due_hint for critical tasks; missing for: "
                    f"{missing_list}"
                )
        rows = []
        # Build a CSV-friendly title: "[<first or ...>] <meta_title or id> (<id>)"
        # Read meta titles from plan when available for fidelity
        for it in items:
            meta = meta_map.get(str(it.action_id)) or {}
            title_src = meta.get("title") or str(it.action_id)
            prefix = f"[{it.article_ids[0]}]" if it.article_ids else "[...]"
            cell_title = f"{prefix} {title_src} ({it.action_id})"
            rows.append(
                {
                    "action_id": it.action_id or "",
                    "uid": it.uid or "",
                    "title": cell_title,
                    "due_hint": it.due or "",
                    "priority": it.priority or "",
                    "applies_to": it.applies_to or "",
                    "content_hash": it.content_hash or "",
                    "version": it.version or "",
                }
            )

        with open(out / BACKLOG_CSV_FILE, "w", newline="", encoding=DEFAULT_ENCODING) as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "action_id",
                    "uid",
                    "title",
                    "due_hint",
                    "priority",
                    "applies_to",
                    "content_hash",
                    "version",
                ],
                quoting=csv.QUOTE_ALL,
            )
            w.writeheader()
            w.writerows(rows)
