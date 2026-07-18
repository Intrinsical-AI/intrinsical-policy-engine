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
    interface (event + data dict) by converting calls to the standard logging API
    using the 'extra' parameter.

    Example:
        >>> import logging
        >>> std_logger = logging.getLogger(__name__)
        >>> adapter = StdlibLoggerAdapter(std_logger)
        >>> adapter.info("event.name", {"key": "value"})
        # Logs with extra={"key": "value"} using standard logging API
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
        self._logger.info(event, extra=extra or {})

    def warning(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log WARNING-level event using standard logging API.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._logger.warning(event, extra=extra or {})

    def error(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log ERROR-level event using standard logging API.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._logger.error(event, extra=extra or {})

    def debug(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log DEBUG-level event using standard logging API.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._logger.debug(event, extra=extra or {})


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

    # Second check: if it already implements StructuredLogger protocol
    # (FsLogger and other structured loggers will pass this check)
    if hasattr(logger, "info") and hasattr(logger, "warning"):
        # Try to detect if it's a standard logging.Logger by checking signature
        # Standard logging.Logger.info accepts *args, not just (msg, extra=...)
        import inspect

        try:
            sig = inspect.signature(logger.info)
            # If it has *args or **kwargs, it's likely standard logging.Logger
            params = list(sig.parameters.values())
            if len(params) > 1 and any(
                p.kind == inspect.Parameter.VAR_POSITIONAL
                or p.kind == inspect.Parameter.VAR_KEYWORD
                for p in params
            ):
                # Object has logging.Logger-like signature, wrap it
                return StdlibLoggerAdapter(logger)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            # If signature inspection fails, assume it's already compatible
            pass

        # Assume it's already a StructuredLogger (e.g., FsLogger)
        return logger  # type: ignore[return-value]

    # Unknown type, return as-is (may cause runtime errors, but preserves existing behavior)
    return logger  # type: ignore[return-value]
