# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Strictness policy configuration.

This module decomposes the overloaded `strict` boolean into granular,
documented failure policies. Each policy flag controls a specific
validation behavior.

Usage:
    # Use preset profiles
    policy = StrictnessPolicy.ci()  # Strict for CI
    policy = StrictnessPolicy.dev()  # Lenient for development

    # Or customize
    policy = StrictnessPolicy(
        fail_on_missing_template_var=True,
        fail_on_evidence_gaps=False,
    )
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import ClassVar


class StrictnessProfile(Enum):
    """Predefined strictness profiles for common use cases."""

    DEV = "dev"  # Development: lenient, fast feedback
    CI = "ci"  # CI/CD: strict on schema, lenient on evidence
    AUDIT = "audit"  # Audit: strict on everything
    CUSTOM = "custom"  # User-defined


@dataclass
class StrictnessPolicy:
    """Granular strictness configuration.

    Each flag controls whether a specific validation failure should
    raise an error (True) or be logged as a warning (False).

    Attributes:
        profile: The profile this policy represents
        fail_on_missing_template_var: Fail if Jinja template has undefined variables
        fail_on_schema_mismatch: Fail if YAML doesn't match JSON schema
        fail_on_evidence_gaps: Fail if required evidence files are missing
        fail_on_unmapped_flags: Fail if flags in conditions aren't defined
        fail_on_broken_refs: Fail if cross-references are broken (action→article)
        fail_on_cycle_detection: Fail if derivation graph has cycles
        fail_on_contract_violation: Fail if framework contract is violated
        fail_on_quality_threshold: Fail if evidence quality below threshold
        warn_on_deprecated: Emit warnings for deprecated features
    """

    # Profile identifier
    profile: StrictnessProfile = StrictnessProfile.CUSTOM

    # Template rendering
    fail_on_missing_template_var: bool = True

    # Schema validation
    fail_on_schema_mismatch: bool = True

    # Evidence validation
    fail_on_evidence_gaps: bool = False
    fail_on_quality_threshold: bool = False

    # Contract validation
    fail_on_unmapped_flags: bool = False
    fail_on_broken_refs: bool = True
    fail_on_cycle_detection: bool = True
    fail_on_contract_violation: bool = False

    # Deprecation
    warn_on_deprecated: bool = True

    # Class-level preset definitions
    _PRESETS: ClassVar[dict[StrictnessProfile, dict]] = {
        StrictnessProfile.DEV: {
            "profile": StrictnessProfile.DEV,
            "fail_on_missing_template_var": False,
            "fail_on_schema_mismatch": True,
            "fail_on_evidence_gaps": False,
            "fail_on_unmapped_flags": False,
            "fail_on_broken_refs": False,
            "fail_on_cycle_detection": True,
            "fail_on_contract_violation": False,
            "fail_on_quality_threshold": False,
            "warn_on_deprecated": True,
        },
        StrictnessProfile.CI: {
            "profile": StrictnessProfile.CI,
            "fail_on_missing_template_var": True,
            "fail_on_schema_mismatch": True,
            "fail_on_evidence_gaps": False,
            "fail_on_unmapped_flags": True,
            "fail_on_broken_refs": True,
            "fail_on_cycle_detection": True,
            "fail_on_contract_violation": True,
            "fail_on_quality_threshold": False,
            "warn_on_deprecated": True,
        },
        StrictnessProfile.AUDIT: {
            "profile": StrictnessProfile.AUDIT,
            "fail_on_missing_template_var": True,
            "fail_on_schema_mismatch": True,
            "fail_on_evidence_gaps": True,
            "fail_on_unmapped_flags": True,
            "fail_on_broken_refs": True,
            "fail_on_cycle_detection": True,
            "fail_on_contract_violation": True,
            "fail_on_quality_threshold": True,
            "warn_on_deprecated": True,
        },
    }

    @classmethod
    def dev(cls) -> StrictnessPolicy:
        """Create a development-friendly lenient policy."""
        return cls(**cls._PRESETS[StrictnessProfile.DEV])

    @classmethod
    def ci(cls) -> StrictnessPolicy:
        """Create a CI/CD-appropriate strict policy."""
        return cls(**cls._PRESETS[StrictnessProfile.CI])

    @classmethod
    def audit(cls) -> StrictnessPolicy:
        """Create an audit-grade strict policy."""
        return cls(**cls._PRESETS[StrictnessProfile.AUDIT])

    @classmethod
    def from_profile_name(cls, name: str) -> StrictnessPolicy:
        """Create policy from profile name string.

        Args:
            name: Profile name ("dev", "ci", "audit")

        Returns:
            Corresponding policy

        Raises:
            ValueError: If profile name is unknown
        """
        name_lower = name.lower()
        if name_lower == "dev":
            return cls.dev()
        elif name_lower == "ci":
            return cls.ci()
        elif name_lower == "audit":
            return cls.audit()
        else:
            raise ValueError(f"Unknown strictness profile: {name}")

    def with_override(self, **kwargs) -> StrictnessPolicy:
        """Create a new policy with specific overrides.

        Args:
            **kwargs: Fields to override

        Returns:
            New policy with overrides applied
        """
        current = asdict(self)
        current.update(kwargs)
        current["profile"] = StrictnessProfile.CUSTOM
        return StrictnessPolicy(**current)

    def summary(self) -> dict[str, bool]:
        """Return a summary of all failure flags.

        Returns:
            Dict mapping flag names to their values
        """
        return {
            "fail_on_missing_template_var": self.fail_on_missing_template_var,
            "fail_on_schema_mismatch": self.fail_on_schema_mismatch,
            "fail_on_evidence_gaps": self.fail_on_evidence_gaps,
            "fail_on_unmapped_flags": self.fail_on_unmapped_flags,
            "fail_on_broken_refs": self.fail_on_broken_refs,
            "fail_on_cycle_detection": self.fail_on_cycle_detection,
            "fail_on_contract_violation": self.fail_on_contract_violation,
            "fail_on_quality_threshold": self.fail_on_quality_threshold,
            "warn_on_deprecated": self.warn_on_deprecated,
        }


# Default policy
DEFAULT_POLICY = StrictnessPolicy.dev()
