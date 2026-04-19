# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Protocol definition for structured logging interface.

This module defines the StructuredLogger protocol that enforces a consistent
logging interface across the codebase, preventing bugs from mixing different
logger implementations (e.g., FsLogger vs logging.Logger).
"""

from __future__ import annotations

from typing import Any, Protocol


class StructuredLogger(Protocol):
    """Protocol for structured logging with event + data dictionary API.

    This protocol enforces a consistent logging interface where:
    - Methods accept an event string and optional data dictionary
    - This differs from logging.Logger which uses msg + *args + **kwargs

    Implementations:
    - FsLogger: Native implementation with this API
    - StdlibLoggerAdapter: Adapts logging.Logger to this interface
    """

    def info(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log an INFO-level event with structured data.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        ...

    def warning(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log a WARNING-level event with structured data.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        ...

    def error(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log an ERROR-level event with structured data.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        ...

    def debug(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log a DEBUG-level event with structured data.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        ...
