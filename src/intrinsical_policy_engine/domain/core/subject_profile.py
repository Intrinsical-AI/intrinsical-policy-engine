# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Framework-neutral subject profile helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intrinsical_policy_engine.domain.contract_models import (
    FrameworkRuntime,
    RuntimeProfileAttributeRule,
)


@dataclass(frozen=True)
class SubjectProfile:
    """Framework-neutral representation of the assessed subject."""

    name: str = "Unnamed Subject"
    framework: str = "unknown"
    classification_tier: str = "none"
    roles: list[str] = field(default_factory=list)
    regimes: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def get_attribute(self, key: str, default: Any = None) -> Any:
        """Return a framework-specific attribute."""
        return self.attributes.get(key, default)

    @property
    def risk_tier(self) -> str:
        """Compatibility alias for packs/templates still using risk_tier."""
        return self.classification_tier

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a flat mapping for plans and templates."""
        base = {
            "name": self.name,
            "framework": self.framework,
            "classification_tier": self.classification_tier,
            "risk_tier": self.classification_tier,
            "roles": list(self.roles),
            "regimes": list(self.regimes),
            "attributes": dict(self.attributes),
        }
        base.update(self.attributes)
        return base


def _attribute_value_from_rule(
    rule: RuntimeProfileAttributeRule,
    final_flags: set[str],
) -> Any:
    if rule.mode == "all_prefixes":
        if not rule.prefix:
            return []
        excluded = set(rule.exclude)
        return sorted(
            flag for flag in final_flags if flag.startswith(rule.prefix) and flag not in excluded
        )

    matches: list[Any] = []
    for candidate in rule.matches:
        matched = False
        if (candidate.flag and candidate.flag in final_flags) or (
            candidate.prefix and any(flag.startswith(candidate.prefix) for flag in final_flags)
        ):
            matched = True
        if matched:
            if rule.mode == "first_match":
                return candidate.value
            matches.append(candidate.value)

    if rule.mode == "all_matches":
        excluded = set(rule.exclude)
        return [value for value in matches if value not in excluded]

    return None


def build_subject_profile(
    runtime: FrameworkRuntime,
    final_flags: set[str],
    outcome_axes: dict[str, Any],
    *,
    name: str | None = None,
) -> SubjectProfile:
    """Build a SubjectProfile using runtime-defined semantics."""

    profile_cfg = runtime.policies.subject_profile
    classification_key = profile_cfg.classification_key or "risk_tier"
    attributes: dict[str, Any] = {}

    for rule in profile_cfg.attributes:
        value = _attribute_value_from_rule(rule, final_flags)
        if value in (None, [], {}, ""):
            continue
        attributes[rule.key] = value

    profile_name = name or profile_cfg.default_name or "Unnamed Subject"
    return SubjectProfile(
        name=profile_name,
        framework=runtime.semantics.framework_id,
        classification_tier=str(outcome_axes.get(classification_key) or "none"),
        roles=[str(role) for role in outcome_axes.get("roles", []) or []],
        regimes=[str(regime) for regime in outcome_axes.get("regimes", []) or []],
        attributes=attributes,
    )


def subject_profile_from_dict(data: dict[str, Any]) -> SubjectProfile:
    """Reconstruct a SubjectProfile from plan/export payloads."""

    known_keys = {
        "name",
        "framework",
        "classification_tier",
        "risk_tier",
        "roles",
        "regimes",
        "attributes",
    }
    raw_attributes = data.get("attributes")
    attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
    for key, value in data.items():
        if key not in known_keys:
            attributes[key] = value

    classification = data.get("classification_tier", data.get("risk_tier", "none"))
    return SubjectProfile(
        name=str(data.get("name") or "Unnamed Subject"),
        framework=str(data.get("framework") or "unknown"),
        classification_tier=str(classification or "none"),
        roles=[str(role) for role in data.get("roles", []) or []],
        regimes=[str(regime) for regime in data.get("regimes", []) or []],
        attributes=attributes,
    )
