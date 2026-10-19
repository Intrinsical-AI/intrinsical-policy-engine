# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The projected plan and compiled extras used by pure template builders."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalContext:
    plan: dict[str, Any]
    extras: dict[str, Any] = field(default_factory=dict)
