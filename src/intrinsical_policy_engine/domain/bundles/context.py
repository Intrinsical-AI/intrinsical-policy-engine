# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Evaluation context for bundle predicates (Domain 3)."""

from dataclasses import dataclass, field
from typing import Any

from intrinsical_policy_engine.domain.core.subject_profile import SubjectProfile
from intrinsical_policy_engine.domain.types import Plan


@dataclass(frozen=True)
class EvalContext:
    """Context passed to predicates for evaluation."""

    plan: Plan
    system_profile: SubjectProfile
    flags: dict[str, bool]

    # Optional extras (e.g. for future extensions)
    extras: dict[str, Any] = field(default_factory=dict)
