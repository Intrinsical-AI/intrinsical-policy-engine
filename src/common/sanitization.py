# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Common sanitization utilities for answers/PII handling.

Provides a single canonical implementation used by tracer and artifact renderer
so that allowlists and redaction strategies do not diverge.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any

__all__ = ["SanitizationMode", "sanitize_value", "sanitize_payload"]


class SanitizationMode(str, Enum):
    """Supported sanitization strategies."""

    HASH = "hash"  # Non-allowlisted strings -> stable hash (trace)
    TYPE_TAG = "type_tag"  # Non-allowlisted strings -> type-tag (human readable)


SAFE_CANONICAL_VALUES = frozenset(
    {
        # Booleans / default answers
        "yes",
        "no",
        "unknown",
        "true",
        "false",
        "n/a",
        "na",
        "none",
        # Roles / risk tiers (tracer previously allowed these)
        "provider",
        "deployer",
        "importer",
        "distributor",
        "user",
        "high",
        "medium",
        "low",
        "critical",
    }
)


def sanitize_value(
    value: str | int | float | bool | list[Any] | dict[str, Any] | None,
    *,
    mode: SanitizationMode = SanitizationMode.HASH,
) -> str:
    """Sanitize a value using the provided strategy.

    Args:
        value: Value to sanitize (str, int, float, bool, list, dict, or None).
        mode: Sanitization strategy (hash vs type tag).

    Returns:
        String-safe representation without leaking PII.

    Example:
        >>> sanitize_value("yes", mode=SanitizationMode.HASH)
        'yes'
        >>> sanitize_value("sensitive data", mode=SanitizationMode.HASH)
        '[hash:...]'
        >>> sanitize_value(42, mode=SanitizationMode.TYPE_TAG)
        '[numeric]'
    """

    if value is None:
        return "[null]"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return "[numeric]"
    if isinstance(value, list):
        return f"[list:{len(value)}]"
    if isinstance(value, dict):
        return f"[object:{len(value)}]"

    # String-likes: normalize for allowlist, but keep original length when tagging
    text = str(value)
    normalized = text.strip().lower()

    if normalized in SAFE_CANONICAL_VALUES:
        return normalized

    if mode == SanitizationMode.TYPE_TAG:
        return f"[text:{len(text)}]"

    value_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"[hash:{value_hash}]"


def sanitize_payload(
    value: Any,
    *,
    mode: SanitizationMode = SanitizationMode.HASH,
) -> Any:
    """Recursively sanitize a payload while preserving structure.

    This helper is safer for wizard answers or nested payloads where we need to
    keep keys but sanitize values. Leaf values are sanitized with sanitize_value.
    """
    if isinstance(value, dict):
        return {k: sanitize_payload(v, mode=mode) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(v, mode=mode) for v in value]
    return sanitize_value(value, mode=mode)
