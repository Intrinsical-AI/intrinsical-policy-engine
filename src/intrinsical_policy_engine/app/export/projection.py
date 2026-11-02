# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Project assessed data once, before any output writer receives it."""

from copy import deepcopy
from typing import Any


def _metadata(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: _metadata(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_metadata(item, key) for item in value]
    if value is None:
        return value
    return "[redacted]"


def project_plan(plan: dict[str, Any], *, include_raw_input: bool = False) -> dict[str, Any]:
    result = deepcopy(plan)
    if include_raw_input:
        return result
    for key in ("system", "provider", "owner", "declared_by", "approvals", "meta"):
        if key in result:
            result[key] = _metadata(result[key])
    profile = result.get("system_profile", {})
    if "name" in profile:
        profile["name"] = "[redacted]"
    raw = result.get("trace", {}).get("answers_raw", {})
    if "input" in raw and "answers" in raw["input"]:
        raw["input"]["answers"] = _metadata(raw["input"]["answers"])
    for key in ("answers", "system", "provider", "owner", "declared_by", "approvals", "meta"):
        if key in raw:
            raw[key] = _metadata(raw[key])
    return result
