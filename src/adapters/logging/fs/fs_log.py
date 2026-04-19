# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Minimal JSONL logger for reproducible CLI/automation runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.app.config.constants import DEFAULT_ENCODING


class FsLogger:
    """Append-only JSONL logger for deterministic local auditing."""

    def __init__(self, path: str | Path) -> None:
        """Create the log file path and ensure parent directories exist."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, level: str, event: str, extra: dict[str, Any] | None = None) -> None:
        """Write a structured record with ISO timestamp and metadata."""
        rec = {
            "ts": (datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")),
            "level": level,
            "event": event,
            "data": extra or {},
        }
        with self.path.open("a", encoding=DEFAULT_ENCODING) as f:
            # Use default=str to ensure non-serializable objects (e.g. Path) are logged safely
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def info(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log an INFO-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("INFO", event, extra)

    def warning(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log a WARN-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("WARN", event, extra)

    def error(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log an ERROR-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("ERROR", event, extra)

    def debug(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log a DEBUG-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("DEBUG", event, extra)
