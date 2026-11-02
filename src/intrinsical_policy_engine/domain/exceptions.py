# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Errors exposed by pack compilation and assessment."""

from typing import Any


class EngineError(Exception):
    """An expected engine failure that the CLI reports without a traceback."""


class ContractValidationError(EngineError):
    """A pack failed compilation, with source diagnostics when available."""

    def __init__(self, message: str, problems: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


class AssessmentError(EngineError):
    """An assessment input or policy evaluation failed."""
