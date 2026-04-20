# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Static Jinja2 template analysis."""

from src.app.template_validation.template_validator import (
    TemplateValidationResult,
    TemplateValidator,
    validate_templates,
)

__all__ = [
    "TemplateValidationResult",
    "TemplateValidator",
    "validate_templates",
]
