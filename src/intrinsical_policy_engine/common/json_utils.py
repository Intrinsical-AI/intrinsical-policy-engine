# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""JSON utility functions for consistent application serialization."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def json_dumps_pretty(data: Any) -> str:
    """Serialize data to pretty JSON string with consistent settings.

    Uses UTF-8 friendly settings for the application:
    - ensure_ascii=False for proper Unicode support
    - indent=2 for human-readable output

    Args:
        data: Any JSON-serializable data

    Returns:
        Pretty-formatted JSON string

    Raises:
        TypeError: If data is not JSON serializable
    """
    return json.dumps(data, ensure_ascii=False, indent=2)


def json_loads_safe(text: str) -> dict[str, Any] | None:
    """Safely parse JSON string with error handling.

    Args:
        text: JSON string to parse

    Returns:
        Parsed dictionary if successful, None otherwise
    """
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("json_loads_safe failed", {"error": str(e)})
        return None


def json_dumps_compact(data: Any) -> str:
    """Serialize data to compact JSON string.

    Args:
        data: Any JSON-serializable data

    Returns:
        Compact JSON string without indentation
    """
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
