# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Compatibility wrapper for the canonical runtime template validator."""

from intrinsical_policy_engine.app.template_validation import (
    IntegrityIssue,
    IntegrityReport,
    validate_integrity,
)

__all__ = ["IntegrityIssue", "IntegrityReport", "validate_integrity"]
