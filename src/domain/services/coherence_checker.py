# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Coherence checking service: detects potential inconsistencies in flags and roles.

This module provides functions to detect logical inconsistencies or
suspicious combinations of flags/roles that may indicate user error
or require manual review.
"""

from __future__ import annotations

from src.domain.types import Flag


def compute_flag_coherence_warnings(final_flags: set[Flag]) -> list[str]:
    """Detect potential inconsistencies in flag combinations.

    Checks for:
    - Sensitive-domain flags without classification
    - Reduced-review exceptions with sensitive-domain flags
    - Reduced-review exceptions without profiling flags

    Args:
        final_flags: Final expanded set of flags.

    Returns:
        List of warning messages for detected issues.

    Example:
        >>> flags = {"use.scoring"}
        >>> warnings = compute_flag_coherence_warnings(flags)
        >>> len(warnings) > 0
        True
        >>> flags_with_classification = {"use.scoring", "classification.employment"}
        >>> warnings = compute_flag_coherence_warnings(flags_with_classification)
        >>> len(warnings) == 0
        True
    """
    sensitive_domain_flags = {
        "use.control_scoring",
        "use.sensitive_domain_risk",
        "impact_review.sensitive_process",
    }
    has_sensitive_domain_flag = any(f in final_flags for f in sensitive_domain_flags)
    has_any_classification = any(f.startswith("classification.") for f in final_flags)

    warnings: list[str] = []

    if has_sensitive_domain_flag and not has_any_classification:
        warnings.append(
            "flag_coherence.sensitive_without_classification: sensitive-domain use detected "
            "without classification flags; review scope answers."
        )

    if "classification.not_review" in final_flags and has_sensitive_domain_flag:
        warnings.append(
            "flag_coherence.not_review_with_sensitive_domain: classification.not_review set "
            "together with sensitive-domain use; review justification."
        )

    # Check classified use + reduced-review exception but no profiling flags.
    if has_any_classification and not has_sensitive_domain_flag:
        has_exception_claimed = any(f.startswith("exception.reduced_review_") for f in final_flags)
        has_profiling = any(f.startswith("profiling.") for f in final_flags)

        if has_exception_claimed and not has_profiling:
            warnings.append(
                "flag_coherence.exception_without_profiling: reduced-review exception "
                "claimed for classified use case without profiling flags. Verify "
                "strictly that NO profiling (automated processing of personal data "
                "to evaluate aspects of a person) is performed, as this often "
                "invalidates the exception."
            )

    return warnings


def compute_role_warnings(roles: list[str]) -> list[str]:
    """Detect potential inconsistencies in role combinations.

    Checks for:
    - Provider + Importer combination
    - Provider + Distributor combination
    - Three or more roles simultaneously

    Args:
        roles: List of detected roles.

    Returns:
        List of warning messages for detected issues.

    Example:
        >>> warnings = compute_role_warnings(["provider", "importer"])
        >>> len(warnings) > 0
        True
        >>> warnings = compute_role_warnings(["provider", "deployer"])
        >>> len(warnings) == 0
        True
        >>> warnings = compute_role_warnings(["provider", "deployer", "distributor", "user"])
        >>> len(warnings) > 0
        True
    """
    roles_set = set(roles)
    warnings: list[str] = []

    if {"provider", "importer"} <= roles_set:
        warnings.append(
            "role_coherence.provider_and_importer: provider and importer roles "
            "detected for the same system; verify value-chain allocation (Art. 23–25)."
        )

    if {"provider", "distributor"} <= roles_set:
        warnings.append(
            "role_coherence.provider_and_distributor: provider and distributor "
            "roles detected simultaneously; verify contractual responsibilities."
        )

    if len(roles_set) >= 3:
        warnings.append(
            "role_coherence.many_roles: three or more roles detected; check if "
            "this matches the contractual structure."
        )

    return warnings
