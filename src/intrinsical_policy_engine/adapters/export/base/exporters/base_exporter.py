# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Shared exporter setup and calendars with stable action identities."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import stable_uid
from intrinsical_policy_engine.adapters.logging import StructuredLogger, adapt_logger
from intrinsical_policy_engine.app.config.constants import ICS_FILE


def _escape_ics(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


def _fold_ics(line: str) -> str:
    """Fold at 75 octets without splitting a Unicode character."""
    result = []
    current = ""
    length = 0
    for character in line:
        size = len(character.encode("utf-8"))
        if length + size > 75:
            result.append(current)
            current, length = " ", 1
        current += character
        length += size
    result.append(current)
    return "\r\n".join(result)


class BaseExporter:
    def __init__(self) -> None:
        self._logger: StructuredLogger | None = None
        self._config: dict[str, Any] = {}

    def setup(
        self,
        logger: StructuredLogger | logging.Logger | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._logger = adapt_logger(logger)
        self._config = dict(config or {})

    def export_ics(self, plan: dict[str, Any], root: Path, *, prodid: str) -> Path | None:
        """Export every authored deadline, failing on malformed dates or writes."""
        deadlines = plan.get("due_hints", {})
        if not deadlines:
            return None
        namespace = plan["export_context"]["uid_namespace"]
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            _fold_ics(f"PRODID:{_escape_ics(prodid)}"),
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
        ]
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        for action_id, due in sorted(deadlines.items()):
            deadline = date.fromisoformat(due)
            if deadline.isoformat() != due:
                raise ValueError(f"Deadline is not a canonical ISO date: {action_id}={due}")
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"DTSTAMP:{timestamp}",
                    f"DTSTART;VALUE=DATE:{due.replace('-', '')}",
                    "DURATION:P1D",
                    _fold_ics(f"SUMMARY:{_escape_ics(f'Compliance - {action_id} deadline')}"),
                    _fold_ics(f"UID:{stable_uid(namespace, action_id)}"),
                    "STATUS:CONFIRMED",
                    "SEQUENCE:0",
                    "END:VEVENT",
                ]
            )
        lines.append("END:VCALENDAR")
        target = root / ICS_FILE
        with target.open("x", encoding="utf-8", newline="") as stream:
            stream.write("\r\n".join(lines) + "\r\n")
        return target
