# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Actionable CLI diagnostics without echoing submitted values."""

from __future__ import annotations

import json
from argparse import Namespace

from pydantic import ValidationError
from yaml import YAMLError

from intrinsical_policy_engine.common.document_io import safe_yaml_error
from intrinsical_policy_engine.domain.exceptions import AssessmentError, ContractValidationError
from intrinsical_policy_engine.domain.output_inventory import OutputCollisionError


class DocumentError(ValueError):
    def __init__(self, kind: str, source: str, cause: Exception) -> None:
        self.kind = kind
        self.source = source
        self.details = safe_details(cause)
        super().__init__(f"{kind}: {source}\n" + "\n".join(self.details))


def safe_details(error: Exception) -> list[str]:
    if isinstance(error, ValidationError):
        return [
            f"{'.'.join(str(part) for part in item['loc']) or 'document'}: {item['msg']}"
            for item in error.errors(include_url=False, include_input=False, include_context=False)
        ]
    if isinstance(error, YAMLError):
        return [safe_yaml_error(error)]
    if isinstance(error, json.JSONDecodeError):
        return [f"line {error.lineno}, column {error.colno}: {error.msg}"]
    if isinstance(error, UnicodeError):
        return ["Document must contain UTF-8 text"]
    return [str(error)]


def format_problem(problem: dict) -> str:
    location = ":".join(
        value for value in (problem.get("source", ""), problem.get("field_path", "")) if value
    )
    return f"[{problem['code']}] {location}: {problem['message']}"


def _hint(error: Exception, args: Namespace) -> str:
    message = str(error)
    if isinstance(error, OutputCollisionError):
        return "Give selected profile nodes distinct paths outside reserved engine namespaces."
    if "operational log" in message.lower() or "serialize log event" in message:
        return "Use regular --log-jsonl outside input/output paths; check directory permissions."
    if isinstance(error, DocumentError):
        if error.kind == "Configuration":
            return "Check --config field names and target requirements; use export --help."
        return "Check the input envelope and use inspect --contracts PACK for IDs and options."
    if isinstance(error, ContractValidationError):
        return "Fix the referenced pack file, then run lint --contracts PACK --strict."
    if isinstance(error, AssessmentError):
        return "Use inspect --contracts PACK to check question IDs, options and declared flags."
    if isinstance(error, FileExistsError):
        return "Choose a destination that does not exist; existing outputs are preserved."
    if isinstance(error, FileNotFoundError):
        return "Check the path; relative paths are resolved from the working directory."
    if "CI-" in message or "Missing evidence" in message:
        return "Supply completed evidence with --evidence-dir for every required path."
    if "base_date" in message or "calendar date" in message:
        return "Set --base-date YYYY-MM-DD when the pack uses relative calendar dates."
    if "GPG" in message or "signature" in message:
        return "Configure local GPG; --no-sign avoids new signing but verifies existing signatures."
    return f"Use {args.command} --help to check arguments and defaults."


def format_error(error: Exception, args: Namespace) -> str:
    if isinstance(error, ContractValidationError) and error.problems:
        details = [format_problem(problem) for problem in error.problems]
    elif isinstance(error, DocumentError):
        details = [f"{error.kind}: {error.source}", *error.details]
    elif isinstance(error, AssessmentError):
        details = [f"Input: {args.answers}", *safe_details(error)]
    else:
        details = safe_details(error)
    return (
        f"Error executing '{args.command}':\n"
        + "\n".join(f"  {line}" for detail in details for line in detail.splitlines())
        + f"\nHint: {_hint(error, args)}\n"
    )
