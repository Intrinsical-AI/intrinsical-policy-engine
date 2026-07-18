# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pre-export validation service (Red Team Fix: Anti-Placeholders).

This module validates that export context has all required fields
before rendering templates. In strict mode, missing critical fields
cause the export to fail fast.
"""

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ExportValidationResult:
    """Result of export context validation.

    Attributes:
        valid: True if export can proceed (no critical issues in strict mode)
        missing_critical: Fields that block export in strict mode
        missing_warnings: Fields that produce warnings but don't block
    """

    valid: bool
    missing_critical: list[str]
    missing_warnings: list[str]


# Critical fields that must be present for production exports
# Format: (dotted.path, human_readable_label)
CRITICAL_FIELDS: list[tuple[str, str]] = [
    ("system.name", "AI System Name"),
    ("meta.generated_at", "Generation timestamp"),
]

# Warning fields that should be present but don't block export
WARNING_FIELDS: list[tuple[str, str]] = [
    ("approvals.by", "Approver name"),
    ("approvals.date", "Approval date"),
]

# Patterns that indicate a placeholder value, not real data
PLACEHOLDER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\[REQUIRED:\s*[^\]]+\]", re.IGNORECASE),
    re.compile(r"\[FILL:\s*[^\]]+\]", re.IGNORECASE),
    re.compile(r"\[TODO:\s*[^\]]+\]", re.IGNORECASE),
]


def _get_nested_value(obj: dict[str, Any], path: str) -> Any:
    """Get a value from a nested dict using dotted path notation.

    Args:
        obj: The dictionary to search
        path: Dotted path like "system.name"

    Returns:
        The value at the path, or None if not found
    """
    parts = path.split(".")
    current: Any = obj

    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None

    return current


def _is_placeholder(value: Any) -> bool:
    """Check if a value is a placeholder (not real data).

    Args:
        value: The value to check

    Returns:
        True if the value matches a placeholder pattern
    """
    if value is None:
        return True

    if not isinstance(value, str):
        return False

    value_str = str(value).strip()

    if not value_str:
        return True

    return any(pattern.search(value_str) for pattern in PLACEHOLDER_PATTERNS)


def validate_export_context(
    context: dict[str, Any],
    strict: bool = True,
) -> ExportValidationResult:
    """Validate that export context has all required fields.

    Args:
        context: The template context dictionary
        strict: If True, missing critical fields cause validation to fail

    Returns:
        ExportValidationResult with validation status and missing fields
    """
    missing_critical: list[str] = []
    missing_warnings: list[str] = []

    # Check critical fields
    for path, _label in CRITICAL_FIELDS:
        value = _get_nested_value(context, path)
        if _is_placeholder(value):
            missing_critical.append(path)

    # Check warning fields
    for path, _label in WARNING_FIELDS:
        value = _get_nested_value(context, path)
        if _is_placeholder(value):
            missing_warnings.append(path)

    # In strict mode, critical fields block export
    # In non-strict mode, critical fields become warnings
    if strict:
        valid = len(missing_critical) == 0
    else:
        # Move critical to warnings in non-strict mode
        missing_warnings.extend(missing_critical)
        missing_critical = []
        valid = True

    return ExportValidationResult(
        valid=valid,
        missing_critical=missing_critical,
        missing_warnings=missing_warnings,
    )
