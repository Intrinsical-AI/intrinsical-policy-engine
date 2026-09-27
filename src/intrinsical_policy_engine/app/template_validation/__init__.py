# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Template analysis and framework-pack referential integrity checks."""

from intrinsical_policy_engine.app.template_validation.integrity import (
    IntegrityIssue,
    IntegrityReport,
    validate_integrity,
)
from intrinsical_policy_engine.app.template_validation.template_validator import (
    TemplateValidationResult,
    TemplateValidator,
    validate_templates,
)

__all__ = [
    "IntegrityIssue",
    "IntegrityReport",
    "TemplateValidationResult",
    "TemplateValidator",
    "validate_integrity",
    "validate_templates",
]
