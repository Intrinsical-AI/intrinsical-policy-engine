# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bundle loading and validation orchestration (Application layer).

This service coordinates loading and validating complete bundles with
proper separation of concerns:
- I/O: YamlContractsAdapter
- Domain validation: BundleEvidenceValidator
- Application orchestration: This module
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.quality.bundle_evidence_validator import (
    BundleEvidenceValidator,
    ValidationReport,
)
from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.contract_models import ContractBundle
from intrinsical_policy_engine.domain.exceptions import StrictContractViolation

logger = logging.getLogger(__name__)


@dataclass
class CompleteBundleResult:
    """Result of loading and validating a complete bundle."""

    contract_bundle: ContractBundle
    bundle_profiles: dict[str, BundleProfile]
    validation_report: ValidationReport

    def is_valid_for_strict_mode(self) -> bool:
        """Return True if bundle is valid for strict mode operations."""
        return not self.validation_report.has_errors()


class BundleOrchestrator:
    """Orchestrates complete bundle loading and validation.

    This service implements the clean architecture approach:
    1. Load files via adapters (I/O layer)
    2. Validate semantics via domain services
    3. Coordinate and decide policy at application layer
    """

    def __init__(
        self,
        *,
        strict: bool = True,
        tolerate_questions_errors: bool = False,
    ):
        """Initialize orchestrator with strictness policy."""
        self.strict = strict
        # In strict mode, fail fast on any contract validation errors.
        self.adapter = YamlContractsAdapter(
            strict=self.strict,
            tolerate_questions_errors=tolerate_questions_errors,
        )
        self.evidence_validator = BundleEvidenceValidator()

    def load_and_validate_complete_bundle(self, contracts_path: str | Path) -> CompleteBundleResult:
        """Load and validate a complete bundle with all integrity checks.

        Args:
            contracts_path: Path to framework pack directory

        Returns:
            CompleteBundleResult with bundle, profiles, and validation report

        Raises:
            StrictContractViolation: If strict mode and validation fails
            Various I/O exceptions: If files cannot be loaded
        """
        contracts_path = Path(contracts_path)

        # Step 1: I/O - Load contract bundle and profiles (no cross-validation)
        logger.debug(f"Loading framework pack bundle from {contracts_path}")
        contract_bundle = self.adapter.load(str(contracts_path))

        logger.debug(f"Loading bundle profiles from {contracts_path}")
        bundle_profiles = self.adapter.load_bundle_profiles(str(contracts_path))

        # Step 2: Domain validation - Cross-reference integrity (INV-B2)
        logger.debug("Validating bundle evidence integrity (INV-B2)")
        validation_report = self.evidence_validator.validate_integrity(
            bundle_profiles, contract_bundle
        )

        # Step 3: Application policy - Fail-hard in strict mode
        if self.strict and validation_report.has_errors():
            error_summary = validation_report.summary()
            logger.error(f"Bundle validation failed in strict mode: {error_summary}")
            raise StrictContractViolation(
                f"Bundle evidence validation failed (INV-B2): {error_summary}",
                error_count=len(validation_report.problems),
                critical_errors=validation_report.problems,
            )

        if validation_report.has_errors():
            logger.warning(
                f"Bundle validation issues (tolerant mode): {validation_report.summary()}"
            )
        else:
            logger.debug("Bundle validation passed")

        return CompleteBundleResult(
            contract_bundle=contract_bundle,
            bundle_profiles=bundle_profiles,
            validation_report=validation_report,
        )
