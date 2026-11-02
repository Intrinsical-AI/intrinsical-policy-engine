# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The event-plus-data logging interface accepted by the engine."""

from __future__ import annotations

from typing import Any, Protocol


class StructuredLogger(Protocol):
    """Log an event name with an optional JSON-serializable data mapping."""

    def info(self, event: str, extra: dict[str, Any] | None = None) -> None: ...

    def warning(self, event: str, extra: dict[str, Any] | None = None) -> None: ...

    def error(self, event: str, extra: dict[str, Any] | None = None) -> None: ...
