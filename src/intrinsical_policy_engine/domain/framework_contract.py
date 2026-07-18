# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Framework-engine contract validation.

This module defines the contract between a compliance framework pack and the
engine that processes it. It ensures that:

1. The framework declares which flag prefixes it uses
2. Required derivations exist for the engine's classification logic
3. The framework version is compatible with the engine version
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION


@dataclass(frozen=True)
class FrameworkContract:
    """Declares the contract a framework pack must fulfill for engine compatibility.

    Attributes:
        name: Framework identifier (e.g., "starter")
        min_engine_version: Minimum engine version required
        dsl_version: DSL grammar version this framework was written for
        required_flag_prefixes: Flag prefixes the engine expects.
        required_derivation_patterns: Derivation IDs or patterns the engine depends on
        required_classifier_tiers: Risk tiers that must exist in classifiers
    """

    name: str
    min_engine_version: str = CANONICAL_ENGINE_VERSION
    dsl_version: str = "1.0.0"
    required_flag_prefixes: tuple[str, ...] = field(default_factory=tuple)
    required_derivation_patterns: tuple[str, ...] = field(default_factory=tuple)
    required_classifier_tiers: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ContractViolation:
    """A single contract violation."""

    code: str
    severity: str  # "error" | "warning"
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class FrameworkContractValidator:
    """Validates a framework bundle against its contract."""

    def __init__(self, contract: FrameworkContract):
        self.contract = contract

    def validate(self, bundle: Any) -> list[ContractViolation]:
        """Validate bundle against contract.

        Args:
            bundle: ContractBundle or similar with flags, rules, etc.

        Returns:
            List of violations (empty if valid).
        """
        violations: list[ContractViolation] = []

        violations.extend(self._check_flag_prefixes(bundle))
        violations.extend(self._check_derivations(bundle))
        violations.extend(self._check_classifier_tiers(bundle))
        violations.extend(self._check_version_compatibility(bundle))

        return violations

    def _check_flag_prefixes(self, bundle: Any) -> list[ContractViolation]:
        """Check that required flag prefixes are present."""
        violations: list[ContractViolation] = []

        flags = getattr(bundle, "flags", None)
        if flags is None or not hasattr(flags, "registry"):
            raise TypeError("bundle.flags must be a FlagsContract")

        flags_registry = flags.registry
        defined_prefixes: set[str] = set()

        for flag in flags_registry:
            if not hasattr(flag, "id"):
                raise TypeError("bundle.flags.registry entries must be FlagDefinition models")
            flag_id = flag.id
            if "." in flag_id:
                prefix = flag_id.split(".")[0] + "."
                defined_prefixes.add(prefix)

        for required_prefix in self.contract.required_flag_prefixes:
            if required_prefix not in defined_prefixes:
                violations.append(
                    ContractViolation(
                        code="CONTRACT-001",
                        severity="error",
                        message=f"Missing required flag prefix: {required_prefix}",
                        details={"prefix": required_prefix},
                    )
                )

        return violations

    def _check_derivations(self, bundle: Any) -> list[ContractViolation]:
        """Check that required derivations exist."""
        violations: list[ContractViolation] = []

        rules = getattr(bundle, "rules", None)
        if rules is None or not hasattr(rules, "derivations"):
            raise TypeError("bundle.rules must be a RulesContract")

        derivation_ids = set()
        for derivation in rules.derivations:
            if not hasattr(derivation, "id"):
                raise TypeError("bundle.rules.derivations entries must be Derivation models")
            derivation_ids.add(derivation.id)

        for required_pattern in self.contract.required_derivation_patterns:
            # Exact match or pattern match
            found = any(
                did == required_pattern or (did and did.startswith(required_pattern))
                for did in derivation_ids
            )
            if not found:
                violations.append(
                    ContractViolation(
                        code="CONTRACT-002",
                        severity="error",
                        message=f"Missing required derivation: {required_pattern}",
                        details={"pattern": required_pattern},
                    )
                )

        return violations

    def _check_classifier_tiers(self, bundle: Any) -> list[ContractViolation]:
        """Check that required classifier tiers are defined."""
        violations: list[ContractViolation] = []

        rules = getattr(bundle, "rules", None)
        if rules is None or not hasattr(rules, "classifiers"):
            raise TypeError("bundle.rules must be a RulesContract")
        classifiers = rules.classifiers or {}
        risk_tiers = classifiers.get("risk_tiers", []) if isinstance(classifiers, dict) else []

        defined_tiers: set[str] = set()
        for tier in risk_tiers:
            output = tier.get("output", {})
            if "tier" in output:
                defined_tiers.add(output["tier"])

        for required_tier in self.contract.required_classifier_tiers:
            if required_tier not in defined_tiers:
                violations.append(
                    ContractViolation(
                        code="CONTRACT-003",
                        severity="warning",
                        message=f"Missing classifier tier: {required_tier}",
                        details={"tier": required_tier},
                    )
                )

        return violations

    def _check_version_compatibility(self, bundle: Any) -> list[ContractViolation]:
        """Check framework version compatibility using robust semantic comparison."""
        violations: list[ContractViolation] = []

        # Try to get version from bundle metadata populated by adapter
        framework_meta = getattr(bundle, "metadata", {}) or {}

        engine_meta: dict[str, Any] = {}
        if isinstance(framework_meta, dict):
            engine_meta = framework_meta.get("engine_compatibility", {}) or {}

        if not engine_meta:
            violations.append(
                ContractViolation(
                    code="CONTRACT-004",
                    severity="error",
                    message=(
                        "Framework metadata missing engine_compatibility; "
                        "cannot verify minimum engine version"
                    ),
                    details={"expected_engine": self.contract.min_engine_version},
                )
            )
            return violations

        min_version_str_raw = engine_meta.get("min_version", "0.0.0")
        min_version_str = str(min_version_str_raw).lstrip(">=").strip()

        def _parse_ver(v: str) -> tuple[int, ...]:
            try:
                return tuple(map(int, v.split(".")))
            except ValueError:
                return (0, 0, 0)

        required_ver = _parse_ver(min_version_str)
        current_ver = _parse_ver(self.contract.min_engine_version)

        if required_ver > current_ver:
            violations.append(
                ContractViolation(
                    code="CONTRACT-004",
                    severity="error",
                    message=(
                        f"Framework requires engine >= {min_version_str}, "
                        f"but current engine is {self.contract.min_engine_version}"
                    ),
                    details={
                        "framework_requires": min_version_str,
                        "current_engine": self.contract.min_engine_version,
                    },
                )
            )

        # DSL compatibility (major version check)
        declared_dsl = engine_meta.get("dsl_version")
        if declared_dsl:
            required_dsl = _parse_ver(str(declared_dsl))
            contract_dsl = _parse_ver(self.contract.dsl_version)
            if required_dsl and contract_dsl and required_dsl[0] != contract_dsl[0]:
                violations.append(
                    ContractViolation(
                        code="CONTRACT-005",
                        severity="error",
                        message=(
                            f"Framework DSL major version {declared_dsl} "
                            f"incompatible with engine DSL {self.contract.dsl_version}"
                        ),
                        details={
                            "framework_dsl": declared_dsl,
                            "engine_dsl": self.contract.dsl_version,
                        },
                    )
                )

        return violations


def validate_framework_contract(
    bundle: Any,
    contract: FrameworkContract,
) -> list[ContractViolation]:
    """Convenience function to validate a bundle against its contract.

    Args:
        bundle: ContractBundle to validate
        contract: Contract to validate against.

    Returns:
        List of violations (empty if valid).
    """
    validator = FrameworkContractValidator(contract)
    return validator.validate(bundle)
