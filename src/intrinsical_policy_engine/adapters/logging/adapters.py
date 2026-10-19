# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Adapter to convert standard logging.Logger to StructuredLogger protocol.

This module provides adapters to bridge the gap between different logging
interfaces, allowing the codebase to use a consistent StructuredLogger protocol
while supporting both FsLogger and standard logging.Logger implementations.
"""

from __future__ import annotations

import logging
from typing import Any

from intrinsical_policy_engine.adapters.logging.protocol import StructuredLogger


class StdlibLoggerAdapter:
    """Adapter that converts logging.Logger to StructuredLogger protocol.

    This adapter wraps a standard logging.Logger and provides the StructuredLogger
    interface (event + data dict), retaining data under one LogRecord attribute
    so authored keys cannot replace standard logging attributes.

    Example:
        >>> import logging
        >>> std_logger = logging.getLogger(__name__)
        >>> adapter = StdlibLoggerAdapter(std_logger)
        >>> adapter.info("event.name", {"key": "value"})
        # Logs with extra={"data": {"key": "value"}}
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize adapter with a standard logging.Logger.

        Args:
            logger: Standard Python logging.Logger instance to wrap.
        """
        self._logger = logger

    def info(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log INFO-level event using standard logging API.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._logger.info(event, extra={"data": extra if extra is not None else {}})

    def warning(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log WARNING-level event using standard logging API.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._logger.warning(event, extra={"data": extra if extra is not None else {}})

    def error(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log ERROR-level event using standard logging API.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._logger.error(event, extra={"data": extra if extra is not None else {}})

    def debug(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log DEBUG-level event using standard logging API.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._logger.debug(event, extra={"data": extra if extra is not None else {}})


def adapt_logger(logger: logging.Logger | StructuredLogger | None) -> StructuredLogger | None:
    """Convert a logger to StructuredLogger protocol if needed.

    This helper function:
    - Returns None if logger is None
    - Returns logger as-is if it already implements StructuredLogger (e.g., FsLogger)
    - Wraps logging.Logger instances in StdlibLoggerAdapter

    Args:
        logger: Logger instance (logging.Logger, FsLogger, or None).

    Returns:
        StructuredLogger instance or None.

    Example:
        >>> import logging
        >>> std_logger = logging.getLogger(__name__)
        >>> adapted = adapt_logger(std_logger)
        >>> # adapted now implements StructuredLogger protocol
    """
    if logger is None:
        return None

    # First check: if it's explicitly a logging.Logger, wrap it immediately
    # This avoids expensive signature inspection for the common case
    if isinstance(logger, logging.Logger):
        return StdlibLoggerAdapter(logger)

    return logger
