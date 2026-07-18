# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
from __future__ import annotations

from typing import Literal, TypeAlias

# Recursive JSON type without Any; supports nested lists and dicts.
JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]

# Canonical controlled outcome type. Extend it to match the real taxonomy.
OutcomeType: TypeAlias = Literal[
    "blocked",
    "review",
    "limited_risk",
    "out_of_scope",
    "unknown",
]

# Residual risk levels used by simulation heuristics.
ResidualRiskLevel: TypeAlias = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Origin of a scenario patch.
ChangeOrigin: TypeAlias = Literal[
    "user_hypothesis",
    "heuristic_optimization",
    "forced_assumption",
]
