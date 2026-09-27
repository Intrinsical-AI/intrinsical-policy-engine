# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Placeholder validation helpers shared across the application."""

from __future__ import annotations

from typing import Any

# Fields that MUST be filled for production exports
REQUIRED_FIELDS = [
    ("system", "name"),
    ("provider", "name"),
]

# Pattern for detecting unfilled placeholders
PLACEHOLDER_PATTERN = "[REQUIRED:"

# Generic system names that indicate unconfigured exports
GENERIC_SYSTEM_NAMES = frozenset(
    {
        "Sistema AI",
        "Sistema AI (sin configurar)",
        "[System name]",
        "[REQUIRED: AI System Name]",
        "",
    }
)


def _scan_for_placeholders(obj: str | dict[str, Any] | list[Any] | None, prefix: str) -> list[str]:
    """Recursively scan for unfilled placeholders in nested structure."""
    errors: list[str] = []

    if isinstance(obj, str) and PLACEHOLDER_PATTERN in obj:
        errors.append(f"Unfilled placeholder at '{prefix}': {obj}")
    elif isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}.{key}" if prefix else str(key)
            errors.extend(_scan_for_placeholders(value, new_prefix))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_prefix = f"{prefix}[{i}]"
            errors.extend(_scan_for_placeholders(item, new_prefix))

    return errors


def validate_required_context_fields(
    ctx: dict[str, Any], *, strict: bool = False
) -> tuple[bool, list[str]]:
    """Validate that required context fields are filled (not placeholders)."""
    errors: list[str] = []

    for path in REQUIRED_FIELDS:
        value = ctx
        for key in path:
            if isinstance(value, dict):
                value = value.get(key, "")
            else:
                value = ""
                break

        if isinstance(value, str) and PLACEHOLDER_PATTERN in value:
            field_path = ".".join(path)
            errors.append(f"Required field '{field_path}' contains unfilled placeholder: {value}")

    system_name = ctx.get("system", {}).get("name", "")
    if system_name in GENERIC_SYSTEM_NAMES:
        errors.append(
            f"system.name is generic or unconfigured: '{system_name}'. "
            "Configure answers.json with a specific system name for production exports."
        )

    if strict:
        additional = _scan_for_placeholders(ctx, prefix="")
        for msg in additional:
            if msg not in errors:
                errors.append(msg)

    return len(errors) == 0, errors
