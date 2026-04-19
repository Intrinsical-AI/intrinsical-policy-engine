# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Domain-specific exceptions for framework-neutral assessment tracking.

This module defines a hierarchy of exceptions that provide better error handling
and debugging capabilities compared to generic ValueError/KeyError exceptions.
"""

from __future__ import annotations

from typing import Any


class AIActError(Exception):
    """Base exception for framework-neutral assessment tracking errors."""

    pass


# Contract and Validation Errors


class ContractError(AIActError):
    """Base exception for contract-related errors."""

    pass


class ContractValidationError(ContractError):
    """Raised when contract validation fails.

    Attributes:
        problems: List of validation problems found in the contract
        contract_path: Path to the contract file that failed validation
    """

    def __init__(
        self,
        message: str,
        problems: list[dict[str, Any]] | None = None,
        contract_path: str | None = None,
    ):
        """Store validation problems and offending path."""
        super().__init__(message)
        self.problems = problems or []
        self.contract_path = contract_path

    def __str__(self) -> str:
        """Include problem counts when available."""
        base = super().__str__()
        if self.problems:
            problem_count = len(self.problems)
            return f"{base} ({problem_count} problem{'s' if problem_count > 1 else ''})"
        return base


class SchemaValidationError(ContractValidationError):
    """Raised when JSON Schema validation fails."""

    pass


class CrossReferenceError(ContractValidationError):
    """Raised when cross-reference validation fails."""

    pass


class YAMLLoadError(ContractError):
    """Raised when YAML file loading fails.

    Attributes:
        file_path: Path to the YAML file that failed to load
        yaml_error: Original YAML error message
    """

    def __init__(
        self,
        message: str,
        file_path: str | None = None,
        yaml_error: str | None = None,
    ):
        """Capture additional YAML context for debugging."""
        super().__init__(message)
        self.file_path = file_path
        self.yaml_error = yaml_error


class ContractTypeError(ContractValidationError):
    """Raised when contract data has incorrect type structure."""

    pass


class StrictContractViolation(ContractValidationError):
    """Raised in strict mode when contracts have validation errors.

    Attributes:
        error_count: Number of validation errors found
        critical_errors: List of critical error messages
    """

    def __init__(
        self,
        message: str,
        error_count: int = 0,
        critical_errors: list[str] | None = None,
    ):
        """Attach strict-mode error counts and detailed messages."""
        super().__init__(message)
        self.error_count = error_count
        self.critical_errors = critical_errors or []


# Export Configuration Errors


class ExportError(AIActError):
    """Base exception for export-related errors."""

    pass


class ExportPathError(ExportError):
    """Raised when an export path is invalid or attempts traversal."""

    pass


class ExportConfigError(ExportError):
    """Raised when export configuration is invalid or incomplete."""

    pass


class StrictModeViolation(ExportConfigError):
    """Raised when strict mode requirements are not met.

    Attributes:
        missing_keys: List of required configuration keys that are missing
        target: The export target (e.g., 'asana', 'jira', 'linear')
    """

    def __init__(
        self,
        message: str,
        missing_keys: list[str] | None = None,
        target: str | None = None,
    ):
        """Capture which keys/targets violate the strict-mode requirements."""
        super().__init__(message)
        self.missing_keys = missing_keys or []
        self.target = target

    def __str__(self) -> str:
        """Append missing key details to the base error message."""
        base = super().__str__()
        if self.missing_keys:
            keys = ", ".join(self.missing_keys)
            return f"{base} - Missing: {keys}"
        return base


class ExporterNotFoundError(ExportError):
    """Raised when requested exporter is not registered."""

    def __init__(self, target: str, available: list[str] | None = None):
        """Report unavailable exporter target along with known ones."""
        self.target = target
        self.available = available or []
        message = f"Unknown exporter target: {target}"
        if self.available:
            message += f". Available: {', '.join(self.available)}"
        super().__init__(message)


class ExportConsistencyError(ExportError):
    """Raised when export integrity checks fail (e.g., plan hash recomputation)."""

    pass


class TemplateNotFoundError(ExportError):
    """Raised when required template is missing in strict mode."""

    def __init__(self, template_name: str, templates_dir: str):
        """Persist the missing template details for downstream handlers."""
        self.template_name = template_name
        self.templates_dir = templates_dir
        super().__init__(
            f"Template '{template_name}' not found in {templates_dir} (strict mode enabled)"
        )


class EvidenceRequiredError(ExportError):
    """Raised when strict mode requires evidence but none is found."""

    def __init__(self, article_count: int):
        """Emit strict-mode evidence requirement failure with article count."""
        self.article_count = article_count
        super().__init__(
            f"Strict mode requires at least one evidence file, "
            f"but none found for {article_count} articles"
        )


# Rule Engine Errors


class RuleEngineError(AIActError):
    """Base exception for rule engine errors."""

    pass


class RuleParseError(RuleEngineError):
    """Raised when a 'when' expression cannot be parsed."""

    def __init__(self, expression: str, reason: str):
        """Store the invalid expression and parse reason."""
        self.expression = expression
        self.reason = reason
        super().__init__(f"Failed to parse rule expression '{expression}': {reason}")


class RuleEvaluationError(RuleEngineError):
    """Raised when rule evaluation fails at runtime."""

    pass


# Quality and Assessment Errors


class QualityError(AIActError):
    """Base exception for evidence quality errors."""

    pass


class AssessmentError(AIActError):
    """Base exception for assessment-related errors."""

    pass


class InvalidArticleError(AssessmentError):
    """Raised when an invalid article ID is referenced."""

    def __init__(self, article_id: str, valid_articles: list[str] | None = None):
        """Include offending id and optionally suggested valid ids."""
        self.article_id = article_id
        self.valid_articles = valid_articles or []
        message = f"Invalid article ID: {article_id}"
        if self.valid_articles:
            message += f". Valid articles: {', '.join(self.valid_articles)}"
        super().__init__(message)


# Fingerprint and Evidence Errors


class FingerprintError(AIActError):
    """Base exception for fingerprint generation errors."""

    pass


class InvalidFileError(FingerprintError):
    """Raised when a file cannot be read for fingerprinting."""

    def __init__(self, file_path: str, reason: str):
        """Capture file path and failure reason for fingerprinting operations."""
        self.file_path = file_path
        self.reason = reason
        super().__init__(f"Cannot read file '{file_path}' for fingerprint: {reason}")
