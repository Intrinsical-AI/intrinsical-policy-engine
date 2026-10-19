# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Errors exposed by pack compilation, assessment, and target selection."""

from typing import Any


class EngineError(Exception):
    """An expected engine failure that the CLI reports without a traceback."""


class ContractValidationError(EngineError):
    """A pack failed compilation, with source diagnostics when available."""

    def __init__(
        self,
        message: str,
        problems: list[dict[str, Any]] | None = None,
        contract_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.problems = problems or []
        self.contract_path = contract_path


class AssessmentError(EngineError):
    """An assessment input or policy evaluation failed."""


class ExporterNotFoundError(EngineError):
    """The requested target has no registered exporter."""

    def __init__(self, target: str, available: list[str] | None = None) -> None:
        self.target = target
        self.available = available or []
        message = f"Unknown exporter target: {target}"
        if self.available:
            message += ". Available: " + ", ".join(self.available)
        super().__init__(message)
