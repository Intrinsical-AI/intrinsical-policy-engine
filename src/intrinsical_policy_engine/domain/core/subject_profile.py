# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Canonical subject classification without naming aliases or fallback axes."""

from __future__ import annotations

from dataclasses import dataclass

from intrinsical_policy_engine.domain.contract_models import FrameworkRuntime


@dataclass(frozen=True)
class SubjectProfile:
    name: str
    framework: str
    risk_tier: str | None = None
    roles: tuple[str, ...] = ()
    regimes: tuple[str, ...] = ()
    blocked: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "framework": self.framework,
            "risk_tier": self.risk_tier,
            "roles": list(self.roles),
            "regimes": list(self.regimes),
            "blocked": self.blocked,
        }


def build_subject_profile(
    runtime: FrameworkRuntime, outcome_axes: dict, *, name: str | None = None
) -> SubjectProfile:
    return SubjectProfile(
        name=name or runtime.policies.subject_profile.default_name,
        framework=runtime.semantics.framework_id,
        risk_tier=outcome_axes["risk_tier"],
        roles=tuple(outcome_axes["roles"]),
        regimes=tuple(outcome_axes["regimes"]),
        blocked=outcome_axes["blocked"],
    )


def subject_profile_from_dict(data: dict) -> SubjectProfile:
    return SubjectProfile(
        name=data["name"],
        framework=data["framework"],
        risk_tier=data["risk_tier"],
        roles=tuple(data["roles"]),
        regimes=tuple(data["regimes"]),
        blocked=data["blocked"],
    )
